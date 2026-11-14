from apiflask import Schema
from apiflask.fields import Boolean, Dict, Float, Integer, List, Nested, String
from apiflask.validators import OneOf, Range, Regexp


def DateTime(**kwargs):
    """ISO-8601 timestamp string (the services already emit ISO strings)."""
    metadata = {"format": "date-time", **kwargs.pop("metadata", {})}
    return String(metadata=metadata, **kwargs)


MACHINE_TYPES = ["washer", "dryer", "mangle", "other"]
MACHINE_STATES = ["free", "busy", "done", "error", "unknown"]


class MachineSchema(Schema):
    id = Integer(allow_none=True, metadata={"description": "Unit number, matches UnitId on receipts"})
    name = String(metadata={"example": "Vaskemaskine 1"})
    type = String(validate=OneOf(MACHINE_TYPES))
    state = String(validate=OneOf(MACHINE_STATES))
    raw_status = String(allow_none=True, metadata={"description": "Status text exactly as reported upstream"})
    program = String(allow_none=True, metadata={"example": "95° Normal"})
    step = String(allow_none=True, metadata={"example": "Hovedvask"})
    remaining_seconds = Integer(allow_none=True)
    ends_at = DateTime(allow_none=True, metadata={"description": "Estimated finish time (UTC)"})


class MachineDetailSchema(MachineSchema):
    location = String(allow_none=True)
    updated_at = DateTime()


class TypeCountSchema(Schema):
    total = Integer()
    free = Integer()
    busy = Integer()


class NextFreeSchema(Schema):
    id = Integer(allow_none=True)
    name = String()
    ends_at = DateTime()


class MachinesSummarySchema(Schema):
    total = Integer()
    free = Integer()
    busy = Integer()
    by_type = Dict(keys=String(), values=Nested(TypeCountSchema))
    next_free = Nested(NextFreeSchema, allow_none=True)


class MachinesSchema(Schema):
    location = String(allow_none=True, metadata={"example": "Sprotoften 51"})
    updated_at = DateTime(metadata={"description": "When the data was fetched from upstream (UTC)"})
    upstream_updated = String(allow_none=True, metadata={"example": "22:18"})
    summary = Nested(MachinesSummarySchema)
    machines = List(Nested(MachineSchema))


class LastPaymentSchema(Schema):
    date = String(allow_none=True)
    amount_dkk = Float()
    description = String()


class BalanceSchema(Schema):
    currency = String(metadata={"example": "DKK"})
    balance_dkk = Float(allow_none=True, metadata={"description": "Negative means money is owed"})
    owed_dkk = Float(allow_none=True)
    previous_period_dkk = Float(allow_none=True)
    last_payment = Nested(LastPaymentSchema, allow_none=True)
    updated_at = DateTime()


class StatementEntrySchema(Schema):
    date = String(allow_none=True, metadata={"example": "2026-11-06"})
    time = String(allow_none=True, metadata={"example": "10:36"})
    kind = String(validate=OneOf(["usage", "payment"]))
    unit_id = Integer(allow_none=True)
    unit_type = String(allow_none=True)
    unit_label = String(allow_none=True)
    description = String()
    program = Integer(allow_none=True)
    temperature_c = Integer(allow_none=True)
    duration_min = Integer(allow_none=True)
    amount_dkk = Float(metadata={"description": "Positive = charge, negative = payment"})


class StatementSchema(Schema):
    updated_at = DateTime()
    total = Integer()
    entries = List(Nested(StatementEntrySchema))


class StatementQuery(Schema):
    limit = Integer(load_default=50, validate=Range(min=1, max=500))
    since = String(load_default=None, metadata={"description": "ISO date, e.g. 2026-01-01"})


class MonthSpendSchema(Schema):
    month = String(metadata={"example": "2026-11"})
    washer_dkk = Float()
    dryer_dkk = Float()
    total_dkk = Float()


class TotalsSchema(Schema):
    washer_dkk = Float()
    dryer_dkk = Float()
    total_dkk = Float()
    washes = Integer()
    dries = Integer()


class FavouriteMachineSchema(Schema):
    name = String()
    count = Integer()


class ProgramCountSchema(Schema):
    program = Integer()
    count = Integer()


class TemperatureCountSchema(Schema):
    temperature_c = Integer()
    count = Integer()


class StatsSchema(Schema):
    updated_at = DateTime()
    months = List(Nested(MonthSpendSchema))
    totals = Nested(TotalsSchema)
    current_month_spend_dkk = Float()
    avg_cost_per_wash_dkk = Float(allow_none=True)
    avg_cost_per_dry_dkk = Float(allow_none=True)
    favourite_machine = Nested(FavouriteMachineSchema, allow_none=True)
    top_programs = List(Nested(ProgramCountSchema))
    top_temperatures = List(Nested(TemperatureCountSchema))
    heatmap = List(
        List(Integer()),
        metadata={"description": "7 rows (Monday=0) x 24 columns (hour of day) of usage counts"},
    )


class StatsQuery(Schema):
    months = Integer(load_default=12, validate=Range(min=1, max=60))


class PeriodDaySchema(Schema):
    date = String(allow_none=True)
    spend_dkk = Float()
    balance_dkk = Float(allow_none=True, metadata={"description": "Running balance after this day, negative = owed"})
    lines = List(Nested(StatementEntrySchema))


class PeriodSchema(Schema):
    title = String(metadata={"example": "6 - 22. november 2024"})
    start = String(allow_none=True)
    end = String(allow_none=True)
    note = String(allow_none=True, metadata={"example": "Forbrug overført til husleje"})
    balance_dkk = Float(allow_none=True, metadata={"description": "Balance at the end of the period, negative = owed"})
    spend_dkk = Float()
    usage_count = Integer()
    days = List(Nested(PeriodDaySchema), metadata={"description": "Only present with details=true"})


class PeriodsSchema(Schema):
    updated_at = DateTime()
    total = Integer()
    periods = List(Nested(PeriodSchema))


class PeriodsQuery(Schema):
    limit = Integer(load_default=30, validate=Range(min=1, max=100))
    details = Boolean(load_default=False, metadata={"description": "Include the daily usage lines of each period"})


SLOT_STATES = [
    "free", "chance", "closed", "past", "limit_reached", "yours", "reserved", "full", "unavailable", "unknown",
]


class SlotSchema(Schema):
    start = String(metadata={"example": "2026-11-14T07:00", "description": "Local time (Europe/Copenhagen)"})
    end = String()
    state = String(validate=OneOf(SLOT_STATES))
    bookable = Boolean(metadata={"description": "True when at least one machine can be reserved"})
    free_unit_ids = List(Integer(), metadata={"description": "Machines that are free in this slot (individual calendars)"})


class CalendarDaySchema(Schema):
    date = String()
    slots = List(Nested(SlotSchema))


class CalendarUnitSchema(Schema):
    id = Integer(allow_none=True)
    name = String(allow_none=True)


class CalendarItemSchema(Schema):
    calendar_id = Integer(allow_none=True)
    description = String(allow_none=True, metadata={"example": "Vaskemaskine 1 - 8"})
    from_ = String(data_key="from", attribute="from", allow_none=True)
    to = String(allow_none=True)
    remaining_bookings = Integer(allow_none=True, metadata={"description": "Null means no limit"})
    disabled = Boolean()
    individual = Boolean(metadata={"description": "Machines are booked individually rather than as a group"})
    units = List(Nested(CalendarUnitSchema))
    days = List(Nested(CalendarDaySchema))


class NextFreeSlotSchema(Schema):
    calendar_id = Integer(allow_none=True)
    start = String()
    end = String()
    free_unit_ids = List(Integer())


class CalendarSchema(Schema):
    updated_at = DateTime()
    available = Boolean(metadata={"description": "False when reservations are not enabled for this location"})
    location = String(allow_none=True)
    calendars = List(Nested(CalendarItemSchema))
    next_free_slot = Nested(NextFreeSlotSchema, allow_none=True)


class CalendarQuery(Schema):
    date = String(
        load_default=None, validate=Regexp(r"^(\d{4}-\d{2}-\d{2}|\d{1,3})$"),
        metadata={"description": "yyyy-mm-dd, or a day offset from today. Defaults to today."},
    )
    days = Integer(load_default=7, validate=Range(min=1, max=31))
    calendar_id = Integer(load_default=0, validate=Range(min=0, max=99), metadata={"description": "0 = all calendars"})


class PriceSchema(Schema):
    label = String(metadata={"example": "Pris pr. start"})
    amount_dkk = Float(allow_none=True)
    text = String(metadata={"example": "18,00 kr."})


class PriceGroupSchema(Schema):
    label = String(allow_none=True, metadata={"example": "Vaskemaskine 2 - 4, 6 - 8"})
    unit_type = String(validate=OneOf(MACHINE_TYPES))
    unit_ids = List(Integer(), metadata={"description": "Machine numbers the price applies to"})
    prices = List(Nested(PriceSchema))


class PricesSchema(Schema):
    updated_at = DateTime()
    available = Boolean()
    location = String(allow_none=True)
    groups = List(Nested(PriceGroupSchema))


class MessageSchema(Schema):
    id = String(allow_none=True)
    received = String(allow_none=True, metadata={"example": "mandag, 5.  august kl. 10:12"})
    subject = String(allow_none=True)
    body = String(metadata={"description": "Plain text. One-time codes and e-mail addresses are masked"})
    redacted = Boolean(metadata={"description": "True when something was masked in the body"})


class MessagesSchema(Schema):
    updated_at = DateTime()
    available = Boolean()
    total = Integer()
    messages = List(Nested(MessageSchema))


class MessagesQuery(Schema):
    limit = Integer(load_default=20, validate=Range(min=1, max=100))


class HealthSchema(Schema):
    status = String(metadata={"example": "ok"})
    version = String()


class ReadySchema(Schema):
    status = String(validate=OneOf(["ready", "not_ready"]))
    reason = String()
    credentials_configured = Boolean()
