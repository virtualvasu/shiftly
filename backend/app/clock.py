"""Time is injected everywhere so rules can be tested at exact moments."""

import time
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(IST)


class FixedClock:
    """Frozen time for tests; move it forward explicitly."""

    def __init__(self, at: datetime):
        self._at = at

    def now(self) -> datetime:
        return self._at

    def advance(self, minutes: int) -> None:
        self._at += timedelta(minutes=minutes)


class ShiftedClock:
    """Starts at a chosen time and then runs in real time.

    Used for the demo so the evening scenario works whatever the wall clock says.
    """

    def __init__(self, start: datetime):
        self._start = start
        self._t0 = time.monotonic()

    def now(self) -> datetime:
        return self._start + timedelta(seconds=time.monotonic() - self._t0)


def build_clock(settings) -> Clock:
    if not settings.demo_start:
        return SystemClock()
    hh, mm = (int(x) for x in settings.demo_start.split(":"))
    start = datetime.now(IST).replace(hour=hh, minute=mm, second=0, microsecond=0)
    return ShiftedClock(start)
