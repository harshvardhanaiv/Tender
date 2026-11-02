"""/api/search puts the stored Public Contracts Scotland notices in the first page of results (design step 3), says how old that list is, and
returns to live-only when SEARCH_USE_INDEX=0. Run: python tests/test_search_reads_pcs_index.py (dev database; own rows, cleaned up)."""
import os
import sys
import time
import types
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENABLE_SCHEDULERS", "0")
os.environ.setdefault("ENABLE_EMAIL_SCHEDULER", "0")

import server  # noqa: E402
from tender_app import tender_index as ti  # noqa: E402
from tender_app.db import get_db_connection  # noqa: E402

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def _client():
    app = server.create_app()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["username"] = "zz_pcs_index_probe"
        s["last_activity"] = time.time()
    return c


def _search(c, q):
    job = types.SimpleNamespace(job_id="zz-job", upserted_ids=set())
    server.start_progressive_search = lambda q_, scope: job
    server.get_job = lambda jid: job
    server.job_snapshot = lambda j: ([], {"total": 0, "source_progress": {}}, "complete")
    server.upsert_tenders = lambda rows: None
    server.search_tenders_db = lambda q_, limit=500: []
    server._JOB_BY_CACHE_KEY.clear()
    r = c.get("/api/search?q=" + q + "&scope=all&progressive=1&refresh=1")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    return r.get_json()


def test_stored_pcs_notices_are_in_the_first_page_and_dated():
    conn = get_db_connection()
    cur = conn.cursor()
    ti.ensure_tables(cur)
    row = {"title": "Zzquux analytics platform for councils", "resource_id": "ZZ-IDX-1", "contracting_authority": "Fife Council",
           "submission_deadline": "20-Nov-26", "date_published": "29/10/2026", "notice_type": "Contract Notice", "detail_url": "https://x.example/1"}
    ti.upsert_notices(cur, [ti.normalise_scraped_row(row, "pcs", "Public Contracts Scotland", NOW)])
    cur.execute("INSERT INTO index_runs (source, finished_at, ok, complete) VALUES ('pcs', now(), TRUE, FALSE)")
    conn.commit()
    try:
        c = _client()
        os.environ["SEARCH_USE_INDEX"] = "1"
        data = _search(c, "zzquux")
        rows = [r for r in data["rows"] if r.get("resource_id") == "ZZ-IDX-1"]
        assert len(rows) == 1 and rows[0]["source"] == "pcs" and rows[0]["indexed_at"], data["rows"]
        assert "_portal_hit" not in rows[0], "a stored row was not returned by the portals for this search"
        fresh = data["meta"]["index_freshness"]["pcs"]
        assert fresh["ok"] and fresh["finished_at"] and fresh["stored_rows"] >= 1, fresh
        os.environ["SEARCH_USE_INDEX"] = "0"
        data = _search(c, "zzquux")
        assert not [r for r in data["rows"] if r.get("resource_id") == "ZZ-IDX-1"], "SEARCH_USE_INDEX=0 must be live-only"
        assert "index_freshness" not in data["meta"]
    finally:
        os.environ.pop("SEARCH_USE_INDEX", None)
        cur.execute("DELETE FROM tender_notices WHERE source = 'pcs' AND resource_id = 'ZZ-IDX-1'")
        cur.execute("DELETE FROM index_runs WHERE source = 'pcs' AND ok AND NOT complete AND rows_seen = 0 AND started_at > now() - interval '10 minutes'")
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
