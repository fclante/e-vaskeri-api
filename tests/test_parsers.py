import json
from datetime import datetime, timezone

import pytest

from evaskeri import parsers


@pytest.mark.parametrize("text,expected", [
    ("-116,40 DKK", -116.4),
    ("1.234,50 DKK", 1234.5),
    ("0,00 DKK", 0.0),
    ("n/a", None),
    (None, None),
])
def test_parse_money(text, expected):
    assert parsers.parse_money(text) == expected


def test_parse_title_date_handles_double_space_and_danish_months():
    assert parsers.parse_title_date("Fredag 6.  november 2026") == "2026-11-06"
    assert parsers.parse_title_date("Tirsdag 29.  september 2026") == "2026-09-29"
    assert parsers.parse_title_date("garbage") is None


def test_unit_type_and_number():
    assert parsers.unit_type("Tørretumbler 9") == "dryer"
    assert parsers.unit_type("Strygerulle 12") == "mangle"
    assert parsers.unit_type("Washer 1") == "washer"
    assert parsers.unit_number("Vaskemaskine 3") == 3


@pytest.mark.parametrize("text,expected", [("31:41", 1901), ("1:02:03", 3723), ("", None), ("abc", None), ("12", None)])
def test_parse_remaining_seconds(text, expected):
    assert parsers.parse_remaining_seconds(text) == expected


def test_normalize_state():
    assert parsers.normalize_state("Fri") == "free"
    assert parsers.normalize_state("FRI") == "free"
    assert parsers.normalize_state("OPTAGET") == "busy"
    assert parsers.normalize_state("something new") == "unknown"
    assert parsers.normalize_state("something new", 60) == "busy"


def test_parse_live(live):
    parsed = parsers.parse_live(live)
    assert parsed["location"] == "Sprotoften 51"
    assert parsed["upstream_updated"] == "22:18"
    assert len(parsed["units"]) == 12
    assert parsed["units"][8] == {
        "id": 9, "name": "Tørretumbler 9", "type": "dryer", "raw_status": "Fri", "svg": parsed["units"][8]["svg"],
    }


def test_build_machine_and_summary(live):
    units = parsers.parse_live(live)["units"]
    now = datetime(2026, 11, 13, 21, 0, tzinfo=timezone.utc)
    busy = parsers.build_machine(
        units[2], {"status": "OPTAGET", "program": "95° Normal", "current_step": "Hovedvask", "remaining_time": "31:41"}, now,
    )
    assert busy["state"] == "busy" and busy["remaining_seconds"] == 1901
    assert busy["ends_at"] == "2026-11-13T21:31:41+00:00"
    free = parsers.build_machine(units[0], None, now)
    assert free["state"] == "free" and free["ends_at"] is None

    summary = parsers.summarize_machines([busy, free])
    assert summary["busy"] == 1 and summary["free"] == 1
    assert summary["next_free"]["name"] == "Vaskemaskine 3"


def test_parse_unit_svg_empty_machine():
    svg = '<text id="R808LAU1">  </text><text id="S808LAU1">FRI</text>'
    assert parsers.parse_unit_svg(svg) == {
        "program": None, "current_step": None, "status": "FRI", "remaining_time": None,
    }


def test_parse_balance(statement):
    balance = parsers.parse_balance(statement)
    assert balance["balance_dkk"] == -116.4
    assert balance["owed_dkk"] == 116.4
    assert balance["previous_period_dkk"] == -109.8
    assert balance["last_payment"]["amount_dkk"] == 109.8
    assert balance["last_payment"]["description"] == "Overført til husleje"


def test_parse_statement(statement):
    entries = parsers.parse_statement(statement)
    assert entries == sorted(entries, key=lambda e: (e["date"] or "", e["time"] or ""), reverse=True)
    newest = entries[0]
    assert newest["date"] == "2026-11-06" and newest["time"] == "12:59"
    assert newest["unit_type"] == "dryer" and newest["duration_min"] == 10 and newest["amount_dkk"] == 6.0
    wash = next(e for e in entries if e["description"].startswith("Program 5, Prewash"))
    assert wash["program"] == 5 and wash["temperature_c"] == 60 and wash["amount_dkk"] == 18.0
    payments = [e for e in entries if e["kind"] == "payment"]
    assert payments and all(p["amount_dkk"] < 0 for p in payments)
    assert sum(1 for e in entries if e["kind"] == "usage") == 174


def test_compute_stats(statement):
    stats = parsers.compute_stats(parsers.parse_statement(statement), months=12)
    assert len(stats["months"]) == 12 and stats["months"][-1]["month"] == "2026-11"
    assert stats["current_month_spend_dkk"] == 116.4
    assert stats["totals"]["washes"] == 123 and stats["totals"]["dries"] == 51
    assert stats["totals"]["total_dkk"] == 3220.52
    assert sum(sum(row) for row in stats["heatmap"]) == 174
    assert len(stats["heatmap"]) == 7 and len(stats["heatmap"][0]) == 24


def test_compute_stats_without_usage():
    stats = parsers.compute_stats([])
    assert stats["months"] == [] and stats["avg_cost_per_wash_dkk"] is None


@pytest.mark.parametrize("title,expected", [
    ("6 - 22. november 2024", ("2024-11-06", "2024-11-22")),
    ("13. oktober 2026", ("2026-10-13", "2026-10-13")),
    ("28. december - 3. januar 2026", ("2025-12-28", "2026-01-03")),
    ("garbage", (None, None)),
    (None, (None, None)),
])
def test_parse_period_title(title, expected):
    assert parsers.parse_period_title(title) == expected


def test_parse_periods_newest_first_with_positive_charges(periods):
    result = parsers.parse_periods(periods)
    assert [p["start"] for p in result] == ["2026-02-06", "2026-01-13", "2025-11-06"]
    first, last = result[0], result[-1]
    assert first["note"] is None and last["note"] == "Forbrug overført til husleje"
    assert first["balance_dkk"] == -62.0 and first["spend_dkk"] == 11.0 and first["usage_count"] == 1
    assert last["spend_dkk"] == 33.0 and last["usage_count"] == 2
    assert [d["date"] for d in last["days"]] == ["2025-11-22", "2025-11-06"]
    line = last["days"][-1]["lines"][0]
    # Upstream sends charges as negative øre, v1 reports them as positive DKK like /statement does.
    assert line["amount_dkk"] == 18.0 and line["unit_id"] == 1 and line["unit_type"] == "washer"
    assert line["program"] == 5 and line["temperature_c"] == 60


@pytest.mark.parametrize("label,expected", [
    ("Vaskemaskine 1 & 5", [1, 5]),
    ("Vaskemaskine 2 - 4, 6 - 8", [2, 3, 4, 6, 7, 8]),
    ("Tørretumbler 9", [9]),
    ("Alle maskiner", []),
    ("Vaskemaskine 4 - 2", []),
])
def test_unit_numbers(label, expected):
    assert parsers.unit_numbers(label) == expected


def test_parse_prices(prices):
    result = parsers.parse_prices(prices)
    assert result["location"] == "Testvej 1"
    assert [g["unit_type"] for g in result["groups"]] == ["washer", "washer", "dryer"]
    assert result["groups"][1]["unit_ids"] == [2, 3, 4, 6, 7, 8]
    assert result["groups"][2]["prices"] == [{"label": "Pris pr. time", "amount_dkk": 36.0, "text": "36,00 kr."}]


def test_parse_messages_masks_codes_and_addresses(messages):
    code_message, plain = parsers.parse_messages(messages)
    assert code_message["subject"] == "Engangskode" and code_message["redacted"] is True
    assert "123456" not in code_message["body"] and "test@example.com" not in code_message["body"]
    assert "Engangskode: ******" in code_message["body"] and "<" not in code_message["body"]
    assert plain["redacted"] is False and plain["id"] == "100000000001"
    assert plain["body"] == "Vaskeriet er lukket torsdag kl. 9-12 pga. service.\nBeklager ulejligheden."


def test_parse_calendar_slots_and_next_free(calendar):
    result = parsers.parse_calendar(calendar)
    cal = result["calendars"][0]
    assert result["location"] == "Testvej 1" and cal["remaining_bookings"] == 2 and cal["individual"] is True
    assert [u["id"] for u in cal["units"]] == [1, 2, 3]
    today, tomorrow, sunday = ({s["start"][11:]: s for s in d["slots"]} for d in cal["days"])
    assert [s["state"] for s in today.values()] == ["past", "past", "free", "free"]
    # Individual calendars report future turns as type 6 with a status per unit.
    assert today["13:00"]["free_unit_ids"] == [3] and today["16:00"]["free_unit_ids"] == [1, 2, 3]
    assert [s["state"] for s in tomorrow.values()] == ["full", "yours", "limit_reached", "unavailable"]
    assert tomorrow["07:00"]["bookable"] is False
    # The spacer is skipped but still takes up time.
    assert sunday["08:00"]["state"] == "free" and sunday["08:00"]["bookable"] is True and sunday["08:00"]["end"] == "2026-11-15T10:00"
    assert [s["state"] for s in sunday.values()] == ["free", "chance", "closed", "unknown"]
    assert result["next_free_slot"] == {
        "calendar_id": 1, "start": "2026-11-13T13:00", "end": "2026-11-13T16:00", "free_unit_ids": [3],
    }


def test_parse_calendar_never_exposes_booking_sessions(calendar):
    assert "REDACT" not in json.dumps(parsers.parse_calendar(calendar))


def test_parse_calendar_unlimited_bookings_and_disabled(calendar):
    cal = calendar["Sections"][0]["Rows"][0]["Calendar"]
    cal["Remaning"], cal["Disabled"] = 32767, True
    result = parsers.parse_calendar(calendar)
    assert result["calendars"][0]["remaining_bookings"] is None
    assert result["next_free_slot"] is None


def test_parse_pages_tolerate_not_available_payload():
    empty = {"Return": 110}
    assert parsers.parse_calendar(empty) == {"location": None, "calendars": [], "next_free_slot": None}
    assert parsers.parse_prices(empty) == {"location": None, "groups": []}
    assert parsers.parse_messages(empty) == [] and parsers.parse_periods(empty) == []
