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
