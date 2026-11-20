import re
from datetime import date, datetime, time, timedelta

from app.clock import IST

_HHMM = re.compile(r"(\d{1,2}):(\d{2})")


def resolve_time(hhmm: str, trip_date: date, current_at: datetime | None, now: datetime) -> datetime | None:
    """Turn the model's "HH:MM" into a concrete cab time, or None if it makes no sense.

    A time is plausible if it is still ahead of us and within 12 hours of the
    booked trip. "make it 8" next to a 19:00 drop therefore means 20:00, and
    "00:30" means just after midnight.
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

    # Same day as given, then the PM reading of a bare hour, then just after
    # midnight ("00:30" next to an evening drop is tonight, not 12:30 today).
    same_day = datetime.combine(trip_date, time(h, mi), tzinfo=IST)
    candidates = [same_day]
    bare_hour = 1 <= h < 12 and not hhmm.strip().startswith("0")  # "8:00", not "08:00"/"00:30"
    if bare_hour:
        candidates.append(same_day + timedelta(hours=12))
    candidates.append(same_day + timedelta(days=1))
    return next((c for c in candidates if plausible(c)), None)


def passed_time(hhmm: str, trip_date: date, now: datetime) -> str | None:
    """The requested time as "HH:MM" if every same-day reading of it is already behind us."""
    m = _HHMM.fullmatch(hhmm.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    latest = datetime.combine(trip_date, time(h + 12 if 1 <= h < 12 and not hhmm.strip().startswith("0") else h, mi), tzinfo=IST)
    return latest.strftime("%H:%M") if latest <= now else None
