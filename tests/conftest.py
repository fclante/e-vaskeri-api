import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
import responses

from evaskeri.app import create_app
from evaskeri.config import Config
from evaskeri.metrics import HEALTH

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://upstream.test"


def svg(program="", step="", status="FRI", remaining=""):
    def text(prefix, value):
        return f'<text id="{prefix}808LAU1" x="50%" style="font-size:500px">{value}</text>'

    return "<svg>" + "".join(text(p, v) for p, v in (("R", remaining), ("P", program), ("C", step), ("S", status))) + "</svg>"


@pytest.fixture
def live():
    return json.loads((FIXTURES / "running_machines.json").read_text())


@pytest.fixture
def statement():
    return json.loads((FIXTURES / "balance.json").read_text())


@pytest.fixture
def config():
    return Config(
        base_url=BASE, username="user", password="secret-pass", api_key=None, machines_ttl=0, account_ttl=0,
        calendar_ttl=0, refresh_check=False, refresh_shadow_live=False, refresh_skip_live=False,
    )


@pytest.fixture
def periods():
    return json.loads((FIXTURES / "periods.json").read_text())


@pytest.fixture
def calendar():
    return json.loads((FIXTURES / "calendar.json").read_text())


@pytest.fixture
def prices():
    return json.loads((FIXTURES / "prices.json").read_text())


@pytest.fixture
def messages():
    return json.loads((FIXTURES / "messages.json").read_text())


@pytest.fixture
def mocked():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


@pytest.fixture
def client(config, mocked):
    HEALTH.reset()
    app = create_app(config)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def upstream(mocked, live, statement, periods, calendar, prices, messages):
    """Registers a happy-path upstream: login + Live5 (unit 3 busy) + Statement5 and the optional pages."""
    live["Sections"][0]["Rows"][0]["Units"][2]["Status"] = "OPTAGET"
    mocked.get(f"{BASE}/User/Login4/", json={"Session": "tok-1"})
    mocked.get(f"{BASE}/Location/Live5/", json=live)
    mocked.get(f"{BASE}/Statement-placeholder", json={})
    mocked.get(f"{BASE}/User/Statement5/", json=statement)
    mocked.get(f"{BASE}/User/Statement4/", json=periods)
    mocked.get(f"{BASE}/Location/Calendar5/", json=calendar)
    mocked.get(f"{BASE}/Location/Prices1/", json=prices)
    mocked.get(f"{BASE}/User/Messages1/", json=messages)
    mocked.get(re.compile(rf"{BASE}/download/.*\.svg"), body=svg("95° Normal", "Hovedvask", "OPTAGET", "31:41"))
    return mocked


def refresh_payload(crc="CRC-A", rate=1, tab=2):
    return {"Return": 100, "TabId": tab, "CRC": crc, "Rate": rate}


@pytest.fixture
def crc_config(config):
    """Like `config`, with change detection on and a zero TTL so every call goes through revalidation."""
    return replace(config, refresh_check=True, refresh_shadow_live=True)


@pytest.fixture
def crc_client(crc_config, mocked):
    HEALTH.reset()
    app = create_app(crc_config)
    app.config["TESTING"] = True
    return app.test_client()
