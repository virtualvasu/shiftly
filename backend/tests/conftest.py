import os

import pytest

from app.config import Settings
from app.db import create_pool, reset_schema

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/shift_test"
)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        seed_on_start=False,
        llm_provider="fake",
        demo_start="",
        sweeper_interval_s=0,
        static_dir="/nonexistent",  # same behaviour on a laptop and inside the image
    )


@pytest.fixture(autouse=True)
async def clean_db():
    pool = await create_pool(TEST_DATABASE_URL)
    try:
        await reset_schema(pool)
    finally:
        await pool.close()
    yield


@pytest.fixture
async def pool(clean_db):
    from app.db import init_schema

    p = await create_pool(TEST_DATABASE_URL)
    await init_schema(p)
    try:
        yield p
    finally:
        await p.close()


TODAY = __import__("datetime").date(2026, 11, 20)


@pytest.fixture
def clock():
    from datetime import datetime

    from app.clock import IST, FixedClock

    return FixedClock(datetime(2026, 11, 20, 17, 30, tzinfo=IST))


@pytest.fixture
def app_kwargs(settings, clock):
    """Default kwargs for running_app: seeded for 2026-11-20, clock at 17:30 IST."""
    return {"settings": settings, "clock": clock, "seed_today": TODAY}


async def get_token(client, employee_id: str) -> str:
    r = await client.post("/api/auth/token", json={"employee_id": employee_id})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def ctx_for(employee_id: str):
    from app.auth import TenantContext

    company = "acme" if employee_id.startswith("A-") else "globex"
    role = "manager" if "-M" in employee_id else "employee"
    return TenantContext(company, employee_id, role)


def at(hhmm: str, day=None):
    from datetime import datetime

    from app.clock import IST

    day = day or TODAY
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


@pytest.fixture
async def seeded(pool):
    from app.seed import seed

    await seed(pool, TODAY)
    return pool


async def fetchall(pool, sql, *args):
    async with pool.connection() as conn:
        return await (await conn.execute(sql, args)).fetchall()


def call(name: str, **args):
    from app.agent.llm import ToolCall

    return ToolCall(name, args)


def plan(*calls):
    from app.agent.llm import PlanResponse

    return PlanResponse(list(calls))


def move(time: str, *, day="today", direction="drop", escalate=False, **extra):
    args = {"date": day, "direction": direction, "change_type": "reschedule", "desired_time": time, **extra}
    if escalate:
        args["escalate"] = True
    return plan(call("raise_shift_change_request", **args))


class Chat:
    """Talks to /api/chat as one employee and checks the LLM-call cap on every turn."""

    def __init__(self, client, token):
        self.client, self.token, self.conversation_id = client, token, None

    async def say(self, text: str, expect_status: int = 200) -> dict:
        body = {"message": text}
        if self.conversation_id:
            body["conversation_id"] = self.conversation_id
        r = await self.client.post("/api/chat", json=body, headers=bearer(self.token))
        assert r.status_code == expect_status, r.text
        data = r.json()
        if r.status_code == 200:
            assert data["llm_calls"] <= 4
            self.conversation_id = data["conversation_id"]
        return data


async def chat_as(client, employee_id: str) -> Chat:
    return Chat(client, await get_token(client, employee_id))
