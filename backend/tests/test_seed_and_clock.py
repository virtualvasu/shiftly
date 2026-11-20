from datetime import date, datetime, timedelta

from app.clock import IST, FixedClock, build_clock
from app.config import Settings
from app.seed import seed

TODAY = date(2026, 11, 20)


async def _scalar(pool, sql, *args):
    async with pool.connection() as conn:
        row = await (await conn.execute(sql, args)).fetchone()
    return next(iter(row.values()))


async def test_seed_companies_have_different_rules(pool):
    await seed(pool, TODAY)
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT id, cutoff_minutes_pickup, cutoff_minutes_drop, max_seats_per_cab "
                "FROM companies ORDER BY id"
            )
        ).fetchall()
    rules = {r["id"]: (r["cutoff_minutes_pickup"], r["cutoff_minutes_drop"], r["max_seats_per_cab"]) for r in rows}
    assert rules == {"acme": (240, 120, 4), "globex": (120, 60, 6)}


async def test_seed_fill_levels(pool):
    await seed(pool, TODAY)
    sql = "SELECT count(*) FROM roster WHERE trip_id = %s AND status = 'active'"
    assert await _scalar(pool, sql, "acme-r1-1120-d-2000") == 4
    assert await _scalar(pool, sql, "acme-r1-1120-d-2030") == 3
    assert await _scalar(pool, sql, "globex-r1-1120-d-2000") == 6


async def test_seed_is_idempotent(pool):
    await seed(pool, TODAY)
    await seed(pool, TODAY)
    assert await _scalar(pool, "SELECT count(*) FROM employees") == 21
    assert await _scalar(pool, "SELECT count(*) FROM trips") == 192


async def test_seed_demo_roster(pool):
    await seed(pool, TODAY)
    sql = (
        "SELECT t.scheduled_at FROM roster r JOIN trips t ON t.id = r.trip_id "
        "WHERE r.employee_id = %s AND r.trip_date = %s AND r.direction = 'drop'"
    )
    a1 = await _scalar(pool, sql, "A-E01", TODAY)
    g1 = await _scalar(pool, sql, "G-E01", TODAY)
    assert a1.astimezone(IST).strftime("%H:%M") == "21:00"
    assert g1.astimezone(IST).strftime("%H:%M") == "19:00"


def test_fixed_clock_advance():
    clock = FixedClock(datetime(2026, 11, 20, 17, 30, tzinfo=IST))
    clock.advance(30)
    assert clock.now() == datetime(2026, 11, 20, 18, 0, tzinfo=IST)


def test_build_clock_demo_start():
    clock = build_clock(Settings(demo_start="17:30"))
    today = datetime.now(IST).date()
    expected = datetime(today.year, today.month, today.day, 17, 30, tzinfo=IST)
    assert abs(clock.now() - expected) < timedelta(seconds=2)


async def test_restart_does_not_reseed(settings, clock):
    from app.testing import running_app

    boot = settings.model_copy(update={"seed_on_start": True})
    async with running_app(settings=boot, clock=clock) as (app, _):
        async with app.state.services.pool.connection() as conn:
            await conn.execute("UPDATE roster SET trip_id = 'acme-r1-1120-d-2130' WHERE employee_id = 'A-E01'")
    async with running_app(settings=boot, clock=clock) as (app, _):
        async with app.state.services.pool.connection() as conn:
            row = await (await conn.execute("SELECT trip_id FROM roster WHERE employee_id = 'A-E01' AND trip_date = '2026-11-20' AND direction='drop'")).fetchone()
    assert row["trip_id"] == "acme-r1-1120-d-2130"
