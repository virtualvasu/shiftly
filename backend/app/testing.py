"""Run the real app in-process. Shared by the tests and the evaluation runner."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from app.main import create_app


@asynccontextmanager
async def running_app(
    app: FastAPI | None = None, **create_app_kwargs
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    app = app or create_app(**create_app_kwargs)
    async with LifespanManager(app) as manager:
        # Behave like a real server: unhandled errors become 500 responses.
        transport = httpx.ASGITransport(app=manager.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client
