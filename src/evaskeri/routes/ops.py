from apiflask import APIBlueprint
from flask import Response, current_app
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .. import __version__
from ..metrics import HEALTH, REGISTRY
from ..schemas import HealthSchema, ReadySchema

bp = APIBlueprint("ops", __name__, tag="ops")


@bp.get("/health")
@bp.output(HealthSchema)
@bp.doc(summary="Liveness probe", description="Does not call upstream.")
def health():
    return {"status": "ok", "version": __version__}


@bp.get("/ready")
@bp.output(ReadySchema)
@bp.doc(summary="Readiness probe", description="503 if credentials are missing or upstream has been failing.", responses={503: "Not ready"})
def ready():
    config = current_app.extensions["evaskeri"]["config"]
    ok, reason = HEALTH.is_ready(config.ready_window)
    if not config.credentials_configured:
        ok, reason = False, "credentials not configured"
    body = {
        "status": "ready" if ok else "not_ready",
        "reason": reason,
        "credentials_configured": config.credentials_configured,
    }
    return body, 200 if ok else 503


@bp.get("/metrics")
@bp.doc(summary="Prometheus metrics", responses={200: "Prometheus text exposition format"})
def metrics():
    return Response(generate_latest(REGISTRY), mimetype=CONTENT_TYPE_LATEST)
