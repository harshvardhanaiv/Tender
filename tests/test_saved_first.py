"""Re-test 6 TN4/T3: Find a Tender and Contracts Finder notices saved earlier fill the table while the live call is still running,
so the first poll shows rows at once and a throttled portal never leaves it empty.

Run: python tests/test_saved_first.py"""
import os
import sys
import time
import unittest

os.environ.setdefault("ENABLE_SCHEDULERS", "0")
os.environ.setdefault("ENABLE_EMAIL_SCHEDULER", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_helper_picks_the_portals_that_have_nothing_yet():
    import server
    pick = server._ocds_portals_needing_saved
    row = {"source": "find_tender"}
    # still running, no rows: fill from the saved copy
    assert pick({"source_progress": {"find_tender": "first_page", "contracts_finder": "pending"}}, [], "all") == ["find_tender", "contracts_finder"]
    # a running portal that already returned rows is left to the live results
    assert pick({"source_progress": {"find_tender": "fetching_all", "contracts_finder": "complete"}}, [row], "all") == []
    # a failed portal is filled even when it is no longer running
    assert pick({"errors": {"find_tender": "HTTP 429"}, "source_progress": {"find_tender": "error"}}, [], "all") == ["find_tender"]
    # a portal outside the search is never filled
    assert pick({"source_progress": {"find_tender": "pending", "pcs": "pending"}}, [], "pcs") == []
    assert pick({"source_progress": {"find_tender": "pending"}}, [], "find_tender,pcs") == ["find_tender"]


def test_first_poll_shows_saved_rows_while_the_portal_runs():
    import server
    from server import create_app
    from etenders_scraper.sources import progressive_search as ps
    from tender_app import tender_index
    saved = [{"source": "find_tender", "source_label": "Find a Tender", "resource_id": "zz-saved-1", "title": "Saved data analytics framework",
              "description": "", "contracting_authority": "Zz Council", "status": "Active", "detail_url": "https://example.test/zz-saved-1",
              "from_saved": True}]
    real = (tender_index.stored_rows, server.upsert_tenders, server.search_tenders_db)
    tender_index.stored_rows = lambda cur, terms, sources, limit=300: [dict(r) for r in saved if r["source"] in sources]
    server.upsert_tenders = lambda rows: None
    server.search_tenders_db = lambda q, limit=500: []
    try:
        app = create_app()
        app.config["TESTING"] = True
        job = ps.SearchJob(job_id="zz-saved-first", query="zz saved first", scope="find_tender", status="loading", rows=[],
                           meta={"errors": {}, "source_counts": {"find_tender": 0}, "source_progress": {"find_tender": "first_page"},
                                 "source_timings_sec": {}, "source_totals_hint": {}, "loading_sources": ["find_tender"],
                                 "pending_sources": ["find_tender"], "total_sources_count": 1, "total": 0, "totalExact": False})
        ps._JOBS[job.job_id] = job
        server._JOB_BY_CACHE_KEY["zz saved first:find_tender"] = job.job_id
        c = app.test_client()
        with c.session_transaction() as s:
            s.update(logged_in=True, username="zz_saved_first", email="zz_saved_first@test.invalid", last_activity=time.time(), csrf_token="x")
        r = c.get("/api/search?q=zz+saved+first&scope=find_tender&progressive=1&jobId=zz-saved-first")
        assert r.status_code == 200, r.status_code
        data = r.get_json()
        assert [x["resource_id"] for x in data["rows"]] == ["zz-saved-1"], data["rows"]
        assert data["phase"] != "complete" if "phase" in data else True
        assert data["meta"]["portal_results"]["find_tender"]["stored"] == 1
    finally:
        tender_index.stored_rows, server.upsert_tenders, server.search_tenders_db = real
        ps._JOBS.pop("zz-saved-first", None)
        server._JOB_BY_CACHE_KEY.pop("zz saved first:find_tender", None)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  ", name)
