"""Offline planner: plain regexes instead of a model.

Used only when no GROQ_API_KEY is set, so the app still runs for anyone who
clones it. It handles the common phrasings and asks otherwise. It is not part
of the real-LLM evaluation.
"""

import json
import re

from app.agent.llm import LLMError, PlanResponse, ToolCall

_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)
_YES = re.compile(r"^\s*(yes|yeah|yep|ok|okay|sure|please|go ahead)\b", re.I)
_CANCEL = re.compile(
    r"\b(cancel|leave|wfh|work(ing)? from home|not coming|off tomorrow|no cab|not travell?ing)\b"
    r"|\bdon'?t need (a |the |my )?(cab|drop|pickup)",
    re.I,
)
_RESTORE = re.compile(r"\b(book (that|it) back|restore|undo|re-?book)\b", re.I)
_STATUS = re.compile(r"\bstatus\b", re.I)
_WITHDRAW = re.compile(r"\bwithdraw\b", re.I)
_ROSTER = re.compile(r"\b(which cab|my cab|what time is my|roster|my trips?)\b", re.I)

ASK = "Could you tell me the new time, and whether it's your pickup or drop?"


def _last_outcome(messages: list[dict]) -> dict | None:
    for m in reversed(messages):
        if m["role"] == "system" and m["content"].startswith("Last result: "):
            try:
                return json.loads(m["content"][len("Last result: "):])
            except ValueError:
                return None
    return None


def _ask(question: str = ASK) -> PlanResponse:
    return PlanResponse([ToolCall("ask_clarification", {"question": question})])


class RulePlanner:
    async def plan(self, messages: list[dict], tools: list[dict], *, timeout_s: float) -> PlanResponse:
        text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        lower = text.lower()
        day = "tomorrow" if "tomorrow" in lower else "today"
        direction = "pickup" if re.search(r"\b(pickup|pick up|pick-up|morning)\b", lower) else None

        if _YES.match(text):
            last = _last_outcome(messages)
            data = (last or {}).get("data", {})
            if last and last.get("kind") == "rejected" and data.get("escalatable"):
                args = {"date": data["day"], "direction": data["direction"], "change_type": data["change_type"]}
                if data.get("to"):
                    args["desired_time"] = data["to"]
                return PlanResponse([ToolCall("raise_shift_change_request", {**args, "escalate": True})])
            return _ask("Sorry, what would you like me to do?")
        if _RESTORE.search(text):
            last = _last_outcome(messages)
            data = (last or {}).get("data", {})
            if last and last.get("kind") == "applied" and data.get("change_type") == "cancel":
                return PlanResponse([ToolCall("raise_shift_change_request", {
                    "date": data["day"], "direction": data["direction"], "change_type": "restore"})])
            return _ask("Which trip would you like me to book again, your pickup or your drop?")
        if _WITHDRAW.search(text):
            return PlanResponse([ToolCall("withdraw_request", {})])
        if _STATUS.search(text):
            return PlanResponse([ToolCall("get_request_status", {})])
        if _CANCEL.search(text):
            dirs = [direction] if direction else (["pickup", "drop"] if day == "tomorrow" else ["drop"])
            return PlanResponse([
                ToolCall("raise_shift_change_request", {"date": day, "direction": d, "change_type": "cancel"})
                for d in dirs
            ])
        m = _TIME.search(text)
        if m:
            h, mi, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
            if ampm == "pm" and h < 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
            return PlanResponse([ToolCall("raise_shift_change_request", {
                "date": day, "direction": direction or "drop", "change_type": "reschedule",
                "desired_time": f"{h}:{mi:02d}" if not ampm else f"{h:02d}:{mi:02d}",
            })])
        if _ROSTER.search(text):
            return PlanResponse([ToolCall("get_roster", {})])
        return _ask()

    async def reply(self, messages: list[dict], *, timeout_s: float) -> str:
        raise LLMError("offline mode: replies come from templates")
