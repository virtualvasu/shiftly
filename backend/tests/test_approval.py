from app.agent.llm import FakeLLM
from app.approval.sweeper import expire_stale
from app.booking import change_trip
from app import repo
from app.testing import running_app
from tests.conftest import TODAY, at, bearer, call, chat_as, ctx_for, fetchall, get_token, move, plan


def ask():
    return plan(call("ask_clarification", question="Anything else?"))


async def escalate(c, emp="A-E02", time="19:30"):
    chat = await chat_as(c, emp)
    await chat.say(f"move my drop to {time}")
    r = await chat.say("yes please")
    rid = r["outcomes"][0]["data"]["request_id"]
    return chat, rid


def escalation_script(time="19:30", extra=()):
    return FakeLLM([move(time), move(time, escalate=True), *extra])


async def decide(c, manager, rid, decision, note=None):
    token = await get_token(c, manager)
    body = {"decision": decision} | ({"note": note} if note else {})
    return await c.post(f"/api/manager/requests/{rid}/decision", json=body, headers=bearer(token))


async def last_message(c, chat):
    r = await c.get(f"/api/conversations/{chat.conversation_id}/messages", headers=bearer(chat.token))
    return r.json()[-1]["text"]


async def roster_trip(pool, emp):
    rows = await fetchall(pool, "SELECT trip_id FROM roster WHERE employee_id=%s AND trip_date=%s AND direction='drop' AND status='active'", emp, TODAY)
    return rows[0]["trip_id"]


async def test_restart_then_manager_approves(app_kwargs):
    async with running_app(**{**app_kwargs, "llm": escalation_script()}) as (_, c):
        chat, rid = await escalate(c)
        pending = await c.get("/api/manager/requests", headers=bearer(await get_token(c, "A-M01")))
        assert [p["request_id"] for p in pending.json()] == [rid]

    # The service restarts: new app, same database, nothing re-seeded.
    fake2 = FakeLLM([ask()])
    restarted = {k: v for k, v in app_kwargs.items() if k != "seed_today"}
    async with running_app(**restarted, llm=fake2) as (app, c):
        # The new process sees the workflow still paused at the interrupt.
        snapshot = await app.state.services.approval_graph.aget_state(
            {"configurable": {"thread_id": f"approval:{rid}"}}
        )
        assert snapshot.next == ("await_decision",)
        assert snapshot.tasks[0].interrupts[0].value == {"request_id": rid}
        r = await decide(c, "A-M01", rid, "approve")
        after = await app.state.services.approval_graph.aget_state({"configurable": {"thread_id": f"approval:{rid}"}})
        assert after.next == () and after.values["result"]["status"] == "APPROVED_APPLIED"
        assert r.status_code == 200 and r.json() == {"status": "APPROVED_APPLIED"}
        assert await roster_trip(app.state.services.pool, "A-E02") == "acme-r1-1120-d-1930"
        chat.client = c
        text = await last_message(c, chat)
        assert text.startswith(f"Your manager approved request {rid}. Your drop today is now at 19:30, cab ")
        await chat.say("thanks")
    assert {"role": "assistant", "content": text} in fake2.last_messages


async def test_manager_reject_with_note(app_kwargs):
    async with running_app(**{**app_kwargs, "llm": escalation_script()}) as (app, c):
        chat, rid = await escalate(c)
        r = await decide(c, "A-M01", rid, "reject", "Not this week")
        assert r.json() == {"status": "MANAGER_REJECTED"}
        assert await last_message(c, chat) == f"Your manager didn't approve request {rid}: Not this week. Your 19:00 drop is still booked."
        assert await roster_trip(app.state.services.pool, "A-E02") == "acme-r1-1120-d-1900"


async def test_cab_filled_during_approval(app_kwargs):
    async with running_app(**{**app_kwargs, "llm": escalation_script("20:30")}) as (app, c):
        chat, rid = await escalate(c, time="20:30")
        async with app.state.services.pool.connection() as conn:
            await conn.execute("UPDATE roster SET trip_id = 'acme-r1-1120-d-2030' WHERE employee_id = 'A-F01'")
        r = await decide(c, "A-M01", rid, "approve")
        assert r.json() == {"status": "APPROVED_NO_SEAT"}
        assert (await last_message(c, chat)).startswith(
            f"Your manager approved request {rid}, but the 20:30 cab filled up in the meantime. Your 19:00 drop is still booked."
        )


async def test_other_company_manager_404(app_kwargs):
    async with running_app(**{**app_kwargs, "llm": escalation_script()}) as (_, c):
        _, rid = await escalate(c)
        r = await decide(c, "G-M01", rid, "approve")
        listing = await c.get("/api/manager/requests", headers=bearer(await get_token(c, "G-M01")))
        status = (await c.get("/api/manager/requests", headers=bearer(await get_token(c, "A-M01")))).json()[0]["status"]
    assert r.status_code == 404 and r.json() == {"error": "Request not found."}
    assert listing.json() == []
    assert status == "PENDING_APPROVAL"


async def test_employee_token_on_manager_api_403(app_kwargs):
    async with running_app(**app_kwargs) as (_, c):
        token = await get_token(c, "A-E01")
        r = await c.get("/api/manager/requests", headers=bearer(token))
        d = await c.post("/api/manager/requests/SCR-00000000/decision", json={"decision": "approve"}, headers=bearer(token))
    assert r.status_code == 403 and d.status_code == 403


async def test_decision_body_is_strict(app_kwargs):
    async with running_app(**{**app_kwargs, "llm": escalation_script()}) as (_, c):
        _, rid = await escalate(c)
        token = await get_token(c, "A-M01")
        r = await c.post(f"/api/manager/requests/{rid}/decision", json={"decision": "maybe"}, headers=bearer(token))
        r2 = await c.post(f"/api/manager/requests/{rid}/decision", json={"decision": "approve", "company_id": "acme"}, headers=bearer(token))
    assert r.status_code == 422 and r2.status_code == 422


async def test_double_decision_409(app_kwargs):
    async with running_app(**{**app_kwargs, "llm": escalation_script()}) as (_, c):
        _, rid = await escalate(c)
        await decide(c, "A-M01", rid, "approve")
        r = await decide(c, "A-M01", rid, "reject")
    assert r.status_code == 409
    assert r.json() == {"error": "This request was already decided (APPROVED_APPLIED)."}


async def test_withdraw_resumes_with_withdrawn(app_kwargs):
    fake = escalation_script(extra=[plan(call("withdraw_request"))])
    async with running_app(**{**app_kwargs, "llm": fake}) as (_, c):
        chat, rid = await escalate(c)
        r = await chat.say("actually, withdraw it")
        approve = await decide(c, "A-M01", rid, "approve")
    assert r["reply"] == f"Okay, I've withdrawn request {rid}. Your 19:00 drop stays as it is."
    assert approve.status_code == 409


async def test_status_while_pending(app_kwargs):
    fake = escalation_script(extra=[plan(call("get_request_status"))])
    async with running_app(**{**app_kwargs, "llm": fake}) as (_, c):
        chat, rid = await escalate(c)
        r = await chat.say("any update?")
    assert "with your manager" in r["reply"] and "sent at 17:30" in r["reply"]


async def test_sweeper_expires(app_kwargs, clock):
    async with running_app(**{**app_kwargs, "llm": escalation_script()}) as (app, c):
        chat, rid = await escalate(c)
        services = app.state.services
        # Review finding 3: expiry is keyed on the earlier cab (the booked 19:00), so the
        # employee is told in time to still catch it.
        clock.advance(70)   # 18:40, before 18:45 (19:00 minus the 15 min lead)
        assert await expire_stale(services) == 0
        clock.advance(10)   # 18:50
        assert await expire_stale(services) == 1
        assert await expire_stale(services) == 0
        text = await last_message(c, chat)
        rows = await fetchall(services.pool, "SELECT status FROM shift_change_requests")
    assert text == f"Your manager didn't decide on request {rid} in time, so nothing changed. Your 19:00 drop is still booked. Call the transport desk if it's urgent."
    assert rows == [{"status": "EXPIRED"}]


async def test_resume_without_interrupt_falls_back(app_kwargs, clock):
    async with running_app(**app_kwargs) as (app, c):
        pool = app.state.services.pool
        ctx = ctx_for("A-E02")
        async with pool.connection() as conn:
            conv = await repo.create_conversation(conn, ctx, "conv-fallback")
        res = await change_trip(pool, ctx, clock, trip_date=TODAY, direction="drop", change_type="reschedule",
                                desired_at=at("19:30"), escalate=True, conversation_id=conv)
        r = await decide(c, "A-M01", res.request_id, "approve")
        msgs = await fetchall(pool, "SELECT text FROM messages WHERE conversation_id=%s", conv)
    assert r.json() == {"status": "APPROVED_APPLIED"}
    assert msgs[-1]["text"].startswith(f"Your manager approved request {res.request_id}.")


async def test_audit_trail(app_kwargs):
    async with running_app(**{**app_kwargs, "llm": escalation_script()}) as (_, c):
        _, rid = await escalate(c)
        await decide(c, "A-M01", rid, "approve")
        a = await c.get("/api/manager/audit", headers=bearer(await get_token(c, "A-M01")))
        g = await c.get("/api/manager/audit", headers=bearer(await get_token(c, "G-M01")))
    actions = [(e["actor_id"], e["action"]) for e in a.json() if e["request_id"] == rid]
    assert ("A-E02", "escalated") in actions and ("A-M01", "approved_applied") in actions
    assert g.json() == []


async def test_direct_change_supersedes_pending_request(app_kwargs, clock):
    # Review finding 5: a later approval must not undo a newer change the employee made themselves.
    clock.advance(1)  # 17:31: Asha's 19:30 target is past the cut-off, 22:00 is not
    fake = FakeLLM([move("19:30"), move("19:30", escalate=True), move("22:00")])
    async with running_app(**{**app_kwargs, "llm": fake}) as (app, c):
        chat = await chat_as(c, "A-E01")
        await chat.say("move my drop to 7:30 pm")
        r = await chat.say("yes")
        rid = r["outcomes"][0]["data"]["request_id"]
        done = await chat.say("actually just make it 10 pm")
        approve = await decide(c, "A-M01", rid, "approve")
        status = (await fetchall(app.state.services.pool, "SELECT status FROM shift_change_requests WHERE id=%s", rid))[0]["status"]
        trip = await roster_trip(app.state.services.pool, "A-E01")
    assert done["outcomes"][0]["kind"] == "applied"
    assert status == "WITHDRAWN" and approve.status_code == 409
    assert trip == "acme-r1-1120-d-2200"
