import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cache_warmer import scheduler


def _now_at(hour, minute, tz_name="Pacific/Auckland"):
    tz = ZoneInfo(tz_name)
    return datetime.datetime(2026, 1, 15, hour, minute, tzinfo=tz)


def _mock_datetime(m, fixed_now):
    m.datetime.now.return_value = fixed_now
    m.datetime.side_effect = datetime.datetime
    m.timedelta = datetime.timedelta


def test_next_run_before_window():
    with patch("cache_warmer.scheduler.datetime") as m:
        _mock_datetime(m, _now_at(0, 30))
        with patch("cache_warmer.scheduler.random.randint", return_value=0):
            result = scheduler.next_run_time("Pacific/Auckland", "01:00", "06:00")
    assert result.hour == 1
    assert result.minute == 0
    assert result.date() == datetime.date(2026, 1, 15)


def test_next_run_after_window_schedules_tomorrow():
    with patch("cache_warmer.scheduler.datetime") as m:
        _mock_datetime(m, _now_at(7, 0))
        with patch("cache_warmer.scheduler.random.randint", return_value=0):
            result = scheduler.next_run_time("Pacific/Auckland", "01:00", "06:00")
    assert result.date() == datetime.date(2026, 1, 16)
    assert result.hour == 1


def test_next_run_mid_window_can_be_any_time_in_window():
    with patch("cache_warmer.scheduler.datetime") as m:
        _mock_datetime(m, _now_at(3, 0))
        with patch("cache_warmer.scheduler.random.randint", return_value=0):
            result = scheduler.next_run_time("Pacific/Auckland", "01:00", "06:00")
    assert result.date() == datetime.date(2026, 1, 16)


def test_sleep_until_in_past_does_not_sleep():
    tz = ZoneInfo("Pacific/Auckland")
    past = datetime.datetime(2020, 1, 1, 0, 0, tzinfo=tz)
    with patch("cache_warmer.scheduler.time.sleep") as mock_sleep:
        scheduler.sleep_until(past)
    mock_sleep.assert_not_called()


def test_sleep_until_future_sleeps():
    tz = ZoneInfo("Pacific/Auckland")
    future = datetime.datetime.now(tz) + datetime.timedelta(seconds=60)
    with patch("cache_warmer.scheduler.time.sleep") as mock_sleep:
        scheduler.sleep_until(future)
    mock_sleep.assert_called_once()
    assert mock_sleep.call_args.args[0] > 0
