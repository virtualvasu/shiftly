import re
from datetime import date, datetime, time, timedelta

from app.clock import IST

_HHMM = re.compile(r"(\d{1,2}):(\d{2})")


def resolve_time(hhmm: str, trip_date: date, current_at: datetime | None, now: datetime) -> datetime | None:
    """Turn the model's "HH:MM" into a concrete cab time, or None if it makes no sense.

    A time is plausible if it is still ahead of us and within 12 hours of the
    booked trip. "make it 8" next to a 19:00 drop therefore means 20:00.
    Requests that are genuinely unclear are the planner's job to ask about.
    """
    m = _HHMM.fullmatch(hhmm.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None

    ref = current_at or now

    def plausible(dt: datetime) -> bool:
        return dt > now and abs(dt - ref) <= timedelta(hours=12)

    candidate = datetime.combine(trip_date, time(h, mi), tzinfo=IST)
    if plausible(candidate):
        return candidate
    if h < 12 and plausible(candidate + timedelta(hours=12)):
        return candidate + timedelta(hours=12)
    return None
