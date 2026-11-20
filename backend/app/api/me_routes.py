from fastapi import APIRouter, Depends, Request

from app.auth import TenantContext, current_ctx

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("")
async def me(request: Request, ctx: TenantContext = Depends(current_ctx)):
    async with request.app.state.services.pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT e.id, e.name, e.role, c.id AS company_id, c.name AS company_name "
                "FROM employees e JOIN companies c ON c.id = e.company_id "
                "WHERE e.id = %s AND e.company_id = %s",
                (ctx.employee_id, ctx.company_id),
            )
        ).fetchone()
    return {
        "employee_id": row["id"],
        "name": row["name"],
        "role": row["role"],
        "company_id": row["company_id"],
        "company_name": row["company_name"],
    }


@router.get("/roster")
async def my_roster(request: Request, ctx: TenantContext = Depends(current_ctx)):
    from datetime import timedelta

    from app import repo
    from app.clock import IST

    services = request.app.state.services
    today = services.clock.now().date()
    async with services.pool.connection() as conn:
        entries = await repo.list_roster(conn, ctx, [today, today + timedelta(days=1)])
        names = {}
        for e in entries:
            if e.trip.route_id not in names:
                names[e.trip.route_id] = await repo.route_name(conn, ctx.company_id, e.trip.route_id)
        pending = await repo.latest_request(conn, ctx, status="PENDING_APPROVAL")
    return [
        {
            "trip_date": e.trip_date.isoformat(),
            "day": "today" if e.trip_date == today else "tomorrow",
            "direction": e.direction,
            "time": e.trip.scheduled_at.astimezone(IST).strftime("%H:%M"),
            "vehicle_no": e.trip.vehicle_no,
            "route": names[e.trip.route_id],
            "pending_request": pending.id if pending and pending.roster_id == e.id else None,
        }
        for e in entries
    ]
