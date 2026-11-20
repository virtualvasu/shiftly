from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, Request

from app.config import Settings, get_settings
from app.db import create_pool, init_schema
from app.services import Services


def create_app(
    settings: Settings | None = None,
    *,
    clock=None,
    llm=None,
    seed_today: date | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = await create_pool(settings.database_url)
        await init_schema(pool)
        app.state.services = Services(settings=settings, pool=pool, clock=clock)
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(title="Shift-Change Request Agent", lifespan=lifespan)

    @app.get("/api/health")
    async def health(request: Request):
        services: Services = request.app.state.services
        async with services.pool.connection() as conn:
            row = await (await conn.execute("SELECT 1 AS ok")).fetchone()
        return {"status": "ok", "db": row["ok"] == 1}

    return app


app = create_app()
