"""Demo-only switches to make a backing system slow or broken."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.faults import Fault, System

router = APIRouter(prefix="/api/dev", tags=["dev"])


class FaultIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system: System
    latency_s: float = Field(0, ge=0, le=10)
    fail: bool = False


@router.get("/faults")
async def get_faults(request: Request):
    return request.app.state.services.faults.snapshot()


@router.post("/faults")
async def set_fault(body: FaultIn, request: Request):
    faults = request.app.state.services.faults
    faults.set(body.system, Fault(latency_s=body.latency_s, fail=body.fail))
    return faults.snapshot()


@router.delete("/faults")
async def clear_faults(request: Request):
    faults = request.app.state.services.faults
    faults.clear()
    return faults.snapshot()
