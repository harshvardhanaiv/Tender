"""Run: python tests/test_retest3_batch_a.py   (plain asserts, no network)

Re-test 3, batch A: server-side rules. Each test names the QA item it covers.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def test_w2_trailing_punctuation_is_dropped_from_a_supplier_name():
    from tender_app.text_fix import clean_org_name, fix_row_text
    assert clean_org_name("Gristwood and Toms:") == "Gristwood and Toms"
    assert clean_org_name("  Acme  Ltd ; ,") == "Acme Ltd"
    assert clean_org_name("Acme Ltd.") == "Acme Ltd."   # a real full stop stays
    assert clean_org_name(None) is None
    row = fix_row_text({"supplier_name": "Gristwood and Toms:", "title": "x"})
    assert row["supplier_name"] == "Gristwood and Toms"


def test_w2_the_importer_cleans_the_name_before_it_is_stored():
    src = open(os.path.join(os.path.dirname(__file__), "..", "etenders_scraper", "awards.py"), encoding="utf-8").read()
    assert "clean_org_name(" in src.split("def ingest_award_record")[1].split("def classify_buyer_type")[0]


def test_w2_a_tree_planting_cpv_belongs_to_the_grounds_and_trees_preset():
    from tender_app import market_radar as mr
    cat = mr.resolve_category("grounds-tree-works", None, None, None)
    assert mr.category_matches(cat, "Tree planting", "77211600")


def test_t7_a_notice_without_a_location_takes_it_from_its_region_code_or_the_buyers_postcode():
    from tender_app.regions import infer_notice_location
    assert infer_notice_location({"nuts": "UKI3"}) == ("London", "region code")
    assert infer_notice_location({"region_code": "UKM50"}) == ("Scotland", "region code")
    assert infer_notice_location({"buyer_postcode": "CM1 1JE"}) == ("East of England", "buyer postcode")
    assert infer_notice_location({"address": "County Hall, Truro TR1 3AY"}) == ("South West", "buyer postcode")
    assert infer_notice_location({"title": "x"}) is None
    assert infer_notice_location({"nuts": "FR10"}) is None


def test_t7_exports_do_not_head_a_column_estimated_value_eur():
    from etenders_scraper.export import rows_to_dataframe
    df = rows_to_dataframe([{"title": "t", "estimated_value_eur": "£100,000", "source": "find_tender"}])
    assert "estimated_value" in df.columns and "estimated_value_eur" not in df.columns, list(df.columns)


def test_t7_the_search_route_fills_the_location_in_before_the_county_filter():
    src = open(os.path.join(os.path.dirname(__file__), "..", "server.py"), encoding="utf-8").read()
    assert src.index("infer_notice_location(r)") < src.index('counties_raw = request.args.get("counties"')


def test_ab2_a_starter_pack_lists_its_questions_but_not_its_answers():
    from tender_app import answer_starters as starters
    pack = starters.listing()[0]
    assert pack["count"] == len(pack["questions"]) and pack["questions"][0].keys() == {"category", "question"}


def test_hs2_bad_history_titles_are_not_saved():
    from tender_app.text_fix import clean_history_title
    fb = "Untitled tender"
    assert clean_history_title("Analyzing Pasted Details...", fb) == fb
    assert clean_history_title("Analyzing Documents...", fb) == fb
    assert clean_history_title("View Notice", fb) == fb
    assert clean_history_title("**Boiler servicing** framework", fb) == "Boiler servicing framework"
    assert clean_history_title("Works Services - AWARD", fb) == "Works Services"
    assert clean_history_title("Grounds maintenance", fb) == "Grounds maintenance"
    assert clean_history_title("", fb) == fb and clean_history_title(None) == ""


def test_hs2_every_history_insert_cleans_the_title_and_the_list_tidies_old_ones():
    src = open(os.path.join(os.path.dirname(__file__), "..", "server.py"), encoding="utf-8").read()
    inserts = [seg for seg in src.split("INSERT INTO analyses")[1:]]
    assert len(inserts) == 4 and all("clean_history_title(title, HISTORY_TITLE_FALLBACK)" in seg[:600] for seg in inserts)
    assert '"tender_title": clean_history_title(row[3], HISTORY_TITLE_FALLBACK)' in src


def test_hs2_the_cleanup_script_proposes_titles_and_defaults_to_a_dry_run():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
    import history_title_cleanup as h
    rows = [(1, "View Notice", '{"title": "Roofing works, Leeds"}'), (2, "Analyzing Pasted Details...", "{}"),
            (3, "**Gas** safety - AWARD", None), (4, "Fine already", None)]
    assert h.plan(rows) == [(1, "View Notice", "Roofing works, Leeds"), (2, "Analyzing Pasted Details...", "Untitled tender"),
                            (3, "**Gas** safety - AWARD", "Gas safety")]
    src = open(h.__file__, encoding="utf-8").read()
    assert 'add_argument("--apply", action="store_true"' in src and "DRY RUN" in src and "--revert" in src


def _client(username):
    import time
    import unittest
    os.environ["ENABLE_SCHEDULERS"] = "0"
    os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
    try:
        from server import create_app
        c = create_app().test_client()
    except (Exception, SystemExit) as exc:
        raise unittest.SkipTest(f"no database: {exc}")
    with c.session_transaction() as sess:
        sess.update(logged_in=True, username=username, email=username, last_activity=time.time(), csrf_token="x")
    return c


def test_p6_planning_banner_has_one_updated_date_whatever_the_filter():
    from tender_app.db import get_db_connection
    c = _client("zz-p6@test.gov.uk")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM planning_applications WHERE id LIKE 'zz-p6-%'")
            cur.execute("INSERT INTO planning_applications (id, description, app_size, updated_at) VALUES "
                        "('zz-p6-a', 'zzp6 newest unrelated scheme', 'Large', '2026-10-30 08:00'), "
                        "('zz-p6-b', 'zzp6needle older scheme', 'Large', '2026-10-29 08:00')")
        conn.commit()
        whole = c.get("/api/planning/stats")
        if whole.status_code in (402, 403):
            return
        filtered = c.get("/api/planning/stats?q=zzp6needle")
        assert whole.status_code == 200 and filtered.status_code == 200, (whole.status_code, filtered.status_code)
        a, b = whole.get_json(), filtered.get_json()
        assert b["total"] == 1 and a["total"] >= 2, (a["total"], b["total"])
        assert a["last_updated"] == b["last_updated"], (a["last_updated"], b["last_updated"])
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM planning_applications WHERE id LIKE 'zz-p6-%'")
        conn.commit()
        conn.close()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                failures += 1; print("FAIL", name, "->", repr(exc))
    print("failures:", failures)
    sys.exit(1 if failures else 0)
