"""The only code that changes the roster.

Each change is one transaction:
  1. lock the employee's roster row        (serialises double-submits)
  2. lock the cab rows, ascending id order (serialises seat races, no deadlocks)
  3. re-read occupancy and re-check the rules on fresh data
  4. write roster + request + audit, commit
"""

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from psycopg_pool import AsyncConnectionPool

from app import repo
from app.auth import TenantContext
from app.clock import Clock
from app.models import ChangeType, Direction, ShiftChangeRequest, Trip, TripOption
from app.rules import Violation, check_change, nearest_alternatives, seat_limit


@dataclass
class BookingResult:
    # change_trip: APPLIED, ALREADY_APPLIED, REJECTED, PENDING_APPROVAL, ALREADY_PENDING, NO_BOOKING
    # apply_decision: APPROVED_APPLIED, APPROVED_NO_SEAT, MANAGER_REJECTED, WITHDRAWN, EXPIRED
    status: str
    request_id: str | None = None
    violation: Violation | None = None
    alternatives: list[TripOption] = field(default_factory=list)
    current: Trip | None = None
    target: Trip | None = None
    request: ShiftChangeRequest | None = None


class RequestNotFound(Exception):
    pass


class AlreadyDecided(Exception):
    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


def new_request_id() -> str:
    return "SCR-" + uuid.uuid4().hex[:8].upper()


def approval_thread_id(request_id: str) -> str:
    return f"approval:{request_id}"


def dedupe_key(employee_id: str, roster_id: int, change_type: str, to_trip_id: str | None) -> str:
    raw = f"{employee_id}|{roster_id}|{change_type}|{to_trip_id or '-'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def change_trip(
    pool: AsyncConnectionPool,
    ctx: TenantContext,
    clock: Clock,
    *,
    trip_date,
    direction: Direction,
    change_type: ChangeType,
    desired_at: datetime | None,
    escalate: bool,
    conversation_id: str | None,
) -> BookingResult:
    now = clock.now()
    today = now.date()
    async with pool.connection() as conn, conn.transaction():
        # Don't queue behind a busy cab for longer than the tool timeout allows.
        await conn.execute("SET LOCAL lock_timeout = '1s'")
        company = await repo.get_company(conn, ctx.company_id)
        entry = await repo.get_roster_entry(conn, ctx, trip_date, direction, for_update=True)
        if change_type == "restore":
            if entry is not None:  # already booked: nothing to restore
                return BookingResult("ALREADY_APPLIED", None, None, [], entry.trip, entry.trip)
            return await _restore(conn, ctx, company, trip_date, direction, now, escalate, conversation_id)
        if entry is None:
            return BookingResult("NO_BOOKING")
        current = entry.trip

        options = await repo.list_trip_options(conn, ctx.company_id, current.route_id, direction, trip_date)
        target_trip = None
        if change_type == "reschedule" and desired_at is not None:
            target_trip = next((o.trip for o in options if o.trip.scheduled_at == desired_at), None)

        await repo.lock_trips(conn, ctx.company_id, [current.id] + ([target_trip.id] if target_trip else []))
        # Re-read occupancy now that we hold the cab locks: this is what makes the last-seat race safe.
        options = await repo.list_trip_options(conn, ctx.company_id, current.route_id, direction, trip_date)
        target = next((o for o in options if target_trip and o.trip.id == target_trip.id), None)

        violation = check_change(company, current, target, change_type, now, today)
        base = {
            "company_id": ctx.company_id,
            "employee_id": ctx.employee_id,
            "conversation_id": conversation_id,
            "roster_id": entry.id,
            "change_type": change_type,
            "from_trip_id": current.id,
            "to_trip_id": target.trip.id if target else None,
            "created_at": now,
        }
        key = dedupe_key(ctx.employee_id, entry.id, change_type, base["to_trip_id"])

        if violation is None:
            if change_type == "cancel":
                await conn.execute("UPDATE roster SET status = 'cancelled' WHERE id = %s AND company_id = %s", (entry.id, ctx.company_id))
            else:
                await conn.execute("UPDATE roster SET trip_id = %s WHERE id = %s AND company_id = %s", (target.trip.id, entry.id, ctx.company_id))
            # A newer change by the employee replaces any request still waiting for a manager.
            stale = await repo.find_pending_for_roster(conn, ctx.company_id, entry.id)
            if stale is not None:
                await repo.set_request_status(conn, ctx.company_id, stale.id, "WITHDRAWN", decided_by=ctx.employee_id,
                                              decided_at=now, note="Replaced by a newer change")
                await repo.insert_audit(conn, ctx.company_id, "employee", ctx.employee_id, "superseded", stale.id, {})
            req = await repo.insert_request(conn, {**base, "id": new_request_id(), "status": "APPLIED", "dedupe_key": key})
            await repo.insert_audit(conn, ctx.company_id, "employee", ctx.employee_id, "applied", req.id,
                                    {"from": current.id, "to": base["to_trip_id"], "type": change_type})
            return BookingResult("APPLIED", req.id, None, [], current, target.trip if target else None, req)

        if violation.code == "SAME_TRIP":
            prev = await repo.find_applied(conn, ctx.company_id, ctx.employee_id, entry.id, current.id)
            if prev is not None:
                return BookingResult("ALREADY_APPLIED", prev.id, None, [], current, current, prev)

        if violation.escalatable and escalate:
            existing = await repo.find_pending_for_roster(conn, ctx.company_id, entry.id)
            if existing is None:
                rid = new_request_id()
                existing_or_new = await repo.insert_request(conn, {
                    **base, "id": rid, "status": "PENDING_APPROVAL", "dedupe_key": key,
                    "reason_code": violation.code, "reason_text": violation.reason,
                    "approval_thread_id": approval_thread_id(rid),
                })
                if existing_or_new is not None:
                    await repo.insert_audit(conn, ctx.company_id, "employee", ctx.employee_id, "escalated", rid,
                                            {"from": current.id, "to": base["to_trip_id"], "reason": violation.code})
                    return BookingResult("PENDING_APPROVAL", rid, violation, [], current,
                                         target.trip if target else None, existing_or_new)
                existing = await repo.find_pending_by_dedupe(conn, ctx.company_id, key)
            target_of_existing = await repo.get_trip(conn, ctx.company_id, existing.to_trip_id) if existing.to_trip_id else None
            return BookingResult("ALREADY_PENDING", existing.id, violation, [], current, target_of_existing, existing)

        alternatives = []
        if change_type == "reschedule" and desired_at is not None:
            alternatives = nearest_alternatives(company, current, options, desired_at, now, today)
        await repo.insert_audit(conn, ctx.company_id, "employee", ctx.employee_id, "rejected", None,
                                {"from": current.id, "to": base["to_trip_id"], "reason": violation.code,
                                 "desired_at": desired_at})
        return BookingResult("REJECTED", None, violation, alternatives, current,
                             target.trip if target else None)


async def _restore(conn, ctx, company, trip_date, direction, now, escalate, conversation_id) -> BookingResult:
    """Book a cancelled trip again, if its seat is still free and it's not too late."""
    entry = await repo.get_cancelled_entry(conn, ctx, trip_date, direction, for_update=True)
    if entry is None:
        return BookingResult("NO_BOOKING")
    trip = entry.trip
    await repo.lock_trips(conn, ctx.company_id, [trip.id])
    target = TripOption(trip, await repo.occupancy(conn, ctx.company_id, trip.id))
    violation = check_change(company, trip, target, "restore", now, now.date())
    key = dedupe_key(ctx.employee_id, entry.id, "restore", trip.id)
    base = {"company_id": ctx.company_id, "employee_id": ctx.employee_id, "conversation_id": conversation_id,
            "roster_id": entry.id, "change_type": "restore", "from_trip_id": trip.id, "to_trip_id": trip.id,
            "created_at": now, "dedupe_key": key}
    if violation is None:
        await conn.execute("UPDATE roster SET status = 'active' WHERE id = %s AND company_id = %s", (entry.id, ctx.company_id))
        req = await repo.insert_request(conn, {**base, "id": new_request_id(), "status": "APPLIED"})
        await repo.insert_audit(conn, ctx.company_id, "employee", ctx.employee_id, "applied", req.id, {"restore": trip.id})
        return BookingResult("APPLIED", req.id, None, [], trip, trip, req)
    if violation.escalatable and escalate:
        existing = await repo.find_pending_for_roster(conn, ctx.company_id, entry.id)
        if existing is not None:
            return BookingResult("ALREADY_PENDING", existing.id, violation, [], trip, trip, existing)
        rid = new_request_id()
        req = await repo.insert_request(conn, {**base, "id": rid, "status": "PENDING_APPROVAL", "reason_code": violation.code,
                                              "reason_text": violation.reason, "approval_thread_id": approval_thread_id(rid)})
        await repo.insert_audit(conn, ctx.company_id, "employee", ctx.employee_id, "escalated", rid, {"restore": trip.id})
        return BookingResult("PENDING_APPROVAL", rid, violation, [], trip, trip, req)
    await repo.insert_audit(conn, ctx.company_id, "employee", ctx.employee_id, "rejected", None,
                            {"restore": trip.id, "reason": violation.code})
    return BookingResult("REJECTED", None, violation, [], trip, trip)


async def apply_decision(
    pool: AsyncConnectionPool,
    company_id: str,
    request_id: str,
    clock: Clock,
    *,
    kind: Literal["approve", "reject", "withdraw", "expire"],
    actor_type: str,
    actor_id: str,
    note: str | None = None,
) -> BookingResult:
    """Settle a pending request. A manager's approval overrides the cut-off, never capacity."""
    now = clock.now()
    async with pool.connection() as conn, conn.transaction():
        req = await repo.get_request(conn, company_id, request_id, for_update=True)
        if req is None:
            raise RequestNotFound(request_id)
        if req.status != "PENDING_APPROVAL":
            raise AlreadyDecided(req.status)

        company = await repo.get_company(conn, company_id)
        entry = await repo.get_roster_by_id(conn, company_id, req.roster_id, for_update=True)
        current = entry.trip
        target = await repo.get_trip(conn, company_id, req.to_trip_id) if req.to_trip_id else None
        await repo.lock_trips(conn, company_id, [current.id] + ([target.id] if target else []))

        alternatives: list[TripOption] = []
        if kind == "approve" and req.change_type == "restore":
            if entry.status == "active":
                status = "EXPIRED"  # already booked again meanwhile
            elif await repo.occupancy(conn, company_id, current.id) >= seat_limit(company, current):
                status = "APPROVED_NO_SEAT"
            else:
                await conn.execute("UPDATE roster SET status = 'active' WHERE id = %s AND company_id = %s", (entry.id, company_id))
                status = "APPROVED_APPLIED"
        elif kind == "approve":
            if entry.status != "active" or entry.trip.id != req.from_trip_id:
                status = "EXPIRED"  # the trip changed meanwhile; approving would undo it
            elif req.change_type == "cancel":
                await conn.execute("UPDATE roster SET status = 'cancelled' WHERE id = %s AND company_id = %s", (entry.id, company_id))
                status = "APPROVED_APPLIED"
            elif await repo.occupancy(conn, company_id, target.id) >= seat_limit(company, target):
                status = "APPROVED_NO_SEAT"
                options = await repo.list_trip_options(conn, company_id, current.route_id, current.direction, current.trip_date)
                alternatives = nearest_alternatives(company, current, options, target.scheduled_at, now, now.date())
            else:
                await conn.execute("UPDATE roster SET trip_id = %s WHERE id = %s AND company_id = %s", (target.id, entry.id, company_id))
                status = "APPROVED_APPLIED"
        else:
            status = {"reject": "MANAGER_REJECTED", "withdraw": "WITHDRAWN", "expire": "EXPIRED"}[kind]

        await repo.set_request_status(conn, company_id, request_id, status, decided_by=actor_id, decided_at=now, note=note)
        await repo.insert_audit(conn, company_id, actor_type, actor_id, status.lower(), request_id, {"note": note})
        decided = await repo.get_request(conn, company_id, request_id)
        return BookingResult(status, request_id, None, alternatives, current, target, decided)
