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
