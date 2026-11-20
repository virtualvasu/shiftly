from datetime import date

from app.agent import templates
from app.agent.guard import reply_is_safe
from app.agent.prompt import build_planner_messages, build_reply_messages
from app.agent.tools import ToolOutcome
from app.models import Company, Employee, RosterEntry, Trip
from tests.conftest import at

ACME = Company("acme", "Acme Tech", "Asia/Kolkata", 240, 120, 4, True)
GLOBEX = Company("globex", "Globex Corp", "Asia/Kolkata", 120, 60, 6, True)
BASE = {"day": "today", "date": "2026-11-20", "direction": "drop"}


def out(kind, **data):
    return ToolOutcome("raise_shift_change_request", kind != "tool_failure", kind, {**BASE, **data},
                       "booking" if kind == "tool_failure" else None)


def test_applied_copy():
    o = out("applied", change_type="reschedule", **{"from": "19:00", "to": "19:30"}, vehicle_no="KA01-0042", request_id="SCR-1A2B3C4D")
    assert templates.render(o, GLOBEX) == "Done. Your drop today is now at 19:30 (was 19:00), cab KA01-0042."


def test_cancel_copy():
    o = out("applied", change_type="cancel", **{"from": "19:00", "to": None}, vehicle_no="KA01-0042", request_id="SCR-1")
    assert templates.render(o, GLOBEX) == "Done. Your 19:00 drop today is cancelled."


def test_cutoff_copy_acme():
    o = out("rejected", change_type="reschedule", **{"from": "19:00", "to": "19:30"}, reason_code="CUTOFF_PASSED",
            reason="", escalatable=True, alternatives=[])
    assert templates.render(o, ACME) == (
        "Acme Tech's rule is that drop changes must come in at least 2 hours before the cab, "
        "so I can't change your 19:00 drop myself. "
        "I can send this to your transport manager for approval. Do you want me to?"
    )


def test_cutoff_copy_with_alternatives_and_no_escalation():
    strict = Company("s", "Strict Co", "Asia/Kolkata", 240, 90, 4, False)
    o = out("rejected", change_type="reschedule", **{"from": "21:00", "to": "19:00"}, reason_code="CUTOFF_PASSED",
            reason="", escalatable=False, alternatives=["21:30"])
    assert templates.render(o, strict) == (
        "Strict Co's rule is that drop changes must come in at least 90 minutes before the cab, "
        "so I can't change your 21:00 drop myself. Nearest cabs I can book: 21:30. "
        "Please call the transport desk if it's urgent."
    )


def test_no_seat_copy():
    o = out("rejected", change_type="reschedule", **{"from": "19:00", "to": "20:00"}, reason_code="NO_SEAT",
            reason="", escalatable=False, alternatives=["19:30", "20:30"])
    assert templates.render(o, GLOBEX) == "The 20:00 cab is full. Nearest free cabs: 19:30 or 20:30. Want one of those?"
    o.data["alternatives"] = []
    assert templates.render(o, GLOBEX) == (
        "The 20:00 cab is full and there are no other free cabs on your route that day. Please call the transport desk."
    )


def test_no_such_slot_copy():
    o = out("rejected", change_type="reschedule", **{"from": "21:00", "to": "00:30"}, reason_code="NO_SUCH_SLOT",
            reason="", escalatable=False, alternatives=["23:30", "23:00"])
    assert templates.render(o, ACME) == "There's no drop cab at 00:30 on your route. Nearest: 23:30 or 23:00."


def test_pending_and_no_booking_copy():
    p = out("pending", change_type="reschedule", **{"from": "19:00", "to": "19:30"}, request_id="SCR-1A2B3C4D")
    assert templates.render(p, ACME) == (
        "I've sent this to your transport manager (request SCR-1A2B3C4D). "
        "Your 19:00 drop stays booked until they decide. I'll post the decision here."
    )
    n = ToolOutcome("raise_shift_change_request", True, "no_booking", {**BASE, "day": "tomorrow", "direction": "pickup"})
    assert templates.render(n, ACME) == "You don't have a pickup booked tomorrow, so there's nothing to change."


def test_tool_failure_copy_mentions_kept_trip():
    o = out("tool_failure", failure="timeout", current="19:00")
    o.system = "cabs"
    assert templates.render(o, ACME) == (
        "I couldn't reach the cab schedule just now, so nothing was changed. Your 19:00 drop is still booked. "
        "Please try again in a minute, or call the transport desk if it's urgent."
    )


def test_status_pending_copy():
    o = ToolOutcome("get_request_status", True, "status_info", {
        "request_id": "SCR-1", "status": "PENDING_APPROVAL", "change_type": "reschedule", "direction": "drop",
        "date": "2026-11-20", "from": "19:00", "to": "19:30", "created": "17:30", "note": None})
    text = templates.render(o, ACME)
    assert "with your manager" in text and "sent at 17:30" in text


def test_decision_copy():
    data = {"request_id": "SCR-1", "day": "today", "direction": "drop", "from": "19:00", "to": "19:30",
            "vehicle_no": "KA01-0042", "alternatives": [], "note": "Not this week"}
    assert templates.decision_message("APPROVED_APPLIED", data) == (
        "Your manager approved request SCR-1. Your drop today is now at 19:30, cab KA01-0042."
    )
    assert templates.decision_message("MANAGER_REJECTED", data) == (
        "Your manager didn't approve request SCR-1: Not this week. Your 19:00 drop is still booked."
    )
    assert templates.decision_message("EXPIRED", data) == (
        "Your manager didn't decide on request SCR-1 in time, so nothing changed. "
        "Your 19:00 drop is still booked. Call the transport desk if it's urgent."
    )
    assert templates.decision_message("APPROVED_NO_SEAT", {**data, "alternatives": ["20:30"]}) == (
        "Your manager approved request SCR-1, but the 19:30 cab filled up in the meantime. "
        "Your 19:00 drop is still booked. Nearest free cabs: 20:30. Want one?"
    )


def test_guard_blocks_false_confirmation():
    rejected = out("rejected", **{"from": "19:00", "to": "19:30"}, reason_code="CUTOFF_PASSED", alternatives=[])
    assert not reply_is_safe("Done! Booked for 19:30.", [rejected])
    assert reply_is_safe("Sorry, it's too late to move your 19:00 drop to 19:30 yourself.", [rejected])


def test_guard_allows_stays_booked_when_pending():
    p = out("pending", **{"from": "19:00", "to": "19:30"}, request_id="SCR-1A2B3C4D")
    assert reply_is_safe("Sent to your manager (SCR-1A2B3C4D). Your 19:00 drop stays booked for now.", [p])


def test_guard_requires_applied_time():
    applied = out("applied", change_type="reschedule", **{"from": "19:00", "to": "21:30"}, vehicle_no="KA01-1", request_id="SCR-1")
    assert not reply_is_safe("All set!", [applied])
    assert reply_is_safe("All set, your drop is now at 21:30.", [applied])


def test_guard_blocks_invented_times_and_ids():
    applied = out("applied", change_type="reschedule", **{"from": "19:00", "to": "21:30"}, vehicle_no="KA01-1", request_id="SCR-1")
    assert not reply_is_safe("Done, your drop is now at 21:30, pickup at 08:00.", [applied])
    assert not reply_is_safe("Done, 21:30. Ref SCR-DEADBEEF", [applied])


def test_guard_blocks_braces_traceback_and_length():
    o = out("rejected", **{"from": "19:00", "to": "19:30"}, reason_code="NO_SEAT", alternatives=[])
    assert not reply_is_safe('{"status": "x"}', [o])
    assert not reply_is_safe("Traceback (most recent call last)", [o])
    assert not reply_is_safe("", [o])
    assert not reply_is_safe("a" * 601, [o])


def _entry(emp, hhmm, direction="drop", day=date(2026, 11, 20)):
    trip = Trip(f"acme-r1-{day:%m%d}-d-{hhmm.replace(':', '')}", "acme", "acme-r1", direction, day,
                at(hhmm, day), 6, "KA01-0012")
    return RosterEntry(1, "acme", emp, day, direction, "active", trip)


def test_prompt_contents():
    emp = Employee("A-E01", "acme", "Asha", "employee", "acme-r1", True)
    msgs = build_planner_messages(
        ACME, emp, [_entry("A-E01", "21:00")],
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        last_outcome={"kind": "rejected", "data": {"to": "20:00"}},
        user_text="make it 8 instead", now=at("17:30"),
    )
    system = msgs[0]["content"]
    assert msgs[0]["role"] == "system"
    assert "Friday 20 Nov 2026 17:30" in system
    assert "Asha" in system and "Acme Tech" in system
    assert "- today drop 21:00 (cab KA01-0012)" in system
    assert "ask_clarification" in system
    assert not any(x in system for x in ("G-E", "Globex", "Arjun"))
    assert {"role": "system", "content": 'Last result: {"kind": "rejected", "data": {"to": "20:00"}}'} in msgs
    assert msgs[-1] == {"role": "user", "content": "make it 8 instead"}
    assert msgs[-4:-2] == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]


def test_prompt_keeps_last_12_history_messages():
    emp = Employee("A-E01", "acme", "Asha", "employee", "acme-r1", True)
    history = [{"role": "user", "content": f"m{i}"} for i in range(20)]
    msgs = build_planner_messages(ACME, emp, [], history, None, "now", at("17:30"))
    contents = [m["content"] for m in msgs if m["role"] == "user"]
    assert contents[0] == "m8" and len(contents) == 13


def test_reply_messages_carry_draft():
    msgs = build_reply_messages("Done. Your drop today is now at 19:30 (was 19:00), cab KA01-0042.")
    assert msgs[0]["role"] == "system" and "exactly" in msgs[0]["content"]
    assert msgs[1]["content"].endswith("cab KA01-0042.")


def test_roster_copy():
    o = ToolOutcome("get_roster", True, "roster_info", {**BASE, "trips": [
        {"date": "2026-11-20", "day": "today", "direction": "drop", "time": "21:00", "vehicle_no": "KA01-0012"},
        {"date": "2026-11-21", "day": "tomorrow", "direction": "pickup", "time": "09:00", "vehicle_no": "KA01-0099"},
    ]})
    assert templates.render(o, ACME) == (
        "Your trips: today drop at 21:00 (cab KA01-0012); tomorrow pickup at 09:00 (cab KA01-0099)."
    )


def test_prompt_states_default_day_rule():
    emp = Employee("A-E01", "acme", "Asha", "employee", "acme-r1", True)
    system = build_planner_messages(ACME, emp, [], [], None, "x", at("17:30"))[0]["content"]
    assert "If the day isn't said, use the next one of that trip that hasn't left yet" in system
