"""Data access. Every query on a tenant table filters by company_id.

There is deliberately no function that reads tenant data without a company.
"""

import json
from datetime import date

from psycopg import AsyncConnection

from app.auth import TenantContext
from app.models import Company, Direction, Employee, RosterEntry, ShiftChangeRequest, Trip, TripOption

_TRIP_COLS = (
    "t.id AS t_id, t.company_id AS t_company_id, t.route_id AS t_route_id, "
    "t.direction AS t_direction, t.trip_date AS t_trip_date, t.scheduled_at AS t_scheduled_at, "
    "t.vehicle_capacity AS t_vehicle_capacity, t.vehicle_no AS t_vehicle_no"
)


def _trip(row: dict) -> Trip:
    return Trip(
        id=row["t_id"], company_id=row["t_company_id"], route_id=row["t_route_id"],
        direction=row["t_direction"], trip_date=row["t_trip_date"], scheduled_at=row["t_scheduled_at"],
        vehicle_capacity=row["t_vehicle_capacity"], vehicle_no=row["t_vehicle_no"],
    )


def _entry(row: dict) -> RosterEntry:
    return RosterEntry(
        id=row["id"], company_id=row["company_id"], employee_id=row["employee_id"],
        trip_date=row["trip_date"], direction=row["direction"], status=row["status"], trip=_trip(row),
    )


async def _one(conn: AsyncConnection, sql: str, params=()) -> dict | None:
    return await (await conn.execute(sql, params)).fetchone()


async def _all(conn: AsyncConnection, sql: str, params=()) -> list[dict]:
    return await (await conn.execute(sql, params)).fetchall()


# --- companies and people -------------------------------------------------

async def get_company(conn, company_id: str) -> Company:
    row = await _one(conn, "SELECT * FROM companies WHERE id = %s", (company_id,))
    return Company(**row)


async def get_employee(conn, ctx: TenantContext) -> Employee | None:
    row = await _one(
        conn, "SELECT * FROM employees WHERE id = %s AND company_id = %s", (ctx.employee_id, ctx.company_id)
    )
    return Employee(**row) if row else None


async def get_employee_name(conn, company_id: str, employee_id: str) -> str | None:
    row = await _one(
        conn, "SELECT name FROM employees WHERE id = %s AND company_id = %s", (employee_id, company_id)
    )
    return row["name"] if row else None


# --- roster and trips -------------------------------------------------------

async def list_roster(conn, ctx: TenantContext, dates: list[date]) -> list[RosterEntry]:
    rows = await _all(
        conn,
        f"SELECT r.*, {_TRIP_COLS} FROM roster r JOIN trips t ON t.id = r.trip_id "
        "WHERE r.company_id = %s AND r.employee_id = %s AND r.trip_date = ANY(%s) AND r.status = 'active' "
        "ORDER BY t.scheduled_at",
        (ctx.company_id, ctx.employee_id, dates),
    )
    return [_entry(r) for r in rows]


async def get_roster_entry(
    conn, ctx: TenantContext, trip_date: date, direction: Direction, *, for_update: bool = False
) -> RosterEntry | None:
    if for_update:
        # Lock the roster row on its own first. Locking through the join would make
        # Postgres re-check a concurrently moved row against the *old* cab row and
        # drop it, so a double-submit would see "no booking" instead of "already done".
        locked = await _one(
            conn,
            "SELECT id FROM roster WHERE company_id = %s AND employee_id = %s AND trip_date = %s "
            "AND direction = %s AND status = 'active' FOR UPDATE",
            (ctx.company_id, ctx.employee_id, trip_date, direction),
        )
        if locked is None:
            return None
    row = await _one(
        conn,
        f"SELECT r.*, {_TRIP_COLS} FROM roster r JOIN trips t ON t.id = r.trip_id "
        "WHERE r.company_id = %s AND r.employee_id = %s AND r.trip_date = %s AND r.direction = %s "
        "AND r.status = 'active'",
        (ctx.company_id, ctx.employee_id, trip_date, direction),
    )
    return _entry(row) if row else None


async def get_roster_by_id(conn, company_id: str, roster_id: int, *, for_update: bool = False) -> RosterEntry | None:
    if for_update:
        await conn.execute("SELECT 1 FROM roster WHERE company_id = %s AND id = %s FOR UPDATE", (company_id, roster_id))
    row = await _one(
        conn,
        f"SELECT r.*, {_TRIP_COLS} FROM roster r JOIN trips t ON t.id = r.trip_id "
        "WHERE r.company_id = %s AND r.id = %s",
        (company_id, roster_id),
    )
    return _entry(row) if row else None


async def list_trip_options(conn, company_id: str, route_id: str, direction: Direction, trip_date: date) -> list[TripOption]:
    rows = await _all(
        conn,
        f"SELECT {_TRIP_COLS}, "
        "(SELECT count(*) FROM roster r WHERE r.trip_id = t.id AND r.status = 'active') AS occupancy "
        "FROM trips t WHERE t.company_id = %s AND t.route_id = %s AND t.direction = %s AND t.trip_date = %s "
        "ORDER BY t.scheduled_at",
        (company_id, route_id, direction, trip_date),
    )
    return [TripOption(_trip(r), r["occupancy"]) for r in rows]


async def get_trip(conn, company_id: str, trip_id: str, *, for_update: bool = False) -> Trip | None:
    lock = " FOR UPDATE" if for_update else ""
    row = await _one(conn, f"SELECT {_TRIP_COLS} FROM trips t WHERE t.company_id = %s AND t.id = %s{lock}", (company_id, trip_id))
    return _trip(row) if row else None


async def lock_trips(conn, company_id: str, trip_ids: list[str]) -> None:
    """Lock cab rows in a fixed (ascending id) order so two transactions never deadlock."""
    for tid in sorted(set(trip_ids)):
        await conn.execute("SELECT 1 FROM trips WHERE company_id = %s AND id = %s FOR UPDATE", (company_id, tid))


async def occupancy(conn, company_id: str, trip_id: str) -> int:
    row = await _one(
        conn,
        "SELECT count(*) AS n FROM roster WHERE company_id = %s AND trip_id = %s AND status = 'active'",
        (company_id, trip_id),
    )
    return row["n"]


async def route_name(conn, company_id: str, route_id: str) -> str | None:
    row = await _one(conn, "SELECT name FROM routes WHERE company_id = %s AND id = %s", (company_id, route_id))
    return row["name"] if row else None


# --- shift-change requests ------------------------------------------------

async def insert_request(conn, req: dict) -> ShiftChangeRequest | None:
    """Insert; returns None if an identical pending request already exists."""
    cols = ", ".join(req)
    vals = ", ".join(f"%({k})s" for k in req)
    row = await _one(
        conn,
        f"INSERT INTO shift_change_requests ({cols}) VALUES ({vals}) "
        "ON CONFLICT (company_id, dedupe_key) WHERE status = 'PENDING_APPROVAL' DO NOTHING RETURNING *",
        req,
    )
    return ShiftChangeRequest(**row) if row else None


async def get_request(
    conn, company_id: str, request_id: str, *, employee_id: str | None = None, for_update: bool = False
) -> ShiftChangeRequest | None:
    sql = "SELECT * FROM shift_change_requests WHERE company_id = %s AND id = %s"
    params: list = [company_id, request_id]
    if employee_id is not None:
        sql += " AND employee_id = %s"
        params.append(employee_id)
    if for_update:
        sql += " FOR UPDATE"
    row = await _one(conn, sql, params)
    return ShiftChangeRequest(**row) if row else None


async def find_pending_for_roster(conn, company_id: str, roster_id: int) -> ShiftChangeRequest | None:
    row = await _one(
        conn,
        "SELECT * FROM shift_change_requests WHERE company_id = %s AND roster_id = %s "
        "AND status = 'PENDING_APPROVAL' ORDER BY created_at DESC LIMIT 1",
        (company_id, roster_id),
    )
    return ShiftChangeRequest(**row) if row else None


async def find_pending_by_dedupe(conn, company_id: str, dedupe_key: str) -> ShiftChangeRequest | None:
    row = await _one(
        conn,
        "SELECT * FROM shift_change_requests WHERE company_id = %s AND dedupe_key = %s AND status = 'PENDING_APPROVAL'",
        (company_id, dedupe_key),
    )
    return ShiftChangeRequest(**row) if row else None


async def find_applied(conn, company_id: str, employee_id: str, roster_id: int, to_trip_id: str) -> ShiftChangeRequest | None:
    row = await _one(
        conn,
        "SELECT * FROM shift_change_requests WHERE company_id = %s AND employee_id = %s AND roster_id = %s "
        "AND to_trip_id = %s AND status IN ('APPLIED', 'APPROVED_APPLIED') ORDER BY created_at DESC LIMIT 1",
        (company_id, employee_id, roster_id, to_trip_id),
    )
    return ShiftChangeRequest(**row) if row else None


async def latest_request(conn, ctx: TenantContext, *, status: str | None = None) -> ShiftChangeRequest | None:
    sql = "SELECT * FROM shift_change_requests WHERE company_id = %s AND employee_id = %s"
    params: list = [ctx.company_id, ctx.employee_id]
    if status:
        sql += " AND status = %s"
        params.append(status)
    row = await _one(conn, sql + " ORDER BY created_at DESC, id DESC LIMIT 1", params)
    return ShiftChangeRequest(**row) if row else None


async def list_requests(conn, company_id: str, status: str | None = None) -> list[ShiftChangeRequest]:
    sql = "SELECT * FROM shift_change_requests WHERE company_id = %s"
    params: list = [company_id]
    if status:
        sql += " AND status = %s"
        params.append(status)
    rows = await _all(conn, sql + " ORDER BY created_at", params)
    return [ShiftChangeRequest(**r) for r in rows]


async def set_request_status(
    conn, company_id: str, request_id: str, status: str, *, decided_by: str | None = None,
    decided_at=None, note: str | None = None,
) -> None:
    await conn.execute(
        "UPDATE shift_change_requests SET status = %s, decided_by = %s, decided_at = %s, note = %s "
        "WHERE company_id = %s AND id = %s",
        (status, decided_by, decided_at, note, company_id, request_id),
    )


# --- audit ------------------------------------------------------------------

async def insert_audit(conn, company_id: str, actor_type: str, actor_id: str, action: str, request_id: str | None, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (company_id, actor_type, actor_id, action, request_id, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (company_id, actor_type, actor_id, action, request_id, json.dumps(payload, default=str)),
    )


async def list_audit(conn, company_id: str, limit: int = 100) -> list[dict]:
    return await _all(
        conn,
        "SELECT id, actor_type, actor_id, action, request_id, payload, created_at FROM audit_log "
        "WHERE company_id = %s ORDER BY id DESC LIMIT %s",
        (company_id, limit),
    )


# --- conversations and messages ---------------------------------------------

async def create_conversation(conn, ctx: TenantContext, conversation_id: str) -> str:
    await conn.execute(
        "INSERT INTO conversations (id, company_id, employee_id) VALUES (%s, %s, %s)",
        (conversation_id, ctx.company_id, ctx.employee_id),
    )
    return conversation_id


async def get_conversation(conn, ctx: TenantContext, conversation_id: str) -> dict | None:
    return await _one(
        conn,
        "SELECT * FROM conversations WHERE id = %s AND company_id = %s AND employee_id = %s",
        (conversation_id, ctx.company_id, ctx.employee_id),
    )


async def latest_conversation(conn, ctx: TenantContext) -> str | None:
    row = await _one(
        conn,
        "SELECT id FROM conversations WHERE company_id = %s AND employee_id = %s ORDER BY created_at DESC LIMIT 1",
        (ctx.company_id, ctx.employee_id),
    )
    return row["id"] if row else None


async def append_message(conn, company_id: str, conversation_id: str, role: str, text: str) -> int:
    row = await _one(
        conn,
        "INSERT INTO messages (conversation_id, company_id, role, text) VALUES (%s, %s, %s, %s) RETURNING id",
        (conversation_id, company_id, role, text),
    )
    return row["id"]


async def list_messages(conn, ctx: TenantContext, conversation_id: str, after_id: int = 0) -> list[dict]:
    return await _all(
        conn,
        "SELECT m.id, m.role, m.text, m.created_at FROM messages m "
        "JOIN conversations c ON c.id = m.conversation_id "
        "WHERE m.conversation_id = %s AND c.company_id = %s AND c.employee_id = %s AND m.id > %s ORDER BY m.id",
        (conversation_id, ctx.company_id, ctx.employee_id, after_id),
    )
