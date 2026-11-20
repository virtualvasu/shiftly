from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app import repo
from app.agent.tools import hhmm
from app.approval.graph import resume_approval
from app.auth import TenantContext, require_manager
from app.booking import AlreadyDecided, RequestNotFound

router = APIRouter(prefix="/api/manager", tags=["manager"])


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    note: str | None = Field(None, max_length=300)


@router.get("/requests")
async def list_requests(request: Request, status: str | None = "PENDING_APPROVAL",
                        ctx: TenantContext = Depends(require_manager)):
    services = request.app.state.services
    today = services.clock.now().date()
    out = []
    async with services.pool.connection() as conn:
        for r in await repo.list_requests(conn, ctx.company_id, status or None):
            from_trip = await repo.get_trip(conn, ctx.company_id, r.from_trip_id)
            to_trip = await repo.get_trip(conn, ctx.company_id, r.to_trip_id) if r.to_trip_id else None
            out.append({
                "request_id": r.id,
                "employee_id": r.employee_id,
                "employee_name": await repo.get_employee_name(conn, ctx.company_id, r.employee_id),
                "direction": from_trip.direction,
                "date": from_trip.trip_date.isoformat(),
                "day": "today" if from_trip.trip_date == today else "tomorrow",
                "change_type": r.change_type,
                "from": hhmm(from_trip.scheduled_at),
                "to": hhmm(to_trip.scheduled_at) if to_trip else None,
                "reason": r.reason_text,
                "status": r.status,
                "created_at": r.created_at.isoformat(),
                "note": r.note,
            })
    return out


@router.post("/requests/{request_id}/decision")
async def decide(request_id: str, body: DecisionIn, request: Request,
                 ctx: TenantContext = Depends(require_manager)):
    try:
        status = await resume_approval(request.app.state.services, request_id, ctx.company_id,
                                       kind=body.decision, actor_type="manager", actor_id=ctx.employee_id,
                                       note=body.note)
    except RequestNotFound:
        raise HTTPException(status_code=404, detail="Request not found.")
    except AlreadyDecided as e:
        raise HTTPException(status_code=409, detail=f"This request was already decided ({e.status}).")
    return {"status": status}


@router.get("/audit")
async def audit(request: Request, ctx: TenantContext = Depends(require_manager)):
    async with request.app.state.services.pool.connection() as conn:
        rows = await repo.list_audit(conn, ctx.company_id)
    return [{**r, "created_at": r["created_at"].isoformat()} for r in rows]
