"""UK clock time without a timezone database (Windows hosts often lack one): British Summer Time runs from 01:00 UTC on
the last Sunday of March to 01:00 UTC on the last Sunday of October."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def _last_sunday(year: int, month: int) -> date:
    day = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return day - timedelta(days=(day.weekday() + 1) % 7)


def _bst(utc: datetime) -> bool:
    start = datetime.combine(_last_sunday(utc.year, 3), datetime.min.time()) + timedelta(hours=1)
    end = datetime.combine(_last_sunday(utc.year, 10), datetime.min.time()) + timedelta(hours=1)
    return start <= utc.replace(tzinfo=None) < end


def to_london(moment: datetime) -> datetime:
    """A moment as naive UK local time. A naive moment is taken to be UTC."""
    utc = moment.astimezone(timezone.utc) if moment.tzinfo else moment
    return utc.replace(tzinfo=None) + (timedelta(hours=1) if _bst(utc) else timedelta(0))


def london_now() -> datetime:
    return to_london(datetime.now(timezone.utc))
