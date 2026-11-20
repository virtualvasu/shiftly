"""Every user-facing sentence the system can produce on its own.

These are the source of truth for facts. The LLM may only rephrase them, and
any rephrasing that fails the guard is replaced by the template.
"""

from app.agent.tools import ToolOutcome
from app.models import Company

DESK = "call the transport desk if it's urgent"

SYSTEM_LABEL = {"roster": "roster", "cabs": "cab schedule", "booking": "booking system"}


def _alts(times: list[str]) -> str:
    if len(times) <= 1:
        return "".join(times)
    return ", ".join(times[:-1]) + " or " + times[-1]


def _hours(minutes: int) -> str:
    if minutes % 60:
        return f"{minutes} minutes"
    h = minutes // 60
    return "1 hour" if h == 1 else f"{h} hours"


def llm_failure() -> str:
    return (
        "I'm having trouble understanding messages right now. Nothing was changed. "
        f"Please try again shortly, or {DESK}."
    )


def not_understood() -> str:
    return "Sorry, I didn't quite get that. Could you tell me the time, and whether it's your pickup or drop?"


def busy() -> str:
    return "Still working on your last message. One moment."


def clarify_missing(field: str, direction: str | None) -> str:
    if field == "direction" or not direction:
        return "Is that for your pickup or your drop?"
    return f"What time would you like your {direction} instead?"


def render(o: ToolOutcome, company: Company) -> str:
    d = o.data
    direction = d.get("direction")
    day = d.get("day", "today")
    k = o.kind

    if k == "applied":
        if d.get("change_type") == "cancel":
            return f"Done. Your {d['from']} {direction} {day} is cancelled."
        if d.get("change_type") == "restore":
            return f"Done. Your {d['to']} {direction} {day} is booked again, cab {d['vehicle_no']}."
        return f"Done. Your {direction} {day} is now at {d['to']} (was {d['from']}), cab {d['vehicle_no']}."
    if k == "already_applied":
        return f"That's already done: your {direction} {day} is at {d['to']}."
    if k == "pending":
        return (
            f"I've sent this to your transport manager (request {d['request_id']}). "
            f"Your {d['from']} {direction} stays booked until they decide. I'll post the decision here."
        )
    if k == "already_pending":
        return f"That request is already with your manager (request {d['request_id']}, sent at {d['created']})."
    if k == "no_booking":
        return f"You don't have a {direction} booked {day}, so there's nothing to change."
    if k == "rejected":
        return _rejected(d, company)
    if k == "tool_failure":
        text = f"I couldn't reach the {SYSTEM_LABEL.get(o.system, 'system')} just now, so nothing was changed."
        if d.get("current") and direction:
            text += f" Your {d['current']} {direction} is still booked."
        return text + f" Please try again in a minute, or {DESK}."
    if k == "withdrawn":
        return f"Okay, I've withdrawn request {d['request_id']}. Your {d['from']} {d['direction']} stays as it is."
    if k == "status_info":
        return _status(d)
    if k == "roster_info":
        return _roster(d)
    if k == "options_info":
        return _options(d)
    if k == "clarify":
        return d.get("question") or clarify_missing(d.get("missing", "time"), direction)
    return not_understood()


def _rejected(d: dict, company: Company) -> str:
    direction, alts, code = d["direction"], d.get("alternatives") or [], d["reason_code"]
    if code == "NO_SEAT":
        if alts:
            return f"The {d['to']} cab is full. Nearest free cabs: {_alts(alts)}. Want one of those?"
        return (
            f"The {d['to']} cab is full and there are no other free cabs on your route that day. "
            "Please call the transport desk."
        )
    if code == "NO_SUCH_SLOT":
        text = f"There's no {direction} cab at {d['to']} on your route."
        return text + (f" Nearest: {_alts(alts)}." if alts else "")
    if code == "SAME_TRIP":
        return f"You're already on the {d['to']} {direction}."
    if code == "DEPARTED":
        return f"Your {d['from']} {direction} {d.get('day', 'today')} has already left, so there's nothing to change."
    if code == "OUT_OF_WINDOW":
        return "I can only change trips for today and tomorrow."
    # CUTOFF_PASSED
    text = (
        f"{company.name}'s rule is that {direction} changes must come in at least "
        f"{_hours(company.cutoff_for(direction))} before the cab, so I can't change your {d['from']} {direction} myself."
    )
    if alts:
        text += f" Nearest cabs I can book: {_alts(alts)}."
    if d.get("escalatable"):
        return text + " I can send this to your transport manager for approval. Do you want me to?"
    return text + f" Please {DESK}."


STATUS_LABEL = {
    "APPLIED": "done",
    "APPROVED_APPLIED": "approved and applied",
    "MANAGER_REJECTED": "not approved by your manager",
    "APPROVED_NO_SEAT": "approved, but the cab was full by then",
    "EXPIRED": "expired without a decision",
    "WITHDRAWN": "withdrawn",
}


def _status(d: dict) -> str:
    if d.get("none"):
        return "You don't have any shift-change requests right now."
    what = {
        "cancel": f"to cancel your {d['from']} {d['direction']}",
        "restore": f"to book your {d['from']} {d['direction']} again",
    }.get(d["change_type"], f"to move your {d['direction']} from {d['from']} to {d['to']}")
    if d["status"] == "PENDING_APPROVAL":
        return (
            f"Request {d['request_id']} {what} is with your manager (sent at {d['created']}). "
            "I'll post the decision here."
        )
    return f"Request {d['request_id']} {what} is {STATUS_LABEL.get(d['status'], d['status'].lower())}."


def _roster(d: dict) -> str:
    trips = d.get("trips") or []
    if not trips:
        return "You don't have any trips booked for today or tomorrow."
    parts = [f"{t['day']} {t['direction']} at {t['time']} (cab {t['vehicle_no']})" for t in trips]
    return "Your trips: " + "; ".join(parts) + "."


def _options(d: dict) -> str:
    req, options = d["requested"], d.get("options") or []
    if req["available"]:
        return f"The {req['time']} {d['direction']} has a free seat. Want me to move you to it?"
    reason = {
        "NO_SEAT": f"The {req['time']} cab is full.",
        "NO_SUCH_SLOT": f"There's no {d['direction']} cab at {req['time']}.",
        "CUTOFF_PASSED": f"It's too late to change to {req['time']} under your company's rule.",
        "SAME_TRIP": f"You're already on the {req['time']} {d['direction']}.",
    }.get(req["reason"], f"I can't book the {req['time']} cab.")
    if options:
        return reason + f" Free cabs I can book: {_alts(options)}."
    return reason + " There are no other free cabs I can book on your route that day."


def decision_message(status: str, d: dict) -> str:
    rid, direction, day = d["request_id"], d["direction"], d.get("day", "today")
    if status == "APPROVED_APPLIED":
        if d.get("change_type") == "cancel":
            return f"Your manager approved request {rid}. Your {d['from']} {direction} {day} is cancelled."
        if d.get("change_type") == "restore":
            return f"Your manager approved request {rid}. Your {d['from']} {direction} {day} is booked again."
        return f"Your manager approved request {rid}. Your {direction} {day} is now at {d['to']}, cab {d['vehicle_no']}."
    if status == "MANAGER_REJECTED":
        note = f": {d['note']}" if d.get("note") else ""
        return f"Your manager didn't approve request {rid}{note}. Your {d['from']} {direction} is still booked."
    if status == "APPROVED_NO_SEAT":
        text = (
            f"Your manager approved request {rid}, but the {d['to']} cab filled up in the meantime. "
            f"Your {d['from']} {direction} is still booked."
        )
        alts = d.get("alternatives") or []
        return text + (f" Nearest free cabs: {_alts(alts)}. Want one?" if alts else " Please call the transport desk.")
    if status == "WITHDRAWN":
        return f"Okay, I've withdrawn request {rid}. Your {d['from']} {direction} stays as it is."
    # EXPIRED
    return (
        f"Your manager didn't decide on request {rid} in time, so nothing changed. "
        f"Your {d['from']} {direction} is still booked. Call the transport desk if it's urgent."
    )
