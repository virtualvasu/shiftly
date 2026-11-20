import time

import pytest
from pydantic import ValidationError

from app.agent import tools
from app.agent.tools import (
    CheckCabOptionsArgs, GetRosterArgs, RaiseRequestArgs, ToolDeps, run_tool, tool_schemas,
)
from app.config import Settings
from app.faults import Fault, FaultInjector
from app.services import DirectApprovals, Services
from tests.conftest import at, ctx_for


@pytest.fixture
def deps_for(seeded, clock):
    def make(emp: str) -> ToolDeps:
        services = Services(settings=Settings(), pool=seeded, clock=clock, faults=FaultInjector())
        services.approvals = DirectApprovals(services)
        return ToolDeps(pool=seeded, ctx=ctx_for(emp), clock=clock, faults=services.faults,
                        conversation_id=None, services=services)
    return make


def test_args_reject_identity_fields():
    with pytest.raises(ValidationError):
        RaiseRequestArgs.model_validate(
            {"date": "today", "direction": "drop", "change_type": "reschedule",
             "desired_time": "19:30", "employee_id": "G-E01"}
        )


def test_args_reject_bad_time_format():
    with pytest.raises(ValidationError):
        CheckCabOptionsArgs.model_validate({"date": "today", "direction": "drop", "desired_time": "8pm"})


def test_tool_schemas_have_no_identity():
    schemas = tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"get_roster", "check_cab_options", "raise_shift_change_request",
                     "get_request_status", "withdraw_request", "ask_clarification"}
    for s in schemas:
        props = s["function"]["parameters"].get("properties", {})
        assert not {"company_id", "employee_id", "role"} & set(props)


async def test_get_roster_own_only(deps_for):
    out = await run_tool("get_roster", GetRosterArgs(date="today"), deps_for("A-E01"))
    assert out.ok and out.kind == "roster_info"
    assert out.data["trips"] == [{"date": "2026-11-20", "day": "today", "direction": "drop", "time": "21:00",
                                  "vehicle_no": out.data["trips"][0]["vehicle_no"]}]


async def test_check_cab_options_scoped(deps_for):
    out = await run_tool(
        "check_cab_options", CheckCabOptionsArgs(date="today", direction="drop", desired_time="20:00"),
        deps_for("G-E01"), desired_at=at("20:00"),
    )
    assert out.ok and out.kind == "options_info"
    assert out.data["requested"]["available"] is False and out.data["requested"]["reason"] == "NO_SEAT"
    assert out.data["options"] == ["19:30", "20:30"]
    assert all(t.startswith("globex-r1") for t in out.data["trip_ids"])


async def test_raise_applied_outcome(deps_for):
    out = await run_tool(
        "raise_shift_change_request",
        RaiseRequestArgs(date="today", direction="drop", change_type="reschedule", desired_time="19:30"),
        deps_for("G-E01"), desired_at=at("19:30"),
    )
    assert out.kind == "applied"
    assert out.data["from"] == "19:00" and out.data["to"] == "19:30"
    assert out.data["request_id"].startswith("SCR-")


async def test_raise_rejected_outcome(deps_for):
    out = await run_tool(
        "raise_shift_change_request",
        RaiseRequestArgs(date="today", direction="drop", change_type="reschedule", desired_time="19:30"),
        deps_for("A-E02"), desired_at=at("19:30"),
    )
    assert out.kind == "rejected"
    assert out.data["reason_code"] == "CUTOFF_PASSED" and out.data["escalatable"] is True


async def test_run_tool_timeout_returns_failure(deps_for):
    deps = deps_for("G-E01")
    deps.faults.set("cabs", Fault(latency_s=3))
    t0 = time.monotonic()
    out = await run_tool(
        "check_cab_options", CheckCabOptionsArgs(date="today", direction="drop", desired_time="20:00"),
        deps, desired_at=at("20:00"),
    )
    elapsed = time.monotonic() - t0
    assert (out.ok, out.kind, out.system) == (False, "tool_failure", "cabs")
    assert out.data["failure"] == "timeout"
    assert out.data["current"] == "19:00"
    assert elapsed < 3.5


async def test_read_tool_retried_once(deps_for, monkeypatch):
    calls = {"n": 0}
    real = tools.repo.list_roster

    async def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connection reset")
        return await real(*a, **kw)

    monkeypatch.setattr(tools.repo, "list_roster", flaky)
    out = await run_tool("get_roster", GetRosterArgs(), deps_for("A-E01"))
    assert out.ok and calls["n"] == 2


async def test_booking_not_retried(deps_for, monkeypatch):
    calls = {"n": 0}

    async def broken(*a, **kw):
        calls["n"] += 1
        raise RuntimeError("db down")

    monkeypatch.setattr(tools, "change_trip", broken)
    out = await run_tool(
        "raise_shift_change_request",
        RaiseRequestArgs(date="today", direction="drop", change_type="reschedule", desired_time="21:30"),
        deps_for("A-E01"), desired_at=at("21:30"),
    )
    assert (out.kind, out.system, calls["n"]) == ("tool_failure", "booking", 1)


async def test_status_and_withdraw(deps_for):
    deps = deps_for("A-E02")
    raised = await run_tool(
        "raise_shift_change_request",
        RaiseRequestArgs(date="today", direction="drop", change_type="reschedule", desired_time="19:30", escalate=True),
        deps, desired_at=at("19:30"),
    )
    assert raised.kind == "pending"
    status = await run_tool("get_request_status", tools.RequestStatusArgs(), deps)
    assert status.kind == "status_info" and status.data["status"] == "PENDING_APPROVAL"
    assert status.data["created"] == "17:30"
    out = await run_tool("withdraw_request", tools.WithdrawArgs(), deps)
    assert out.kind == "withdrawn" and out.data["request_id"] == raised.data["request_id"]


async def test_status_cannot_read_other_employee_request(deps_for):
    a = await run_tool(
        "raise_shift_change_request",
        RaiseRequestArgs(date="today", direction="drop", change_type="reschedule", desired_time="19:30", escalate=True),
        deps_for("A-E02"), desired_at=at("19:30"),
    )
    out = await run_tool("get_request_status", tools.RequestStatusArgs(request_id=a.data["request_id"]), deps_for("G-E01"))
    assert out.kind == "status_info" and out.data == {"none": True}


async def test_dev_fault_switches(app_kwargs):
    from app.testing import running_app

    async with running_app(**app_kwargs) as (_, c):
        r = await c.post("/api/dev/faults", json={"system": "cabs", "fail": True})
        assert r.json() == {"cabs": {"latency_s": 0, "fail": True}}
        assert (await c.delete("/api/dev/faults")).json() == {}
        assert (await c.post("/api/dev/faults", json={"system": "payroll"})).status_code == 422
