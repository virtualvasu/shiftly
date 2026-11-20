"""Sample data for two companies with different rules.

Tests depend on these exact values; see docs/design.md §5.2.
"""

from datetime import date, datetime, time, timedelta

from psycopg_pool import AsyncConnectionPool

from app.clock import IST

COMPANIES = [
    # id, name, cutoff pickup, cutoff drop, max seats per cab, escalation allowed
    ("acme", "Acme Tech", 240, 120, 4, True),
    ("globex", "Globex Corp", 120, 60, 6, True),
]

ROUTES = [
    ("acme-r1", "acme", "Whitefield"),
    ("acme-r2", "acme", "Electronic City"),
    ("globex-r1", "globex", "Hebbal"),
    ("globex-r2", "globex", "Sarjapur"),
]

DEMO_PEOPLE = [
    # id, company, name, role, route
    ("A-E01", "acme", "Asha", "employee", "acme-r1"),
    ("A-E02", "acme", "Rohan", "employee", "acme-r1"),
    ("A-E03", "acme", "Meera", "employee", "acme-r2"),
    ("A-M01", "acme", "Kavita", "manager", None),
    ("G-E01", "globex", "Arjun", "employee", "globex-r1"),
    ("G-E02", "globex", "Sneha", "employee", "globex-r1"),
    ("G-E03", "globex", "Vikram", "employee", "globex-r2"),
    ("G-M01", "globex", "Farah", "manager", None),
]

# Other riders that make some cabs full: (employee id, route, drop time today)
FILLERS = (
    [(f"A-F0{i}", "acme-r1", "20:00") for i in range(1, 5)]  # full at Acme's cap of 4
    + [(f"A-F0{i}", "acme-r1", "20:30") for i in range(5, 8)]  # one seat left
    + [(f"G-F0{i}", "globex-r1", "20:00") for i in range(1, 7)]  # full at 6
)

VEHICLE_CAPACITY = 6


def _slots(start: str, end: str) -> list[str]:
    h, m = map(int, start.split(":"))
    cur = datetime(2000, 1, 1, h, m)
    h2, m2 = map(int, end.split(":"))
    last = datetime(2000, 1, 1, h2, m2)
    out = []
    while cur <= last:
        out.append(cur.strftime("%H:%M"))
        cur += timedelta(minutes=30)
    return out


DROP_SLOTS = _slots("17:00", "23:30")
PICKUP_SLOTS = _slots("06:00", "10:30")


def trip_id(route: str, day: date, direction: str, hhmm: str) -> str:
    return f"{route}-{day:%m%d}-{'d' if direction == 'drop' else 'p'}-{hhmm.replace(':', '')}"


def _at(day: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(day, time(h, m), tzinfo=IST)


async def seed(pool: AsyncConnectionPool, today: date) -> None:
    """Replace all domain data with the sample data for `today` and tomorrow."""
    tomorrow = today + timedelta(days=1)
    route_company = {r: c for r, c, _ in ROUTES}
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "TRUNCATE audit_log, shift_change_requests, messages, conversations, roster, "
            "trips, employees, routes, companies RESTART IDENTITY CASCADE"
        )
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO companies VALUES (%s, %s, 'Asia/Kolkata', %s, %s, %s, %s)", COMPANIES
            )
            await cur.executemany("INSERT INTO routes VALUES (%s, %s, %s)", ROUTES)

            people = [(*p, True) for p in DEMO_PEOPLE] + [
                (fid, route_company[route], f"Rider {fid}", "employee", route, False)
                for fid, route, _ in FILLERS
            ]
            await cur.executemany("INSERT INTO employees VALUES (%s, %s, %s, %s, %s, %s)", people)

            trips, n = [], 0
            for route, company, _ in ROUTES:
                for day in (today, tomorrow):
                    for direction, slots in (("pickup", PICKUP_SLOTS), ("drop", DROP_SLOTS)):
                        for hhmm in slots:
                            n += 1
                            trips.append((
                                trip_id(route, day, direction, hhmm), company, route, direction,
                                day, _at(day, hhmm), VEHICLE_CAPACITY, f"KA01-{n:04d}",
                            ))
            await cur.executemany("INSERT INTO trips VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", trips)

            roster = []
            for emp, company, _, role, route in DEMO_PEOPLE:
                if role != "employee":
                    continue
                today_drop = "21:00" if emp == "A-E01" else "19:00"
                roster += [
                    (company, emp, today, "drop", trip_id(route, today, "drop", today_drop)),
                    (company, emp, tomorrow, "drop", trip_id(route, tomorrow, "drop", "19:00")),
                    (company, emp, tomorrow, "pickup", trip_id(route, tomorrow, "pickup", "09:00")),
                ]
            for fid, route, hhmm in FILLERS:
                roster.append((route_company[route], fid, today, "drop", trip_id(route, today, "drop", hhmm)))
            await cur.executemany(
                "INSERT INTO roster (company_id, employee_id, trip_date, direction, trip_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                roster,
            )
