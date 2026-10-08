"""Run: python tests/test_search_api_portal_status.py   (plain asserts; needs the dev database for create_app()).

The /api/search response must say what every portal did: Round 27 found a search that completed with no
word about the portal that returned nothing. This drives the real Flask route and job machinery with
fake portals (no network) and a fake "saved tenders" store (the route merges rows saved by earlier
searches into every answer), and checks the `meta` the page builds its progress line, per-portal summary
and header from:

  * portal_results: status / count / reason per portal, plus how many of the rows on screen are saved
    ones (`stored`) and how many the county filter removed (`hidden_by_county`),
  * pending_sources / total_sources_count, with the job's private bookkeeping kept out of the JSON,
  * a stalled portal is timed out, and /api/search/retry re-runs one portal.

Nothing is written to the database: the route's save step is replaced by a no-op and the search
user is a throwaway session.
"""
import os
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# create_app() starts the email scheduler, which really sends mail, unless it is switched off
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
os.environ["ENABLE_SCHEDULERS"] = "0"

from etenders_scraper.sources import progressive_search as ps  # noqa: E402
from etenders_scraper.sources.registry import SOURCES  # noqa: E402

IDS = ["find_tender", "gca_agreements", "procontract"]
SCOPE = ",".join(IDS)
USER = f"zz_search_status_{uuid.uuid4().hex[:8]}"
CSRF = "test-csrf-token"
H = {"X-CSRF-Token": CSRF}

ps._log = lambda *a, **k: None
ps.FIRST_PAGE_BUDGET_SEC = 0.6
ps.FULL_LIST_BUDGET_SEC = 1.2
ps._FIRST_PAGE_BUDGET_OVERRIDES = {}
ps.WATCHDOG_TICK_SEC = 0.05


class Skip(Exception):
    pass


def row(sid, n, authority="Kent County Council", title=None, **extra):
    return {"title": title or f"Construction works lot {n}", "resource_id": f"{sid}-{n}", "source": sid,
            "source_label": SOURCES[sid]["label"], "contracting_authority": authority, "description": "",
            "submission_deadline": "", **extra}


class Portals:
    def __init__(self):
        self.first, self.full, self.calls = {}, {}, []
        ps._PORTAL_RESULTS_CACHE.clear()
        ps._fetch_source_first_page = self._first
        ps._fetch_source_batch = self._full

    def _first(self, source_id, cfg, keyword, notes=None):
        self.calls.append(("first", source_id))
        batch, err = self.first[source_id](notes if notes is not None else {})
        return source_id, batch, err, None

    def _full(self, source_id, cfg, keyword, notes=None):
        self.calls.append(("full", source_id))
        batch, err = self.full[source_id](notes if notes is not None else {})
        return source_id, batch, err

    def everyone(self, per_portal):
        for sid in IDS:
            rows = per_portal.get(sid, [])
            self.first[sid] = lambda notes, rows=rows: (rows, None)
            self.full[sid] = lambda notes, rows=rows: (rows, None)
        return self


_app = None
_stored = []  # rows the fake "saved tenders" store answers with


def app():
    global _app
    if _app is None:
        import server
        server.upsert_tenders = lambda rows: None
        server.search_tenders_db = lambda q, limit=500: list(_stored)
        _app = server.create_app()
    return _app


def client(logged_in=True):
    c = app().test_client()
    if logged_in:
        with c.session_transaction() as s:
            s.update(logged_in=True, username=USER, email=f"{USER}@test.invalid", last_activity=time.time(), csrf_token=CSRF)
    return c


def search(c, q, scope=SCOPE, extra="", refresh=True, job_id=None):
    url = f"/api/search?q={q}&scope={scope}&progressive=1{'&refresh=1' if refresh else ''}{extra}"
    if job_id:
        url += f"&jobId={job_id}"
    r = c.get(url)
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:300])
    return r.get_json()


def poll(c, q, job_id, scope=SCOPE, extra="", timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        data = search(c, q, scope, extra, refresh=False, job_id=job_id)
        if data["searchPhase"] == "complete":
            return data
        time.sleep(0.05)
    raise AssertionError("the search never completed")


def run(c, q, scope=SCOPE, extra=""):
    first = search(c, q, scope, extra)
    return poll(c, q, first["jobId"], scope, extra)


# ── tests ────────────────────────────────────────────────────────────────────────────────────

def test_the_response_says_what_every_portal_did():
    _stored.clear()
    Portals().everyone({"find_tender": [row("find_tender", i) for i in range(1, 4)], "procontract": [row("procontract", 1)]})
    data = run(client(), "api status all")
    meta = data["meta"]
    assert data["searchPhase"] == "complete" and meta["pending_sources"] == [] and meta["loading_sources"] == []
    assert meta["total_sources_count"] == 3
    results = meta["portal_results"]
    assert list(results) == IDS
    assert (results["find_tender"]["status"], results["find_tender"]["count"], results["find_tender"]["shown"]) == ("ok", 3, 3)
    assert (results["gca_agreements"]["status"], results["gca_agreements"]["count"], results["gca_agreements"]["shown"]) == ("empty", 0, 0)
    assert (results["procontract"]["status"], results["procontract"]["count"]) == ("ok", 1)
    for r in results.values():
        assert {"portal", "label", "status", "count", "durationMs", "message", "partial", "available", "late",
                "shown", "stored", "hidden_by_county"} <= set(r)
        assert r["stored"] == 0 and r["hidden_by_county"] == 0
    assert not [k for k in meta if k.startswith("_")], "the job's private bookkeeping stays on the server"
    assert len(data["rows"]) == 4


def test_rows_saved_by_earlier_searches_are_told_apart_from_what_the_portal_returned():
    # the QA case: a portal that returned nothing to THIS search still has rows on screen, from earlier searches
    _stored[:] = [row("gca_agreements", 90, title="Construction works saved earlier A"), row("gca_agreements", 91, title="Construction works saved earlier B")]
    try:
        Portals().everyone({"find_tender": [row("find_tender", 1)]})
        data = run(client(), "api status stored")
        gca = data["meta"]["portal_results"]["gca_agreements"]
        assert (gca["status"], gca["count"], gca["shown"], gca["stored"]) == ("empty", 0, 2, 2)
        assert data["meta"]["portal_results"]["find_tender"]["stored"] == 0
        assert len(data["rows"]) == 3
    finally:
        _stored.clear()


def test_the_county_filter_is_accounted_for_per_portal():
    _stored.clear()
    Portals().everyone({
        "find_tender": [row("find_tender", 1, authority="Kent County Council", location="Kent"),
                        row("find_tender", 2, authority="Some Ministry", location="Surrey"),
                        row("find_tender", 3, authority="Another Department", location="Essex")],
        "procontract": [row("procontract", 1, authority="Kent County Council", location="Kent")],
    })
    data = run(client(), "api status county", extra="&counties=Kent")
    meta = data["meta"]
    assert [r["source"] for r in data["rows"]].count("find_tender") == 1
    assert meta["county_filter"]["hidden_by_source"] == {"find_tender": 2}
    assert meta["portal_results"]["find_tender"]["hidden_by_county"] == 2
    assert meta["portal_results"]["find_tender"]["count"] == 3, "what the portal returned, before the county filter"
    assert meta["portal_results"]["find_tender"]["shown"] == 1
    assert meta["portal_results"]["procontract"]["hidden_by_county"] == 0

def test_unstated_location_notice_is_kept_and_flagged():
    _stored.clear()
    Portals().everyone({
        "find_tender": [row("find_tender", 1, authority="Some Body", location=""),
                        row("find_tender", 2, authority="Another Body", location="Surrey")],
    })
    data = run(client(), "api status unstated", extra="&counties=Kent")
    meta = data["meta"]
    rows = data["rows"]
    assert len(rows) == 1
    assert rows[0]["resource_id"] == "find_tender-1"
    assert rows[0].get("location_not_stated") is True
    assert meta["county_filter"]["unstated_total"] == 1
    assert meta["county_filter"]["hidden_by_source"] == {"find_tender": 1}


def test_a_notice_that_names_only_its_nation_is_kept_when_the_whole_nation_is_selected():
    # Find a Tender locates notices as "UKD - North West (England)", never by county
    _stored.clear()
    Portals().everyone({"find_tender": [
        row("find_tender", 1, authority="Some Ministry", location="UKD - North West (England)"),
        row("find_tender", 2, authority="Another Department", location="UKI - London"),
        row("find_tender", 3, authority="Scottish Body", location="UKM - Scotland"),
        row("find_tender", 4, authority="Other Place Ltd", location="Surrey"),
    ]})
    c = client()

    def kept(q, extra):
        data = run(c, q, extra=extra)
        return sorted(r["resource_id"] for r in data["rows"] if r["source"] == "find_tender"), data["meta"]

    ids, meta = kept("api nations whole england", "&counties=Kent,Essex&nations=England")
    assert ids == ["find_tender-1", "find_tender-2"], "the English notices stay, the Scottish one and the unplaced one go"
    assert meta["county_filter"]["hidden_by_source"] == {"find_tender": 2}
    assert kept("api nations none claimed", "&counties=Kent,Essex")[0] == [], "without the nations nothing but county names counts"
    assert kept("api nations bogus", "&counties=Kent&nations=Atlantis,England%20Ltd")[0] == [], "unknown nations are ignored"
    ids, _ = kept("api nations scotland", "&counties=Fife&nations=Scotland")
    assert ids == ["find_tender-3"]


def test_the_counts_reconcile_per_portal_and_in_total():
    """fetched - invalid - duplicates + saved earlier - hidden by county == shown, for every portal and overall (QA 3.3)."""
    _stored[:] = [row("gca_agreements", 90, title="Construction saved earlier Kent", location="Kent", authority="Kent County Council"),
                  row("gca_agreements", 91, title="Construction saved earlier Surrey", location="Surrey", authority="Some Ministry")]
    try:
        Portals().everyone({
            "find_tender": [row("find_tender", 1, title="Construction works Alpha lot", location="Kent"),
                            row("find_tender", 2, title="Construction works Alpha lot", location="Kent"),      # same portal + same title: a duplicate
                            row("find_tender", 3, title="Construction works Beta lot", location="Surrey", authority="Some Ministry"),     # county filter
                            row("find_tender", 4, title="Construction works Gamma lot", location="Essex", authority="Another Department")],    # county filter
            "procontract": [row("procontract", 1, title="Construction works Delta lot", location="Kent")],
        })
        c = client()
        data = run(c, "api reconcile", extra="&counties=Kent")
        rec = data["meta"]["reconciliation"]
        ft, gca, pro = rec["by_source"]["find_tender"], rec["by_source"]["gca_agreements"], rec["by_source"]["procontract"]
        assert (ft["fetched"], ft["dropped_duplicate"], ft["hidden_by_county"], ft["shown"]) == (4, 1, 2, 1), ft
        assert (gca["fetched"], gca["saved_added"], gca["hidden_by_county"], gca["shown"]) == (0, 2, 1, 1), gca
        assert (pro["fetched"], pro["shown"]) == (1, 1)
        for e in rec["by_source"].values():
            assert e["balanced"] and e["fetched"] - e["dropped_invalid"] - e["dropped_duplicate"] + e["saved_added"] - e["hidden_by_county"] == e["shown"]
        assert rec["total"]["shown"] == len(data["rows"]) == 3 and rec["total"]["balanced"]
        assert data["meta"]["portal_results"]["find_tender"]["dropped_duplicate"] == 1
        # asking again (a retry / re-poll) must not change the saved-earlier figure
        again = run(c, "api reconcile", extra="&counties=Kent")
        assert again["meta"]["reconciliation"]["by_source"]["gca_agreements"]["saved_added"] == 2
        assert again["meta"]["portal_results"]["gca_agreements"]["stored"] == data["meta"]["portal_results"]["gca_agreements"]["stored"]
    finally:
        _stored.clear()


def test_failures_and_timeouts_come_through_with_their_reasons():
    _stored.clear()
    gate = threading.Event()
    p = Portals().everyone({"gca_agreements": [row("gca_agreements", 1)]})

    def refuse(notes):
        raise RuntimeError("Find a Tender is rate-limiting requests (HTTP 429). Try again in a minute.")

    p.first["find_tender"] = refuse
    p.first["procontract"] = lambda notes: (gate.wait(20) and [row("procontract", 1)], None)
    try:
        started = time.monotonic()
        data = run(client(), "api status failures")
        assert time.monotonic() - started < 4, "a stalled portal must not hold the search"
        results = data["meta"]["portal_results"]
        assert results["find_tender"]["status"] == "error" and "HTTP 429" in results["find_tender"]["message"]
        assert results["procontract"]["status"] == "timeout" and "did not respond in time" in results["procontract"]["message"]
        assert results["gca_agreements"]["status"] == "ok"
        assert data["meta"]["pending_sources"] == []
    finally:
        gate.set()


def test_retry_runs_one_portal_again():
    _stored.clear()
    p = Portals().everyone({"gca_agreements": [row("gca_agreements", 1)], "procontract": [row("procontract", 1)]})
    attempts = []

    def flaky(notes):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("Find a Tender answered with its error page twice")
        return [row("find_tender", i) for i in range(1, 6)], None

    p.first["find_tender"] = flaky
    p.full["find_tender"] = lambda notes: ([row("find_tender", i) for i in range(1, 6)], None)
    c = client()
    first = search(c, "api status retry")
    job_id = first["jobId"]
    done = poll(c, "api status retry", job_id)
    assert done["meta"]["portal_results"]["find_tender"]["status"] == "error"
    before = {sid: sum(1 for call in p.calls if call[1] == sid) for sid in ("gca_agreements", "procontract")}

    r = c.post("/api/search/retry", json={"jobId": job_id, "sourceId": "find_tender"}, headers=H)
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    again = poll(c, "api status retry", job_id)
    results = again["meta"]["portal_results"]
    assert results["find_tender"]["status"] == "ok" and results["find_tender"]["count"] == 5 and results["find_tender"]["message"] is None
    assert results["gca_agreements"]["status"] == "ok" and results["procontract"]["status"] == "ok"
    assert {sid: sum(1 for call in p.calls if call[1] == sid) for sid in ("gca_agreements", "procontract")} == before
    assert len(again["rows"]) == 7

    assert c.post("/api/search/retry", json={"jobId": job_id}, headers=H).status_code == 400
    assert c.post("/api/search/retry", json={"sourceId": "find_tender"}, headers=H).status_code == 400
    assert c.post("/api/search/retry", json={"jobId": "no-such-job", "sourceId": "find_tender"}, headers=H).status_code == 404
    assert c.post("/api/search/retry", json={"jobId": job_id, "sourceId": "sell2wales"}, headers=H).status_code == 400
    assert c.post("/api/search/retry", json={"jobId": job_id, "sourceId": "find_tender"}).status_code == 403, "CSRF is enforced"
    assert client(logged_in=False).post("/api/search/retry", json={"jobId": job_id, "sourceId": "find_tender"}, headers=H).status_code == 401


def test_an_empty_search_still_reports_each_portal_as_an_answer():
    _stored.clear()
    Portals().everyone({})
    data = run(client(), "api status nothing")
    assert data["rows"] == []
    assert {sid: r["status"] for sid, r in data["meta"]["portal_results"].items()} == {sid: "empty" for sid in IDS}


def main() -> None:
    try:
        from tender_app.db import get_db_connection
        get_db_connection().close()
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP: the database is not reachable from here ({type(exc).__name__}: {exc})")
        return
    app()
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    tests.sort(key=lambda item: item[1].__code__.co_firstlineno)
    failed = []
    for name, fn in tests:
        started = time.time()
        try:
            fn()
            print(f"ok    {name} ({time.time() - started:.1f}s)")
        except Exception:  # noqa: BLE001
            failed.append(name)
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - len(failed)} of {len(tests)} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
