"""Re-test 3, second batch: section addresses (G0), the in-app 404 page, and the grant project-cost wording (FN3).

Run: python tests/test_retest3_batch2.py  (needs the dev database)
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
        s.update(logged_in=True, username="routes@test.gov.uk", email="routes@test.gov.uk", last_activity=time.time(), csrf_token="x")
    return c


def test_each_section_has_an_address_that_serves_the_app():
    c = _client()
    for path in ("/growth", "/answers", "/documents", "/history", "/pipeline", "/planning", "/suppliers"):
        r = c.get(path)
        assert r.status_code == 200 and b"<title>" in r.data, (path, r.status_code)


def test_an_unknown_page_shows_the_app_with_a_404_but_files_and_the_api_stay_plain():
    c = _client()
    r = c.get("/growth-studio")
    assert r.status_code == 404 and b"app.js" in r.data, "an unknown page must load the app so it can say 'Page not found'"
    r = c.get("/definitely-missing.js")
    assert r.status_code == 404 and b"app.js" not in r.data, "a missing file stays a plain 404"
    r = c.get("/api/definitely-missing")
    assert r.status_code == 404 and b"app.js" not in r.data


def test_the_project_cost_rule_is_worded_as_project_cost_not_turnover():
    from tender_app import funding_fit as ff
    text = "Eligible projects must exceed £100 million in total costs."
    assert ff._project_cost_floor(text) == 100_000_000
    import inspect
    src = inspect.getsource(ff)
    assert "add your turnover to your profile" not in src
    assert "must cost " in src and "Confirm before applying" in src


def test_a_check_verdict_always_has_something_to_check_and_off_sector_grants_are_unlikely():
    """Re-test 3 FN4: 37 grants said 'Check' with no reasons, checks or blockers (Access to Elected Office Fund, SuDS in Schools ...)."""
    from tender_app import funding_fit as ff
    it_firm = ff.applicant_from_profile({"business_type": "J", "enterprise_type": "small", "company_type": "limited"}, "software development")
    generic = {"name": "Community resilience programme", "summary": "Supports local projects.", "eligibility": "", "locations": [ff.UK_WIDE], "applicants": []}
    out = ff.assess(generic, it_firm, has_profile=True)
    assert out["verdict"] == "check" and out["checks"], out
    out = ff.assess(generic, it_firm, has_profile=False)
    assert out["verdict"] == "check" and out["checks"], out
    elected = ff.assess({"name": "Access to Elected Office Fund", "summary": "Supports disabled people standing for election.", "locations": [ff.UK_WIDE], "applicants": []}, it_firm, has_profile=True)
    assert elected["verdict"] == "unlikely" and elected["blockers"], elected
    schools = ff.assess({"name": "SuDS in Schools", "summary": "Sustainable drainage in schools.", "locations": [ff.UK_WIDE], "applicants": []}, it_firm, has_profile=True)
    assert schools["verdict"] == "unlikely" and "education" in schools["blocker"], schools
    ai = ff.assess({"name": "AI adoption grant", "summary": "Helps firms adopt AI and software.", "locations": [ff.UK_WIDE], "applicants": []}, it_firm, has_profile=True)
    assert ai["verdict"] != "unlikely", ai


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok   ", name)
            except unittest.SkipTest as exc:
                print("skip ", name, exc)
            n += 1
    print(f"{n} run")
