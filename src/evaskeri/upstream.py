import logging
import threading
import time
from typing import Any, Callable
from urllib.parse import urlencode

import requests

from .config import Config
from .metrics import UPSTREAM_DURATION, UPSTREAM_LOGIN, UPSTREAM_REQUESTS, mark_failure, mark_success

log = logging.getLogger(__name__)

APP_PARAMS = {"App": "TUK", "native": "false"}
LOGIN_BACKOFF_SECONDS = 30
SUCCESS_RETURN = 100
# "No access to this tab at this location": a valid answer for optional features.
NOT_AVAILABLE_RETURN = 110


class UpstreamError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class UpstreamClient:
    """Talks to the upstream API and hides its query-param session handling."""

    def __init__(self, config: Config, http: requests.Session | None = None):
        self.config = config
        self.http = http or requests.Session()
        self._session_token: str | None = None
        self._login_blocked_until = 0.0
        self._lock = threading.Lock()

    @property
    def _timeout(self) -> tuple[float, float]:
        return (self.config.connect_timeout, self.config.read_timeout)

    def _tick(self) -> int:
        return int(time.time() * 1000)

    def _url(self, path: str, params: dict[str, Any]) -> str:
        # The JSON format flag must be a bare ".json": requests would send ".json=", which makes
        # upstream answer with its HTML help page instead of data.
        return f"{self.config.base_url}{path}?{urlencode(params)}&.json"

    def login(self) -> str:
        if not self.config.credentials_configured:
            raise UpstreamError("E_VASKERI_USERNAME and E_VASKERI_PASSWORD are not configured", 503)
        params = {
            **APP_PARAMS, "session": "", "username": self.config.username, "password": self.config.password,
            "language": "da", "guid": "", "tick": self._tick(), "domain": "https://e-vaskeri.dk",
        }
        started = time.perf_counter()
        try:
            response = self.http.get(self._url("/User/Login4/", params), timeout=self._timeout)
            response.raise_for_status()
            token = response.json().get("Session")
        except (requests.RequestException, ValueError) as exc:
            UPSTREAM_LOGIN.labels("error").inc()
            mark_failure()
            log.warning("upstream login failed", extra={"error": type(exc).__name__})
            raise UpstreamError("Failed to log in to upstream") from exc
        finally:
            UPSTREAM_DURATION.labels("login").observe(time.perf_counter() - started)
        if not token:
            UPSTREAM_LOGIN.labels("rejected").inc()
            mark_failure()
            raise UpstreamError("Upstream rejected the credentials", 401)
        UPSTREAM_LOGIN.labels("ok").inc()
        mark_success()
        log.info("upstream login ok")
        return token

    def session_token(self, force_refresh: bool = False) -> str:
        with self._lock:
            if force_refresh or not self._session_token:
                if time.monotonic() < self._login_blocked_until:
                    raise UpstreamError("Upstream login failed recently, backing off", 503)
                try:
                    self._session_token = self.login()
                except UpstreamError:
                    self._session_token = None
                    self._login_blocked_until = time.monotonic() + LOGIN_BACKOFF_SECONDS
                    raise
            return self._session_token

    def _request(self, endpoint: str, fetch: Callable[[], requests.Response]) -> requests.Response:
        started = time.perf_counter()
        try:
            response = fetch()
        except requests.RequestException as exc:
            UPSTREAM_REQUESTS.labels(endpoint, "error").inc()
            mark_failure()
            log.warning("upstream request failed", extra={"endpoint": endpoint, "error": type(exc).__name__})
            raise UpstreamError(f"Upstream {endpoint} request failed") from exc
        finally:
            UPSTREAM_DURATION.labels(endpoint).observe(time.perf_counter() - started)
        UPSTREAM_REQUESTS.labels(endpoint, "ok" if response.status_code == 200 else str(response.status_code)).inc()
        return response

    def get_json(
        self, path: str, endpoint: str, session: str | None = None,
        params: dict[str, Any] | None = None, accept: tuple[int, ...] = (),
    ) -> dict[str, Any]:
        """GET a session-authenticated JSON endpoint; re-login once if the session is rejected.

        A caller-supplied session is used as is and never refreshed. `params` are extra upstream query
        parameters and `accept` lists additional `Return` codes (e.g. 110) that are valid answers.
        """
        ok_returns = (SUCCESS_RETURN, *accept)
        for attempt in (0, 1):
            token = session or self.session_token(force_refresh=attempt == 1)
            query = {**APP_PARAMS, "session": token, "tick": self._tick(), **(params or {})}
            url = self._url(path, query)
            response = self._request(endpoint, lambda: self.http.get(url, timeout=self._timeout))
            data = None
            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError:
                    data = None
            # Upstream reports errors in the body ("Return" != 100, e.g. 207/208 = session expired) with HTTP 200.
            if isinstance(data, dict) and data and data.get("Return", SUCCESS_RETURN) in ok_returns:
                mark_success()
                return data
            if session or attempt == 1:
                mark_failure()
                reason = f"return code {data.get('Return')}" if isinstance(data, dict) and "Return" in data else f"status {response.status_code}"
                raise UpstreamError(f"Upstream {endpoint} failed with {reason}", response.status_code if response.status_code != 200 else 502)
            log.info("upstream session rejected, logging in again", extra={"endpoint": endpoint})
        raise UpstreamError(f"Upstream {endpoint} failed")  # pragma: no cover

    def get_text(self, url: str, endpoint: str) -> str:
        response = self._request(endpoint, lambda: self.http.get(url, timeout=self._timeout))
        if response.status_code != 200:
            raise UpstreamError(f"Upstream {endpoint} returned status {response.status_code}", response.status_code)
        # The SVG is served without a charset, so requests would guess ISO-8859-1 and garble "°".
        return response.content.decode("utf-8", errors="replace")

    def svg_url(self, svg_path: str) -> str:
        return f"{self.config.base_url}/download/{svg_path}/4.svg"
