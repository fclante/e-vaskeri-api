import threading
import time
from typing import Callable

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from . import __version__

REGISTRY = CollectorRegistry()

HTTP_REQUESTS = Counter(
    "evaskeri_http_requests_total", "HTTP requests handled", ["method", "route", "status"], registry=REGISTRY
)
HTTP_DURATION = Histogram(
    "evaskeri_http_request_duration_seconds", "HTTP request duration", ["method", "route"], registry=REGISTRY
)
UPSTREAM_REQUESTS = Counter(
    "evaskeri_upstream_requests_total", "Requests to the upstream API", ["endpoint", "outcome"], registry=REGISTRY
)
UPSTREAM_DURATION = Histogram(
    "evaskeri_upstream_request_duration_seconds", "Upstream request duration", ["endpoint"], registry=REGISTRY
)
UPSTREAM_LOGIN = Counter(
    "evaskeri_upstream_login_total", "Upstream login attempts", ["outcome"], registry=REGISTRY
)
LAST_REFRESH = Gauge(
    "evaskeri_last_successful_refresh_timestamp_seconds", "Unix time of the last successful data refresh",
    ["source"], registry=REGISTRY,
)
BUILD_INFO = Gauge("evaskeri_build_info", "Build information", ["version"], registry=REGISTRY)
BUILD_INFO.labels(__version__).set(1)
REFRESH_CHECKS = Counter(
    "evaskeri_refresh_checks_total", "Upstream Refresh1 change checks", ["tab", "outcome"], registry=REGISTRY
)
REFRESH_SKIPS = Counter(
    "evaskeri_refresh_skipped_total", "Full upstream fetches avoided because Refresh1 reported no change",
    ["source"], registry=REGISTRY,
)
LIVE_CRC_MISMATCH = Counter(
    "evaskeri_live_crc_mismatch_total",
    "Shadow-mode disagreements between the Live CRC and the parsed Live data", ["kind"], registry=REGISTRY,
)


class UpstreamHealth:
    """Tracks upstream success/failure for the readiness probe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.last_success: float | None = None
        self.last_failure: float | None = None

    def reset(self) -> None:
        with self._lock:
            self.last_success = self.last_failure = None

    def success(self) -> None:
        with self._lock:
            self.last_success = time.time()

    def failure(self) -> None:
        with self._lock:
            self.last_failure = time.time()

    def is_ready(self, window: float) -> tuple[bool, str]:
        with self._lock:
            if self.last_failure is None:
                return True, "ok"
            if self.last_success is not None and time.time() - self.last_success <= window:
                return True, "ok"
            return False, "upstream unavailable"


HEALTH = UpstreamHealth()


def mark_success() -> None:
    HEALTH.success()


def mark_failure() -> None:
    HEALTH.failure()


class DomainCollector(Collector):
    """Exposes machine and balance gauges computed from cached upstream data at scrape time."""

    def __init__(self) -> None:
        self.machines: Callable[[], dict] | None = None
        self.balance: Callable[[], dict] | None = None
        self.stats: Callable[[], dict] | None = None

    def collect(self):
        busy = GaugeMetricFamily("evaskeri_machine_busy", "1 if the machine is in use", labels=["id", "name", "type"])
        remaining = GaugeMetricFamily(
            "evaskeri_machine_remaining_seconds", "Seconds left on the running program", labels=["id", "name", "type"]
        )
        free = GaugeMetricFamily("evaskeri_machines_free", "Free machines per type", labels=["type"])
        balance = GaugeMetricFamily("evaskeri_balance_dkk", "Current balance in DKK (negative = owed)")
        spend = GaugeMetricFamily("evaskeri_period_spend_dkk", "Spend in the current calendar month in DKK")

        data = self._safe(self.machines)
        if data:
            for machine in data["machines"]:
                labels = [str(machine["id"]), machine["name"], machine["type"]]
                busy.add_metric(labels, 1 if machine["state"] in ("busy", "done") else 0)
                if machine["remaining_seconds"] is not None:
                    remaining.add_metric(labels, machine["remaining_seconds"])
            for kind, counts in data["summary"]["by_type"].items():
                free.add_metric([kind], counts["free"])
        bal = self._safe(self.balance)
        if bal and bal.get("balance_dkk") is not None:
            balance.add_metric([], bal["balance_dkk"])
        stats = self._safe(self.stats)
        if stats:
            spend.add_metric([], stats["current_month_spend_dkk"])
        yield from (busy, remaining, free, balance, spend)

    @staticmethod
    def _safe(provider: Callable[[], dict] | None) -> dict | None:
        if provider is None:
            return None
        try:
            return provider()
        except Exception:  # a failing upstream must not break the scrape
            return None


DOMAIN = DomainCollector()
REGISTRY.register(DOMAIN)
