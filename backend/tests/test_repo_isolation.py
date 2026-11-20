from app import repo
from app.booking import change_trip
from app.testing import running_app
from tests.conftest import TODAY, at, bearer, ctx_for, get_token


async def test_list_roster_only_own(seeded):
    async with seeded.connection() as conn:
        entries = await repo.list_roster(conn, ctx_for("A-E01"), [TODAY])
    assert entries and all(e.employee_id == "A-E01" for e in entries)
    assert all(e.company_id == "acme" for e in entries)


async def test_same_employee_id_in_wrong_company_sees_nothing(seeded):
    from app.auth import TenantContext

    forged = TenantContext("globex", "A-E01", "employee")
    async with seeded.connection() as conn:
        assert await repo.list_roster(conn, forged, [TODAY]) == []
        assert await repo.get_employee(conn, forged) is None


async def test_cannot_fetch_other_company_request(seeded, clock):
    res = await change_trip(
        seeded, ctx_for("G-E01"), clock, trip_date=TODAY, direction="drop",
        change_type="reschedule", desired_at=at("19:30"), escalate=False, conversation_id=None,
    )
    async with seeded.connection() as conn:
        assert await repo.get_request(conn, "acme", res.request_id) is None
        assert (await repo.get_request(conn, "globex", res.request_id)).employee_id == "G-E01"


async def test_trip_options_scoped_to_company(seeded):
    async with seeded.connection() as conn:
        opts = await repo.list_trip_options(conn, "acme", "globex-r1", "drop", TODAY)
    assert opts == []


async def test_me_roster_isolation(app_kwargs):
    async with running_app(**app_kwargs) as (_, c):
        a = (await c.get("/api/me/roster", headers=bearer(await get_token(c, "A-E01")))).json()
        g = (await c.get("/api/me/roster", headers=bearer(await get_token(c, "G-E01")))).json()
    assert {x["route"] for x in a} == {"Whitefield"}
    assert {x["route"] for x in g} == {"Hebbal"}
    today_drop = [x for x in a if x["trip_date"] == "2026-11-20" and x["direction"] == "drop"]
    assert today_drop[0]["time"] == "21:00"


async def test_roster_api_labels_days_from_app_clock(app_kwargs):
    # Review finding 7: after today's only trip is cancelled, tomorrow's must not show as "today".
    async with running_app(**app_kwargs) as (app, c):
        async with app.state.services.pool.connection() as conn:
            await conn.execute("UPDATE roster SET status='cancelled' WHERE employee_id='G-E01' AND trip_date='2026-11-20'")
        trips = (await c.get("/api/me/roster", headers=bearer(await get_token(c, "G-E01")))).json()
    assert {t["day"] for t in trips} == {"tomorrow"}
