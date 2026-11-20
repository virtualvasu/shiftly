import json

import httpx
import pytest

from app.agent.llm import (
    BudgetExceeded, FakeLLM, GroqClient, InvalidModelOutput, LLMBudget, LLMError,
    PlanResponse, ToolCall, build_llm,
)
from app.agent.rule_planner import RulePlanner
from app.config import Settings


def test_budget_caps_at_four():
    b = LLMBudget()
    for _ in range(4):
        b.take()
    assert b.used == 4
    with pytest.raises(BudgetExceeded):
        b.take()


async def test_fake_llm_scripted_order_and_exhaustion():
    fake = FakeLLM([PlanResponse([ToolCall("get_roster", {})]), LLMError("429")], replies=["hi"])
    assert (await fake.plan([{"role": "user", "content": "x"}], [], timeout_s=1)).tool_calls[0].name == "get_roster"
    with pytest.raises(LLMError):
        await fake.plan([], [], timeout_s=1)
    with pytest.raises(LLMError, match="exhausted"):
        await fake.plan([], [], timeout_s=1)
    assert await fake.reply([], timeout_s=1) == "hi"
    assert fake.plan_calls == 3 and fake.reply_calls == 1
    assert fake.last_messages == []


async def test_fake_llm_without_replies_raises():
    with pytest.raises(LLMError):
        await FakeLLM([]).reply([], timeout_s=1)


def _groq(handler) -> GroqClient:
    return GroqClient("key", "model-x", transport=httpx.MockTransport(handler))


async def test_groq_parses_tool_calls():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "get_roster", "arguments": '{"date": "today"}'}}
        ]}}]})

    out = await _groq(handler).plan([{"role": "user", "content": "hi"}], [{"type": "function"}], timeout_s=2)
    assert out == PlanResponse(tool_calls=[ToolCall("get_roster", {"date": "today"})], text=None)
    assert seen["body"]["model"] == "model-x" and seen["body"]["temperature"] == 0
    assert seen["body"]["tool_choice"] == "auto"
    assert seen["auth"] == "Bearer key"


async def test_groq_text_reply():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hello there"}}]})

    assert await _groq(handler).reply([{"role": "user", "content": "x"}], timeout_s=2) == "Hello there"


async def test_groq_bad_json_args_is_invalid_output():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [
            {"function": {"name": "get_roster", "arguments": "{not json"}}]}}]})

    with pytest.raises(InvalidModelOutput):
        await _groq(handler).plan([], [], timeout_s=2)


async def test_groq_tool_use_failed_is_invalid_output():
    def handler(request):
        return httpx.Response(400, json={"error": {"code": "tool_use_failed", "message": "bad call"}})

    with pytest.raises(InvalidModelOutput):
        await _groq(handler).plan([], [], timeout_s=2)


async def test_groq_429_is_llm_error():
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    with pytest.raises(LLMError) as e:
        await _groq(handler).plan([], [], timeout_s=2)
    assert not isinstance(e.value, InvalidModelOutput)


async def test_groq_timeout_is_llm_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError):
        await _groq(handler).plan([], [], timeout_s=0.1)


def test_build_llm_selection():
    assert isinstance(build_llm(Settings(llm_provider="auto", groq_api_key="")), RulePlanner)
    assert isinstance(build_llm(Settings(llm_provider="auto", groq_api_key="k")), GroqClient)
    fake = FakeLLM([])
    assert build_llm(Settings(llm_provider="fake"), fake) is fake


async def _plan(text: str, last: dict | None = None) -> list[ToolCall]:
    msgs = [{"role": "system", "content": "rules"}]
    if last is not None:
        msgs.append({"role": "system", "content": "Last result: " + json.dumps(last)})
    msgs.append({"role": "user", "content": text})
    return (await RulePlanner().plan(msgs, [], timeout_s=1)).tool_calls


async def test_rule_planner_reschedule_pm():
    assert await _plan("move my drop to 8:30 pm") == [ToolCall("raise_shift_change_request", {
        "date": "today", "direction": "drop", "change_type": "reschedule", "desired_time": "20:30"})]


async def test_rule_planner_bare_hour_and_pickup_tomorrow():
    calls = await _plan("change my pickup tomorrow to 8")
    assert calls == [ToolCall("raise_shift_change_request", {
        "date": "tomorrow", "direction": "pickup", "change_type": "reschedule", "desired_time": "8:00"})]


async def test_rule_planner_leave_tomorrow_cancels_both():
    calls = await _plan("I'm on leave tomorrow")
    assert [(c.name, c.args["direction"], c.args["change_type"], c.args["date"]) for c in calls] == [
        ("raise_shift_change_request", "pickup", "cancel", "tomorrow"),
        ("raise_shift_change_request", "drop", "cancel", "tomorrow"),
    ]


async def test_rule_planner_vague_asks():
    calls = await _plan("can I go a bit late?")
    assert [c.name for c in calls] == ["ask_clarification"]


async def test_rule_planner_yes_escalates_last_cutoff():
    last = {"kind": "rejected", "data": {"day": "today", "direction": "drop", "to": "19:30",
                                         "reason_code": "CUTOFF_PASSED", "escalatable": True,
                                         "change_type": "reschedule"}}
    assert await _plan("yes please", last) == [ToolCall("raise_shift_change_request", {
        "date": "today", "direction": "drop", "change_type": "reschedule", "desired_time": "19:30", "escalate": True})]


async def test_rule_planner_yes_without_context_asks():
    assert [c.name for c in await _plan("yes")] == ["ask_clarification"]


async def test_rule_planner_status_and_roster():
    assert [c.name for c in await _plan("what's the status of my request?")] == ["get_request_status"]
    assert [c.name for c in await _plan("which cab am I on tonight?")] == ["get_roster"]
    assert [c.name for c in await _plan("withdraw my request")] == ["withdraw_request"]


async def test_rule_planner_reply_unavailable():
    with pytest.raises(LLMError):
        await RulePlanner().reply([], timeout_s=1)
