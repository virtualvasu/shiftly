"""Check the planner's choices against what the employee actually wrote.

The model sometimes fills in a day or a trip the employee never mentioned
("tomorrow" for "move my drop to 7 pm", or "pickup" for "cancel my drop").
These checks only look for explicit words; anything subtler stays with the
model and the rules.
"""

import re

_TOMORROW = re.compile(r"\b(tomorrow|tmrw|tmw|tomorow)\b", re.I)
_DROP = re.compile(r"\bdrop(s|-?off)?\b", re.I)
_PICKUP = re.compile(r"\bpick[\s-]?ups?\b", re.I)


def mentions_tomorrow(texts: list[str]) -> bool:
    return any(_TOMORROW.search(t) for t in texts)


def mentioned_direction(texts: list[str]) -> str | None:
    """The one trip the employee named, most recent message first; None if unclear."""
    for t in texts:
        drop, pickup = bool(_DROP.search(t)), bool(_PICKUP.search(t))
        if drop and pickup:
            return None
        if drop:
            return "drop"
        if pickup:
            return "pickup"
    return None
