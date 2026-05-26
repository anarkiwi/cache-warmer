import datetime
import random
import time
from zoneinfo import ZoneInfo


def next_run_time(
    timezone: str, window_start: str, window_end: str
) -> datetime.datetime:
    tz = ZoneInfo(timezone)
    now = datetime.datetime.now(tz)

    def random_in_window(date: datetime.date) -> datetime.datetime:
        sh, sm = (int(x) for x in window_start.split(":"))
        eh, em = (int(x) for x in window_end.split(":"))
        open_ = datetime.datetime(date.year, date.month, date.day, sh, sm, tzinfo=tz)
        close = datetime.datetime(date.year, date.month, date.day, eh, em, tzinfo=tz)
        secs = int((close - open_).total_seconds())
        return open_ + datetime.timedelta(seconds=random.randint(0, secs))

    run_at = random_in_window(now.date())
    if now >= run_at:
        run_at = random_in_window(now.date() + datetime.timedelta(days=1))
    return run_at


def sleep_until(target: datetime.datetime) -> None:
    now = datetime.datetime.now(target.tzinfo)
    delta = (target - now).total_seconds()
    if delta > 0:
        time.sleep(delta)
