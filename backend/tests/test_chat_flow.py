from app.agent import templates
from app.agent.llm import FakeLLM, LLMError, PlanResponse
from app.faults import Fault
from app.testing import running_app
from tests.conftest import call, chat_as, fetchall, move, plan, TODAY


def kw(app_kwargs, llm, settings=None):
    out = {**app_kwargs, "llm": llm}
    if settings is not None:
        out["settings"] = settings
    return out


async def roster_trip(app, emp, direction="drop"):
    rows = await fetchall(
        app.state.services.pool,
        "SELECT trip_id FROM roster WHERE employee_id=%s AND trip_date=%s AND direction=%s AND status='active'",
        emp, TODAY, direction,
    )
    return rows[0]["trip_id"]


ACME_CUTOFF = (
    "Acme Tech's rule is that drop changes must come in at least 2 hours before the cab, "
    "so I can't change your 19:00 drop myself. "
    "I can send this to your transport manager for approval. Do you want me to?"
)


async def test_tool_selection_reschedule_applied(app_kwargs):
    fake = FakeLLM([move("19:30")])
    async with running_app(**kw(app_kwargs, fake)) as (app, c):
        r = await (await chat_as(c, "G-E01")).say("move my drop to 7:30 pm")
        assert await roster_trip(app, "G-E01") == "globex-r1-1120-d-1930"
    assert r["reply"].startswith("Done. Your drop today is now at 19:30 (was 19:00), cab ")
    assert "Ref: SCR-" in r["reply"]
    assert r["tools"] == ["raise_shift_change_request"]
    assert r["llm_calls"] == 2


async def test_cutoff_rejection_explains_and_offers(app_kwargs):
    fake = FakeLLM([move("19:30")])
    async with running_app(**kw(app_kwargs, fake)) as (app, c):
        r = await (await chat_as(c, "A-E02")).say("move my drop to 7:30 pm")
        assert await roster_trip(app, "A-E02") == "acme-r1-1120-d-1900"
    assert r["reply"] == ACME_CUTOFF
    assert r["outcomes"][0]["data"]["reason_code"] == "CUTOFF_PASSED"


async def test_same_request_different_company_outcome(app_kwargs):
    fake = FakeLLM([move("19:30"), move("19:30")])
    async with running_app(**kw(app_kwargs, fake)) as (_, c):
        g = await (await chat_as(c, "G-E01")).say("move my drop to 7:30 pm")
        a = await (await chat_as(c, "A-E02")).say("move my drop to 7:30 pm")
    assert g["outcomes"][0]["kind"] == "applied"
    assert a["outcomes"][0]["kind"] == "rejected"


async def test_vague_request_gets_question_not_guess(app_kwargs):
    fake = FakeLLM([plan(call("ask_clarification", question="What time would you like to leave instead?"))])
    async with running_app(**kw(app_kwargs, fake)) as (app, c):
        r = await (await chat_as(c, "G-E01")).say("can I go a bit late?")
        rows = await fetchall(app.state.services.pool, "SELECT id FROM shift_change_requests")
    assert r["reply"] == "What time would you like to leave instead?"
    assert r["tools"] == ["ask_clarification"] and r["llm_calls"] == 1
    assert rows == []


async def test_missing_time_is_code_clarified(app_kwargs):
    fake = FakeLLM([plan(call("raise_shift_change_request", date="today", direction="drop", change_type="reschedule"))])
    async with running_app(**kw(app_kwargs, fake)) as (_, c):
        r = await (await chat_as(c, "G-E01")).say("change my drop")
    assert r["reply"] == "What time would you like your drop instead?"
    assert r["llm_calls"] == 1


async def test_unusable_time_is_code_clarified(app_kwargs):
    fake = FakeLLM([move("17:00")])  # already past at 17:30
    async with running_app(**kw(app_kwargs, fake)) as (_, c):
        r = await (await chat_as(c, "G-E01")).say("make it 5")
    assert r["reply"] == "What time would you like your drop instead?"


async def test_context_carried_across_turns(app_kwargs):
    fake = FakeLLM([
        plan(call("ask_clarification", question="What time would you like to leave instead?")),
        move("8:00"),
    ])
    async with running_app(**kw(app_kwargs, fake)) as (_, c):
        chat = await chat_as(c, "G-E01")
        await chat.say("can I go a bit late?")
        r = await chat.say("make it 8 instead")
    second = fake.all_plan_messages[1]
    assert {"role": "user", "content": "can I go a bit late?"} in second
    assert {"role": "assistant", "content": "What time would you like to leave instead?"} in second
    assert r["reply"] == "The 20:00 cab is full. Nearest free cabs: 19:30 or 20:30. Want one of those?"


async def test_last_result_passed_to_planner(app_kwargs):
    fake = FakeLLM([move("19:30"), move("19:30", escalate=True)])
    async with running_app(**kw(app_kwargs, fake)) as (_, c):
        chat = await chat_as(c, "A-E02")
        await chat.say("move my drop to 7:30 pm")
        await chat.say("yes please")
    hints = [m["content"] for m in fake.all_plan_messages[1] if m["content"].startswith("Last result: ")]
    assert len(hints) == 1 and "CUTOFF_PASSED" in hints[0]


async def test_escalation_requires_yes(app_kwargs):
    fake = FakeLLM([move("19:30"), move("19:30", escalate=True)])
    async with running_app(**kw(app_kwargs, fake)) as (app, c):
        chat = await chat_as(c, "A-E02")
        first = await chat.say("move my drop to 7:30 pm")
        rows_before = await fetchall(app.state.services.pool, "SELECT id FROM shift_change_requests")
        r = await chat.say("yes please")
        rows = await fetchall(app.state.services.pool, "SELECT status FROM shift_change_requests")
    assert first["reply"] == ACME_CUTOFF and rows_before == []
    assert r["reply"].startswith("I've sent this to your transport manager (request SCR-")
    assert rows == [{"status": "PENDING_APPROVAL"}]


async def test_malformed_plan_retried_then_not_understood(app_kwargs):
    fake = FakeLLM([
        plan(call("drop_database")),
        plan(call("raise_shift_change_request", employee_id="G-E01")),
    ])
    async with running_app(**kw(app_kwargs, fake)) as (app, c):
        r = await (await chat_as(c, "A-E01")).say("move my drop")
        rows = await fetchall(app.state.services.pool, "SELECT id FROM shift_change_requests")
        g = await roster_trip(app, "G-E01")
    assert r["reply"] == templates.not_understood()
    assert r["llm_calls"] == 2 and r["tools"] == []
    assert rows == [] and g == "globex-r1-1120-d-1900"
    retry_hint = fake.all_plan_messages[1][-1]
    assert retry_hint["role"] == "system" and "invalid" in retry_hint["content"]


async def test_model_identity_args_never_reach_tools(app_kwargs):
    fake = FakeLLM([move("21:30", company_id="globex"), move("21:30")])
    async with running_app(**kw(app_kwargs, fake)) as (app, c):
        r = await (await chat_as(c, "A-E01")).say("move my drop to 9:30 pm")
        a = await roster_trip(app, "A-E01")
        globex_changes = await fetchall(app.state.services.pool, "SELECT id FROM shift_change_requests WHERE company_id='globex'")
    assert r["outcomes"][0]["kind"] == "applied"
    assert a == "acme-r1-1120-d-2130"
    assert globex_changes == []


async def test_text_only_answer_used_when_safe(app_kwargs):
    fake = FakeLLM([PlanResponse([], "Hi! I can help you change your office cab.")])
    async with running_app(**kw(app_kwargs, fake)) as (_, c):
        r = await (await chat_as(c, "A-E01")).say("hello")
    assert r["reply"] == "Hi! I can help you change your office cab."


async def test_text_only_false_claim_replaced(app_kwargs):
    fake = FakeLLM([PlanResponse([], "Done, I moved your drop to 20:00.")])
    async with running_app(**kw(app_kwargs, fake)) as (_, c):
        r = await (await chat_as(c, "A-E01")).say("move me to 8")
    assert r["reply"] == templates.not_understood()


async def test_llm_down_plain_message(app_kwargs):
    fake = FakeLLM([LLMError("429")])
    async with running_app(**kw(app_kwargs, fake)) as (_, c):
        r = await (await chat_as(c, "A-E01")).say("move my drop to 9:30 pm")
    assert r["reply"] == templates.llm_failure()


async def test_tool_timeout_plain_message(app_kwargs):
    fake = FakeLLM([move("21:30")])
    async with running_app(**kw(app_kwargs, fake)) as (app, c):
        app.state.services.faults.set("booking", Fault(fail=True))
        r = await (await chat_as(c, "A-E01")).say("move my drop to 9:30 pm")
        a = await roster_trip(app, "A-E01")
    assert r["reply"] == (
        "I couldn't reach the booking system just now, so nothing was changed. Your 21:00 drop is still booked. "
        "Please try again in a minute, or call the transport desk if it's urgent."
    )
    assert a == "acme-r1-1120-d-2100"


async def test_false_confirmation_from_model_replaced(app_kwargs):
    fake = FakeLLM([move("19:30")], replies=["Done, you're booked!"])
    async with running_app(**kw(app_kwargs, fake)) as (_, c):
        r = await (await chat_as(c, "A-E02")).say("move my drop to 7:30 pm")
    assert r["reply"] == ACME_CUTOFF
    assert r["llm_calls"] == 2


async def test_safe_rephrase_used_and_ref_appended(app_kwargs):
    fake = FakeLLM([move("19:30")], replies=["All set! Your drop today now leaves at 19:30."])
    async with running_app(**kw(app_kwargs, fake)) as (_, c):
        r = await (await chat_as(c, "G-E01")).say("move my drop to 7:30 pm")
    assert r["reply"].startswith("All set! Your drop today now leaves at 19:30.\n\nRef: SCR-")


async def test_responder_skipped_when_out_of_time(app_kwargs, settings):
    fake = FakeLLM([move("19:30")], replies=["All set! 19:30."])
    tight = settings.model_copy(update={"turn_budget_s": 0.1})
    async with running_app(**kw(app_kwargs, fake, tight)) as (_, c):
        r = await (await chat_as(c, "G-E01")).say("move my drop to 7:30 pm")
    assert r["reply"].startswith("Done. Your drop today is now at 19:30")
    assert fake.reply_calls == 0 and r["llm_calls"] == 1


async def test_leave_tomorrow_cancels_both(app_kwargs):
    fake = FakeLLM([plan(
        call("raise_shift_change_request", date="tomorrow", direction="pickup", change_type="cancel"),
        call("raise_shift_change_request", date="tomorrow", direction="drop", change_type="cancel"),
    )])
    async with running_app(**kw(app_kwargs, fake)) as (_, c):
        r = await (await chat_as(c, "G-E01")).say("I'm on leave tomorrow")
    assert [o["kind"] for o in r["outcomes"]] == ["applied", "applied"]
    assert r["reply"].startswith(
        "Done. Your 09:00 pickup tomorrow is cancelled. Done. Your 19:00 drop tomorrow is cancelled."
    )


async def test_roster_question_uses_get_roster(app_kwargs):
    fake = FakeLLM([plan(call("get_roster", date="today"))])
    async with running_app(**kw(app_kwargs, fake)) as (_, c):
        r = await (await chat_as(c, "A-E01")).say("which cab am I on tonight?")
    assert r["tools"] == ["get_roster"]
    assert r["reply"].startswith("Your trips: today drop at 21:00 (cab ")


async def test_too_many_tool_calls_is_invalid(app_kwargs):
    many = plan(*[call("get_roster") for _ in range(4)])
    fake = FakeLLM([many, plan(call("get_roster"))])
    async with running_app(**kw(app_kwargs, fake)) as (_, c):
        r = await (await chat_as(c, "A-E01")).say("roster x4")
    assert r["tools"] == ["get_roster"] and r["llm_calls"] == 3


async def test_offline_mode_end_to_end(app_kwargs, settings):
    offline = settings.model_copy(update={"llm_provider": "offline"})
    async with running_app(**{**app_kwargs, "settings": offline}) as (_, c):
        chat = await chat_as(c, "A-E02")
        first = await chat.say("move my drop to 7:30 pm")
        second = await chat.say("yes please")
    assert first["reply"] == ACME_CUTOFF
    assert second["reply"].startswith("I've sent this to your transport manager (request SCR-")
    assert second["llm_calls"] == 2
