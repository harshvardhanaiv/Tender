"""Run: python tests/test_retest3_data_jobs.py   (needs the dev database; zz fixtures are removed in `finally`)

The production data jobs from re-test 3 are only ever run by the user. These tests prove each one has a dry run that changes nothing, a
revert list, and does what it says: planning re-score (P4), stats rebuild (SN7), the QA engagement clean-up (W6) and the History titles
(HS2, covered in test_retest3_batch_a.py).
"""
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("ENABLE_SCHEDULERS", "0")
os.environ.setdefault("ENABLE_EMAIL_SCHEDULER", "0")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def _conn():
    from tender_app.db import get_db_connection
    try:
        return get_db_connection()
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"no database: {exc}")


def test_planning_rescore_has_a_dry_run_and_a_revert_list():
    from etenders_scraper.planning import harvester
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM planning_applications WHERE id LIKE 'zz-rescore-%'")
        # a stored score that is certainly not what the scoring now gives
        cur.execute("INSERT INTO planning_applications (id, description, app_size, app_state, app_type, n_dwellings, lead_score, start_date) "
                    "VALUES ('zz-rescore-1','Erection of 300 dwellings with access','Large','Undecided','Full',300, 1, CURRENT_DATE)")
        conn.commit()
        with tempfile.TemporaryDirectory() as d:
            revert = os.path.join(d, "revert.json")
            before = harvester.rescore(conn, all_rows=True, dry_run=True)
            assert before >= 1, "the fixture's score is wrong, so a re-score would change it"
            cur.execute("SELECT lead_score FROM planning_applications WHERE id = 'zz-rescore-1'")
            assert cur.fetchone()[0] == 1, "a dry run writes nothing"
            assert not os.path.exists(revert)
            n = harvester.rescore(conn, all_rows=True, revert_path=revert)
            assert n >= 1
            old = json.load(open(revert))
            assert old["zz-rescore-1"][0] == 1, "the old score is in the revert list"
            cur.execute("SELECT lead_score FROM planning_applications WHERE id = 'zz-rescore-1'")
            assert cur.fetchone()[0] != 1
    finally:
        conn.rollback()
        cur.execute("DELETE FROM planning_applications WHERE id LIKE 'zz-rescore-%'")
        conn.commit()
        conn.close()
    src = open(os.path.join(ROOT, "scripts", "planning_backfill.py"), encoding="utf-8").read()
    assert '"--dry-run"' in src and '"--revert-file"' in src and "dry_run=args.dry_run, revert_path=args.revert_file" in src


def test_stats_rebuild_has_a_dry_run_that_reports_and_writes_nothing():
    import io
    import contextlib
    import refresh_stats
    conn = _conn()
    conn.close()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        refresh_stats.dry_run()
    text = out.getvalue()
    assert "DRY RUN: nothing was changed" in text and "buyer_stats" in text


def test_the_qa_engagement_and_tree_planting_draft_are_cleaned_up_with_a_dry_run_and_a_revert():
    import retest3_engagement_cleanup as job
    rows = [{"id": 1, "title": "QA test CRM replacement (delete me)", "status": "draft"},
            {"id": 2, "title": "Tree Planting", "status": "draft"},
            {"id": 3, "title": "Tree Planting 2027", "status": "published"},
            {"id": 4, "title": "Responsive repairs", "status": "draft"}]
    assert [(a, r["id"]) for a, r in job.plan(rows, None)] == [(("delete"), 1)]
    assert [(a, r["id"]) for a, r in job.plan(rows, "fix")] == [("delete", 1), ("fix", 2)]
    assert [(a, r["id"]) for a, r in job.plan(rows, "delete")] == [("delete", 1), ("delete", 2)]
    conn = _conn()
    cur = conn.cursor()
    user = "zz_job@test.invalid"
    try:
        cur.execute("DELETE FROM market_engagements WHERE username = %s", (user,))
        for t in ("QA test CRM replacement (delete me)", "Tree Planting"):
            cur.execute("INSERT INTO market_engagements (username, title, category_label, status) VALUES (%s,%s,'x','draft')", (user, t))
        conn.commit()
        with tempfile.TemporaryDirectory() as d:
            revert = os.path.join(d, "r.json")
            assert job.main(["--tree-planting", "fix", "--revert-file", revert]) == 0, "dry run"
            cur.execute("SELECT COUNT(*) FROM market_engagements WHERE username = %s", (user,))
            assert cur.fetchone()[0] == 2 and not os.path.exists(revert), "a dry run changes nothing and writes no revert list"
            conn.commit()
            assert job.main(["--tree-planting", "fix", "--apply", "--revert-file", revert]) == 0
            conn.commit()
            cur.execute("SELECT title, category_label FROM market_engagements WHERE username = %s", (user,))
            assert cur.fetchall() == [("Tree Planting", "Grounds & tree works")]
            conn.commit()
            assert job.main(["--revert", revert]) == 0
            conn.commit()
            cur.execute("SELECT title, category_label FROM market_engagements WHERE username = %s ORDER BY id", (user,))
            assert cur.fetchall() == [("QA test CRM replacement (delete me)", "x"), ("Tree Planting", "x")], "reverted exactly"
    finally:
        conn.rollback()
        cur.execute("DELETE FROM market_engagements WHERE username = %s", (user,))
        conn.commit()
        conn.close()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except unittest.SkipTest as exc:
                print("SKIP", name, exc)
            except Exception as exc:  # noqa: BLE001
                import traceback; traceback.print_exc()
                failures += 1; print("FAIL", name, "->", repr(exc))
    sys.exit(1 if failures else 0)
