"""Closing moments: the service's 00:00 means the end of the previous day, UK time, and a grant lists until it closes."""
from datetime import datetime, timezone

from tender_app import fag_import as fag
from tender_app import uk_time


def test_midnight_means_end_of_previous_day():
    m = fag.close_moment("2028-04-01T00:00")
    assert m == datetime(2028, 3, 31, 23, 59)  # Find a Grant: "31 March 2028, 11:59pm"
    assert fag.close_moment("2026-11-04T11:00") == datetime(2026, 11, 4, 11, 0)
    assert fag.close_moment("2035-03-31T23:59") == datetime(2035, 3, 31, 23, 59)
    assert fag.close_moment(None) is None and fag.close_moment("soon") is None


def test_offset_times_are_converted_to_uk_time():
    assert fag.close_moment("2030-09-30T23:59+00:00") == datetime(2030, 10, 1, 0, 59)  # BST in September
    assert fag.close_moment("2030-12-15T23:59+00:00") == datetime(2030, 12, 15, 23, 59)  # GMT in December


def test_record_carries_day_and_time():
    rec = fag.build_record({"label": "x", "grantName": "X", "grantApplicationCloseDate": "2028-04-01T00:00"})
    assert rec["close_date"] == "2028-03-31" and rec["close_at"] == "2028-03-31T23:59"
    assert fag.build_record({"label": "x", "grantName": "X"})["close_at"] is None


def test_uk_time_summer_and_winter():
    assert uk_time.to_london(datetime(2026, 10, 28, 22, 20, tzinfo=timezone.utc)) == datetime(2026, 10, 28, 22, 20)  # clocks went back on 25 Oct
    assert uk_time.to_london(datetime(2026, 10, 24, 22, 20, tzinfo=timezone.utc)) == datetime(2026, 10, 24, 23, 20)
    assert uk_time.to_london(datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc)) == datetime(2026, 3, 29, 0, 59)
    assert uk_time.to_london(datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)) == datetime(2026, 3, 29, 2, 0)
