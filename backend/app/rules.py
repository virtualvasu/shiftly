"""Company rules as pure functions. No I/O, no model: same inputs, same answer.

This is the only place that decides whether a change is allowed.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from app.models import ChangeType, Company, Trip, TripOption

ViolationCode = Literal["CUTOFF_PASSED", "NO_SEAT", "NO_SUCH_SLOT", "OUT_OF_WINDOW", "SAME_TRIP", "DEPARTED"]


@dataclass(frozen=True)
class Violation:
    code: ViolationCode
    reason: str
    escalatable: bool


def seat_limit(company: Company, trip: Trip) -> int:
    """Seats we may fill: the vehicle's capacity, capped by company policy."""
    return min(trip.vehicle_capacity, company.max_seats_per_cab)


def cutoff_deadline(company: Company, trip: Trip) -> datetime:
    return trip.scheduled_at - timedelta(minutes=company.cutoff_for(trip.direction))


def check_change(
    company: Company,
    current: Trip,
    target: TripOption | None,
    change_type: ChangeType,
    now: datetime,
    today: date,
) -> Violation | None:
    """Return the blocking violation, or None if the change is allowed.

    Hard violations (nothing a manager can fix) are checked first, so they win
    over the cut-off, which a manager may override.
    """
    if current.trip_date not in (today, today + timedelta(days=1)):
        return Violation("OUT_OF_WINDOW", "Only today's and tomorrow's trips can be changed.", False)
    if now >= current.scheduled_at:
        return Violation("DEPARTED", "That cab has already left.", False)

    if change_type == "reschedule":
        if target is None:
            return Violation("NO_SUCH_SLOT", "There is no cab at that time on this route.", False)
        if target.trip.id == current.id:
            return Violation("SAME_TRIP", "That is already the booked cab.", False)
        if target.occupancy >= seat_limit(company, target.trip):
            return Violation("NO_SEAT", "That cab has no free seat.", False)

    late_for_current = now > cutoff_deadline(company, current)
    late_for_target = change_type == "reschedule" and now > cutoff_deadline(company, target.trip)
    if late_for_current or late_for_target:
        minutes = company.cutoff_for(current.direction)
        return Violation(
            "CUTOFF_PASSED",
            f"{company.name} requires {current.direction} changes at least {minutes} minutes before the cab.",
            company.allow_escalation,
        )
    return None


def nearest_alternatives(
    company: Company,
    current: Trip,
    options: list[TripOption],
    desired_at: datetime,
    now: datetime,
    today: date,
    limit: int = 2,
) -> list[TripOption]:
    """Cabs the employee could move to right now, closest to the wanted time first."""
    allowed = [
        o
        for o in options
        if o.trip.id != current.id
        and check_change(company, current, o, "reschedule", now, today) is None
    ]
    allowed.sort(key=lambda o: (abs(o.trip.scheduled_at - desired_at), o.trip.scheduled_at))
    return allowed[:limit]
