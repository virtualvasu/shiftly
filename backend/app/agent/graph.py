"""The chat agent: plan (LLM) -> validate (code) -> execute (code) -> respond (LLM, guarded).

LLM calls per message: plan 1, one plan retry if the output was invalid, and
one optional rephrase of the reply. That is 3 at most; LLMBudget enforces 4.
"""

import asyncio
import logging
import operator
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import ValidationError

from app import repo
from app.agent import templates
from app.agent.grounding import is_day_off, mentioned_direction, mentions_tomorrow
from app.agent.guard import reply_is_safe
from app.agent.llm import InvalidModelOutput, LLMBudget, LLMClient, LLMError, ToolCall
from app.agent.prompt import build_planner_messages, build_reply_messages
from app.agent.rule_planner import RulePlanner
from app.agent.timeparse import passed_time, resolve_time
from app.api.errors import SERVER_ERROR
from app.agent.tools import TOOL_SPECS, ToolDeps, ToolOutcome, day_of, run_tool, tool_schemas
from app.auth import TenantContext

log = logging.getLogger("app.agent")

MAX_TOOL_CALLS = 3
MAX_BOOKING_CALLS = 2
PLANNER_TIMEOUT_S = 2.5
REPLY_TIMEOUT_S = 1.5
HARD_TURN_TIMEOUT_S = 10
MAX_MESSAGE_CHARS = 1000
STOP_KINDS = {"rejected", "tool_failure", "no_booking", "clarify"}
# The LLM may only rephrase turns where nothing was refused or left pending; a refusal
# or a mixed turn always uses the template, so it can't be reworded into a confirmation.
REPHRASE_KINDS = {"applied", "already_applied", "withdrawn", "status_info", "roster_info"}
TAKING_TOO_LONG = (
    "This is taking longer than it should. Please check your trips in a minute before trying again, "
    "or call the transport desk if it's urgent."
)


class ChatState(TypedDict, total=False):
    messages: Annotated[list[dict], operator.add]  # conversation context, kept across turns
    last_outcome: dict | None                       # hint for follow-ups like "yes"
    # per-turn fields, reset by every new message:
    user_text: str
    calls: list[dict]
    clarify: str | None
    outcomes: list[dict]
    reply: str
    llm_calls: int
    tools_used: list[str]
    fallback: bool  # the model was unavailable; the rule planner planned this turn


@dataclass
class TurnDeps:
    services: Any
    ctx: TenantContext
    llm: LLMClient
    budget: LLMBudget = field(default_factory=LLMBudget)
    started: float = field(default_factory=time.monotonic)
    conversation_id: str | None = None

    def remaining(self) -> float:
        return self.services.settings.turn_budget_s - (time.monotonic() - self.started)


class PlanError(Exception):
    pass


def validate_plan(tool_calls: list[ToolCall]) -> list[dict]:
    """Check the model's tool calls against our schemas. Raises PlanError."""
    if len(tool_calls) > MAX_TOOL_CALLS:
        raise PlanError(f"at most {MAX_TOOL_CALLS} tool calls")
    if sum(c.name == "raise_shift_change_request" for c in tool_calls) > MAX_BOOKING_CALLS:
        raise PlanError(f"at most {MAX_BOOKING_CALLS} raise_shift_change_request calls")
    out = []
    for c in tool_calls:
        spec = TOOL_SPECS.get(c.name)
        if spec is None:
            raise PlanError(f"unknown tool {c.name!r}")
        try:
            args = spec[0].model_validate(c.args)
        except ValidationError as e:
            fields = ", ".join(".".join(map(str, err["loc"])) or "args" for err in e.errors())
            raise PlanError(f"bad arguments for {c.name}: {fields}") from e
        out.append({"name": c.name, "args": args.model_dump(), "desired_at": None})
    return out


# --- nodes ------------------------------------------------------------------

async def plan_node(state: ChatState, runtime: Runtime[TurnDeps]) -> dict:
    deps = runtime.context
    s = deps.services
    now = s.clock.now()
    async with s.pool.connection() as conn:
        company = await repo.get_company(conn, deps.ctx.company_id)
        employee = await repo.get_employee(conn, deps.ctx)
        roster = await repo.list_roster(conn, deps.ctx, [now.date(), now.date() + timedelta(days=1)])
    messages = build_planner_messages(
        company, employee, roster, state.get("messages", []), state.get("last_outcome"), state["user_text"], now
    )

    for attempt in (1, 2):
        try:
            deps.budget.take()
            resp = await deps.llm.plan(messages, tool_schemas(), timeout_s=max(0.5, min(PLANNER_TIMEOUT_S, deps.remaining())))
            if not resp.tool_calls:
                text = (resp.text or "").strip()
                return {"reply": text if reply_is_safe(text, []) else templates.not_understood()}
            return {"calls": validate_plan(resp.tool_calls)}
        except (InvalidModelOutput, PlanError) as e:
            log.info("invalid plan (attempt %d): %s", attempt, e)
            messages = messages + [{
                "role": "system",
                "content": f"Your previous output was invalid: {e}. Call the tools again with valid arguments.",
            }]
        except LLMError as e:
            # Down or rate-limited: plan with the built-in rule planner instead. Its plan
            # goes through exactly the same validation and rules.
            log.warning("planner unavailable (%s); using the rule planner", e)
            try:
                resp = await RulePlanner().plan(messages, [], timeout_s=0)
                return {"calls": validate_plan(resp.tool_calls), "fallback": True}
            except (LLMError, PlanError):
                return {"reply": templates.llm_failure()}
    return {"reply": templates.not_understood()}


async def validate_node(state: ChatState, runtime: Runtime[TurnDeps]) -> dict:
    """Code-side checks the model cannot skip: ask instead of guessing a time."""
    deps = runtime.context
    s = deps.services
    now = s.clock.now()
    # What the employee wrote this turn, then their earlier messages (most recent first).
    said = [state["user_text"]] + [m["content"] for m in reversed(state.get("messages", [])) if m["role"] == "user"][:2]
    calls = []
    for c in state["calls"]:
        if c["name"] == "ask_clarification":
            return {"clarify": c["args"]["question"], "tools_used": ["ask_clarification"]}
        args = c["args"]
        if "direction" in args:
            args = _grounded(args, said)
            c = {**c, "args": args}
        if "direction" in args and args.get("date") is None:
            args = {**args, "date": await _next_trip_day(s, deps.ctx, args["direction"], now)}
            c = {**c, "args": args}
        needs_time = c["name"] == "check_cab_options" or (
            c["name"] == "raise_shift_change_request" and args["change_type"] == "reschedule"
        )
        if needs_time:
            if not args.get("desired_time"):
                return {"clarify": templates.clarify_missing("time", args["direction"])}
            trip_date = day_of(args["date"], now.date())
            async with s.pool.connection() as conn:
                entry = await repo.get_roster_entry(conn, deps.ctx, trip_date, args["direction"])
            at = resolve_time(args["desired_time"], trip_date, entry.trip.scheduled_at if entry else None, now)
            if at is None:
                gone = passed_time(args["desired_time"], trip_date, now)
                if gone:
                    return {"clarify": templates.clarify_time_passed(gone, args["direction"])}
                return {"clarify": templates.clarify_missing("time", args["direction"])}
            c = {**c, "desired_at": at.isoformat()}
        if c["name"] == "raise_shift_change_request" and args.get("escalate"):
            if not _escalation_was_offered(state.get("last_outcome"), args, c["desired_at"]):
                c = {**c, "args": {**args, "escalate": False}}
        calls.append(c)
    calls += await _other_trip_of_day_off(s, deps.ctx, calls, said, now)
    return {"calls": calls}


async def _other_trip_of_day_off(s, ctx, calls: list[dict], said: list[str], now) -> list[dict]:
    """'On leave tomorrow' means both trips. If the model cancelled only one, add the other
    (when the employee didn't name one trip, and that trip exists and hasn't left)."""
    if not is_day_off(said[0]) or mentioned_direction(said[:1]):
        return []
    cancels = {(c["args"]["date"], c["args"]["direction"]) for c in calls
               if c["name"] == "raise_shift_change_request" and c["args"]["change_type"] == "cancel"}
    extra = []
    for day in {d for d, _ in cancels}:
        trip_date = day_of(day, now.date())
        for direction in ("pickup", "drop"):
            if (day, direction) in cancels:
                continue
            async with s.pool.connection() as conn:
                entry = await repo.get_roster_entry(conn, ctx, trip_date, direction)
            if entry and entry.trip.scheduled_at > now:
                extra.append({"name": "raise_shift_change_request", "desired_at": None,
                              "args": {"date": day, "direction": direction, "change_type": "cancel",
                                       "desired_time": None, "escalate": False}})
    return extra


def _grounded(args: dict, said: list[str]) -> dict:
    """Drop or correct a day/trip the model chose that the employee didn't say."""
    if args.get("date") == "tomorrow" and not mentions_tomorrow(said):
        args = {**args, "date": None}
    named = mentioned_direction(said)
    if named and args.get("direction") != named:
        args = {**args, "direction": named}
    return args


async def _next_trip_day(s, ctx, direction: str, now) -> str:
    """The day the employee means when they don't say: the next such trip that hasn't left."""
    async with s.pool.connection() as conn:
        today = await repo.get_roster_entry(conn, ctx, now.date(), direction)
    return "today" if today and today.trip.scheduled_at > now else "tomorrow"


def _escalation_was_offered(last: dict | None, args: dict, desired_at: str | None) -> bool:
    """Escalate only the exact change we offered to escalate in the previous turn.

    The model may set escalate=true too early; the employee must have been told
    the rule and asked first. Otherwise the request is checked normally, which
    produces that explanation and offer.
    """
    if not last or last.get("kind") != "rejected" or not last["data"].get("escalatable"):
        return False
    d = last["data"]
    same_trip = (d.get("day"), d.get("direction"), d.get("change_type")) == (
        args["date"], args["direction"], args["change_type"])
    if args["change_type"] in ("cancel", "restore"):
        return same_trip
    wanted = datetime.fromisoformat(desired_at).strftime("%H:%M") if desired_at else None
    return same_trip and d.get("to") == wanted


async def execute_node(state: ChatState, runtime: Runtime[TurnDeps]) -> dict:
    deps = runtime.context
    s = deps.services
    tool_deps = ToolDeps(s.pool, deps.ctx, s.clock, s.faults, deps.conversation_id, s)
    outcomes, used = [], []
    for c in state["calls"]:
        args = TOOL_SPECS[c["name"]][0].model_validate(c["args"])
        desired_at = datetime.fromisoformat(c["desired_at"]) if c["desired_at"] else None
        outcome = await run_tool(c["name"], args, tool_deps, desired_at=desired_at)
        outcomes.append(outcome.to_dict())
        used.append(c["name"])
        if outcome.kind in STOP_KINDS:
            break
    return {"outcomes": outcomes, "tools_used": used}


async def respond_node(state: ChatState, runtime: Runtime[TurnDeps]) -> dict:
    deps = runtime.context
    s = deps.services
    outcomes = [ToolOutcome(**o) for o in state.get("outcomes", [])]

    if state.get("reply"):
        final = state["reply"]
    elif state.get("clarify"):
        final = state["clarify"]
    else:
        async with s.pool.connection() as conn:
            company = await repo.get_company(conn, deps.ctx.company_id)
        draft = " ".join(templates.render(o, company) for o in outcomes) or templates.not_understood()
        final = draft
        rephrase = outcomes and all(o.kind in REPHRASE_KINDS for o in outcomes)
        if rephrase and not state.get("fallback") and deps.remaining() > REPLY_TIMEOUT_S:
            try:
                deps.budget.take()
                text = (await deps.llm.reply(build_reply_messages(draft), timeout_s=REPLY_TIMEOUT_S)).strip()
                if reply_is_safe(text, outcomes):
                    final = text
            except LLMError as e:
                log.info("rephrase skipped: %s", e)
        # The reference number always comes from code.
        for o in outcomes:
            rid = o.data.get("request_id")
            if o.kind in ("applied", "pending") and rid and rid not in final:
                final += f"\n\nRef: {rid}"

    update: dict = {
        "reply": final,
        "llm_calls": deps.budget.used,
        "messages": [
            {"role": "user", "content": state["user_text"]},
            {"role": "assistant", "content": final},
        ],
    }
    if outcomes:
        last = outcomes[-1]
        update["last_outcome"] = {"kind": last.kind, "data": last.data}
    return update


def build_chat_graph(checkpointer):
    g = StateGraph(ChatState, context_schema=TurnDeps)
    g.add_node("plan", plan_node)
    g.add_node("validate", validate_node)
    g.add_node("execute", execute_node)
    g.add_node("respond", respond_node)
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", lambda s: "respond" if s.get("reply") else "validate", ["respond", "validate"])
    g.add_conditional_edges("validate", lambda s: "respond" if s.get("clarify") else "execute", ["respond", "execute"])
    g.add_edge("execute", "respond")
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer)


# --- one employee message ----------------------------------------------------

class ConversationBusy(Exception):
    pass


@dataclass
class ChatResult:
    conversation_id: str
    reply: str
    outcomes: list[dict]
    llm_calls: int
    tools: list[str]


def new_conversation_id() -> str:
    return uuid.uuid4().hex


async def handle_message(services, ctx: TenantContext, conversation_id: str, text: str) -> ChatResult:
    """Run one turn. One turn at a time per conversation (Postgres advisory lock)."""
    text = text[:MAX_MESSAGE_CHARS]
    async with services.pool.connection() as lock_conn:
        row = await (await lock_conn.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS ok", (conversation_id,))).fetchone()
        if not row["ok"]:
            raise ConversationBusy()
        try:
            async with services.pool.connection() as conn:
                await repo.append_message(conn, ctx.company_id, conversation_id, "user", text, services.clock.now())

            deps = TurnDeps(services=services, ctx=ctx, llm=services.llm, conversation_id=conversation_id)
            turn_input = {"user_text": text, "calls": [], "clarify": None, "outcomes": [], "reply": "",
                          "llm_calls": 0, "tools_used": [], "fallback": False}
            config = {"configurable": {"thread_id": conversation_id}}
            try:
                state = await asyncio.wait_for(
                    services.chat_graph.ainvoke(turn_input, config, context=deps), HARD_TURN_TIMEOUT_S
                )
                result = ChatResult(conversation_id, state["reply"], state.get("outcomes", []),
                                    state.get("llm_calls", deps.budget.used), state.get("tools_used", []))
            except asyncio.TimeoutError:
                log.error("turn timed out conversation=%s", conversation_id)
                result = ChatResult(conversation_id, TAKING_TOO_LONG, [], deps.budget.used, [])
            except Exception:
                log.exception("turn failed conversation=%s", conversation_id)
                result = ChatResult(conversation_id, SERVER_ERROR, [], deps.budget.used, [])

            async with services.pool.connection() as conn:
                await repo.append_message(conn, ctx.company_id, conversation_id, "assistant", result.reply, services.clock.now())
            return result
        finally:
            await lock_conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (conversation_id,))
