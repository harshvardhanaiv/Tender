"""Item 12 (Re-test 23 Nov): on the live site every poll of a finished search still took 2.8 to 3.6 s, all of it the stored-notice, index and
CPV reads repeated on each poll. They are now read once per search and reused by the polls that name its job; a new search reads them
afresh. Run: python tests/test_search_db_part_cache.py (no database: the index is switched off and the stored-notice read is a stub)."""
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENABLE_SCHEDULERS", "0")
os.environ.setdefault("ENABLE_EMAIL_SCHEDULER", "0")
os.environ["SEARCH_USE_INDEX"] = "0"

import server  # noqa: E402

CALLS = []


def _client():
    app = server.create_app()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["username"] = "zz_db_part_probe"
        s["last_activity"] = time.time()
    return c


def _setup():
    job = types.SimpleNamespace(job_id="zz-job", upserted_ids=set())
    server.start_progressive_search = lambda q_, scope: job
    server.get_job = lambda jid: job
    server.job_snapshot = lambda j: ([], {"total": 0, "source_progress": {}}, "loading")
    server.upsert_tenders = lambda rows: None

    def stub(q_, limit=500):
        CALLS.append(q_)
        return [{"title": "Stored cleaning tender", "source": "contracts_finder", "resource_id": "ZZ1", "contracting_authority": "A Council"}]

    server.search_tenders_db = stub
    server._JOB_BY_CACHE_KEY.clear()
    server._SEARCH_DB_PART.clear()


def test_polls_reuse_the_database_reads_and_a_new_search_reads_again():
    _setup()
    c = _client()
    r1 = c.get("/api/search?q=zzcleaning&scope=all&progressive=1")
    assert r1.status_code == 200 and len(CALLS) == 1, CALLS
    rows1 = [r for r in r1.get_json()["rows"] if r.get("resource_id") == "ZZ1"]
    assert rows1, "the stored notice is in the first response"
    for _ in range(3):
        r = c.get("/api/search?q=zzcleaning&scope=all&progressive=1&jobId=zz-job")
        assert r.status_code == 200
        assert [x for x in r.get_json()["rows"] if x.get("resource_id") == "ZZ1"], "and in every poll"
    assert len(CALLS) == 1, "three polls did not read the database again: %s" % CALLS
    c.get("/api/search?q=zzcleaning&scope=all&progressive=1&refresh=1")
    assert len(CALLS) == 2, "a new search (no job named) reads afresh"


def test_the_cache_is_per_query_and_expires():
    _setup()
    c = _client()
    c.get("/api/search?q=zzone&scope=all&progressive=1")
    c.get("/api/search?q=zztwo&scope=all&progressive=1&jobId=zz-job")
    assert len(CALLS) >= 2, "a different query is not served from another query's reads"
    n = len(CALLS)
    c.get("/api/search?q=zzone&scope=all&progressive=1&jobId=zz-job")
    assert len(CALLS) == n, "the same query's reads are reused"
    for v in server._SEARCH_DB_PART.values():
        v["at"] -= server.SEARCH_DB_PART_TTL_SEC + 1
    c.get("/api/search?q=zzone&scope=all&progressive=1&jobId=zz-job")
    assert len(CALLS) == n + 1, "an old copy is read again"


if __name__ == "__main__":
    test_polls_reuse_the_database_reads_and_a_new_search_reads_again()
    CALLS.clear()
    test_the_cache_is_per_query_and_expires()
    print("search db-part cache: 2 tests passed")
