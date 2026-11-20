import asyncio

from app.agent import templates
from app.agent.llm import FakeLLM
from app.testing import running_app
from tests.conftest import bearer, call, chat_as, get_token, plan


def ask(q="When would you like to leave?"):
    return plan(call("ask_clarification", question=q))


async def test_body_identity_rejected(app_kwargs):
    async with running_app(**app_kwargs) as (_, c):
        token = await get_token(c, "A-E01")
        r = await c.post("/api/chat", json={"message": "hi", "company_id": "globex"}, headers=bearer(token))
    assert r.status_code == 422


async def test_foreign_conversation_404(app_kwargs):
    async with running_app(**{**app_kwargs, "llm": FakeLLM([ask()])}) as (_, c):
        a = await chat_as(c, "A-E01")
        await a.say("hi")
        g_token = await get_token(c, "G-E01")
        r = await c.post("/api/chat", json={"message": "hi", "conversation_id": a.conversation_id}, headers=bearer(g_token))
        m = await c.get(f"/api/conversations/{a.conversation_id}/messages", headers=bearer(g_token))
    assert r.status_code == 404 and r.json() == {"error": "Conversation not found."}
    assert m.status_code == 404


async def test_messages_log_and_latest(app_kwargs):
    async with running_app(**{**app_kwargs, "llm": FakeLLM([ask()])}) as (_, c):
        a = await chat_as(c, "A-E01")
        await a.say("hi")
        latest = await c.get("/api/conversations/latest", headers=bearer(a.token))
        msgs = await c.get(f"/api/conversations/{a.conversation_id}/messages", headers=bearer(a.token))
        after = await c.get(
            f"/api/conversations/{a.conversation_id}/messages?after={msgs.json()[0]['id']}", headers=bearer(a.token)
        )
    assert latest.json() == {"conversation_id": a.conversation_id}
    assert [(m["role"], m["text"]) for m in msgs.json()] == [("user", "hi"), ("assistant", "When would you like to leave?")]
    assert [m["role"] for m in after.json()] == ["assistant"]


async def test_manager_token_cannot_chat(app_kwargs):
    async with running_app(**app_kwargs) as (_, c):
        token = await get_token(c, "A-M01")
        r = await c.post("/api/chat", json={"message": "hi"}, headers=bearer(token))
    assert r.status_code == 403
    assert r.json() == {"error": "This action isn't available for your account."}


async def test_empty_message_422(app_kwargs):
    async with running_app(**app_kwargs) as (_, c):
        token = await get_token(c, "A-E01")
        r1 = await c.post("/api/chat", json={"message": ""}, headers=bearer(token))
        r2 = await c.post("/api/chat", json={"message": "   \n "}, headers=bearer(token))
    assert r1.status_code == 422 and r2.status_code == 422


async def test_long_message_truncated(app_kwargs):
    fake = FakeLLM([ask()])
    async with running_app(**{**app_kwargs, "llm": fake}) as (_, c):
        await (await chat_as(c, "A-E01")).say("x" * 10_000)
    assert len(fake.last_messages[-1]["content"]) == 1000


async def test_double_click_send_one_turn(app_kwargs):
    fake = FakeLLM([ask(), ask()], delay_s=0.5)
    async with running_app(**{**app_kwargs, "llm": fake}) as (_, c):
        chat = await chat_as(c, "A-E01")
        await chat.say("first")
        body = {"message": "again", "conversation_id": chat.conversation_id}
        r1, r2 = await asyncio.gather(
            c.post("/api/chat", json=body, headers=bearer(chat.token)),
            c.post("/api/chat", json=body, headers=bearer(chat.token)),
        )
    assert sorted([r1.status_code, r2.status_code]) == [200, 409]
    busy = r1 if r1.status_code == 409 else r2
    assert busy.json() == {"error": templates.busy()}
    assert fake.plan_calls == 2


async def test_message_times_follow_app_clock(app_kwargs):
    async with running_app(**{**app_kwargs, "llm": FakeLLM([ask()])}) as (_, c):
        a = await chat_as(c, "A-E01")
        await a.say("hi")
        msgs = (await c.get(f"/api/conversations/{a.conversation_id}/messages", headers=bearer(a.token))).json()
    assert all(m["created_at"].startswith("2026-11-20T17:30") for m in msgs)
