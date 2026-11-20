import asyncio
import contextlib
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, Request

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agent.graph import build_chat_graph
from app.api import auth_routes, chat_routes, dev_routes, manager_routes, me_routes
from app.approval.graph import GraphApprovals, build_approval_graph
from app.approval.sweeper import run_sweeper
from app.agent.llm import build_llm
from app.api.errors import install_error_handlers
from app.clock import build_clock
from app.config import Settings, get_settings
from app.db import create_pool, init_schema
from app.seed import seed
from app.faults import FaultInjector
from app.services import Services


async def _is_empty(pool) -> bool:
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT count(*) AS n FROM companies")).fetchone()
    return row["n"] == 0


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
        app_clock = clock or build_clock(settings)
        # Seed sample data on first boot only, so a restart keeps pending requests.
        if seed_today or (settings.seed_on_start and await _is_empty(pool)):
            await seed(pool, seed_today or app_clock.now().date())
        services = Services(settings=settings, pool=pool, clock=app_clock, faults=FaultInjector())
        services.llm = build_llm(settings, llm)
        services.checkpointer = AsyncPostgresSaver(pool)
        await services.checkpointer.setup()
        services.chat_graph = build_chat_graph(services.checkpointer)
        services.approval_graph = build_approval_graph(services.checkpointer)
        services.approvals = GraphApprovals(services)
        app.state.services = services
        sweeper = None
        if settings.sweeper_interval_s > 0:
            sweeper = asyncio.create_task(run_sweeper(services, settings.sweeper_interval_s))
        try:
            yield
        finally:
            if sweeper:
                sweeper.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sweeper
            await pool.close()

    app = FastAPI(title="Shift-Change Request Agent", lifespan=lifespan)
    install_error_handlers(app)
    if settings.dev_auth:
        app.include_router(auth_routes.router)
        app.include_router(dev_routes.router)
    app.include_router(me_routes.router)
    app.include_router(chat_routes.router)
    app.include_router(manager_routes.router)

    @app.get("/api/health")
    async def health(request: Request):
        services: Services = request.app.state.services
        async with services.pool.connection() as conn:
            row = await (await conn.execute("SELECT 1 AS ok")).fetchone()
        return {"status": "ok", "db": row["ok"] == 1}

    return app


app = create_app()
