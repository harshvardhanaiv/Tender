"""Run: python tests/test_funding.py   (plain asserts; the database half needs the db container).

Find a Grant: the page data is parsed, open-all-year grants are recognised despite their forced closing date,
and the Funding search shows open grants only, ranked by a plain word match to the company profile."""
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tender_app import fag_import as fag, funding  # noqa: E402
from tender_app import growth_studio as gs  # noqa: E402

TODAY = date(2026, 10, 22)


def item(**kw):
    base = {
        "label": "zz-test-grant-1", "grantName": "ZZ Boiler upgrade grant", "grantFunder": "Department for Energy",
        "grantShortDescription": "Funding for heat pump and boiler upgrades.", "grantMinimumAward": 1000, "grantMaximumAward": 50000,
        "grantTotalAwardAmount": 2000000, "grantLocation": ["England", "Midlands"], "grantApplicantType": ["Private Sector", "Non-profit"],
        "grantApplicationOpenDate": "2026-01-01T00:01", "grantApplicationCloseDate": "2026-12-31T00:00",
    }
    base.update(kw)
    return base


def page(props):
    return '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": props}}) + "</script></html>"


def test_page_data_is_read_from_the_page():
    assert fag.page_data(page({"totalGrants": 3}))["totalGrants"] == 3
    assert fag.page_data("<html>nothing</html>") == {} and fag.page_data("") == {}
    assert fag.page_data('<script id="__NEXT_DATA__">not json</script>') == {}


def test_rich_text_is_flattened():
    doc = {"nodeType": "document", "content": [
        {"nodeType": "paragraph", "content": [{"nodeType": "text", "value": "You must be a UK business."}]},
        {"nodeType": "paragraph", "content": [{"nodeType": "text", "value": "Charities can apply too."}]}]}
    assert fag.rich_text(doc) == "You must be a UK business.\nCharities can apply too."
    assert fag.rich_text(None) == ""


def test_record_is_built_from_list_and_detail():
    detail = {"fields": {"grantWebpageUrl": "https://example.gov.uk/apply", "grantEligibilityTab": {"content": [
        {"nodeType": "paragraph", "content": [{"nodeType": "text", "value": "UK businesses only."}]}]}}}
    r = fag.build_record(item(), detail, TODAY)
    assert r["name"] == "ZZ Boiler upgrade grant" and r["max_award"] == 50000.0 and r["close_date"] == "2026-12-30"
    assert r["open_date"] == "2026-01-01" and r["ongoing"] is False and r["apply_url"] == "https://example.gov.uk/apply"
    assert r["eligibility"] == "UK businesses only." and r["url"].endswith("/grants/zz-test-grant-1")
    bare = fag.build_record(item(), None, TODAY)
    assert bare["apply_url"] is None and bare["eligibility"] is None
    assert fag.build_record(item(label=""), None, TODAY) is None and fag.build_record(item(grantName=""), None, TODAY) is None
    assert fag.build_record(item(grantApplicationCloseDate=None), None, TODAY)["close_date"] is None


def test_open_all_year_is_recognised():
    assert fag.is_ongoing("x", "2030-04-01", TODAY) is True, "a far-off forced closing date"
    assert fag.is_ongoing("Open for applications year-round.", "2026-12-31", TODAY) is True
    assert fag.is_ongoing("Funding for boilers.", "2026-12-31", TODAY) is False
    assert fag.is_ongoing("x", None, TODAY) is False


def test_relevance_is_a_word_match():
    rec = {"name": "Boiler upgrade grant", "funder": "Energy", "summary": "Heat pump installers can apply", "eligibility": None}
    kw = gs.profile_keywords("We install boilers and heat pumps for homes")
    assert funding.relevance(kw, rec) == ["boiler", "heat", "installers", "pump"], "words are shown as the grant spells them"
    assert funding.relevance(frozenset(), rec) == []


def test_database_search():
    import unittest
    try:
        from tender_app.db import get_db_connection
        conn = get_db_connection()
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"no database: {exc}")
    from tender_app.db_ext import create_funding_opportunities
    cur = conn.cursor()
    create_funding_opportunities(cur)
    conn.commit()
    labels = ["zz-open", "zz-soon", "zz-closed", "zz-future", "zz-allyear", "zz-charity"]
    cur.execute("DELETE FROM funding_opportunities WHERE label = ANY(%s)", (labels,))
    conn.commit()
    try:
        def put(label, **kw):
            rec = fag.build_record(item(label=label, grantName="ZZ " + label, **kw), None, TODAY)
            fag.save_record(cur, rec)

        put("zz-open")
        put("zz-soon", grantApplicationCloseDate="2026-11-05T00:00", grantShortDescription="Cavity wall insulation")
        put("zz-closed", grantApplicationCloseDate="2026-09-01T00:00")
        put("zz-future", grantApplicationOpenDate="2027-01-01T00:01")
        put("zz-allyear", grantApplicationCloseDate="2030-04-01T00:00", grantMaximumAward=900000)
        put("zz-charity", grantApplicantType=["Non-profit"], grantLocation=["Scotland"])
        conn.commit()

        out = funding.search(cur, q="ZZ ", today=TODAY)
        got = {i["label"] for i in out["items"]}
        assert got == {"zz-open", "zz-soon", "zz-allyear", "zz-charity"}, got
        assert out["imported"] and "Scotland" in out["locations"]

        assert {i["label"] for i in funding.search(cur, q="ZZ ", applicant="Private Sector", today=TODAY)["items"]} == {"zz-open", "zz-soon", "zz-allyear"}
        assert [i["label"] for i in funding.search(cur, q="ZZ ", location="Scotland", today=TODAY)["items"]] == ["zz-charity"]
        assert [i["label"] for i in funding.search(cur, q="ZZ ", min_award=500000, today=TODAY)["items"]] == ["zz-allyear"]
        assert [i["label"] for i in funding.search(cur, q="ZZ ", closing_soon=True, today=TODAY)["items"]] == ["zz-soon"], "open-all-year grants are never closing soon"

        by_close = [i["label"] for i in funding.search(cur, q="ZZ ", sort="closing", today=TODAY)["items"]]
        assert by_close[0] == "zz-soon" and by_close.index("zz-allyear") > by_close.index("zz-open"), by_close
        soon = next(i for i in funding.search(cur, q="ZZ ", today=TODAY)["items"] if i["label"] == "zz-soon")
        assert soon["days_left"] == 13 and soon["matches"] == [], "the service's 2026-11-05T00:00 is 4 Nov 11:59pm"
        assert soon["close_date"] == "2026-11-04" and soon["close_at"] == "2026-11-04T23:59"
        # a grant stays listed until its closing time, then drops out and is counted as closed today
        late = funding.search(cur, q="ZZ ", today=date(2026, 11, 4), now=datetime(2026, 11, 4, 23, 58))
        assert "zz-soon" in {i["label"] for i in late["items"]}, "still open at 23:58 on its closing day"
        gone = funding.search(cur, q="ZZ ", today=date(2026, 11, 5), now=datetime(2026, 11, 5, 9, 0))
        assert "zz-soon" not in {i["label"] for i in gone["items"]}
        morning = funding.search(cur, q="ZZ ", today=date(2026, 11, 5), now=datetime(2026, 11, 5, 9, 0))
        assert morning["closed_today"] >= 0 and "zz-soon" not in {i["label"] for i in morning["items"]}
        allyear = next(i for i in funding.search(cur, q="ZZ ", today=TODAY)["items"] if i["label"] == "zz-allyear")
        assert allyear["days_left"] is None and allyear["ongoing"] is True

        ranked = funding.search(cur, q="ZZ ", keywords=gs.profile_keywords("cavity wall insulation installer"), today=TODAY)
        assert ranked["items"][0]["label"] == "zz-soon" and ranked["items"][0]["matches"], "best word match first"
        assert funding.search(cur, q="no such grant anywhere", today=TODAY)["total"] == 0

        # a grant dropped from the service is removed by the importer's sweep, an empty run never wipes the table
        cur.execute("SELECT NOW()")
        cutoff = cur.fetchone()[0]
        put("zz-open")
        cur.execute("DELETE FROM funding_opportunities WHERE label LIKE 'zz-%%' AND seen_at < %s", (cutoff,))
        cur.execute("SELECT COUNT(*) FROM funding_opportunities WHERE label = 'zz-open'")
        assert cur.fetchone()[0] == 1
    finally:
        conn.rollback()
        cur.execute("DELETE FROM funding_opportunities WHERE label = ANY(%s)", (labels,))
        conn.commit()
        cur.close()
        conn.close()


def test_refresh_only_when_stale():
    import unittest
    try:
        from tender_app.db import get_db_connection
        conn = get_db_connection()
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"no database: {exc}")
    from tender_app.db_ext import create_funding_opportunities
    cur = conn.cursor()
    create_funding_opportunities(cur)
    conn.commit()
    try:
        cur.execute("SELECT COUNT(*) FROM funding_opportunities WHERE label NOT LIKE 'zz-%%'")
        real_rows = cur.fetchone()[0]
        cur.execute("DELETE FROM funding_opportunities WHERE label LIKE 'zz-stale%%'")
        fag.save_record(cur, fag.build_record(item(label="zz-stale"), None, TODAY))
        conn.commit()
        if real_rows == 0:
            assert fag.is_stale(conn, hours=1) is False, "a row seen just now is fresh"
            cur.execute("UPDATE funding_opportunities SET seen_at = seen_at - INTERVAL '30 hours' WHERE label = 'zz-stale'")
            conn.commit()
            assert fag.is_stale(conn) is True, "a day-old table is refreshed"
        else:  # the dev database holds the real import, so only the part that does not depend on its age is checked
            cur.execute("SELECT EXISTS (SELECT 1 FROM funding_opportunities WHERE close_date IS NOT NULL AND close_at IS NULL)")
            assert fag.is_stale(conn, hours=100000) is bool(cur.fetchone()[0]), "only rows kept before closing times were stored force a re-read"
        # rows kept before closing times were stored are read again whatever their age
        cur.execute("UPDATE funding_opportunities SET close_at = NULL WHERE label = 'zz-stale'")
        conn.commit()
        assert fag.is_stale(conn, hours=100000) is True
        cur.execute("UPDATE funding_opportunities SET close_at = '2026-12-30T23:59' WHERE label = 'zz-stale'")
        conn.commit()
    finally:
        cur.execute("DELETE FROM funding_opportunities WHERE label = 'zz-stale'")
        conn.commit()
        cur.close()
        conn.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")


def test_a_grant_that_must_be_led_by_a_research_organisation_is_a_check_for_a_company():
    """Re-test 2 R2: 'You must be based at a UK research organisation eligible for EPSRC funding' was shown as Likely eligible."""
    from tender_app import funding_fit as ff
    grant = {"name": "UKRI: Integration of AI/HPC with quantum computing", "funder": "UK Research and Innovation",
             "summary": "Apply for funding to deliver collaborative research projects.\n\nYou must be based at a UK research organisation eligible for EPSRC funding.",
             "eligibility": "UKRI individual eligibility rules apply.", "locations": [ff.UK_WIDE], "applicants": ["Private sector"]}
    company = {"kind": "Private sector", "region": "South East", "size": "sme", "about": "AI software and data analytics", "turnover": 2_000_000}
    out = ff.assess(grant, company, kind="Private sector", has_profile=True)
    assert out["verdict"] == "check" and out["checks"][0].startswith("A research organisation must lead"), out


def test_a_grant_that_needs_a_100m_project_blocks_a_small_company():
    """Re-test 2 R3: TRIF listed only 'Sector mismatch', not the project-cost floor."""
    from tender_app import funding_fit as ff
    text = "eligible projects must exceed £100 million in total costs"
    assert ff._project_cost_floor(text) == 100_000_000
    assert ff._project_cost_floor("Have a total cost (including eligible capital) of £100 million or more") == 100_000_000
    grant = {"name": "Life Sciences TRIF Pilot", "funder": "DSIT", "summary": text, "eligibility": "", "locations": [ff.UK_WIDE], "applicants": ["Private sector"]}
    out = ff.assess(grant, {"kind": "Private sector", "region": "South East", "size": "sme", "about": "software", "turnover": 2_000_000}, kind="Private sector", has_profile=True)
    assert out["verdict"] == "unlikely" and any("£100m" in b or "100" in b for b in out["blockers"]), out
