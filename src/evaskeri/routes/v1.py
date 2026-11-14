from apiflask import APIBlueprint, abort
from flask import current_app

from ..schemas import (
    BalanceSchema, CalendarQuery, CalendarSchema, MachineDetailSchema, MachinesSchema, MessagesQuery, MessagesSchema,
    PeriodsQuery, PeriodsSchema, PricesSchema, StatementQuery, StatementSchema, StatsQuery, StatsSchema,
)

bp = APIBlueprint("v1", __name__, url_prefix="/api/v1", tag="v1")


def _service():
    return current_app.extensions["evaskeri"]["service"]


@bp.get("/machines")
@bp.output(MachinesSchema)
@bp.doc(summary="All machines with live state", description="Free/busy state per machine, plus program, step and estimated finish time for running ones.")
def machines():
    return _service().machines()


@bp.get("/machines/<int:machine_id>")
@bp.output(MachineDetailSchema)
@bp.doc(summary="A single machine")
def machine(machine_id: int):
    found = _service().machine(machine_id)
    if found is None:
        abort(404, "Unknown machine")
    return found


@bp.get("/balance")
@bp.output(BalanceSchema)
@bp.doc(summary="Current balance in DKK")
def balance():
    return _service().balance()


@bp.get("/statement")
@bp.input(StatementQuery, location="query")
@bp.output(StatementSchema)
@bp.doc(summary="Usage and payments, newest first, with ISO dates and DKK amounts")
def statement(query_data):
    return _service().statement(query_data["limit"], query_data["since"])


@bp.get("/stats")
@bp.input(StatsQuery, location="query")
@bp.output(StatsSchema)
@bp.doc(summary="Derived usage statistics", description="Monthly spend, averages, favourites and a weekday x hour heatmap.")
def stats(query_data):
    return _service().stats(query_data["months"])


@bp.get("/periods")
@bp.input(PeriodsQuery, location="query")
@bp.output(PeriodsSchema)
@bp.doc(summary="Billing periods, newest first", description="Each period has its spend and closing balance. Add `details=true` for the daily usage lines.")
def periods(query_data):
    return _service().periods(query_data["limit"], query_data["details"])


@bp.get("/calendar")
@bp.input(CalendarQuery, location="query")
@bp.output(CalendarSchema)
@bp.doc(summary="Booking availability", description="Reservation slots per day (local time) and the next bookable slot. `available` is false when the location has no reservations.")
def calendar(query_data):
    return _service().calendar(query_data["date"], query_data["days"], query_data["calendar_id"])


@bp.get("/prices")
@bp.output(PricesSchema)
@bp.doc(summary="Price list per machine group")
def prices():
    return _service().prices()


@bp.get("/messages")
@bp.input(MessagesQuery, location="query")
@bp.output(MessagesSchema)
@bp.doc(summary="Landlord and system messages", description="Newest first. One-time codes and e-mail addresses in the text are masked.")
def messages(query_data):
    return _service().messages(query_data["limit"])
