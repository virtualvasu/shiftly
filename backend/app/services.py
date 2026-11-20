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
    llm: Any = None
    checkpointer: Any = None
    chat_graph: Any = None
    approval_graph: Any = None
    faults: Any = None
    approvals: Any = None


class DirectApprovals:
    """Approval hooks without a workflow: pending requests just wait in the table.

    Replaced by the LangGraph-backed approvals once those are wired in.
    """

    def __init__(self, services: Services):
        self.services = services

    async def on_pending(self, request_id: str, company_id: str) -> None:
        return None

    async def withdraw(self, ctx, request_id: str):
        from app.booking import apply_decision

        return await apply_decision(
            self.services.pool, ctx.company_id, request_id, self.services.clock,
            kind="withdraw", actor_type="employee", actor_id=ctx.employee_id,
        )
