import pytest

from app.booking import AlreadyDecided, apply_decision, change_trip
from tests.conftest import TODAY, at, ctx_for, fetchall


async def change(pool, clock, emp, hhmm=None, *, direction="drop", change_type="reschedule", escalate=False, day=TODAY):
    return await change_trip(
        pool, ctx_for(emp), clock, trip_date=day, direction=direction, change_type=change_type,
        desired_at=at(hhmm, day) if hhmm else None, escalate=escalate, conversation_id=None,
    )


async def roster_trip(pool, emp, direction="drop", day=TODAY):
    rows = await fetchall(
        pool,
        "SELECT trip_id FROM roster WHERE employee_id=%s AND trip_date=%s AND direction=%s AND status='active'",
        emp, day, direction,
    )
    return rows[0]["trip_id"] if rows else None


async def test_change_applied_globex(seeded, clock):
    res = await change(seeded, clock, "G-E01", "19:30")
    assert res.status == "APPLIED"
    assert res.request_id.startswith("SCR-") and len(res.request_id) == 12
    assert await roster_trip(seeded, "G-E01") == "globex-r1-1120-d-1930"
    reqs = await fetchall(seeded, "SELECT status FROM shift_change_requests")
    assert [r["status"] for r in reqs] == ["APPLIED"]
    audit = await fetchall(seeded, "SELECT action FROM audit_log WHERE request_id=%s", res.request_id)
    assert [a["action"] for a in audit] == ["applied"]


async def test_change_rejected_cutoff_acme_not_applied(seeded, clock):
    res = await change(seeded, clock, "A-E02", "19:30")
    assert res.status == "REJECTED"
    assert res.violation.code == "CUTOFF_PASSED" and res.violation.escalatable
    assert await roster_trip(seeded, "A-E02") == "acme-r1-1120-d-1900"
    assert await fetchall(seeded, "SELECT id FROM shift_change_requests") == []
    audit = await fetchall(seeded, "SELECT action FROM audit_log")
    assert [a["action"] for a in audit] == ["rejected"]


async def test_change_no_seat_offers_alternatives(seeded, clock):
    res = await change(seeded, clock, "A-E01", "20:00")
    assert res.status == "REJECTED" and res.violation.code == "NO_SEAT"
    assert [a.trip.scheduled_at.strftime("%H:%M") for a in res.alternatives] == ["19:30", "20:30"]


async def test_escalate_creates_pending_once(seeded, clock):
    first = await change(seeded, clock, "A-E02", "19:30", escalate=True)
    second = await change(seeded, clock, "A-E02", "19:30", escalate=True)
    assert first.status == "PENDING_APPROVAL"
    assert second.status == "ALREADY_PENDING" and second.request_id == first.request_id
    rows = await fetchall(seeded, "SELECT status, approval_thread_id FROM shift_change_requests")
    assert rows == [{"status": "PENDING_APPROVAL", "approval_thread_id": f"approval:{first.request_id}"}]
    assert await roster_trip(seeded, "A-E02") == "acme-r1-1120-d-1900"


async def test_escalate_ignored_for_hard_violation(seeded, clock):
    res = await change(seeded, clock, "A-E01", "20:00", escalate=True)
    assert res.status == "REJECTED" and res.violation.code == "NO_SEAT"
    assert await fetchall(seeded, "SELECT id FROM shift_change_requests") == []


async def test_cancel_frees_seat(seeded, clock):
    res = await change(seeded, clock, "G-F01", change_type="cancel")
    assert res.status == "APPLIED"
    occ = await fetchall(seeded, "SELECT count(*) AS n FROM roster WHERE trip_id='globex-r1-1120-d-2000' AND status='active'")
    assert occ[0]["n"] == 5


async def test_no_booking(seeded, clock):
    res = await change(seeded, clock, "A-E01", "08:00", direction="pickup")
    assert res.status == "NO_BOOKING"
    assert await fetchall(seeded, "SELECT id FROM shift_change_requests") == []


async def test_after_last_slot_no_such_slot(seeded, clock):
    from datetime import timedelta

    res = await change_trip(
        seeded, ctx_for("A-E01"), clock, trip_date=TODAY, direction="drop", change_type="reschedule",
        desired_at=at("00:30", TODAY + timedelta(days=1)), escalate=False, conversation_id=None,
    )
    assert res.status == "REJECTED" and res.violation.code == "NO_SUCH_SLOT"
    assert [a.trip.scheduled_at.strftime("%H:%M") for a in res.alternatives] == ["23:30", "23:00"]


async def test_apply_decision_approve_applies(seeded, clock):
    pending = await change(seeded, clock, "A-E02", "19:30", escalate=True)
    res = await apply_decision(seeded, "acme", pending.request_id, clock, kind="approve", actor_type="manager", actor_id="A-M01")
    assert res.status == "APPROVED_APPLIED"
    assert await roster_trip(seeded, "A-E02") == "acme-r1-1120-d-1930"


async def test_apply_decision_approve_rechecks_capacity(seeded, clock):
    pending = await change(seeded, clock, "A-E02", "20:30", escalate=True)
    async with seeded.connection() as conn:
        # Another rider takes the last 20:30 seat while the manager is deciding.
        await conn.execute("UPDATE roster SET trip_id = 'acme-r1-1120-d-2030' WHERE employee_id = 'A-F01'")
    res = await apply_decision(seeded, "acme", pending.request_id, clock, kind="approve", actor_type="manager", actor_id="A-M01")
    assert res.status == "APPROVED_NO_SEAT"
    assert await roster_trip(seeded, "A-E02") == "acme-r1-1120-d-1900"


async def test_apply_decision_other_company_not_found(seeded, clock):
    from app.booking import RequestNotFound

    pending = await change(seeded, clock, "A-E02", "19:30", escalate=True)
    with pytest.raises(RequestNotFound):
        await apply_decision(seeded, "globex", pending.request_id, clock, kind="approve", actor_type="manager", actor_id="G-M01")


async def test_apply_decision_twice_raises(seeded, clock):
    pending = await change(seeded, clock, "A-E02", "19:30", escalate=True)
    await apply_decision(seeded, "acme", pending.request_id, clock, kind="approve", actor_type="manager", actor_id="A-M01")
    with pytest.raises(AlreadyDecided) as e:
        await apply_decision(seeded, "acme", pending.request_id, clock, kind="reject", actor_type="manager", actor_id="A-M01")
    assert e.value.status == "APPROVED_APPLIED"


async def test_restore_cancelled_trip(seeded, clock):
    await change(seeded, clock, "G-E01", change_type="cancel")
    res = await change(seeded, clock, "G-E01", change_type="restore")
    assert res.status == "APPLIED"
    assert await roster_trip(seeded, "G-E01") == "globex-r1-1120-d-1900"


async def test_restore_needs_a_cancelled_trip(seeded, clock):
    res = await change(seeded, clock, "G-E01", change_type="restore")
    assert res.status == "ALREADY_APPLIED"


async def test_restore_checks_the_seat_again(seeded, clock):
    await change(seeded, clock, "G-E01", change_type="cancel")
    async with seeded.connection() as conn:  # the freed seat and the rest get taken
        await conn.execute("UPDATE roster SET trip_id='globex-r1-1120-d-1900' WHERE employee_id IN ('G-F01','G-F02','G-F03','G-F04','G-F05')")
    res = await change(seeded, clock, "G-E01", change_type="restore")
    assert res.status == "REJECTED" and res.violation.code == "NO_SEAT"
    assert await roster_trip(seeded, "G-E01") is None
