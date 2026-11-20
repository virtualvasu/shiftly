"""Mock identity provider for the demo. Disabled unless DEV_AUTH=true.

In production the client company's IdP issues the token; this service only
verifies it. These are the only queries that look a person up without a
tenant context, because they *are* the identity provider.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.auth import TenantContext, issue_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str


@router.post("/token")
async def token(body: TokenIn, request: Request):
    services = request.app.state.services
    async with services.pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT id, company_id, role FROM employees WHERE id = %s", (body.employee_id,)
            )
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown person.")
    ctx = TenantContext(row["company_id"], row["id"], row["role"])
    return {"token": issue_token(ctx, services.settings, services.clock.now())}


@router.get("/demo-users")
async def demo_users(request: Request):
    async with request.app.state.services.pool.connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT c.id AS company_id, c.name AS company_name, e.id, e.name, e.role "
                "FROM employees e JOIN companies c ON c.id = e.company_id "
                "WHERE e.is_demo ORDER BY c.id, e.id"
            )
        ).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        g = groups.setdefault(
            r["company_id"],
            {"company_id": r["company_id"], "company_name": r["company_name"], "people": []},
        )
        g["people"].append({"employee_id": r["id"], "name": r["name"], "role": r["role"]})
    return list(groups.values())
