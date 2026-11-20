"""Checks an LLM-written reply against what actually happened.

If the model claims success that code did not produce, invents a time or a
request id, or leaks anything that looks like internals, we use the template.
"""

import re

from app.agent.tools import ToolOutcome

MAX_LEN = 600
SUCCESS_KINDS = {"applied", "already_applied", "withdrawn"}

# "booked" is only a success claim when it isn't "still booked" / "stays booked".
_SUCCESS = re.compile(
    r"\b(confirm(ed)?|done|approved|changed|moved|updated|rescheduled|all set|cancell?ed|"
    r"(?<!still )(?<!stays )(?<!remains )booked)\b",
    re.I,
)
_TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
_REQ_ID = re.compile(r"\bSCR-[0-9A-F]+\b", re.I)


def _norm(t: str) -> str:
    h, m = t.split(":")
    return f"{int(h):02d}:{m}"


def _facts(outcomes: list[ToolOutcome]) -> tuple[set[str], set[str]]:
    times, ids = set(), set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            times.update(_norm(t) for t in _TIME.findall(v))
            ids.update(i.upper() for i in _REQ_ID.findall(v))

    for o in outcomes:
        walk(o.data)
    return times, ids


def reply_is_safe(text: str, outcomes: list[ToolOutcome]) -> bool:
    if not text or not text.strip() or len(text) > MAX_LEN:
        return False
    if "Traceback" in text or "{" in text or "}" in text:
        return False

    kinds = {o.kind for o in outcomes}
    if _SUCCESS.search(text) and not kinds & SUCCESS_KINDS:
        return False

    for o in outcomes:
        if o.kind == "applied":
            must = o.data["from"] if o.data.get("change_type") in ("cancel", "restore") else o.data["to"]
            if must and must not in text:
                return False

    allowed_times, allowed_ids = _facts(outcomes)
    if any(_norm(t) not in allowed_times for t in _TIME.findall(text)):
        return False
    if any(i.upper() not in allowed_ids for i in _REQ_ID.findall(text)):
        return False
    return True
