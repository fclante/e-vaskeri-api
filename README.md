# E-Vaskeri API

A wrapper API for the E-Vaskeri laundry service, which provides the state of the washing machines and dryers in the laundry room of my apartment complex, plus the account statement.
It logs in to the upstream service itself, so clients never handle the upstream session token.

- Swagger UI: `GET /api/swagger` (OpenAPI spec: `GET /api/openapi.json`)
- Health/readiness: `GET /health`, `GET /ready`
- Prometheus metrics: `GET /metrics`

## Endpoints

### `/api/v1` (normalized, use these)

| Endpoint | Description |
|---|---|
| `GET /api/v1/machines` | All machines with `state` (`free`/`busy`/`done`/`error`/`unknown`), program, step, `remaining_seconds`, `ends_at`, plus a summary per machine type and the next machine to become free. |
| `GET /api/v1/machines/{id}` | A single machine. |
| `GET /api/v1/balance` | `balance_dkk` (negative = owed), `owed_dkk`, `previous_period_dkk`, last payment. |
| `GET /api/v1/statement?limit=&since=` | Usage and payments, newest first, with ISO dates and DKK amounts (positive = charge, negative = payment). |
| `GET /api/v1/stats?months=` | Monthly spend split by washer/dryer, averages, favourite machine, top programs/temperatures and a weekday x hour heatmap. |
| `GET /api/v1/periods?limit=&details=` | Billing periods (newest first) with start/end date, spend, closing balance and the note upstream attaches (e.g. "usage transferred to rent"). `details=true` adds the daily usage lines. |
| `GET /api/v1/calendar?date=&days=&calendar_id=` | Reservation slots per day (local time) with `state` (`free`, `full`, `yours`, `past`, ...), free machine ids, bookings left and the next bookable slot. `available` is `false` when the location has no reservations. |
| `GET /api/v1/prices` | Price per machine group, with the machine numbers each price applies to. |
| `GET /api/v1/messages?limit=` | Landlord and system messages as plain text. One-time codes and e-mail addresses are masked (`redacted: true`). |

Machine data is cached for 20 s and account data for 10 min (see below). If upstream fails, the last cached data is served. Calendar data is cached for 2 min. The calendar never exposes the per-slot booking session that upstream hands out.

#### Change detection (`Refresh1`)

Upstream offers a cheap `User/Refresh1?tabid=` call that returns a CRC and a polling-rate hint. The statement tab (2) and the live tab (3) are the only ones with a usable CRC.

- **Statement and periods:** when the cache is older than `ACCOUNT_TTL_SECONDS`, the API first compares the CRC. If it is unchanged the full download (about 40 KB) is skipped, until `ACCOUNT_MAX_AGE_SECONDS` forces a real refresh. Disable with `REFRESH_CHECK=false`.
- **Live machines:** observed only by default (`REFRESH_SHADOW_LIVE=true`). Each refresh also reads the live CRC and counts, in `evaskeri_live_crc_mismatch_total`, whenever the machine data changed while the CRC did not. Whether the CRC moves while a program is running is not proven yet. Only turn on `REFRESH_SKIP_LIVE=true` once `data_changed_crc_same` stays at 0 across real wash cycles. Even then it only skips while every machine is free, and never longer than `LIVE_MAX_AGE_SECONDS`.
- Prices, messages and the calendar have no CRC upstream and use plain TTLs.
- The upstream `Rate` hint limits how often a tab's CRC is read.

### Ops

| Endpoint | Description |
|---|---|
| `GET /health` | Liveness. Never calls upstream. |
| `GET /ready` | Readiness. 503 if credentials are missing or upstream has been failing for more than `READY_WINDOW_SECONDS`. |
| `GET /metrics` | Prometheus metrics (see below). |

### Legacy (deprecated)

`GET /login`, `GET /running-machines`, `GET /balance` and `GET /status` still return the raw upstream data. The upstream `session` query param is now optional on the first three; when omitted the API's own login is used. `/status?machineType=&machineNumber=` now works for every unit, not only washers.

### Upstream reference

[docs/upstream-admin-endpoints.md](docs/upstream-admin-endpoints.md) documents how the upstream backend is called (URL template, session lifecycle, return codes) and its admin, device and write functions, which this API deliberately never calls. It also covers the unauthenticated `Location/Find5` exposure, including a draft disclosure note.

## Metrics

| Metric | Labels |
|---|---|
| `evaskeri_http_requests_total`, `evaskeri_http_request_duration_seconds` | method, route, status |
| `evaskeri_upstream_requests_total`, `evaskeri_upstream_request_duration_seconds` | endpoint, outcome |
| `evaskeri_upstream_login_total` | outcome |
| `evaskeri_machine_busy`, `evaskeri_machine_remaining_seconds` | id, name, type |
| `evaskeri_machines_free` | type |
| `evaskeri_balance_dkk`, `evaskeri_period_spend_dkk` | |
| `evaskeri_last_successful_refresh_timestamp_seconds` | source |
| `evaskeri_refresh_checks_total` | tab, outcome (`changed`/`unchanged`/`error`) |
| `evaskeri_refresh_skipped_total` | source (full fetches avoided by an unchanged CRC) |
| `evaskeri_live_crc_mismatch_total` | kind (`data_changed_crc_same`/`crc_changed_data_same`) |
| `evaskeri_build_info` | version |

Machine and balance gauges are computed from the cache at scrape time, so scraping does not call upstream more often than the cache TTL.

## Logging

One JSON object per line on stdout. Request logs contain the path only, never the query string, and credentials/tokens are never logged. Set the level with `LOG_LEVEL`.

---

## Development

Dependencies are managed with [uv](https://docs.astral.sh/uv/).

```sh
uv sync                                   # create .venv and install locked dependencies
uv run pytest                             # run the tests
uv run gunicorn --chdir src main:app -c gunicorn.conf.py   # run the API
uv add <package>                          # add a dependency (updates pyproject.toml and uv.lock)
uv lock --upgrade                         # upgrade locked versions
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `E_VASKERI_USERNAME` | | Your E-Vaskeri username (required) |
| `E_VASKERI_PASSWORD` | | Your E-Vaskeri password (required) |
| `LOG_LEVEL` | `INFO` | Log level |
| `MACHINES_TTL_SECONDS` | `20` | Cache time for machine state |
| `ACCOUNT_TTL_SECONDS` | `600` | Cache time for balance/statement/stats/periods/prices/messages |
| `CALENDAR_TTL_SECONDS` | `120` | Cache time for the booking calendar |
| `REFRESH_CHECK` | `true` | Skip statement/periods downloads while the upstream CRC is unchanged |
| `ACCOUNT_MAX_AGE_SECONDS` | `3600` | Longest an unchanged-CRC statement is kept before a real refresh |
| `REFRESH_SHADOW_LIVE` | `true` | Compare the live CRC with the parsed live data and count disagreements (never skips a fetch) |
| `REFRESH_SKIP_LIVE` | `false` | Skip live refetches while all machines are free and the CRC is unchanged. Enable only after the shadow metric stays clean |
| `LIVE_MAX_AGE_SECONDS` | `300` | Longest a live snapshot is reused when `REFRESH_SKIP_LIVE` is on |
| `UPSTREAM_CONNECT_TIMEOUT` / `UPSTREAM_READ_TIMEOUT` | `5` / `10` | Upstream timeouts in seconds |
| `READY_WINDOW_SECONDS` | `300` | How long upstream may fail before `/ready` returns 503 |
| `E_VASKERI_BASE_URL` | `https://back-nortec.datpro2.dk` | Upstream base URL |

The app runs a single gunicorn worker with threads so the session, cache and Prometheus registry stay consistent.

### Docker Compose

Copy `.env.example` to `.env`, fill in the credentials, then start the API on port 5000:

```sh
cp .env.example .env
docker compose up --build
```

### Build & publish

The image is built and pushed manually:

```sh
docker build -t <your-registry>/e-vaskeri-api:<tag> .
docker push <your-registry>/e-vaskeri-api:<tag>

# for ARM nodes (e.g. Raspberry Pi) build a multi-arch image instead
docker buildx build --platform linux/amd64,linux/arm64 -t <your-registry>/e-vaskeri-api:<tag> --push .
```

Run these from this directory (`api/`).
