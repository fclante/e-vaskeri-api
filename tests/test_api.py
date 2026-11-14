import logging
import re
import time
from dataclasses import replace

import pytest
import responses

from evaskeri.app import create_app
from evaskeri.config import Config
from evaskeri.logging_config import JsonFormatter
from evaskeri.metrics import HEALTH, LIVE_CRC_MISMATCH, REFRESH_SKIPS

from .conftest import BASE, refresh_payload, svg


def test_machines(client, upstream):
    body = client.get("/api/v1/machines").json
    assert body["location"] == "Sprotoften 51"
    assert body["summary"]["total"] == 12 and body["summary"]["busy"] == 1 and body["summary"]["free"] == 11
    machine = next(m for m in body["machines"] if m["id"] == 3)
    assert machine["state"] == "busy" and machine["program"] == "95° Normal" and machine["step"] == "Hovedvask"
    assert machine["remaining_seconds"] == 1901 and machine["ends_at"]
    assert body["summary"]["by_type"]["dryer"] == {"total": 3, "free": 3, "busy": 0}
    # Only the busy unit needed a detail request.
    svg_calls = [c for c in upstream.calls if "/download/" in c.request.url]
    assert len(svg_calls) == 1


def test_machine_text_is_decoded_as_utf8_without_charset(client, upstream):
    upstream.replace(
        responses.GET, re.compile(rf"{BASE}/download/.*\.svg"),
        body=svg("95° Normal", "Hovedvask", "OPTAGET", "31:41").encode(), content_type="text/plain",
    )
    assert client.get("/api/v1/machines/3").json["program"] == "95° Normal"


def test_machine_by_id_and_unknown(client, upstream):
    assert client.get("/api/v1/machines/3").json["name"] == "Vaskemaskine 3"
    assert client.get("/api/v1/machines/99").status_code == 404


def test_balance_statement_stats(client, upstream):
    assert client.get("/api/v1/balance").json["balance_dkk"] == -116.4
    statement = client.get("/api/v1/statement?limit=5&since=2026-10-01").json
    assert len(statement["entries"]) == 5 and all(e["date"] >= "2026-10-01" for e in statement["entries"])
    assert client.get("/api/v1/statement?limit=0").status_code == 422
    stats = client.get("/api/v1/stats?months=6").json
    assert len(stats["months"]) == 6


def test_session_is_cached_and_refreshed_once(client, upstream):
    client.get("/api/v1/balance")
    client.get("/api/v1/balance")
    logins = [c for c in upstream.calls if "/User/Login4/" in c.request.url]
    assert len(logins) == 1

    upstream.replace(responses.GET, f"{BASE}/User/Statement5/", status=401)
    upstream.add(responses.GET, f"{BASE}/User/Statement5/", json={"Receipts": [], "Sections": []})
    assert client.get("/api/v1/balance").status_code == 200
    assert len([c for c in upstream.calls if "/User/Login4/" in c.request.url]) == 2


def test_expired_session_reported_in_body_triggers_relogin(client, upstream):
    client.get("/api/v1/balance")
    upstream.replace(responses.GET, f"{BASE}/User/Statement5/", json={"Return": 208})
    upstream.add(responses.GET, f"{BASE}/User/Statement5/", json={"Return": 100, "Receipts": [], "Sections": []})
    assert client.get("/api/v1/balance").status_code == 200
    assert len([c for c in upstream.calls if "/User/Login4/" in c.request.url]) == 2


def test_stale_data_served_when_upstream_fails(client, upstream):
    assert client.get("/api/v1/balance").status_code == 200
    upstream.replace(responses.GET, f"{BASE}/User/Statement5/", status=500)
    assert client.get("/api/v1/balance").json["balance_dkk"] == -116.4


def test_upstream_down_is_bad_gateway(client, mocked):
    mocked.get(f"{BASE}/User/Login4/", status=500)
    response = client.get("/api/v1/machines")
    assert response.status_code == 502 and "message" in response.json


def test_legacy_endpoints(client, upstream, live):
    assert client.get("/login").json == {"session": "tok-1"}
    assert "Sections" in client.get("/running-machines").json
    assert "Receipts" in client.get("/balance?session=caller-token").json
    assert any("session=caller-token" in c.request.url for c in upstream.calls)
    status = client.get("/status?machineType=Vaskemaskine&machineNumber=3").json
    assert status == {"program": "95° Normal", "currentStep": "Hovedvask", "status": "OPTAGET", "remainingTime": "31:41"}
    assert client.get("/status?machineType=Vaskemaskine&machineNumber=99").status_code == 400
    assert client.get("/status").status_code == 400


def test_swagger_and_spec(client):
    assert client.get("/api/swagger").status_code == 200
    spec = client.get("/api/openapi.json").json
    for path in (
        "/api/v1/machines", "/api/v1/balance", "/api/v1/statement", "/api/v1/stats", "/api/v1/periods",
        "/api/v1/calendar", "/api/v1/prices", "/api/v1/messages", "/health", "/ready", "/metrics",
    ):
        assert path in spec["paths"]
    assert spec["paths"]["/login"]["get"]["deprecated"] is True


def calls_to(mock, fragment):
    return [c for c in mock.calls if fragment in c.request.url]


def test_upstream_requests_use_bare_json_flag(client, upstream):
    client.get("/api/v1/balance")
    urls = [c.request.url for c in upstream.calls]
    # ".json=" would make upstream answer with its HTML help page.
    assert urls and all(u.endswith("&.json") for u in urls if "/download/" not in u)


def test_periods(client, upstream):
    body = client.get("/api/v1/periods").json
    assert body["total"] == 3 and body["periods"][0]["start"] == "2026-02-06"
    assert body["periods"][0]["balance_dkk"] == -62.0 and "days" not in body["periods"][0]
    detailed = client.get("/api/v1/periods?details=true&limit=1").json
    assert len(detailed["periods"]) == 1 and detailed["periods"][0]["days"][0]["lines"][0]["amount_dkk"] == 11.0
    assert client.get("/api/v1/periods?limit=0").status_code == 422


def test_calendar(client, upstream):
    body = client.get("/api/v1/calendar?days=3&calendar_id=1&date=2026-11-13").json
    assert body["available"] is True and body["location"] == "Testvej 1"
    assert body["next_free_slot"]["start"] == "2026-11-13T13:00"
    assert body["calendars"][0]["from"] == "2026-11-13" and body["calendars"][0]["remaining_bookings"] == 2
    assert "REDACT" not in client.get("/api/v1/calendar").text
    query = calls_to(upstream, "/Location/Calendar5/")[0].request.url
    assert "calendarid=1" in query and "days=3" in query and "date=2026-11-13" in query
    for bad in ("date=tomorrow", "days=0", "days=99", "calendar_id=-1"):
        assert client.get(f"/api/v1/calendar?{bad}").status_code == 422, bad


def test_optional_pages_not_available_for_location(client, upstream):
    for path in ("Location/Calendar5", "Location/Prices1", "User/Messages1"):
        upstream.replace(responses.GET, f"{BASE}/{path}/", json={"Return": 110})
    calendar, prices, messages = (client.get(f"/api/v1/{p}").json for p in ("calendar", "prices", "messages"))
    assert calendar["available"] is False and calendar["calendars"] == [] and calendar["next_free_slot"] is None
    assert prices["available"] is False and prices["groups"] == []
    assert messages["available"] is False and messages["messages"] == []


def test_prices_and_messages(client, upstream):
    prices = client.get("/api/v1/prices").json
    assert prices["groups"][0]["unit_ids"] == [1, 5] and prices["groups"][0]["prices"][0]["amount_dkk"] == 18.0
    messages = client.get("/api/v1/messages?limit=1").json
    assert messages["total"] == 2 and len(messages["messages"]) == 1
    text = client.get("/api/v1/messages").text
    assert "123456" not in text and "test@example.com" not in text


def test_ttl_cache_is_bounded(monkeypatch):
    from evaskeri import services

    monkeypatch.setattr(services, "MAX_CACHE_ENTRIES", 3)
    cache = services.TTLCache()
    for i in range(6):
        cache.get_or_load(f"k{i}", 60, lambda i=i: i)
    assert len(cache._data) == 3 and cache.get_or_load("k5", 60, lambda: "new") == 5


def mismatches(kind):
    # Read the counter directly: a registry scrape would also run the domain collector and call upstream.
    return LIVE_CRC_MISMATCH.labels(kind)._value.get()


def test_unchanged_crc_skips_account_refetch(crc_client, upstream):
    upstream.get(f"{BASE}/User/Refresh1/", json=refresh_payload("CRC-A"))
    for _ in range(3):
        assert crc_client.get("/api/v1/balance").status_code == 200
    assert len(calls_to(upstream, "/User/Statement5/")) == 1
    # Periods share the statement tab: one fetch of their own, then skipped as well.
    for _ in range(2):
        crc_client.get("/api/v1/periods")
    assert len(calls_to(upstream, "/User/Statement4/")) == 1


def test_changed_crc_refetches(crc_client, upstream):
    upstream.get(f"{BASE}/User/Refresh1/", json=refresh_payload("CRC-A", rate=1))
    crc_client.get("/api/v1/balance")
    upstream.replace(responses.GET, f"{BASE}/User/Refresh1/", json=refresh_payload("CRC-B", rate=1))
    time.sleep(1.05)  # the upstream Rate hint throttles how often the CRC is read
    crc_client.get("/api/v1/balance")
    assert len(calls_to(upstream, "/User/Statement5/")) == 2


def test_max_age_forces_refetch_despite_unchanged_crc(crc_config, mocked, upstream):
    upstream.get(f"{BASE}/User/Refresh1/", json=refresh_payload("CRC-A"))
    app = create_app(replace(crc_config, account_max_age=0))
    client = app.test_client()
    client.get("/api/v1/balance")
    client.get("/api/v1/balance")
    assert len(calls_to(upstream, "/User/Statement5/")) == 2


def test_failing_refresh_check_falls_back_to_fetching(crc_client, upstream):
    upstream.get(f"{BASE}/User/Refresh1/", status=500)
    crc_client.get("/api/v1/balance")
    crc_client.get("/api/v1/balance")
    assert len(calls_to(upstream, "/User/Statement5/")) == 2


def test_live_is_never_skipped_by_default(crc_client, upstream):
    upstream.get(f"{BASE}/User/Refresh1/", json=refresh_payload("CRC-L", tab=3))
    crc_client.get("/api/v1/machines")
    crc_client.get("/api/v1/machines")
    assert len(calls_to(upstream, "/Location/Live5/")) == 2


def test_shadow_mode_counts_crc_disagreements(crc_client, upstream, live):
    upstream.get(f"{BASE}/User/Refresh1/", json=refresh_payload("CRC-L", tab=3))
    before = mismatches("data_changed_crc_same"), mismatches("crc_changed_data_same")
    crc_client.get("/api/v1/machines")
    live["Sections"][0]["Rows"][0]["Units"][0]["Status"] = "OPTAGET"
    upstream.replace(responses.GET, f"{BASE}/Location/Live5/", json=live)
    crc_client.get("/api/v1/machines")
    assert mismatches("data_changed_crc_same") == before[0] + 1
    upstream.replace(responses.GET, f"{BASE}/User/Refresh1/", json=refresh_payload("CRC-M", tab=3))
    crc_client.get("/api/v1/machines")
    assert mismatches("crc_changed_data_same") == before[1] + 1
    assert len(calls_to(upstream, "/Location/Live5/")) == 3


def skip_live_client(crc_config, **overrides):
    app = create_app(replace(crc_config, refresh_skip_live=True, **overrides))
    return app.test_client()


def test_skip_live_only_when_all_machines_are_free(crc_config, mocked, live, statement):
    mocked.get(f"{BASE}/User/Login4/", json={"Session": "tok-1"})
    mocked.get(f"{BASE}/User/Refresh1/", json=refresh_payload("CRC-L", tab=3))
    mocked.get(f"{BASE}/Location/Live5/", json=live)
    client = skip_live_client(crc_config)
    for _ in range(3):
        assert client.get("/api/v1/machines").json["summary"]["free"] == 12
    assert len(calls_to(mocked, "/Location/Live5/")) == 1
    assert REFRESH_SKIPS.labels("live")._value.get() >= 2


def test_skip_live_still_refetches_while_a_machine_is_busy(crc_config, mocked, upstream):
    mocked.get(f"{BASE}/User/Refresh1/", json=refresh_payload("CRC-L", tab=3))
    client = skip_live_client(crc_config)
    client.get("/api/v1/machines")
    client.get("/api/v1/machines")
    assert len(calls_to(mocked, "/Location/Live5/")) == 2


def test_skip_live_respects_max_age(crc_config, mocked, live):
    mocked.get(f"{BASE}/User/Login4/", json={"Session": "tok-1"})
    mocked.get(f"{BASE}/User/Refresh1/", json=refresh_payload("CRC-L", tab=3))
    mocked.get(f"{BASE}/Location/Live5/", json=live)
    client = skip_live_client(crc_config, live_max_age=0)
    client.get("/api/v1/machines")
    client.get("/api/v1/machines")
    assert len(calls_to(mocked, "/Location/Live5/")) == 2


def test_health_and_ready(client, mocked):
    assert client.get("/health").json["status"] == "ok"
    assert client.get("/ready").status_code == 200
    mocked.get(f"{BASE}/User/Login4/", status=500)
    client.get("/api/v1/machines")
    ready = client.get("/ready")
    assert ready.status_code == 503 and ready.json["reason"] == "upstream unavailable"


def test_not_ready_without_credentials(mocked):
    HEALTH.reset()
    app = create_app(Config(base_url=BASE, username=None, password=None, api_key=None))
    response = app.test_client().get("/ready")
    assert response.status_code == 503 and response.json["credentials_configured"] is False


def test_metrics(client, upstream):
    client.get("/api/v1/machines")
    client.get("/api/v1/balance")
    client.get("/api/v1/stats")
    text = client.get("/metrics").text
    for series in (
        'evaskeri_machine_busy{id="3",name="Vaskemaskine 3",type="washer"} 1.0',
        'evaskeri_machine_busy{id="1",name="Vaskemaskine 1",type="washer"} 0.0',
        'evaskeri_machine_remaining_seconds{id="3",name="Vaskemaskine 3",type="washer"} 1901.0',
        'evaskeri_machines_free{type="dryer"} 3.0',
        "evaskeri_balance_dkk -116.4",
        "evaskeri_period_spend_dkk 116.4",
        'evaskeri_http_requests_total{method="GET",route="/api/v1/machines",status="200"}',
        'evaskeri_upstream_login_total{outcome="ok"}',
        "evaskeri_build_info",
        'evaskeri_last_successful_refresh_timestamp_seconds{source="live"}',
    ):
        assert series in text, series


def test_metrics_survive_upstream_failure(client, mocked):
    mocked.get(f"{BASE}/User/Login4/", status=500)
    response = client.get("/metrics")
    assert response.status_code == 200 and "evaskeri_build_info" in response.text


def test_api_key(mocked, upstream):
    HEALTH.reset()
    app = create_app(Config(base_url=BASE, username="u", password="p", api_key="s3cret", machines_ttl=0))
    client = app.test_client()
    assert client.get("/api/v1/machines").status_code == 401
    assert client.get("/api/v1/machines", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/v1/machines", headers={"X-API-Key": "s3cret"}).status_code == 200
    for open_path in ("/health", "/ready", "/metrics", "/api/swagger", "/api/openapi.json"):
        assert client.get(open_path).status_code != 401, open_path


def test_secrets_never_logged(client, upstream, caplog):
    caplog.set_level(logging.DEBUG)
    client.get("/login")
    client.get("/balance?session=caller-token")
    client.get("/api/v1/machines")
    output = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    assert '"msg": "request"' in output
    for secret in ("secret-pass", "tok-1", "caller-token", "Login4"):
        assert secret not in output
