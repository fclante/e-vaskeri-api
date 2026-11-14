"""Original pass-through endpoints, kept for compatibility. Prefer /api/v1."""
from apiflask import APIBlueprint
from flask import current_app, jsonify, request

from ..upstream import UpstreamError

bp = APIBlueprint("legacy", __name__, tag="legacy")

SESSION_DOC = "Optional `session` query param. When omitted the API uses its own server-side login."


def _upstream():
    return current_app.extensions["evaskeri"]["upstream"]


@bp.get("/login")
@bp.doc(summary="Log in and return an upstream session token", deprecated=True)
def login():
    return jsonify({"session": _upstream().session_token(force_refresh=True)})


@bp.get("/running-machines")
@bp.doc(summary="Raw upstream live view", deprecated=True, description=SESSION_DOC)
def running_machines():
    return jsonify(_upstream().get_json("/Location/Live5/", "live_raw", request.args.get("session")))


@bp.get("/balance")
@bp.doc(summary="Raw upstream statement", deprecated=True, description=SESSION_DOC)
def balance():
    return jsonify(_upstream().get_json("/User/Statement5/", "statement_raw", request.args.get("session")))


@bp.get("/status")
@bp.doc(summary="Raw SVG status of one machine", deprecated=True)
def status():
    machine_type = request.args.get("machineType")
    machine_number = request.args.get("machineNumber")
    if not machine_type or not machine_number:
        return jsonify({"error": "machineType and machineNumber are required"}), 400
    detail = current_app.extensions["evaskeri"]["service"].unit_status(f"{machine_type} {machine_number}")
    if detail is None:
        return jsonify({"error": "Invalid machine type or number"}), 400
    return jsonify({
        "program": detail.get("program"),
        "currentStep": detail.get("current_step"),
        "status": detail.get("status"),
        "remainingTime": detail.get("remaining_time"),
    })
