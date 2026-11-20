from datetime import timedelta

from app.agent.timeparse import resolve_time
from tests.conftest import TODAY, at

NOW = at("17:30")
TOMORROW = TODAY + timedelta(days=1)


def test_bare_hour_next_to_evening_drop_means_pm():
    assert resolve_time("8:00", TODAY, at("19:00"), NOW) == at("20:00")


def test_24h_time_kept():
    assert resolve_time("21:30", TODAY, at("19:00"), NOW) == at("21:30")


def test_past_morning_hour_becomes_evening():
    assert resolve_time("7:00", TODAY, at("19:00"), NOW) == at("19:00")


def test_tomorrow_morning_pickup_kept():
    assert resolve_time("6:00", TOMORROW, at("09:00", TOMORROW), NOW) == at("06:00", TOMORROW)


def test_invalid_times():
    assert resolve_time("25:00", TODAY, at("19:00"), NOW) is None
    assert resolve_time("8", TODAY, at("19:00"), NOW) is None
    assert resolve_time("19:75", TODAY, at("19:00"), NOW) is None


def test_time_already_past_is_none():
    assert resolve_time("17:00", TODAY, at("19:00"), NOW) is None
