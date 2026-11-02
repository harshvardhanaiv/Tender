"""The Public Contracts Scotland index (docs/PORTAL_INDEX_DESIGN.md steps 3 and 4): scraped rows are stored once, found by words, and handed
back in the shape a live search returns.

Run: python tests/test_pcs_index.py   (the database half needs the dev database; it uses its own source name and cleans up)
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_app import tender_index as ti  # noqa: E402

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def pcs_row(**over):
    row = {"title": "Business Intelligence and Analytics Platform", "resource_id": "ZZ-PCS-0001", "contracting_authority": "Glasgow City Council",
           "description": "A platform for reporting and analytics.", "date_published": "29/10/2026", "submission_deadline": "20/11/2026 12:00",
           "notice_type": "Contract Notice", "detail_url": "https://www.publiccontractsscotland.gov.uk/search/show/search_view.aspx?ID=ZZ1",
           "location": "Glasgow"}
    row.update(over)
    return row


def test_day_first_dates_are_read_as_uk_dates():
    assert ti._parse_uk_ts("20/11/2026 12:00") == datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    assert ti._parse_uk_ts("01/02/2026").month == 2   # 1 February, not 2 January
    assert ti._parse_uk_ts("2026-10-30").day == 30
    assert ti._parse_uk_ts("24-Nov-26") == datetime(2026, 11, 24, tzinfo=timezone.utc)   # PCS's own deadline format
    assert ti._parse_uk_ts("24 November 2026 10:30") == datetime(2026, 11, 24, 10, 30, tzinfo=timezone.utc)
    assert ti._parse_uk_ts("Notice Type:") is None and ti._parse_uk_ts("") is None


def test_a_notice_with_a_future_deadline_is_open_and_an_award_never_is():
    n = ti.normalise_scraped_row(pcs_row(), "zz_pcs", "Public Contracts Scotland", NOW)
    assert n["status"] == "open" and n["notice_type"] == "tender" and n["raw"]["source"] == "zz_pcs"
    assert n["published_at"].day == 29 and n["deadline_at"].day == 20
    award = ti.normalise_scraped_row(pcs_row(title="Award of Cleaning Services", submission_deadline=""), "zz_pcs", "PCS", NOW)
    assert award["status"] == "awarded"
    past = ti.normalise_scraped_row(pcs_row(submission_deadline="01/10/2026"), "zz_pcs", "PCS", NOW)
    assert past["status"] == "closed"
    none = ti.normalise_scraped_row(pcs_row(submission_deadline="Notice Type:"), "zz_pcs", "PCS", NOW)
    assert none["status"] == "closed", "a label where the deadline should be is no deadline, so not open"
    assert ti.normalise_scraped_row(pcs_row(resource_id=""), "zz_pcs", "PCS", NOW) is None


def test_stored_rows_are_found_by_words_and_come_back_as_live_rows():
    from tender_app.db import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        ti.ensure_tables(cur)
        cur.execute("DELETE FROM tender_notices WHERE source = 'pcs' AND resource_id LIKE 'ZZ-PCS-%'")
        rows = [ti.normalise_scraped_row(pcs_row(), "pcs", "Public Contracts Scotland", NOW),
                ti.normalise_scraped_row(pcs_row(resource_id="ZZ-PCS-0002", title="School catering contract", description="Meals"), "pcs", "PCS", NOW)]
        seen, new = ti.upsert_notices(cur, rows)
        assert (seen, new) == (2, 2)
        assert ti.upsert_notices(cur, rows[:1]) == (1, 0), "a notice already stored is refreshed, never duplicated"
        found = ti.search_notices(cur, ["business intelligence"], ["pcs"])
        mine = [r for r in found if str(r.get("resource_id", "")).startswith("ZZ-PCS-")]
        assert [r["resource_id"] for r in mine] == ["ZZ-PCS-0001"], mine
        assert mine[0]["source"] == "pcs" and mine[0]["title"].startswith("Business Intelligence") and mine[0]["indexed_at"]
        assert not [r for r in ti.search_notices(cur, ["business intelligence"], ["contracts_finder"])], "only the scraped portals are read from the index"
        either = ti.search_notices(cur, ["catering", "analytics"], ["pcs"])
        assert {r["resource_id"] for r in either if str(r["resource_id"]).startswith("ZZ-PCS-")} == {"ZZ-PCS-0001", "ZZ-PCS-0002"}
        assert ti.search_notices(cur, [], ["pcs"]) == []
    finally:
        conn.rollback()
        cur.execute("DELETE FROM tender_notices WHERE source = 'pcs' AND resource_id LIKE 'ZZ-PCS-%'")
        conn.commit()
        conn.close()


def test_an_ordinary_run_stops_at_the_first_page_it_already_holds():
    from tender_app.db import get_db_connection
    from etenders_scraper.sources import bravo_search as bs

    def fake_pages(site_root, source_id, label, max_pages, pause):
        yield [pcs_row(resource_id="ZZ-PCS-0101"), pcs_row(resource_id="ZZ-PCS-0102", title="Second business notice title")]
        yield [pcs_row(resource_id="ZZ-PCS-0103", title="Third business notice title")]

    real = bs.iter_newest_pcs_pages
    bs.iter_newest_pcs_pages = fake_pages
    conn = get_db_connection()
    try:
        ti.ensure_tables(conn.cursor())
        conn.cursor().execute("DELETE FROM tender_notices WHERE source = 'pcs' AND resource_id LIKE 'ZZ-PCS-01%'")
        conn.commit()
        first = ti.ingest_pcs(get_db_connection, stop_at_known=True, pause=0)
        assert first["ok"] and first["seen"] == 3 and first["new"] == 3, first
        again = ti.ingest_pcs(get_db_connection, stop_at_known=True, pause=0)
        assert again["ok"] and again["seen"] == 0 and "already stored" in again["error"], again
    finally:
        bs.iter_newest_pcs_pages = real
        cur = conn.cursor()
        cur.execute("DELETE FROM tender_notices WHERE source = 'pcs' AND resource_id LIKE 'ZZ-PCS-01%'")
        cur.execute("DELETE FROM index_runs WHERE source = 'pcs' AND started_at > now() - interval '5 minutes' AND rows_seen IN (0, 3)")
        conn.commit()
        conn.close()


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok   ", name)
    print(f"{n} passed")
