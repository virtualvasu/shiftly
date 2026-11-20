"""Manager approval as a durable LangGraph workflow.

Each escalated request gets its own thread, "approval:<request id>":

    record -> await_decision (interrupt, checkpointed in Postgres) -> apply -> notify

It is resumed with Command(resume=...) by the manager, the employee
(withdraw) or the expiry sweeper. Because the checkpoint and the request row
both live in Postgres, a restart between pause and decision changes nothing.

It runs on its own thread, not the chat thread, so the employee can keep
chatting (ask for status, change another trip) while the manager decides.
"""

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from app import repo
from app.agent import templates
from app.agent.tools import hhmm
from app.booking import AlreadyDecided, RequestNotFound, apply_decision

log = logging.getLogger("app.approval")


class ApprovalState(TypedDict, total=False):
    request_id: str
    company_id: str
    decision: dict | None
    result: dict | None


@dataclass
class ApprovalDeps:
    services: Any


async def record_node(state: ApprovalState, runtime: Runtime[ApprovalDeps]) -> dict:
    s = runtime.context.services
    async with s.pool.connection() as conn:
        await repo.insert_audit(conn, state["company_id"], "system", "approval", "awaiting_manager", state["request_id"], {})
    return {}


async def await_decision_node(state: ApprovalState) -> dict:
    decision = interrupt({"request_id": state["request_id"]})
    return {"decision": decision}


async def apply_node(state: ApprovalState, runtime: Runtime[ApprovalDeps]) -> dict:
    s = runtime.context.services
    d = state["decision"]
    try:
        res = await apply_decision(
            s.pool, state["company_id"], state["request_id"], s.clock,
            kind=d["kind"], actor_type=d["actor_type"], actor_id=d["actor_id"], note=d.get("note"),
        )
    except AlreadyDecided as e:
        return {"result": {"status": e.status, "already_decided": True}}
    return {"result": {"status": res.status, "alternatives": [hhmm(a.trip.scheduled_at) for a in res.alternatives]}}


async def notify_node(state: ApprovalState, runtime: Runtime[ApprovalDeps]) -> dict:
    result = state["result"]
    if not result.get("already_decided") and state["decision"].get("notify", True):
        await notify_employee(runtime.context.services, state["company_id"], state["request_id"],
                              result["status"], result.get("alternatives", []))
    return {}


def build_approval_graph(checkpointer):
    g = StateGraph(ApprovalState, context_schema=ApprovalDeps)
    g.add_node("record", record_node)
    g.add_node("await_decision", await_decision_node)
    g.add_node("apply", apply_node)
    g.add_node("notify", notify_node)
    g.add_edge(START, "record")
    g.add_edge("record", "await_decision")
    g.add_edge("await_decision", "apply")
    g.add_edge("apply", "notify")
    g.add_edge("notify", END)
    return g.compile(checkpointer=checkpointer)


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def notify_employee(services, company_id: str, request_id: str, status: str, alternatives: list[str]) -> None:
    """Post the decision into the employee's conversation: the message log and the chat graph's memory."""
    async with services.pool.connection() as conn:
        req = await repo.get_request(conn, company_id, request_id)
        if req is None or req.conversation_id is None:
            return
        from_trip = await repo.get_trip(conn, company_id, req.from_trip_id)
        to_trip = await repo.get_trip(conn, company_id, req.to_trip_id) if req.to_trip_id else None
        today = services.clock.now().date()
        data = {
            "request_id": req.id,
            "direction": from_trip.direction,
            "day": "today" if from_trip.trip_date == today else "tomorrow",
            "change_type": req.change_type,
            "from": hhmm(from_trip.scheduled_at),
            "to": hhmm(to_trip.scheduled_at) if to_trip else None,
            "vehicle_no": (to_trip or from_trip).vehicle_no,
            "alternatives": alternatives,
            "note": req.note,
        }
        text = templates.decision_message(status, data)
        await repo.append_message(conn, company_id, req.conversation_id, "assistant", text, services.clock.now())

    # Add it to the chat graph's memory too, so "ok, then 9?" makes sense next turn.
    # Take the conversation's turn lock so we don't race a turn in progress.
    async with services.pool.connection() as lock_conn:
        await lock_conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (req.conversation_id,))
        try:
            await services.chat_graph.aupdate_state(
                _config(req.conversation_id),
                {"messages": [{"role": "assistant", "content": text}],
                 "last_outcome": {"kind": "decision", "data": {**data, "status": status}}},
                as_node="respond",
            )
        except Exception:
            log.exception("could not add decision to chat memory for %s", req.conversation_id)
        finally:
            await lock_conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (req.conversation_id,))


async def start_approval(services, request_id: str, company_id: str) -> None:
    async with services.pool.connection() as conn:
        req = await repo.get_request(conn, company_id, request_id)
    await services.approval_graph.ainvoke(
        {"request_id": request_id, "company_id": company_id, "decision": None, "result": None},
        _config(req.approval_thread_id),
        context=ApprovalDeps(services),
    )


async def resume_approval(
    services, request_id: str, company_id: str, *, kind: str, actor_type: str, actor_id: str,
    note: str | None = None, notify: bool = True,
) -> str:
    """Deliver a decision to the paused workflow. Returns the request's new status."""
    async with services.pool.connection() as conn:
        req = await repo.get_request(conn, company_id, request_id)
    if req is None:
        raise RequestNotFound(request_id)
    if req.status != "PENDING_APPROVAL":
        raise AlreadyDecided(req.status)

    decision = {"kind": kind, "actor_type": actor_type, "actor_id": actor_id, "note": note, "notify": notify}
    config = _config(req.approval_thread_id)
    snapshot = await services.approval_graph.aget_state(config)
    waiting = any(t.interrupts for t in snapshot.tasks)

    if waiting:
        state = await services.approval_graph.ainvoke(Command(resume=decision), config, context=ApprovalDeps(services))
        result = state["result"]
        if result.get("already_decided"):
            raise AlreadyDecided(result["status"])
        return result["status"]

    # No paused workflow (it never started, e.g. a crash right after the request
    # was saved). The request row is the source of truth, so settle it directly.
    log.warning("no approval thread waiting for %s; applying directly", request_id)
    res = await apply_decision(services.pool, company_id, request_id, services.clock,
                               kind=kind, actor_type=actor_type, actor_id=actor_id, note=note)
    if notify:
        await notify_employee(services, company_id, request_id, res.status,
                              [hhmm(a.trip.scheduled_at) for a in res.alternatives])
    return res.status


class GraphApprovals:
    """Approval hooks used by the tools: start the workflow, or withdraw from it."""

    def __init__(self, services):
        self.services = services

    async def on_pending(self, request_id: str, company_id: str) -> None:
        await start_approval(self.services, request_id, company_id)

    async def withdraw(self, ctx, request_id: str) -> str:
        # The employee is mid-conversation and gets the reply directly, so no notify.
        return await resume_approval(self.services, request_id, ctx.company_id, kind="withdraw",
                                     actor_type="employee", actor_id=ctx.employee_id, notify=False)
