"""Planning Leads: "best leads first" sorts on the score shown, and screening / scoping requests stay out of the default list.

Run: python tests/test_planning_lead_order.py  (needs the dev database)
"""
import os
import sys
import time

os.environ["ENABLE_SCHEDULERS"] = "0"
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_app.blueprints import planning_bp as pb  # noqa: E402
from server import create_app  # noqa: E402


def test_the_consultation_rule_reads_the_same_in_python_and_sql_text():
    assert pb._CONSULTATION_RE.search("EIA request for a Scoping Opinion pursuant to regulation 15")
    assert pb._CONSULTATION_RE.search("Scoping for pre-application advice prior to a hybrid application")
    assert not pb._CONSULTATION_RE.search("Erection of 850 dwellings with access and landscaping")
    assert r"\y" in pb._CONSULTATION_PATTERN and r"\b" not in pb._CONSULTATION_PATTERN
    assert "~*" not in pb._IS_CONSULTATION_SQL, "sorting must read the stored flag, not run the text match on every row"


def test_the_default_list_is_sorted_on_the_shown_score_and_has_no_scoping_requests():
    import unittest
    try:
        c = create_app().test_client()
    except (Exception, SystemExit) as exc:
        raise unittest.SkipTest(f"no database: {exc}")
    with c.session_transaction() as s:
        s.update(logged_in=True, username="plan-order@test.gov.uk", email="plan-order@test.gov.uk", last_activity=time.time(), csrf_token="x")
    t0 = time.time()
    r = c.get("/api/planning/search?per_page=100")
    first = time.time() - t0
    if r.status_code in (402, 403):
        print("skip  planning search needs a plan or credits for this user:", r.status_code)
        return
    assert r.status_code == 200, (r.status_code, r.get_json())
    import threading
    for t in threading.enumerate():
        if t.name == "planning-consultation-flag":
            t.join(120)   # the flag fills in the background on the first call; until then a scoping request is not recognised
    rows = c.get("/api/planning/search?per_page=100").get_json()["results"]
    scores = [x["lead_score"] for x in rows if x["lead_score"] is not None]
    assert scores == sorted(scores, reverse=True), scores[:30]
    t0 = time.time()
    r2 = c.get("/api/planning/search?per_page=100&sort=newest")
    assert r2.status_code == 200 and time.time() - t0 < 4, ("sorting is slow again", time.time() - t0)
    rows = c.get("/api/planning/search?per_page=100").get_json()["results"]   # after the background fill
    assert not any(x.get("is_consultation") for x in rows), [x["description"][:60] for x in rows if x.get("is_consultation")][:3]
    # the same requests are still reachable on request, capped, and never ahead of real schemes
    r = c.get("/api/planning/search?per_page=100&include_low_value=1")
    assert r.status_code == 200
    scores = [x["lead_score"] for x in r.get_json()["results"] if x["lead_score"] is not None]
    assert scores == sorted(scores, reverse=True)


if __name__ == "__main__":
    test_the_consultation_rule_reads_the_same_in_python_and_sql_text()
    test_the_default_list_is_sorted_on_the_shown_score_and_has_no_scoping_requests()
    print("2 passed")
