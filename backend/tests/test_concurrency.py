"""Bonus: exactly one seat and exactly one request under concurrent submits."""

import asyncio
from datetime import datetime

from app.booking import change_trip
from app.clock import IST, FixedClock
from tests.conftest import TODAY, at, ctx_for, fetchall


def noon():
    return FixedClock(datetime(2026, 11, 20, 12, 0, tzinfo=IST))


async def _move(pool, clock, emp, hhmm):
    return await change_trip(
        pool, ctx_for(emp), clock, trip_date=TODAY, direction="drop", change_type="reschedule",
        desired_at=at(hhmm), escalate=False, conversation_id=None,
    )


async def test_last_seat_race_exactly_one(seeded):
    clock = noon()
    a, b = await asyncio.gather(_move(seeded, clock, "A-E01", "20:30"), _move(seeded, clock, "A-E02", "20:30"))
    statuses = sorted([a.status, b.status])
    assert statuses == ["APPLIED", "REJECTED"]
    loser = a if a.status == "REJECTED" else b
    assert loser.violation.code == "NO_SEAT"
    occ = await fetchall(seeded, "SELECT count(*) AS n FROM roster WHERE trip_id='acme-r1-1120-d-2030' AND status='active'")
    assert occ[0]["n"] == 4


async def test_double_submit_exactly_one_request(seeded):
    clock = noon()
    a, b = await asyncio.gather(_move(seeded, clock, "A-E01", "21:30"), _move(seeded, clock, "A-E01", "21:30"))
    assert sorted([a.status, b.status]) == ["ALREADY_APPLIED", "APPLIED"]
    assert a.request_id == b.request_id
    rows = await fetchall(seeded, "SELECT id FROM shift_change_requests")
    assert len(rows) == 1


async def test_double_submit_escalation_exactly_one_pending(seeded, clock):
    async def esc():
        return await change_trip(
            seeded, ctx_for("A-E02"), clock, trip_date=TODAY, direction="drop", change_type="reschedule",
            desired_at=at("19:30"), escalate=True, conversation_id=None,
        )

    a, b = await asyncio.gather(esc(), esc())
    assert sorted([a.status, b.status]) == ["ALREADY_PENDING", "PENDING_APPROVAL"]
    rows = await fetchall(seeded, "SELECT id FROM shift_change_requests WHERE status='PENDING_APPROVAL'")
    assert len(rows) == 1
