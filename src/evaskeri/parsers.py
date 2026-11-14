"""Pure functions that turn the upstream payloads into clean, typed data."""
import html
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

MONTHS = {
    "januar": 1, "februar": 2, "marts": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
}

UNIT_TYPES = (
    ("vaskemaskine", "washer"),
    ("washer", "washer"),
    ("tørretumbler", "dryer"),
    ("dryer", "dryer"),
    ("strygerulle", "mangle"),
    ("mangle", "mangle"),
)

FREE_STATES = {"fri", "free", "ledig"}
BUSY_STATES = {"optaget", "busy", "occupied", "i brug"}
DONE_STATES = {"færdig", "faerdig", "done", "finished"}
ERROR_STATES = {"fejl", "error", "ude af drift", "out of order"}


def parse_money(text: str | None) -> float | None:
    """'-1.116,40 DKK' -> -1116.4"""
    if not text:
        return None
    match = re.search(r"-?\d[\d.]*(?:,\d+)?", text.replace("\u2212", "-").replace(" ", ""))
    if not match:
        return None
    return round(float(match.group(0).replace(".", "").replace(",", ".")), 2)


def ore_to_dkk(amount: int | float | None) -> float | None:
    return None if amount is None else round(amount / 100, 2)


def parse_title_date(title: str | None) -> str | None:
    """'Fredag 6.  november 2026' -> '2026-11-06'"""
    if not title:
        return None
    match = re.search(r"(\d{1,2})\.\s*([A-Za-zæøåÆØÅ]+)\s+(\d{4})", title)
    if not match:
        return None
    month = MONTHS.get(match.group(2).lower())
    if not month:
        return None
    try:
        return datetime(int(match.group(3)), month, int(match.group(1))).date().isoformat()
    except ValueError:
        return None


def unit_type(name: str) -> str:
    lowered = name.lower()
    for prefix, kind in UNIT_TYPES:
        if lowered.startswith(prefix):
            return kind
    return "other"


def unit_number(name: str) -> int | None:
    match = re.search(r"(\d+)\s*$", name)
    return int(match.group(1)) if match else None


def parse_remaining_seconds(text: str | None) -> int | None:
    """'31:41' is MM:SS, '1:02:03' is H:MM:SS."""
    if not text:
        return None
    parts = text.strip().split(":")
    if not all(p.isdigit() for p in parts) or not 2 <= len(parts) <= 3:
        return None
    numbers = [int(p) for p in parts]
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]


def normalize_state(raw_status: str | None, remaining_seconds: int | None = None) -> str:
    status = (raw_status or "").strip().casefold()
    if status in FREE_STATES:
        return "free"
    if status in BUSY_STATES:
        return "busy"
    if status in DONE_STATES:
        return "done"
    if status in ERROR_STATES:
        return "error"
    if remaining_seconds:
        return "busy"
    return "unknown"


_SVG_FIELDS = {"P": "program", "C": "current_step", "S": "status", "R": "remaining_time"}


def parse_unit_svg(svg: str) -> dict[str, str | None]:
    """Extract program/step/status/remaining text from a unit SVG."""
    result: dict[str, str | None] = {name: None for name in _SVG_FIELDS.values()}
    for key, value in re.findall(r'<text id="([PCSR])\d+LAU\d+"[^>]*>(.*?)</text>', svg, re.DOTALL):
        name = _SVG_FIELDS[key]
        if result[name] is None:
            result[name] = html.unescape(re.sub(r"<[^>]+>", "", value)).strip() or None
    return result


def parse_live(payload: dict) -> dict:
    """Flatten the Live5 payload into location info and a unit list."""
    location, updated_label, units = None, None, []
    for section in payload.get("Sections") or []:
        location = location or section.get("Title")
        for row in section.get("Rows") or []:
            for unit in row.get("Units") or []:
                name = unit.get("Text") or ""
                units.append({
                    "id": unit_number(name),
                    "name": name,
                    "type": unit_type(name),
                    "raw_status": unit.get("Status"),
                    "svg": unit.get("SVG"),
                })
            cells = (row.get("Columns2") or {}).get("Cells") or []
            for cell in cells:
                match = re.search(r"(\d{1,2}:\d{2})", cell or "")
                if match:
                    updated_label = match.group(1)
    return {"location": location, "upstream_updated": updated_label, "units": units}


def build_machine(unit: dict, svg: dict | None, now: datetime) -> dict:
    svg = svg or {}
    remaining = parse_remaining_seconds(svg.get("remaining_time"))
    raw_status = svg.get("status") or unit.get("raw_status")
    state = normalize_state(raw_status, remaining)
    if state == "unknown":
        state = normalize_state(unit.get("raw_status"), remaining)
    ends_at = (now + timedelta(seconds=remaining)).isoformat() if remaining is not None and state == "busy" else None
    return {
        "id": unit["id"],
        "name": unit["name"],
        "type": unit["type"],
        "state": state,
        "raw_status": raw_status,
        "program": svg.get("program"),
        "step": svg.get("current_step"),
        "remaining_seconds": remaining,
        "ends_at": ends_at,
    }


def summarize_machines(machines: list[dict]) -> dict:
    by_type: dict[str, dict[str, int]] = {}
    for machine in machines:
        entry = by_type.setdefault(machine["type"], {"total": 0, "free": 0, "busy": 0})
        entry["total"] += 1
        if machine["state"] == "free":
            entry["free"] += 1
        elif machine["state"] in ("busy", "done"):
            entry["busy"] += 1
    busy_with_end = [m for m in machines if m.get("ends_at")]
    next_free = min(busy_with_end, key=lambda m: m["ends_at"]) if busy_with_end else None
    return {
        "total": len(machines),
        "free": sum(1 for m in machines if m["state"] == "free"),
        "busy": sum(1 for m in machines if m["state"] in ("busy", "done")),
        "by_type": by_type,
        "next_free": None if next_free is None else {
            "id": next_free["id"], "name": next_free["name"], "ends_at": next_free["ends_at"],
        },
    }


def parse_balance(payload: dict) -> dict:
    """Pull the balance out of the Saldo section, falling back to the newest receipt."""
    current = previous = None
    for section in payload.get("Sections") or []:
        for row in section.get("Rows") or []:
            cells = (row.get("Columns2") or {}).get("Cells") or []
            if len(cells) < 2:
                continue
            label = cells[0].lower()
            if "nuværende" in label or "current" in label:
                current = parse_money(cells[1])
            elif "sidste" in label or "previous" in label or "last" in label:
                previous = parse_money(cells[1])
    receipts = payload.get("Receipts") or []
    if current is None and receipts:
        current = ore_to_dkk(receipts[0].get("Saldo"))
    entries = parse_statement(payload)
    payments = [e for e in entries if e["kind"] == "payment"]
    last_payment = payments[0] if payments else None
    return {
        "currency": payload.get("Currency") or "DKK",
        "balance_dkk": current,
        "owed_dkk": None if current is None else round(max(0.0, -current), 2),
        "previous_period_dkk": previous,
        "last_payment": None if last_payment is None else {
            "date": last_payment["date"],
            "amount_dkk": abs(last_payment["amount_dkk"]),
            "description": last_payment["description"],
        },
    }


_PROGRAM_RE = re.compile(r"Program\s+(\d+)", re.IGNORECASE)
_TEMP_RE = re.compile(r"(\d+)\s*°\s*C?", re.IGNORECASE)
_MINUTES_RE = re.compile(r"(\d+)\s*min", re.IGNORECASE)


def _usage_entry(date: str | None, time: str | None, unit_label: str, unit_id: int | None, text: str, amount_dkk: float | None) -> dict:
    program = _PROGRAM_RE.search(text)
    temperature = _TEMP_RE.search(text)
    minutes = _MINUTES_RE.search(text)
    return {
        "date": date,
        "time": time,
        "kind": "usage",
        "unit_id": unit_id or None,
        "unit_type": unit_type(unit_label),
        "unit_label": unit_label,
        "description": text,
        "program": int(program.group(1)) if program else None,
        "temperature_c": int(temperature.group(1)) if temperature else None,
        "duration_min": int(minutes.group(1)) if minutes else None,
        "amount_dkk": amount_dkk,
    }


def parse_statement(payload: dict) -> list[dict]:
    """Flatten receipts into dated entries, newest first."""
    entries = []
    for receipt in payload.get("Receipts") or []:
        date = parse_title_date(receipt.get("Title"))
        for line in receipt.get("Lines") or []:
            entries.append(_usage_entry(
                date, line.get("Time"), line.get("Unit") or "", line.get("UnitId"),
                line.get("Text") or "", ore_to_dkk(line.get("Amount")),
            ))
        for payment in receipt.get("Payments") or []:
            entries.append({
                "date": date,
                "time": payment.get("Time"),
                "kind": "payment",
                "unit_id": None,
                "unit_type": None,
                "unit_label": None,
                "description": payment.get("Text") or "",
                "program": None,
                "temperature_c": None,
                "duration_min": None,
                "amount_dkk": ore_to_dkk(payment.get("Amount")),
            })
    entries.sort(key=lambda e: (e["date"] or "", e["time"] or ""), reverse=True)
    return entries


def _months_back(end: str, count: int) -> list[str]:
    year, month = int(end[:4]), int(end[5:7])
    months = []
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return months[::-1]


def compute_stats(entries: list[dict], months: int = 12) -> dict:
    """Aggregate usage entries so clients can render charts without extra maths."""
    usage = [e for e in entries if e["kind"] == "usage" and e["date"]]
    if not usage:
        return {
            "months": [], "totals": {"washer_dkk": 0.0, "dryer_dkk": 0.0, "total_dkk": 0.0, "washes": 0, "dries": 0},
            "current_month_spend_dkk": 0.0, "avg_cost_per_wash_dkk": None, "avg_cost_per_dry_dkk": None,
            "favourite_machine": None, "top_programs": [], "top_temperatures": [],
            "heatmap": [[0] * 24 for _ in range(7)],
        }
    per_month: dict[str, dict[str, float]] = defaultdict(lambda: {"washer": 0.0, "dryer": 0.0, "other": 0.0})
    for e in usage:
        per_month[e["date"][:7]][e["unit_type"] if e["unit_type"] in ("washer", "dryer") else "other"] += e["amount_dkk"] or 0.0

    newest = max(e["date"] for e in usage)[:7]
    month_rows = [
        {
            "month": m,
            "washer_dkk": round(per_month[m]["washer"], 2),
            "dryer_dkk": round(per_month[m]["dryer"], 2),
            "total_dkk": round(sum(per_month[m].values()), 2),
        }
        for m in _months_back(newest, months)
    ]

    washes = [e for e in usage if e["unit_type"] == "washer"]
    dries = [e for e in usage if e["unit_type"] == "dryer"]
    washer_total = sum(e["amount_dkk"] or 0 for e in washes)
    dryer_total = sum(e["amount_dkk"] or 0 for e in dries)

    machines = Counter((e["unit_label"]) for e in usage if e["unit_label"])
    favourite = machines.most_common(1)
    programs = Counter(e["program"] for e in washes if e["program"] is not None)
    temperatures = Counter(e["temperature_c"] for e in washes if e["temperature_c"] is not None)

    heatmap = [[0] * 24 for _ in range(7)]
    for e in usage:
        try:
            weekday = datetime.fromisoformat(e["date"]).weekday()
            hour = int((e["time"] or "0:0").split(":")[0])
        except (ValueError, TypeError):
            continue
        if 0 <= hour < 24:
            heatmap[weekday][hour] += 1

    return {
        "months": month_rows,
        "totals": {
            "washer_dkk": round(washer_total, 2),
            "dryer_dkk": round(dryer_total, 2),
            "total_dkk": round(washer_total + dryer_total, 2),
            "washes": len(washes),
            "dries": len(dries),
        },
        "current_month_spend_dkk": month_rows[-1]["total_dkk"],
        "avg_cost_per_wash_dkk": round(washer_total / len(washes), 2) if washes else None,
        "avg_cost_per_dry_dkk": round(dryer_total / len(dries), 2) if dries else None,
        "favourite_machine": None if not favourite else {"name": favourite[0][0], "count": favourite[0][1]},
        "top_programs": [{"program": p, "count": c} for p, c in programs.most_common(5)],
        "top_temperatures": [{"temperature_c": t, "count": c} for t, c in temperatures.most_common(5)],
        "heatmap": heatmap,
    }


_RANGE_RE = re.compile(
    r"(\d{1,2})\.?(?:\s*([A-Za-zæøåÆØÅ]+))?\s*-\s*(\d{1,2})\.\s*([A-Za-zæøåÆØÅ]+)\s*(\d{4})"
)


def parse_period_title(title: str | None) -> tuple[str | None, str | None]:
    """'6 - 22. november 2024' -> ('2024-11-06', '2024-11-22'); '13. oktober 2026' -> the same day twice."""
    match = _RANGE_RE.search(title or "")
    if not match:
        day = parse_title_date(title)
        return day, day
    start_day, start_month_name, end_day, end_month_name, year = match.groups()
    end_month = MONTHS.get(end_month_name.lower())
    start_month = MONTHS.get(start_month_name.lower()) if start_month_name else end_month
    if not start_month or not end_month:
        return None, None
    start_year = int(year) - 1 if start_month > end_month else int(year)
    try:
        start = datetime(start_year, start_month, int(start_day)).date().isoformat()
        end = datetime(int(year), end_month, int(end_day)).date().isoformat()
    except ValueError:
        return None, None
    return start, end


def parse_periods(payload: dict) -> list[dict]:
    """Billing periods from Statement4, newest first. Upstream lists them oldest first with charges as negative øre."""
    periods = []
    for period in payload.get("Periods") or []:
        days = []
        for section in period.get("Sections") or []:
            date = parse_title_date(section.get("Title"))
            lines = [
                _usage_entry(
                    date, line.get("Time"), line.get("UnitText") or "", line.get("UnitId"),
                    line.get("Text") or "", ore_to_dkk(-line["Amount"]) if line.get("Amount") is not None else None,
                )
                for line in section.get("Lines") or []
            ]
            days.append({
                "date": date,
                "spend_dkk": ore_to_dkk(-(section.get("Subtotal") or 0)),
                "balance_dkk": ore_to_dkk(section.get("Saldo")),
                "lines": lines[::-1],
            })
        start, end = parse_period_title(period.get("Title"))
        periods.append({
            "title": period.get("Title"),
            "start": start,
            "end": end,
            "note": " ".join(period.get("Description") or []) or None,
            "balance_dkk": ore_to_dkk(period.get("Saldo")),
            "spend_dkk": round(sum(day["spend_dkk"] for day in days), 2),
            "usage_count": sum(len(day["lines"]) for day in days),
            "days": days[::-1],
        })
    return periods[::-1]


_PRICE_UNIT_PART_RE = re.compile(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*")


def unit_numbers(label: str) -> list[int]:
    """'Vaskemaskine 2 - 4, 6 - 8' -> [2, 3, 4, 6, 7, 8]; [] if the label is not a unit list."""
    first_digit = re.search(r"\d", label)
    if not first_digit:
        return []
    numbers: list[int] = []
    for part in re.split(r"[,&]", label[first_digit.start():]):
        match = _PRICE_UNIT_PART_RE.fullmatch(part)
        if not match:
            return []
        low = int(match.group(1))
        high = int(match.group(2) or low)
        if high < low or high - low > 100:
            return []
        numbers.extend(range(low, high + 1))
    return numbers


def parse_prices(payload: dict) -> dict:
    """Price groups from Prices1: a header row (machines) followed by 'label / price' rows."""
    location, groups, current = None, [], None
    for section in payload.get("Sections") or []:
        if location is None and section.get("Description"):
            location = section["Description"][0] or None
        for row in section.get("Rows") or []:
            columns = row.get("Columns2") or {}
            cells = columns.get("Cells") or []
            kind = columns.get("Type")
            if kind in (1, 3) and cells:
                current = {
                    "label": cells[0], "unit_type": unit_type(cells[0]), "unit_ids": unit_numbers(cells[0]), "prices": [],
                }
                groups.append(current)
            elif kind == 4 and len(cells) >= 2:
                if current is None:
                    current = {"label": None, "unit_type": "other", "unit_ids": [], "prices": []}
                    groups.append(current)
                current["prices"].append({"label": cells[0], "amount_dkk": parse_money(cells[1]), "text": cells[1]})
    return {"location": location, "groups": groups}


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ONE_TIME_CODE_RE = re.compile(r"(\w*(?:kode|code))\s*[:\-]?\s*\d{4,10}\b", re.IGNORECASE)


def html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(head|style|script)\b.*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d)>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", "", markup))
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\r", "").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def redact_sensitive(text: str) -> tuple[str, bool]:
    """Mask one-time security codes and e-mail addresses: these messages are copies of account e-mails."""
    redacted = _ONE_TIME_CODE_RE.sub(lambda m: f"{m.group(1)}: ******", text)
    redacted = _EMAIL_RE.sub("[email]", redacted)
    return redacted, redacted != text


def parse_messages(payload: dict) -> list[dict]:
    """Landlord and system messages from Messages1, in upstream order (newest first)."""
    messages = []
    for section in payload.get("Sections") or []:
        subject, body_parts = None, []
        for row in section.get("Rows") or []:
            columns = row.get("Columns2") or {}
            cells = columns.get("Cells") or []
            kind = columns.get("Type")
            if kind == 11 and cells and subject is None:
                subject = html_to_text(cells[0])
            elif kind in (12, 13, 14) and cells:
                body_parts.append(html_to_text(cells[0]))
        body, redacted = redact_sensitive("\n\n".join(part for part in body_parts if part))
        id_digits = re.search(r"(\d+)$", section.get("Name") or "")
        messages.append({
            "id": id_digits.group(1) if id_digits else None,
            "received": section.get("Title"),
            "subject": subject,
            "body": body,
            "redacted": redacted,
        })
    return messages


# Calendar5 turn types, documented by the upstream help page.
_TURN_STATES = {
    0: "free", 1: "chance", 2: "closed", 3: "past", 5: "limit_reached", 7: "yours", 8: "reserved",
    9: "unavailable", 10: "unavailable", 11: "unavailable",
}
_UNIT_FREE, _UNIT_YOURS = 0, 7
_UNIT_OCCUPIED = (5, 8)
UNLIMITED_BOOKINGS = 32767


def _turn_state(turn: dict, unit_ids: list[int]) -> tuple[str, list[int]]:
    """State of a calendar turn. Individual calendars report future turns as type 6 with one status per unit."""
    kind = turn.get("Type")
    occupied = turn.get("Occupied")
    if kind == 6 or (kind == 0 and occupied):
        if not occupied:
            return "unknown", []
        free = [unit_ids[i] if i < len(unit_ids) else i + 1 for i, status in enumerate(occupied) if status == _UNIT_FREE]
        if free:
            return "free", free
        if _UNIT_YOURS in occupied:
            return "yours", []
        if any(status in _UNIT_OCCUPIED for status in occupied):
            return "full", []
        return "unavailable", []
    if kind == 0:
        return "free", []
    state = _TURN_STATES.get(kind)
    if state is None:
        log.warning("unknown calendar turn type", extra={"turn_type": kind})
        return "unknown", []
    return state, []


def _parse_calendar(section: dict, calendar: dict) -> dict:
    units = [{"id": unit.get("UnitId"), "name": unit.get("Text")} for unit in calendar.get("Units") or []]
    unit_ids = [unit["id"] for unit in units]
    days = []
    for day in calendar.get("Days") or []:
        day_start = datetime.fromisoformat((day.get("Date") or "")[:10])
        minute = calendar.get("Start") or 0
        slots = []
        for turn in day.get("Turns") or []:
            duration = turn.get("Duration") or 0
            start, minute = minute, minute + duration
            if turn.get("Type") == 4:
                continue  # spacer
            state, free_units = _turn_state(turn, unit_ids)
            slots.append({
                "start": (day_start + timedelta(minutes=start)).isoformat(timespec="minutes"),
                "end": (day_start + timedelta(minutes=minute)).isoformat(timespec="minutes"),
                "state": state,
                "bookable": state == "free",
                "free_unit_ids": free_units,
            })
        days.append({"date": day_start.date().isoformat(), "slots": slots})
    remaining = calendar.get("Remaning")
    return {
        "calendar_id": calendar.get("CalendarId"),
        "description": " ".join(section.get("Description") or []) or None,
        "from": (calendar.get("From") or "")[:10] or None,
        "to": (calendar.get("To") or "")[:10] or None,
        "remaining_bookings": None if remaining in (None, UNLIMITED_BOOKINGS) else remaining,
        "disabled": bool(calendar.get("Disabled")),
        "individual": bool(calendar.get("Individual")),
        "units": units,
        "days": days,
    }


def parse_calendar(payload: dict) -> dict:
    """Booking calendars with normalised slots. The per-turn booking session is deliberately dropped:
    upstream uses it to make a reservation with a plain GET."""
    location, calendars = None, []
    for section in payload.get("Sections") or []:
        location = location or section.get("Title")
        for row in section.get("Rows") or []:
            if row.get("Calendar"):
                calendars.append(_parse_calendar(section, row["Calendar"]))
    candidates = [
        {"calendar_id": c["calendar_id"], "start": s["start"], "end": s["end"], "free_unit_ids": s["free_unit_ids"]}
        for c in calendars if not c["disabled"] for d in c["days"] for s in d["slots"] if s["bookable"]
    ]
    return {
        "location": location,
        "calendars": calendars,
        "next_free_slot": min(candidates, key=lambda s: s["start"]) if candidates else None,
    }
