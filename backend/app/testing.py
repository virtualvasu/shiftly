"""Run the real app in-process. Shared by the tests and the evaluation runner."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from app.main import create_app


@asynccontextmanager
async def running_app(**create_app_kwargs) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    app = create_app(**create_app_kwargs)
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client
