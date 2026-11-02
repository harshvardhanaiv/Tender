"""Re-test 4 P2: who the applicant is, from what the register published, the description, or the council's own page.
Run: python tests/test_planning_applicant.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etenders_scraper.planning import applicant as ap  # noqa: E402


def test_register_placeholders_are_not_applicants():
    for v in ("C/O Agent", "c/o the agent", "See source", "Not available", "N/A", "Confidential", "-", "", None):
        assert ap.clean_party(v) is None, v
    assert ap.clean_party("  Persimmon   Homes Ltd ") == "Persimmon Homes Ltd"


def test_a_company_the_description_names_is_found_and_a_planning_condition_is_not():
    assert ap.applicant_from_description("EIA Screening Opinion on behalf of Riverdale Developments, in respect of 300 homes") == "Riverdale Developments"
    assert ap.applicant_from_description("Scoping Report on behalf of Arrow Valley Solar Limited for a solar farm") == "Arrow Valley Solar Limited"
    assert ap.applicant_from_description("Discharge of condition 9 (Level 4 of the Code for Sustainable Homes)") is None
    assert ap.applicant_from_description("Erection of 12 dwellings submitted by Mr J Smith") is None, "an individual is never named"
    assert ap.applicant_from_description(None) is None


def test_resolve_prefers_the_register_and_says_where_a_name_came_from():
    assert ap.resolve({"applicant_company": "Barratt Homes", "description": "on behalf of Other Homes Ltd"}) == ("Barratt Homes", "register")
    assert ap.resolve({"applicant_company": "C/O Agent", "description": "submitted by Riverdale Developments"}) == ("Riverdale Developments", "description")
    assert ap.resolve({"applicant_company": "", "description": "52 dwellings"}) == (None, None)


def test_only_idox_records_have_a_further_information_page():
    u = "https://publicaccess.guildford.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=_GUILD_DCAPR_214736"
    assert ap.details_url(u).endswith("activeTab=details&keyVal=_GUILD_DCAPR_214736")
    assert ap.details_url("https://example.org/plan/1") is None and ap.details_url(None) is None
    assert ap.details_url("ftp://x/online-applications/applicationDetails.do?keyVal=1") is None


def test_the_further_information_page_is_read_and_placeholders_dropped():
    html = ('<table id="applicationDetails"><tr><th>Applicant Name</th><td>Cala Homes (Thames) Ltd</td></tr>'
            '<tr><th>Agent Company Name</th><td>Lichfields</td></tr></table>')
    assert ap.parse_details_page(html) == {"applicant": "Cala Homes (Thames) Ltd", "agent_company": "Lichfields"}
    assert ap.parse_details_page('<table id="applicationDetails"><tr><th>Applicant Name</th><td>C/O Agent</td></tr></table>') == {}
    assert ap.is_company("Cala Homes (Thames) Ltd") and not ap.is_company("Mr Paddy Hynes") and not ap.is_company("Jane Smith")


def test_the_find_applicant_endpoint_answers_from_the_description_and_says_why_when_it_cannot():
    import time
    os.environ.setdefault("ENABLE_SCHEDULERS", "0")
    os.environ.setdefault("ENABLE_EMAIL_SCHEDULER", "0")
    from server import create_app
    from tender_app.db import get_db_connection
    app = create_app()
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(logged_in=True, username="zz_applicant_probe", email="zz@test.invalid", last_activity=time.time(), csrf_token="t")
    conn = get_db_connection()
    cur = conn.cursor()
    ids = ("ZZ/APPL/1", "ZZ/APPL/2")
    try:
        cur.execute("DELETE FROM planning_applications WHERE id = ANY(%s)", (list(ids),))
        for pid, desc in zip(ids, ("Scoping Report on behalf of Riverdale Developments for 300 homes", "52 dwellings")):
            cur.execute("INSERT INTO planning_applications (id, authority, description, app_size, detail_url, applicant_company) VALUES (%s, 'ZZ', %s, 'Large', 'https://example.org/p', 'C/O Agent')", (pid, desc))
        conn.commit()
        post = lambda pid: c.post(f"/api/planning/application/{pid}/find-applicant", headers={"X-CSRF-Token": "t"})
        r = post(ids[0]).get_json()
        assert r["applicant"] == "Riverdale Developments" and r["source"] == "description", r
        r = post(ids[1]).get_json()
        assert r["applicant"] is None and "not on a portal" in r["reason"], r
        assert post("ZZ/APPL/none").status_code == 404
        shown = c.get(f"/api/planning/application/{ids[0]}").get_json()
        assert shown["applicant_company"] == "Riverdale Developments" and shown["applicant_source"] == "description"
        assert c.get(f"/api/planning/application/{ids[1]}").get_json()["applicant_company"] is None, "a placeholder is not an applicant"
    finally:
        cur.execute("DELETE FROM planning_applications WHERE id = ANY(%s)", (list(ids),))
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
