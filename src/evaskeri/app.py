import hmac
import logging
import time

from apiflask import APIFlask
from flask import g, request

from . import __version__
from .config import Config
from .logging_config import configure_logging
from .metrics import DOMAIN, HTTP_DURATION, HTTP_REQUESTS
from .routes import legacy, ops, v1
from .services import LaundryService
from .upstream import UpstreamClient, UpstreamError

log = logging.getLogger("evaskeri.access")

# Reachable without the API key: probes, Prometheus and the API docs.
OPEN_PATHS = ("/health", "/ready", "/metrics", "/api/swagger", "/api/openapi.json")
QUIET_PATHS = ("/health", "/ready", "/metrics")


def create_app(config: Config | None = None, upstream: UpstreamClient | None = None) -> APIFlask:
    config = config or Config()
    configure_logging(config.log_level)

    app = APIFlask(
        __name__,
        title="E-Vaskeri API",
        version=__version__,
        docs_path="/api/swagger",
        spec_path="/api/openapi.json",
        docs_ui="swagger-ui",
    )
    app.config["DESCRIPTION"] = (
        "Wrapper around the e-vaskeri (Nortec) laundry service. "
        "Use `/api/v1` for normalized data; the legacy endpoints are kept for compatibility."
    )
    app.config["TAGS"] = [
        {"name": "v1", "description": "Normalized machines, balance, statistics, periods, bookings, prices and messages"},
        {"name": "ops", "description": "Health, readiness and Prometheus metrics"},
        {"name": "legacy", "description": "Deprecated pass-through endpoints"},
    ]
    app.config["SECURITY_SCHEMES"] = {"ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}
    app.config["JSON_SORT_KEYS"] = False

    upstream = upstream or UpstreamClient(config)
    service = LaundryService(config, upstream)
    app.extensions["evaskeri"] = {"config": config, "upstream": upstream, "service": service}
    DOMAIN.machines, DOMAIN.balance, DOMAIN.stats = service.machines, service.balance, service.stats

    app.register_blueprint(v1.bp)
    app.register_blueprint(ops.bp)
    app.register_blueprint(legacy.bp)

    @app.before_request
    def start_timer():
        g.started = time.perf_counter()
        if config.api_key and not request.path.startswith(OPEN_PATHS):
            supplied = request.headers.get("X-API-Key", "")
            if not hmac.compare_digest(supplied.encode(), config.api_key.encode()):
                return {"code": 401, "message": "Invalid or missing API key"}, 401

    @app.after_request
    def record_request(response):
        duration = time.perf_counter() - g.get("started", time.perf_counter())
        route = request.url_rule.rule if request.url_rule else "unmatched"
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        HTTP_DURATION.labels(request.method, route).observe(duration)
        # Log the path only: query strings can carry upstream session tokens.
        level = logging.DEBUG if request.path in QUIET_PATHS else logging.INFO
        log.log(level, "request", extra={
            "method": request.method, "path": request.path, "status": response.status_code,
            "duration_ms": round(duration * 1000, 1),
        })
        return response

    @app.errorhandler(UpstreamError)
    def upstream_error(error: UpstreamError):
        return {"code": error.status_code, "message": str(error), "error": str(error)}, error.status_code

    return app
