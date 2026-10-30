"""Buyer search suggests the near-miss spelling when nothing matches (re-test 2 B5: "Mancester" returned nothing).

Run: python tests/test_buyer_did_you_mean.py  (needs the dev database)
"""
import os
import sys
import time
import unittest

os.environ["ENABLE_SCHEDULERS"] = "0"
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _client():
    from server import create_app
    try:
        c = create_app().test_client()
    except (Exception, SystemExit) as exc:
        raise unittest.SkipTest(f"no database: {exc}")
    with c.session_transaction() as s:
        s.update(logged_in=True, username="dym@test.gov.uk", email="dym@test.gov.uk", last_activity=time.time(), csrf_token="x")
    return c


def test_a_misspelt_buyer_name_gets_a_suggestion_and_a_real_one_does_not():
    c = _client()
    j = c.get("/api/buyers/opportunity-feed?q=Mancester").get_json()
    assert j["total_matches"] == 0 and j["did_you_mean"] and j["did_you_mean"]["name"].lower().startswith("manchester"), j.get("did_you_mean")
    assert c.get("/api/buyers/opportunity-feed?q=Leeds").get_json()["did_you_mean"] is None
    assert c.get("/api/buyers/opportunity-feed?q=zzzqqq").get_json()["did_you_mean"] is None


if __name__ == "__main__":
    test_a_misspelt_buyer_name_gets_a_suggestion_and_a_real_one_does_not()
    print("1 passed")
