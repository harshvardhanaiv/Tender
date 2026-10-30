"""The Buyer page sends one sort to both the feed and the radar; the radar must accept every sort the page can send
(a live regression: sort=best_match came back 422 and the radar tab stayed empty).

Run: python tests/test_buyer_radar_sorts.py  (needs the dev database)
"""
import os
import sys
import time

os.environ["ENABLE_SCHEDULERS"] = "0"
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import create_app  # noqa: E402

PAGE_SORTS = ("best_match", "spend", "highest_value", "soonest_renewal", "days_left", "name", "buyer_name", "value")


def test_the_radar_accepts_every_sort_the_page_sends():
    import unittest
    try:
        c = create_app().test_client()
    except (Exception, SystemExit) as exc:
        raise unittest.SkipTest(f"no database: {exc}")
    with c.session_transaction() as s:
        s.update(logged_in=True, username="radar-sort@test.gov.uk", email="radar-sort@test.gov.uk", last_activity=time.time(), csrf_token="x")
    for sort in PAGE_SORTS:
        r = c.get(f"/api/buyers/opportunity-radar?sort={sort}&per_page=3&min_days=0")
        assert r.status_code == 200, (sort, r.status_code, r.get_json())
        assert len(r.get_json()["renewals"]) <= 3
    assert c.get("/api/buyers/opportunity-radar?sort=bogus").status_code == 422


if __name__ == "__main__":
    test_the_radar_accepts_every_sort_the_page_sends()
    print("ok    test_the_radar_accepts_every_sort_the_page_sends\n1 passed, 0 skipped, 0 failed")
