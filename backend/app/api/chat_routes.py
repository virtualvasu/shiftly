from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StringConstraints

from app import repo
from app.agent import templates
from app.agent.graph import ConversationBusy, handle_message, new_conversation_id
from app.auth import TenantContext, require_employee

router = APIRouter(prefix="/api", tags=["chat"])

NOT_FOUND = "Conversation not found."


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str | None = None
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


@router.post("/chat")
async def chat(body: ChatIn, request: Request, ctx: TenantContext = Depends(require_employee)):
    services = request.app.state.services
    async with services.pool.connection() as conn:
        if body.conversation_id is None:
            conversation_id = await repo.create_conversation(conn, ctx, new_conversation_id())
        elif await repo.get_conversation(conn, ctx, body.conversation_id) is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        else:
            conversation_id = body.conversation_id
    try:
        result = await handle_message(services, ctx, conversation_id, body.message)
    except ConversationBusy:
        raise HTTPException(status_code=409, detail=templates.busy())
    return result


@router.get("/conversations/latest")
async def latest_conversation(request: Request, ctx: TenantContext = Depends(require_employee)):
    async with request.app.state.services.pool.connection() as conn:
        return {"conversation_id": await repo.latest_conversation(conn, ctx)}


@router.get("/conversations/{conversation_id}/messages")
async def messages(conversation_id: str, request: Request, after: int = 0,
                   ctx: TenantContext = Depends(require_employee)):
    async with request.app.state.services.pool.connection() as conn:
        if await repo.get_conversation(conn, ctx, conversation_id) is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return await repo.list_messages(conn, ctx, conversation_id, after)
