"""The tools the planner may call.

The model only ever supplies business arguments (which day, pickup or drop,
what time). Who is asking comes from ToolDeps.ctx, which the API builds from
the verified JWT. Argument models forbid extra fields, so a model that tries
to pass `employee_id` or `company_id` produces an invalid plan.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app import repo
from app.auth import TenantContext
from app.booking import BookingResult, change_trip
from app.clock import IST, Clock
from app.faults import FaultInjector, ToolFailure, call_tool
from app.models import ChangeType, Direction, Trip
from app.rules import check_change, nearest_alternatives

log = logging.getLogger("app.tools")

DayKey = Literal["today", "tomorrow"]
TIME_PATTERN = r"^\d{1,2}:\d{2}$"


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetRosterArgs(_Args):
    date: DayKey | None = Field(None, description="Which day; omit for today and tomorrow.")


class CheckCabOptionsArgs(_Args):
    date: DayKey | None = Field(None, description="Omit unless the employee said today or tomorrow; the system then uses the next trip that has not left")
    direction: Direction = Field(description="pickup = to office, drop = home from office")
    desired_time: str = Field(pattern=TIME_PATTERN, description="24h HH:MM")


class RaiseRequestArgs(_Args):
    date: DayKey | None = Field(None, description="Omit unless the employee said today or tomorrow; the system then uses the next trip that has not left")
    direction: Direction = Field(description="pickup = to office, drop = home from office")
    change_type: ChangeType = Field(description="reschedule = move to another cab; cancel = not travelling (leave/WFH); restore = book a cancelled trip again")
    desired_time: str | None = Field(None, pattern=TIME_PATTERN, description="24h HH:MM, required for reschedule")
    escalate: bool = Field(False, description="True only after the employee agreed to send it to their manager")


class RequestStatusArgs(_Args):
    request_id: str | None = Field(None, description="Omit for the latest request")


class WithdrawArgs(_Args):
    request_id: str | None = Field(None, description="Omit for the pending request")


class AskClarificationArgs(_Args):
    question: str = Field(min_length=8, max_length=300, pattern=r"\?\s*$", description="A full question ending with '?'")
    missing: list[str] = Field(default_factory=list)


TOOL_SPECS: dict[str, tuple[type[BaseModel], str]] = {
    "get_roster": (GetRosterArgs, "Show the employee's booked cab trips."),
    "check_cab_options": (CheckCabOptionsArgs, "Check whether a cab at a given time is possible and list the nearest free ones. Changes nothing."),
    "raise_shift_change_request": (RaiseRequestArgs, "Move the employee's pickup/drop to another cab time, or cancel it. Applies it if company rules allow."),
    "get_request_status": (RequestStatusArgs, "Status of the employee's shift-change request."),
    "withdraw_request": (WithdrawArgs, "Withdraw the employee's request that is waiting for manager approval."),
    "ask_clarification": (AskClarificationArgs, "Ask the employee a question when the time or pickup/drop is unclear. Never guess."),
}

SYSTEM_OF = {
    "get_roster": "roster",
    "check_cab_options": "cabs",
    "raise_shift_change_request": "booking",
    "get_request_status": "roster",
    "withdraw_request": "booking",
}


def tool_schemas() -> list[dict]:
    out = []
    for name, (model, description) in TOOL_SPECS.items():
        params = model.model_json_schema()
        params.pop("title", None)
        out.append({"type": "function", "function": {"name": name, "description": description, "parameters": params}})
    return out


@dataclass
class ToolDeps:
    pool: Any
    ctx: TenantContext
    clock: Clock
    faults: FaultInjector
    conversation_id: str | None
    services: Any


@dataclass
class ToolOutcome:
    tool: str
    ok: bool
    kind: str
    data: dict = field(default_factory=dict)
    system: str | None = None

    def to_dict(self) -> dict:
        return {"tool": self.tool, "ok": self.ok, "kind": self.kind, "data": self.data, "system": self.system}


def hhmm(dt: datetime | None) -> str | None:
    return dt.astimezone(IST).strftime("%H:%M") if dt else None


def day_of(key: DayKey | None, today: date) -> date:
    return today + timedelta(days=1) if key == "tomorrow" else today


def _trip_time(trip: Trip | None) -> str | None:
    return hhmm(trip.scheduled_at) if trip else None


async def _current_time(deps: ToolDeps, trip_date: date, direction: str) -> str | None:
    """Best-effort lookup of what is still booked, for failure messages."""
    try:
        async with deps.pool.connection() as conn:
            entry = await repo.get_roster_entry(conn, deps.ctx, trip_date, direction)
        return _trip_time(entry.trip) if entry else None
    except Exception:
        return None


async def run_tool(name: str, args: BaseModel, deps: ToolDeps, *, desired_at: datetime | None = None) -> ToolOutcome:
    """Execute one validated tool call. Never raises: failures become outcomes."""
    today = deps.clock.now().date()
    trip_date = day_of(getattr(args, "date", None), today)
    direction = getattr(args, "direction", None)
    base = {"date": trip_date.isoformat(), "day": getattr(args, "date", None) or "today", "direction": direction}
    try:
        if name == "ask_clarification":
            return ToolOutcome(name, True, "clarify", {"question": args.question})
        handler = _HANDLERS[name]
        return await handler(args, deps, trip_date, base, desired_at)
    except ToolFailure as e:
        current = await _current_time(deps, trip_date, direction) if direction else None
        return ToolOutcome(name, False, "tool_failure", {**base, "failure": e.kind, "current": current}, e.system)


async def _get_roster(args: GetRosterArgs, deps, trip_date, base, _):
    today = deps.clock.now().date()
    dates = [trip_date] if args.date else [today, today + timedelta(days=1)]

    async def read():
        async with deps.pool.connection() as conn:
            return await repo.list_roster(conn, deps.ctx, dates)

    entries = await call_tool("roster", read, deps.faults, retries=1)
    trips = [
        {"date": e.trip_date.isoformat(), "day": "today" if e.trip_date == today else "tomorrow",
         "direction": e.direction, "time": _trip_time(e.trip), "vehicle_no": e.trip.vehicle_no}
        for e in entries
    ]
    return ToolOutcome("get_roster", True, "roster_info", {**base, "trips": trips})


async def _check_cab_options(args: CheckCabOptionsArgs, deps, trip_date, base, desired_at):
    now = deps.clock.now()

    async def read():
        async with deps.pool.connection() as conn:
            company = await repo.get_company(conn, deps.ctx.company_id)
            entry = await repo.get_roster_entry(conn, deps.ctx, trip_date, args.direction)
            if entry is None:
                return None
            options = await repo.list_trip_options(conn, deps.ctx.company_id, entry.trip.route_id, args.direction, trip_date)
            return company, entry, options

    found = await call_tool("cabs", read, deps.faults, retries=1)
    if found is None:
        return ToolOutcome("check_cab_options", True, "no_booking", base)
    company, entry, options = found
    target = next((o for o in options if o.trip.scheduled_at == desired_at), None)
    violation = check_change(company, entry.trip, target, "reschedule", now, now.date())
    alts = nearest_alternatives(company, entry.trip, options, desired_at, now, now.date()) if desired_at else []
    return ToolOutcome("check_cab_options", True, "options_info", {
        **base,
        "current": _trip_time(entry.trip),
        "requested": {"time": hhmm(desired_at), "available": violation is None,
                      "reason": violation.code if violation else None},
        "options": [_trip_time(a.trip) for a in alts],
        "trip_ids": [a.trip.id for a in alts],
    })


def _booking_outcome(res: BookingResult, base: dict, desired_at: datetime | None, change_type: str) -> ToolOutcome:
    name = "raise_shift_change_request"
    common = {**base, "change_type": change_type, "from": _trip_time(res.current)}
    if res.status == "APPLIED":
        vehicle = res.target.vehicle_no if res.target else res.current.vehicle_no
        return ToolOutcome(name, True, "applied", {**common, "request_id": res.request_id,
                                                   "to": _trip_time(res.target), "vehicle_no": vehicle})
    if res.status == "ALREADY_APPLIED":
        return ToolOutcome(name, True, "already_applied", {**common, "request_id": res.request_id, "to": _trip_time(res.target)})
    if res.status == "PENDING_APPROVAL":
        return ToolOutcome(name, True, "pending", {**common, "request_id": res.request_id, "to": _trip_time(res.target)})
    if res.status == "ALREADY_PENDING":
        return ToolOutcome(name, True, "already_pending", {**common, "request_id": res.request_id,
                                                           "to": _trip_time(res.target), "created": hhmm(res.request.created_at)})
    if res.status == "NO_BOOKING":
        return ToolOutcome(name, True, "no_booking", base)
    v = res.violation
    return ToolOutcome(name, True, "rejected", {
        **common,
        "to": _trip_time(res.target) or hhmm(desired_at),
        "reason_code": v.code, "reason": v.reason, "escalatable": v.escalatable,
        "alternatives": [_trip_time(a.trip) for a in res.alternatives],
    })


async def _raise_request(args: RaiseRequestArgs, deps, trip_date, base, desired_at):
    if args.change_type == "reschedule" and desired_at is None:
        return ToolOutcome("raise_shift_change_request", True, "clarify", {"missing": "time", **base})

    async def write():
        return await change_trip(
            deps.pool, deps.ctx, deps.clock, trip_date=trip_date, direction=args.direction,
            change_type=args.change_type, desired_at=desired_at if args.change_type == "reschedule" else None,
            escalate=args.escalate, conversation_id=deps.conversation_id,
        )

    before = await _latest_request_id(deps)
    try:
        res = await call_tool("booking", write, deps.faults, retries=0)
    except ToolFailure:
        # The write may have committed just before the timeout fired. Never say
        # "nothing was changed" without checking.
        landed = await _request_written_since(deps, before)
        if landed is None:
            raise
        return landed
    if res.status == "PENDING_APPROVAL":
        try:
            await deps.services.approvals.on_pending(res.request_id, deps.ctx.company_id)
        except Exception:
            # The request row exists; a manager decision still works without the
            # approval thread (resume falls back to applying directly).
            log.exception("could not start approval thread for %s", res.request_id)
    return _booking_outcome(res, base, desired_at, args.change_type)


async def _latest_request_id(deps: ToolDeps) -> str | None:
    try:
        async with deps.pool.connection() as conn:
            req = await repo.latest_request(conn, deps.ctx)
        return req.id if req else None
    except Exception:
        return None


async def _request_written_since(deps: ToolDeps, before: str | None) -> ToolOutcome | None:
    """If a booking timed out but its request row exists, describe what really happened."""
    try:
        async with deps.pool.connection() as conn:
            req = await repo.latest_request(conn, deps.ctx)
            if req is None or req.id == before or req.status not in ("APPLIED", "PENDING_APPROVAL"):
                return None
            data = await _status_data(conn, deps, req)
            to_trip = await repo.get_trip(conn, deps.ctx.company_id, req.to_trip_id) if req.to_trip_id else None
            from_trip = await repo.get_trip(conn, deps.ctx.company_id, req.from_trip_id)
    except Exception:
        return None
    today = deps.clock.now().date()
    data["day"] = "today" if from_trip.trip_date == today else "tomorrow"
    if req.status == "APPLIED":
        data["vehicle_no"] = (to_trip or from_trip).vehicle_no
        return ToolOutcome("raise_shift_change_request", True, "applied", data)
    return ToolOutcome("raise_shift_change_request", True, "pending", data)


async def _status_data(conn, deps: ToolDeps, req) -> dict:
    from_trip = await repo.get_trip(conn, deps.ctx.company_id, req.from_trip_id)
    to_trip = await repo.get_trip(conn, deps.ctx.company_id, req.to_trip_id) if req.to_trip_id else None
    return {
        "request_id": req.id, "status": req.status, "change_type": req.change_type,
        "direction": from_trip.direction, "date": from_trip.trip_date.isoformat(),
        "from": _trip_time(from_trip), "to": _trip_time(to_trip),
        "created": hhmm(req.created_at), "note": req.note,
    }


async def _find_request(conn, deps: ToolDeps, request_id: str | None, *, pending_only: bool):
    if request_id:
        req = await repo.get_request(conn, deps.ctx.company_id, request_id, employee_id=deps.ctx.employee_id)
        if req and pending_only and req.status != "PENDING_APPROVAL":
            return None
        return req
    return await repo.latest_request(conn, deps.ctx, status="PENDING_APPROVAL" if pending_only else None)


async def _get_request_status(args: RequestStatusArgs, deps, trip_date, base, _):
    async def read():
        async with deps.pool.connection() as conn:
            req = await _find_request(conn, deps, args.request_id, pending_only=False)
            return await _status_data(conn, deps, req) if req else None

    data = await call_tool("roster", read, deps.faults, retries=1)
    return ToolOutcome("get_request_status", True, "status_info", data or {"none": True})


async def _withdraw(args: WithdrawArgs, deps, trip_date, base, _):
    async def act():
        async with deps.pool.connection() as conn:
            req = await _find_request(conn, deps, args.request_id, pending_only=True)
            if req is None:
                return None
            data = await _status_data(conn, deps, req)
        await deps.services.approvals.withdraw(deps.ctx, req.id)
        return data

    data = await call_tool("booking", act, deps.faults, retries=0)
    if data is None:
        return ToolOutcome("withdraw_request", True, "status_info", {"none": True})
    return ToolOutcome("withdraw_request", True, "withdrawn", data)


_HANDLERS = {
    "get_roster": _get_roster,
    "check_cab_options": _check_cab_options,
    "raise_shift_change_request": _raise_request,
    "get_request_status": _get_request_status,
    "withdraw_request": _withdraw,
}
