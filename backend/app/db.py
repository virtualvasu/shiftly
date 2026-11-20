from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


async def create_pool(url: str) -> AsyncConnectionPool:
    # autocommit + dict_row + no prepared statements is what the LangGraph
    # Postgres checkpointer expects, so one pool serves both.
    pool = AsyncConnectionPool(
        url,
        min_size=2,
        max_size=20,
        open=False,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": 0,
            # timestamps come back in local (IST) time, which is what employees see
            "options": "-c timezone=Asia/Kolkata",
        },
    )
    await pool.open(wait=True)
    return pool


async def reset_schema(pool: AsyncConnectionPool) -> None:
    """Drop everything. Only for tests and the evaluation runner."""
    async with pool.connection() as conn:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")


async def init_schema(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        await conn.execute(SCHEMA_FILE.read_text(), prepare=False)
