"""Supplier search says how many companies it lists when they all fit on page 1; Funding ?location= is an alias of ?where=.

Run: python tests/test_report6_count_alias.py  (needs the dev database)
"""
import os
import sys
import time

os.environ["ENABLE_SCHEDULERS"] = "0"
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import create_app  # noqa: E402


def _client():
    import unittest
    try:
        c = create_app().test_client()
    except (Exception, SystemExit) as exc:
        raise unittest.SkipTest(f"no database: {exc}")
    with c.session_transaction() as s:
        s.update(logged_in=True, username="r6@test.gov.uk", email="r6@test.gov.uk", last_activity=time.time(), csrf_token="x")
    return c


def test_a_search_that_fits_one_page_reports_the_companies_it_lists():
    c = _client()
    for q in ("IBM", "serco", "Capita plc"):
        j = c.get(f"/api/suppliers/search?q={q}&per_page=100&page=1").get_json()
        if j["total_count"] <= 100:
            assert j["total_count"] == len(j["suppliers"]), (q, j["total_count"], len(j["suppliers"]))
            assert j["total_pages"] <= 1


def test_funding_location_is_an_alias_of_where():
    c = _client()
    a = c.get("/api/funding?where=Scotland").get_json()
    b = c.get("/api/funding?location=Scotland").get_json()
    assert a["total"] == b["total"] and [i["label"] for i in a["items"]] == [i["label"] for i in b["items"]]


if __name__ == "__main__":
    test_a_search_that_fits_one_page_reports_the_companies_it_lists()
    test_funding_location_is_an_alias_of_where()
    print("2 passed")
