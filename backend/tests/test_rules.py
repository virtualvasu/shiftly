from datetime import date, datetime, timedelta

from app.clock import IST
from app.models import Company, Trip, TripOption
from app.rules import check_change, nearest_alternatives, seat_limit

TODAY = date(2026, 11, 20)
NOW = datetime(2026, 11, 20, 17, 30, tzinfo=IST)

ACME = Company("acme", "Acme Tech", "Asia/Kolkata", 240, 120, 4, True)
GLOBEX = Company("globex", "Globex Corp", "Asia/Kolkata", 120, 60, 6, True)


def trip(company: Company, hhmm: str, day: date = TODAY, direction="drop", capacity=6) -> Trip:
    h, m = map(int, hhmm.split(":"))
    at = datetime(day.year, day.month, day.day, h, m, tzinfo=IST)
    return Trip(
        id=f"{company.id}-r1-{day:%m%d}-{direction[0]}-{hhmm.replace(':', '')}",
        company_id=company.id, route_id=f"{company.id}-r1", direction=direction,
        trip_date=day, scheduled_at=at, vehicle_capacity=capacity, vehicle_no="KA01-0001",
    )


def opt(company, hhmm, occupancy=0, **kw) -> TripOption:
    return TripOption(trip(company, hhmm, **kw), occupancy)


def test_acme_drop_cutoff_rejects_1900_at_1730():
    v = check_change(ACME, trip(ACME, "19:00"), opt(ACME, "19:30"), "reschedule", NOW, TODAY)
    assert v.code == "CUTOFF_PASSED"
    assert v.escalatable is True


def test_globex_drop_cutoff_allows_1900_at_1730():
    assert check_change(GLOBEX, trip(GLOBEX, "19:00"), opt(GLOBEX, "19:30"), "reschedule", NOW, TODAY) is None


def test_cutoff_boundary_inclusive():
    # Acme drop cut-off for 19:30 is exactly 17:30: still allowed.
    assert check_change(ACME, trip(ACME, "19:30"), opt(ACME, "21:00"), "reschedule", NOW, TODAY) is None


def test_one_minute_past_cutoff_rejected():
    v = check_change(ACME, trip(ACME, "19:30"), opt(ACME, "21:00"), "reschedule", NOW + timedelta(minutes=1), TODAY)
    assert v.code == "CUTOFF_PASSED"


def test_target_cutoff_also_applies():
    v = check_change(ACME, trip(ACME, "21:00"), opt(ACME, "19:00"), "reschedule", NOW, TODAY)
    assert v.code == "CUTOFF_PASSED"


def test_pickup_uses_pickup_cutoff():
    tomorrow = TODAY + timedelta(days=1)
    current = trip(ACME, "09:00", day=tomorrow, direction="pickup")
    # Acme pickup cut-off is 240 min: 08:00 pickup tomorrow → deadline 04:00 tomorrow.
    assert check_change(ACME, current, opt(ACME, "08:00", day=tomorrow, direction="pickup"), "reschedule", NOW, TODAY) is None
    late = datetime(2026, 11, 21, 4, 1, tzinfo=IST)
    v = check_change(ACME, current, opt(ACME, "08:00", day=tomorrow, direction="pickup"), "reschedule", late, TODAY)
    assert v.code == "CUTOFF_PASSED"


def test_capacity_uses_company_cap_not_vehicle():
    assert seat_limit(ACME, trip(ACME, "20:00")) == 4
    assert seat_limit(GLOBEX, trip(GLOBEX, "20:00", capacity=5)) == 5
    v = check_change(ACME, trip(ACME, "21:00"), opt(ACME, "21:30", occupancy=4), "reschedule", NOW, TODAY)
    assert v.code == "NO_SEAT"
    assert check_change(GLOBEX, trip(GLOBEX, "21:00"), opt(GLOBEX, "21:30", occupancy=4), "reschedule", NOW, TODAY) is None


def test_no_seat_beats_cutoff():
    v = check_change(ACME, trip(ACME, "19:00"), opt(ACME, "19:30", occupancy=4), "reschedule", NOW, TODAY)
    assert v.code == "NO_SEAT"
    assert v.escalatable is False


def test_same_trip():
    v = check_change(ACME, trip(ACME, "21:00"), opt(ACME, "21:00", occupancy=1), "reschedule", NOW, TODAY)
    assert v.code == "SAME_TRIP"


def test_no_such_slot():
    v = check_change(ACME, trip(ACME, "21:00"), None, "reschedule", NOW, TODAY)
    assert v.code == "NO_SUCH_SLOT"
    assert v.escalatable is False


def test_out_of_window_day_after_tomorrow():
    v = check_change(ACME, trip(ACME, "21:00", day=TODAY + timedelta(days=2)), None, "cancel", NOW, TODAY)
    assert v.code == "OUT_OF_WINDOW"


def test_cancel_checks_only_current_cutoff():
    assert check_change(GLOBEX, trip(GLOBEX, "20:00"), None, "cancel", NOW, TODAY) is None
    v = check_change(ACME, trip(ACME, "19:00"), None, "cancel", NOW, TODAY)
    assert v.code == "CUTOFF_PASSED"


def test_escalation_flag_follows_company_policy():
    strict = Company("strict", "Strict Co", "Asia/Kolkata", 240, 120, 4, False)
    v = check_change(strict, trip(strict, "19:00"), opt(strict, "19:30"), "reschedule", NOW, TODAY)
    assert v.code == "CUTOFF_PASSED" and v.escalatable is False


def _acme_r1_today_drop_options():
    occ = {"19:00": 1, "20:00": 4, "20:30": 3, "21:00": 1}
    slots = [f"{h:02d}:{m:02d}" for h in range(17, 24) for m in (0, 30)]
    return [opt(ACME, s, occupancy=occ.get(s, 0)) for s in slots]


def test_alternatives_nearest_ties_earlier_first():
    current = trip(ACME, "21:00")
    desired = datetime(2026, 11, 20, 20, 0, tzinfo=IST)
    alts = nearest_alternatives(ACME, current, _acme_r1_today_drop_options(), desired, NOW, TODAY)
    assert [a.trip.scheduled_at.strftime("%H:%M") for a in alts] == ["19:30", "20:30"]


def test_alternatives_exclude_current_trip():
    current = trip(ACME, "21:00")
    desired = datetime(2026, 11, 20, 21, 0, tzinfo=IST)
    alts = nearest_alternatives(ACME, current, _acme_r1_today_drop_options(), desired, NOW, TODAY)
    assert current.id not in [a.trip.id for a in alts]
    assert [a.trip.scheduled_at.strftime("%H:%M") for a in alts] == ["20:30", "21:30"]


def test_after_last_slot_alternatives():
    current = trip(ACME, "21:00")
    desired = datetime(2026, 11, 21, 0, 30, tzinfo=IST)
    assert check_change(ACME, current, None, "reschedule", NOW, TODAY).code == "NO_SUCH_SLOT"
    alts = nearest_alternatives(ACME, current, _acme_r1_today_drop_options(), desired, NOW, TODAY)
    assert [a.trip.scheduled_at.strftime("%H:%M") for a in alts] == ["23:30", "23:00"]


def test_no_alternatives_when_current_cutoff_passed():
    current = trip(ACME, "19:00")
    desired = datetime(2026, 11, 20, 19, 30, tzinfo=IST)
    assert nearest_alternatives(ACME, current, _acme_r1_today_drop_options(), desired, NOW, TODAY) == []


def test_departed_trip_cannot_be_changed_or_escalated():
    # Review finding 2: a cab that has already left is not "past the cut-off", it's gone.
    gone = trip(GLOBEX, "17:00")
    v = check_change(GLOBEX, gone, None, "cancel", NOW, TODAY)
    assert v.code == "DEPARTED" and v.escalatable is False
    v2 = check_change(GLOBEX, gone, opt(GLOBEX, "21:00"), "reschedule", NOW, TODAY)
    assert v2.code == "DEPARTED"
