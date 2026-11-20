from dataclasses import dataclass
from typing import Any

from psycopg_pool import AsyncConnectionPool

from app.config import Settings


@dataclass
class Services:
    """Everything a request handler needs, built once in the app lifespan."""

    settings: Settings
    pool: AsyncConnectionPool
    clock: Any = None
    llm_factory: Any = None
    checkpointer: Any = None
    chat_graph: Any = None
    approval_graph: Any = None
    faults: Any = None
