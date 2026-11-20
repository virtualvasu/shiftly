"""Expires approvals nobody decided in time, so a request never hangs forever."""

import asyncio
import logging
from datetime import timedelta

from app.approval.graph import resume_approval
from app.booking import AlreadyDecided, RequestNotFound

log = logging.getLogger("app.sweeper")

# A decision this close to the earlier of the two cabs (booked or requested) is too
# late: the employee must still be able to catch the one they're booked on.
EXPIRY_LEAD = timedelta(minutes=15)


async def expire_stale(services) -> int:
    now = services.clock.now()
    async with services.pool.connection() as conn:
        # System job: the only query that looks across companies, and it only
        # reads ids; each request is then settled within its own company.
        rows = await (await conn.execute(
            "SELECT r.id, r.company_id, LEAST(f.scheduled_at, COALESCE(t.scheduled_at, f.scheduled_at)) AS scheduled_at "
            "FROM shift_change_requests r JOIN trips f ON f.id = r.from_trip_id "
            "LEFT JOIN trips t ON t.id = r.to_trip_id WHERE r.status = 'PENDING_APPROVAL'"
        )).fetchall()
    expired = 0
    for row in rows:
        if now < row["scheduled_at"] - EXPIRY_LEAD:
            continue
        try:
            await resume_approval(services, row["id"], row["company_id"], kind="expire",
                                  actor_type="system", actor_id="sweeper")
            expired += 1
        except (AlreadyDecided, RequestNotFound):
            pass
    return expired


async def run_sweeper(services, interval_s: int) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            n = await expire_stale(services)
            if n:
                log.info("expired %d pending requests", n)
        except Exception:
            log.exception("sweeper run failed")
