"""LLM access behind one small interface.

GroqClient talks to the real model. FakeLLM replays a script for tests.
RulePlanner (rule_planner.py) is the offline stand-in when no key is set.
LLMBudget is the hard cap on calls per employee message.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Protocol

import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_CALLS_PER_MESSAGE = 4


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class PlanResponse:
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str | None = None


class LLMError(Exception):
    """The model could not be used: down, rate-limited, timed out."""


class InvalidModelOutput(LLMError):
    """The model answered, but with something we cannot use (bad tool JSON)."""


class BudgetExceeded(LLMError):
    pass


class LLMBudget:
    def __init__(self, max_calls: int = MAX_CALLS_PER_MESSAGE):
        self.max_calls = max_calls
        self.used = 0

    def take(self) -> None:
        if self.used >= self.max_calls:
            raise BudgetExceeded(f"more than {self.max_calls} LLM calls for one message")
        self.used += 1


class LLMClient(Protocol):
    async def plan(self, messages: list[dict], tools: list[dict], *, timeout_s: float) -> PlanResponse: ...

    async def reply(self, messages: list[dict], *, timeout_s: float) -> str: ...


class GroqClient:
    def __init__(self, api_key: str, model: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.model = model
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"}, transport=transport
        )

    async def _complete(self, body: dict, timeout_s: float) -> dict:
        try:
            r = await self._client.post(GROQ_URL, json={"model": self.model, "temperature": 0, **body}, timeout=timeout_s)
        except (httpx.HTTPError, asyncio.TimeoutError) as e:
            raise LLMError(f"request failed: {type(e).__name__}") from e
        if r.status_code == 400 and "tool_use_failed" in r.text:
            raise InvalidModelOutput("model produced a malformed tool call")
        if r.status_code != 200:
            raise LLMError(f"HTTP {r.status_code}")
        try:
            return r.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError) as e:
            raise LLMError("unexpected response shape") from e

    async def plan(self, messages: list[dict], tools: list[dict], *, timeout_s: float) -> PlanResponse:
        msg = await self._complete({"messages": messages, "tools": tools, "tool_choice": "auto"}, timeout_s)
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError as e:
                raise InvalidModelOutput("tool arguments were not JSON") from e
            if not isinstance(args, dict):
                raise InvalidModelOutput("tool arguments were not an object")
            calls.append(ToolCall(str(fn.get("name", "")), args))
        return PlanResponse(tool_calls=calls, text=msg.get("content"))

    async def reply(self, messages: list[dict], *, timeout_s: float) -> str:
        msg = await self._complete({"messages": messages, "max_tokens": 200}, timeout_s)
        return (msg.get("content") or "").strip()


class FakeLLM:
    """Scripted model for tests: returns (or raises) the next item each call."""

    def __init__(self, plans: list, replies: list | None = None, *, delay_s: float = 0):
        self.plans = list(plans)
        self.replies = None if replies is None else list(replies)
        self.delay_s = delay_s
        self.plan_calls = 0
        self.reply_calls = 0
        self.last_messages: list[dict] = []
        self.all_plan_messages: list[list[dict]] = []

    async def plan(self, messages, tools, *, timeout_s):
        self.plan_calls += 1
        self.last_messages = messages
        self.all_plan_messages.append(messages)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if not self.plans:
            raise LLMError("fake exhausted")
        item = self.plans.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def reply(self, messages, *, timeout_s):
        self.reply_calls += 1
        if not self.replies:
            raise LLMError("no scripted reply")
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def build_llm(settings, injected: LLMClient | None = None) -> LLMClient:
    from app.agent.rule_planner import RulePlanner

    provider = settings.llm_provider
    if provider == "fake":
        # Tests inject a scripted model; without one, every call fails cleanly.
        return injected if injected is not None else FakeLLM([])
    if injected is not None:
        return injected
    if provider == "offline" or (provider == "auto" and not settings.groq_api_key):
        return RulePlanner()
    return GroqClient(settings.groq_api_key, settings.groq_model)
