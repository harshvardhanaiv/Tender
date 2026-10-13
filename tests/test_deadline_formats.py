from datetime import date

from etenders_scraper.deadline import enrich_deadline_fields, parse_deadline


def test_day_month_name_formats_are_read():
    assert parse_deadline("12-Nov-26") == date(2026, 11, 12)
    assert parse_deadline("12-Nov-2026 17:00") == date(2026, 11, 12)
    assert parse_deadline("12 Nov 2026") == date(2026, 11, 12)


def test_existing_formats_and_bad_dates_unchanged():
    assert parse_deadline("19 June 2026, 12:00pm") == date(2026, 6, 19)
    assert parse_deadline("05/03/2026") == date(2026, 3, 5)
    assert parse_deadline("31/04/2026") is None
    assert parse_deadline("Nov") is None


def test_enrich_sets_days_for_pcs_style_deadline():
    row = enrich_deadline_fields({"submission_deadline": "12-Nov-26"}, today=date(2026, 10, 13))
    assert row["days_until_deadline"] == 30
    assert row["deadline_urgency"] == "soon"
