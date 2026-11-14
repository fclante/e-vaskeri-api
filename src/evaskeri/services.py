import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from . import parsers
from .config import Config
from .metrics import LAST_REFRESH, LIVE_CRC_MISMATCH, REFRESH_CHECKS, REFRESH_SKIPS
from .upstream import NOT_AVAILABLE_RETURN, UpstreamClient, UpstreamError

log = logging.getLogger(__name__)

LIVE_PATH = "/Location/Live5/"
STATEMENT_PATH = "/User/Statement5/"
PERIODS_PATH = "/User/Statement4/"
CALENDAR_PATH = "/Location/Calendar5/"
PRICES_PATH = "/Location/Prices1/"
MESSAGES_PATH = "/User/Messages1/"
REFRESH_PATH = "/User/Refresh1/"

# Refresh1 tab ids that expose a usable CRC (Home returns "0" and the others none).
STATEMENT_TAB = 2
LIVE_TAB = 3

MAX_CACHE_ENTRIES = 128
MIN_REFRESH_RATE, MAX_REFRESH_RATE = 1.0, 60.0


class TTLCache:
    """Small thread-safe cache that serves stale data if a refresh fails.

    Once an entry is older than its TTL, an optional `revalidate` callback can confirm it is still current
    (e.g. an unchanged CRC) so the full loader is skipped, until `max_age` forces a real reload.
    """

    def __init__(self) -> None:
        # key -> (last checked, value, loaded at)
        self._data: dict[str, tuple[float, Any, float]] = {}
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def _evict(self) -> None:
        with self._lock:
            while len(self._data) > MAX_CACHE_ENTRIES:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                del self._data[oldest]
                lock = self._key_locks.get(oldest)
                if lock is not None and not lock.locked():
                    del self._key_locks[oldest]

    def get_or_load(
        self, key: str, ttl: float, loader: Callable[[], Any],
        revalidate: Callable[[Any], bool] | None = None, max_age: float | None = None,
    ) -> Any:
        with self._lock:
            lock = self._key_locks.setdefault(key, threading.Lock())
        with lock:
            cached = self._data.get(key)
            now = time.monotonic()
            if cached and now - cached[0] < ttl:
                return cached[1]
            if cached and revalidate and (max_age is None or now - cached[2] < max_age):
                try:
                    unchanged = revalidate(cached[1])
                except UpstreamError:
                    unchanged = False
                if unchanged:
                    self._data[key] = (now, cached[1], cached[2])
                    return cached[1]
            try:
                value = loader()
            except UpstreamError:
                if cached:
                    log.warning("serving stale data", extra={"key": key})
                    return cached[1]
                raise
            loaded = time.monotonic()
            self._data[key] = (loaded, value, loaded)
        self._evict()
        return value


class ChangeDetector:
    """Cheap 'did anything change?' checks against Refresh1, throttled by the upstream Rate hint."""

    def __init__(self, upstream: UpstreamClient):
        self.upstream = upstream
        self._lock = threading.Lock()
        # tab -> (checked at, crc, minimum seconds between checks)
        self._last: dict[int, tuple[float, str | None, float]] = {}

    def check(self, tab: int, fresh: bool = False) -> str | None:
        """The tab's current CRC, or None when it cannot be determined (callers then fetch normally)."""
        with self._lock:
            last = self._last.get(tab)
            if last and not fresh and time.monotonic() - last[0] < last[2]:
                return last[1]
        # Login problems must surface to the caller as usual, only a failing Refresh1 call itself is tolerated.
        self.upstream.session_token()
        try:
            data = self.upstream.get_json(REFRESH_PATH, "refresh", params={"tabid": tab})
        except UpstreamError:
            REFRESH_CHECKS.labels(str(tab), "error").inc()
            return None
        crc = data.get("CRC")
        crc = None if crc in (None, "") else str(crc)
        try:
            rate = min(max(float(data.get("Rate")), MIN_REFRESH_RATE), MAX_REFRESH_RATE)
        except (TypeError, ValueError):
            rate = MIN_REFRESH_RATE
        with self._lock:
            previous = self._last.get(tab)
            self._last[tab] = (time.monotonic(), crc, rate)
        REFRESH_CHECKS.labels(str(tab), "unchanged" if previous and previous[1] == crc else "changed").inc()
        return crc


class LaundryService:
    def __init__(self, config: Config, upstream: UpstreamClient):
        self.config = config
        self.upstream = upstream
        self.cache = TTLCache()
        self.detector = ChangeDetector(upstream)
        self._live_previous: tuple[str | None, tuple] | None = None

    def _account_revalidator(self, source: str) -> Callable[[Any], bool] | None:
        if not self.config.refresh_check:
            return None

        def revalidate(value: dict) -> bool:
            unchanged = value.get("crc") is not None and self.detector.check(STATEMENT_TAB) == value["crc"]
            if unchanged:
                REFRESH_SKIPS.labels(source).inc()
            return unchanged

        return revalidate

    def _statement_crc(self) -> str | None:
        return self.detector.check(STATEMENT_TAB, fresh=True) if self.config.refresh_check else None

    def _shadow_live(self, crc: str | None, parsed: dict) -> None:
        """Compare the Live CRC with the parsed data without ever skipping a fetch."""
        fingerprint = tuple((u["name"], u["raw_status"]) for u in parsed["units"])
        previous, self._live_previous = self._live_previous, (crc, fingerprint)
        if previous is None or crc is None or previous[0] is None:
            return
        if fingerprint != previous[1] and crc == previous[0]:
            LIVE_CRC_MISMATCH.labels("data_changed_crc_same").inc()
            log.warning("live data changed but CRC did not")
        elif fingerprint == previous[1] and crc != previous[0]:
            LIVE_CRC_MISMATCH.labels("crc_changed_data_same").inc()

    def _live(self) -> dict:
        observe = self.config.refresh_shadow_live or self.config.refresh_skip_live

        def load() -> dict:
            crc = self.detector.check(LIVE_TAB, fresh=True) if observe else None
            payload = self.upstream.get_json(LIVE_PATH, "live")
            LAST_REFRESH.labels("live").set(time.time())
            parsed = parsers.parse_live(payload)
            if self.config.refresh_shadow_live:
                self._shadow_live(crc, parsed)
            return {**parsed, "crc": crc}

        def revalidate(value: dict) -> bool:
            # A running machine's progress is not proven to move the CRC, so only an all-free room is skipped.
            if any(parsers.normalize_state(u["raw_status"]) != "free" for u in value["units"]):
                return False
            unchanged = value.get("crc") is not None and self.detector.check(LIVE_TAB, fresh=True) == value["crc"]
            if unchanged:
                REFRESH_SKIPS.labels("live").inc()
            return unchanged

        return self.cache.get_or_load(
            "live", self.config.machines_ttl, load,
            revalidate=revalidate if self.config.refresh_skip_live else None, max_age=self.config.live_max_age,
        )

    def _svg(self, unit: dict) -> dict | None:
        if not unit.get("svg"):
            return None
        try:
            return parsers.parse_unit_svg(self.upstream.get_text(self.upstream.svg_url(unit["svg"]), "unit_svg"))
        except UpstreamError:
            log.warning("could not read unit detail", extra={"unit": unit["name"]})
            return None

    def unit_status(self, name: str) -> dict | None:
        """Raw SVG-derived status for a unit, looked up by its exact name (e.g. 'Vaskemaskine 1')."""
        unit = next((u for u in self._live()["units"] if u["name"].casefold() == name.casefold()), None)
        return None if unit is None else (self._svg(unit) or {})

    def machines(self) -> dict:
        def load() -> dict:
            live = self._live()
            now = datetime.now(timezone.utc)
            units = live["units"]
            busy = [u for u in units if parsers.normalize_state(u["raw_status"]) != "free"]
            details: dict[str, dict | None] = {}
            if busy:
                with ThreadPoolExecutor(max_workers=min(6, len(busy))) as pool:
                    for unit, svg in zip(busy, pool.map(self._svg, busy)):
                        details[unit["name"]] = svg
            machines = [parsers.build_machine(u, details.get(u["name"]), now) for u in units]
            return {
                "location": live["location"],
                "updated_at": now.isoformat(),
                "upstream_updated": live["upstream_updated"],
                "summary": parsers.summarize_machines(machines),
                "machines": machines,
            }

        return self.cache.get_or_load("machines", self.config.machines_ttl, load)

    def machine(self, machine_id: int) -> dict | None:
        data = self.machines()
        found = next((m for m in data["machines"] if m["id"] == machine_id), None)
        return None if found is None else {"location": data["location"], "updated_at": data["updated_at"], **found}

    def _statement_payload(self) -> dict:
        def load() -> dict:
            crc = self._statement_crc()
            payload = self.upstream.get_json(STATEMENT_PATH, "statement")
            LAST_REFRESH.labels("statement").set(time.time())
            return {
                "balance": parsers.parse_balance(payload),
                "entries": parsers.parse_statement(payload),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "crc": crc,
            }

        return self.cache.get_or_load(
            "statement", self.config.account_ttl, load,
            revalidate=self._account_revalidator("statement"), max_age=self.config.account_max_age,
        )

    def _periods_payload(self) -> dict:
        def load() -> dict:
            crc = self._statement_crc()
            payload = self.upstream.get_json(PERIODS_PATH, "periods")
            LAST_REFRESH.labels("periods").set(time.time())
            return {
                "periods": parsers.parse_periods(payload),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "crc": crc,
            }

        return self.cache.get_or_load(
            "periods", self.config.account_ttl, load,
            revalidate=self._account_revalidator("periods"), max_age=self.config.account_max_age,
        )

    def _optional(self, key: str, ttl: float, path: str, endpoint: str, parse: Callable[[dict], Any], params: dict | None = None) -> dict:
        """Fetch a page that is not enabled for every location (Return 110 means 'not available here')."""
        def load() -> dict:
            payload = self.upstream.get_json(path, endpoint, params=params, accept=(NOT_AVAILABLE_RETURN,))
            LAST_REFRESH.labels(endpoint).set(time.time())
            return {
                "available": payload.get("Return") != NOT_AVAILABLE_RETURN,
                "data": parse(payload),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

        return self.cache.get_or_load(key, ttl, load)

    def balance(self) -> dict:
        data = self._statement_payload()
        return {**data["balance"], "updated_at": data["fetched_at"]}

    def statement(self, limit: int = 50, since: str | None = None) -> dict:
        data = self._statement_payload()
        entries = [e for e in data["entries"] if not since or (e["date"] and e["date"] >= since)]
        return {"updated_at": data["fetched_at"], "total": len(entries), "entries": entries[:limit]}

    def stats(self, months: int = 12) -> dict:
        data = self._statement_payload()
        return {"updated_at": data["fetched_at"], **parsers.compute_stats(data["entries"], months)}

    def periods(self, limit: int = 30, details: bool = False) -> dict:
        data = self._periods_payload()
        periods = [p if details else {k: v for k, v in p.items() if k != "days"} for p in data["periods"][:limit]]
        return {"updated_at": data["fetched_at"], "total": len(data["periods"]), "periods": periods}

    def calendar(self, date: str | None = None, days: int = 7, calendar_id: int = 0) -> dict:
        params = {"calendarid": calendar_id, "days": days, **({"date": date} if date else {})}
        entry = self._optional(
            f"calendar:{calendar_id}:{date}:{days}", self.config.calendar_ttl, CALENDAR_PATH, "calendar",
            parsers.parse_calendar, params,
        )
        return {"updated_at": entry["fetched_at"], "available": entry["available"], **entry["data"]}

    def prices(self) -> dict:
        entry = self._optional("prices", self.config.account_ttl, PRICES_PATH, "prices", parsers.parse_prices)
        return {"updated_at": entry["fetched_at"], "available": entry["available"], **entry["data"]}

    def messages(self, limit: int = 20) -> dict:
        entry = self._optional("messages", self.config.account_ttl, MESSAGES_PATH, "messages", parsers.parse_messages)
        return {
            "updated_at": entry["fetched_at"], "available": entry["available"],
            "total": len(entry["data"]), "messages": entry["data"][:limit],
        }
