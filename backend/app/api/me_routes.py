from fastapi import APIRouter, Depends, Request

from app.auth import TenantContext, current_ctx

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("")
async def me(request: Request, ctx: TenantContext = Depends(current_ctx)):
    async with request.app.state.services.pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT e.id, e.name, e.role, c.id AS company_id, c.name AS company_name "
                "FROM employees e JOIN companies c ON c.id = e.company_id "
                "WHERE e.id = %s AND e.company_id = %s",
                (ctx.employee_id, ctx.company_id),
            )
        ).fetchone()
    return {
        "employee_id": row["id"],
        "name": row["name"],
        "role": row["role"],
        "company_id": row["company_id"],
        "company_name": row["company_name"],
    }
