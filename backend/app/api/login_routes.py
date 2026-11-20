"""Email + password sign-in. The token carries the company and role from the database."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth import TenantContext, issue_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

BAD_LOGIN = "Email or password is incorrect."


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
async def login(body: LoginIn, request: Request):
    services = request.app.state.services
    async with services.pool.connection() as conn:
        # Sign-in is the one lookup that happens before we know the company.
        row = await (await conn.execute(
            "SELECT id, company_id, role, password_hash FROM employees WHERE lower(email) = lower(%s)",
            (body.email.strip(),),
        )).fetchone()
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail=BAD_LOGIN)
    ctx = TenantContext(row["company_id"], row["id"], row["role"])
    return {"token": issue_token(ctx, services.settings, services.clock.now())}
