"""Run: python tests/test_progressive_search_status.py   (plain asserts; no network, no database).

A multi-portal search must always be able to say what each portal did. Round 27 found a search that
sat at "2 of 3 finished" for over a minute, named the wrong portal as the one it was waiting for, then
finished on its own with no word about the portal that had returned nothing. These tests run the real
job machinery (threads, locks, the watchdog) against fake portals and check that:

  * `pending_sources` is exactly the portals that have not settled, whichever order they settle in,
    and the job is complete only when none is left,
  * a portal that stalls is timed out (keeping any rows it had), the search completes without it, and a
    late answer is kept without re-opening the search,
  * every portal ends as ok / empty / error / timeout / stopped with its reason (a genuine zero is
    "empty", a failure never is), a warning alongside rows is "partial", and
  * one portal can be retried without disturbing the others.
"""
import itertools
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etenders_scraper.sources import progressive_search as ps  # noqa: E402
from etenders_scraper.sources.registry import SOURCES  # noqa: E402

IDS = ["find_tender", "gca_agreements", "procontract"]  # two two-phase portals and one single-phase portal
SCOPE = ",".join(IDS)

ps._log = lambda *a, **k: None
ps.FIRST_PAGE_BUDGET_SEC = 0.6
ps.FULL_LIST_BUDGET_SEC = 1.2
ps._FIRST_PAGE_BUDGET_OVERRIDES = {}
ps.WATCHDOG_TICK_SEC = 0.05


def rows(sid, n, start=1):
    return [{"title": f"Construction works lot {i}", "resource_id": f"{sid}-{i}", "source": sid,
             "source_label": SOURCES[sid]["label"], "contracting_authority": "Example Council",
             "description": "", "submission_deadline": ""} for i in range(start, start + n)]


class Fakes:
    """Stand-ins for the two fetch functions: per portal, a callable returning (rows, warning)."""

    def __init__(self):
        self.first, self.full, self.calls = {}, {}, []
        ps._PORTAL_RESULTS_CACHE.clear()
        ps._fetch_source_first_page = self._first_page
        ps._fetch_source_batch = self._full_list

    def _first_page(self, source_id, cfg, keyword, notes=None):
        self.calls.append(("first", source_id))
        batch, err = self.first[source_id](keyword, notes if notes is not None else {})
        return source_id, batch, err, None

    def _full_list(self, source_id, cfg, keyword, notes=None):
        self.calls.append(("full", source_id))
        batch, err = self.full[source_id](keyword, notes if notes is not None else {})
        return source_id, batch, err

    def everyone_answers(self, n=2):
        for sid in IDS:
            self.first[sid] = lambda kw, notes, sid=sid: (rows(sid, n), None)
            self.full[sid] = lambda kw, notes, sid=sid: (rows(sid, n), None)
        return self

    def count(self, phase, sid):
        return sum(1 for c in self.calls if c == (phase, sid))


def wait_for(predicate, timeout=8.0, what="the condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def snap(job):
    return ps.job_snapshot(job)


def start(keyword, scope=SCOPE):
    return ps.start_progressive_search(keyword, scope)


def finished(job):
    return snap(job)[2] == "complete"


def result(job, sid):
    return snap(job)[1]["portal_results"][sid]


def statuses(job):
    return {sid: r["status"] for sid, r in snap(job)[1]["portal_results"].items()}


# ── tests ────────────────────────────────────────────────────────────────────────────────────

def test_every_portal_answers():
    fk = Fakes().everyone_answers()
    fk.first["find_tender"] = lambda kw, notes: (notes.update(available=15419) or rows("find_tender", 3), None)
    fk.full["find_tender"] = lambda kw, notes: (rows("find_tender", 3), None)
    fk.first["gca_agreements"] = lambda kw, notes: ([], None)  # answered, had nothing
    fk.full["gca_agreements"] = lambda kw, notes: ([], None)
    job = start("test every portal answers")
    wait_for(lambda: finished(job), what="the search to finish")
    live, meta, status = snap(job)
    assert status == "complete" and meta["pending_sources"] == [] and meta["loading_sources"] == []
    assert meta["total_sources_count"] == 3 and meta["totalExact"] is True
    results = meta["portal_results"]
    assert list(results) == IDS, "in the order the search lists them"
    assert (results["find_tender"]["status"], results["find_tender"]["count"], results["find_tender"]["available"]) == ("ok", 3, 15419)
    assert (results["gca_agreements"]["status"], results["gca_agreements"]["count"]) == ("empty", 0), "a genuine zero"
    assert (results["procontract"]["status"], results["procontract"]["count"]) == ("ok", 2)
    assert all(r["durationMs"] is not None and r["durationMs"] >= 0 for r in results.values())
    assert all(r["message"] is None and r["partial"] is False and r["late"] is False for r in results.values())
    assert len(live) == 5


def test_pending_is_exactly_the_portals_not_yet_settled_in_every_order():
    for order in itertools.permutations(IDS):
        gates = {sid: threading.Event() for sid in IDS}
        fk = Fakes()
        for sid in IDS:
            fk.first[sid] = lambda kw, notes, sid=sid: (gates[sid].wait(10) and rows(sid, 2), None)
            fk.full[sid] = lambda kw, notes, sid=sid: (rows(sid, 2), None)
        job = start(f"test pending order {'-'.join(order)}")
        try:
            settled = []
            assert snap(job)[1]["pending_sources"] == IDS
            for sid in order:
                gates[sid].set()
                wait_for(lambda: sid not in snap(job)[1]["pending_sources"], what=f"{sid} to settle")
                settled.append(sid)
                _, meta, status = snap(job)
                waiting = [s for s in IDS if s not in settled]
                assert meta["pending_sources"] == waiting, (order, settled, meta["pending_sources"])
                assert meta["total_sources_count"] - len(meta["pending_sources"]) == len(settled), "the 'N of 3 finished' count"
                assert (status == "complete") == (not waiting), "complete only once nothing is pending"
                for s in IDS:
                    expected = "running" if s in waiting else "ok"
                    assert meta["portal_results"][s]["status"] == expected, (order, s)
        finally:
            for g in gates.values():
                g.set()
        wait_for(lambda: finished(job))


def test_a_portal_that_stalls_is_timed_out_and_the_search_finishes_without_it():
    gate = threading.Event()
    fk = Fakes().everyone_answers()
    fk.first["procontract"] = lambda kw, notes: (gate.wait(20) and rows("procontract", 2), None)
    job = start("test a stalled portal")
    try:
        started = time.monotonic()
        wait_for(lambda: finished(job), what="the watchdog to give up on the stalled portal")
        assert time.monotonic() - started < 3, "the search must not wait on the stalled portal"
        _, meta, status = snap(job)
        pro = meta["portal_results"]["procontract"]
        assert status == "complete" and meta["pending_sources"] == []
        assert pro["status"] == "timeout" and pro["count"] == 0 and "did not respond in time" in pro["message"]
        assert meta["portal_results"]["find_tender"]["status"] == "ok" and meta["portal_results"]["gca_agreements"]["status"] == "ok"
        assert meta["errors"]["procontract"].startswith("ProContract")

        gate.set()  # the portal answers after all, long after the search finished
        wait_for(lambda: result(job, "procontract")["status"] == "ok", what="the late answer")
        late = result(job, "procontract")
        assert late["late"] is True and late["count"] == 2 and late["message"] is None
        assert snap(job)[2] == "complete", "a late answer never re-opens a finished search"
    finally:
        gate.set()


def test_a_portal_that_stops_part_way_keeps_its_first_rows():
    gate = threading.Event()
    fk = Fakes().everyone_answers()
    fk.full["find_tender"] = lambda kw, notes: (gate.wait(20) and rows("find_tender", 9), None)
    job = start("test full list too slow")
    try:
        wait_for(lambda: finished(job), what="the full-list budget to run out")
        ft = result(job, "find_tender")
        assert ft["status"] == "timeout" and ft["partial"] is True and ft["count"] == 2
        assert "first 2 results" in ft["message"]
        assert len([r for r in snap(job)[0] if r["source"] == "find_tender"]) == 2, "the rows it had stay on screen"
    finally:
        gate.set()


def test_failures_say_why_and_a_zero_is_not_one_of_them():
    fk = Fakes().everyone_answers()

    def refuse(kw, notes):
        raise RuntimeError("Find a Tender is rate-limiting requests (HTTP 429). Try again in a minute.")

    fk.first["find_tender"] = refuse
    fk.first["gca_agreements"] = lambda kw, notes: ([], "GCA said no")
    job = start("test failures")
    wait_for(lambda: finished(job))
    results = snap(job)[1]["portal_results"]
    assert results["find_tender"]["status"] == "error" and "HTTP 429" in results["find_tender"]["message"]
    assert results["gca_agreements"]["status"] == "error" and results["gca_agreements"]["message"] == "GCA said no"
    assert results["procontract"]["status"] == "ok"
    assert snap(job)[1]["errors"]["find_tender"].startswith("Find a Tender is rate-limiting")


def test_a_warning_alongside_rows_is_partial_not_failed():
    fk = Fakes().everyone_answers()
    fk.full["gca_agreements"] = lambda kw, notes: (rows("gca_agreements", 4), "Only part of the feed could be searched, so these results are incomplete.")
    job = start("test partial")
    wait_for(lambda: finished(job))
    gca = result(job, "gca_agreements")
    assert gca["status"] == "ok" and gca["partial"] is True and gca["count"] == 4 and "incomplete" in gca["message"]


def test_a_second_search_reuses_a_good_answer_and_keeps_its_notes():
    fk = Fakes().everyone_answers()
    fk.first["find_tender"] = lambda kw, notes: (notes.update(available=15419) or rows("find_tender", 3), None)
    fk.full["find_tender"] = lambda kw, notes: (rows("find_tender", 3), None)
    fk.first["gca_agreements"] = lambda kw, notes: ([], None)
    fk.full["gca_agreements"] = lambda kw, notes: ([], None)
    first = start("test reuse a good answer")
    wait_for(lambda: finished(first))
    asked = list(fk.calls)
    again = start("test reuse a good answer")
    wait_for(lambda: finished(again))
    assert fk.calls == asked, "every portal's answer was reused, a genuine zero included"
    ft = result(again, "find_tender")
    assert (ft["status"], ft["count"], ft["available"]) == ("ok", 3, 15419), "the 'of 15,419' note survives the reuse"
    assert result(again, "gca_agreements")["status"] == "empty"


def test_a_portal_that_failed_is_asked_again_by_the_next_search():
    fk = Fakes().everyone_answers()
    fk.full["find_tender"] = lambda kw, notes: ([], "Find a Tender is rate-limiting requests (HTTP 429).")
    first = start("test failures are not reused")
    wait_for(lambda: finished(first))
    ft = result(first, "find_tender")
    assert ft["count"] == 2 and ft["partial"] is True, "the first page it did return stays on screen"
    again = start("test failures are not reused")
    wait_for(lambda: finished(again))
    assert (fk.count("first", "find_tender"), fk.count("full", "find_tender")) == (2, 2), "Find a Tender is tried again"
    for sid, phases in (("gca_agreements", (1, 1)), ("procontract", (1, 0))):  # ProContract is a single-phase portal
        assert (fk.count("first", sid), fk.count("full", sid)) == phases, f"{sid} gave a good answer, which is reused"


def test_one_portal_can_be_retried_without_touching_the_others():
    fk = Fakes().everyone_answers()
    attempts = []

    def flaky(kw, notes):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("Find a Tender answered with its error page twice")
        return rows("find_tender", 3), None

    fk.first["find_tender"] = flaky
    fk.full["find_tender"] = lambda kw, notes: (rows("find_tender", 3), None)
    job = start("test retry")
    wait_for(lambda: finished(job))
    assert statuses(job) == {"find_tender": "error", "gca_agreements": "ok", "procontract": "ok"}
    before = {sid: (fk.count("first", sid), fk.count("full", sid)) for sid in IDS}

    assert ps.retry_source_search(job.job_id, "find_tender") is True
    assert snap(job)[2] == "loading" or finished(job)
    wait_for(lambda: finished(job) and result(job, "find_tender")["status"] == "ok", what="the retry to finish")
    assert result(job, "find_tender")["count"] == 3 and result(job, "find_tender")["message"] is None
    assert "find_tender" not in snap(job)[1]["errors"]
    assert statuses(job) == {"find_tender": "ok", "gca_agreements": "ok", "procontract": "ok"}
    for sid in ("gca_agreements", "procontract"):
        assert (fk.count("first", sid), fk.count("full", sid)) == before[sid], f"{sid} must not run again"

    assert ps.retry_source_search("no-such-job", "find_tender") is None
    assert ps.retry_source_search(job.job_id, "sell2wales") is False, "not part of this search"


def test_a_retry_that_finds_nothing_is_empty_not_the_old_failure():
    fk = Fakes().everyone_answers()
    calls = []

    def fails_then_finds_nothing(kw, notes):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Find a Tender is rate-limiting requests (HTTP 429).")
        return [], None

    fk.first["find_tender"] = fails_then_finds_nothing
    fk.full["find_tender"] = lambda kw, notes: ([], None)
    job = start("test retry empty")
    wait_for(lambda: finished(job))
    assert result(job, "find_tender")["status"] == "error"
    assert ps.retry_source_search(job.job_id, "find_tender") is True
    wait_for(lambda: finished(job) and result(job, "find_tender")["status"] != "running" and len(calls) == 2, what="the retry")
    now = result(job, "find_tender")
    assert (now["status"], now["message"]) == ("empty", None), "the old failure must not outlive a retry that succeeded"


def test_retrying_a_running_portal_does_not_start_it_twice():
    gate = threading.Event()
    fk = Fakes().everyone_answers()
    fk.first["procontract"] = lambda kw, notes: (gate.wait(20) and rows("procontract", 2), None)
    job = start("test double retry")
    try:
        wait_for(lambda: fk.count("first", "procontract") == 1)
        assert ps.retry_source_search(job.job_id, "procontract") is True
        assert ps.retry_source_search(job.job_id, "procontract") is True
        time.sleep(0.2)
        assert fk.count("first", "procontract") == 1
    finally:
        gate.set()
    wait_for(lambda: finished(job))


def test_a_retried_portal_that_stalls_again_is_timed_out_again():
    stall = threading.Event()
    fk = Fakes().everyone_answers()
    fk.first["procontract"] = lambda kw, notes: (stall.wait(20) and rows("procontract", 2), None)
    job = start("test retry stalls")
    try:
        wait_for(lambda: finished(job))
        assert result(job, "procontract")["status"] == "timeout"
        wait_for(lambda: not job.watchdog_running, what="the first watchdog to stand down")
        assert ps.retry_source_search(job.job_id, "procontract") is True
        assert snap(job)[2] == "loading", "retrying re-opens the search"
        wait_for(lambda: finished(job), what="the watchdog to time the retry out as well")
        assert result(job, "procontract")["status"] == "timeout"
    finally:
        stall.set()


def test_a_stopped_portal_is_settled_and_stays_stopped():
    gate = threading.Event()
    fk = Fakes().everyone_answers()
    fk.first["procontract"] = lambda kw, notes: (gate.wait(20) and rows("procontract", 2), None)
    job = start("test stop one")
    try:
        wait_for(lambda: statuses(job)["find_tender"] == "ok" and statuses(job)["gca_agreements"] == "ok")
        assert ps.stop_source_search(job.job_id, "procontract") is True
        wait_for(lambda: finished(job))
        _, meta, _ = snap(job)
        assert meta["portal_results"]["procontract"]["status"] == "stopped"
        assert meta["pending_sources"] == [] and meta["loading_sources"] == [], "a stopped portal is not 'still loading'"
    finally:
        gate.set()
    time.sleep(0.3)
    assert result(job, "procontract")["status"] == "stopped", "a late answer does not undo the stop"


def test_the_search_is_not_complete_while_a_portal_is_outstanding():
    gate = threading.Event()
    fk = Fakes().everyone_answers()
    fk.first["find_tender"] = lambda kw, notes: (gate.wait(20) and rows("find_tender", 2), None)
    job = start("test not complete early")
    try:
        wait_for(lambda: statuses(job)["gca_agreements"] == "ok" and statuses(job)["procontract"] == "ok")
        time.sleep(0.3)  # well inside the time limit
        _, meta, status = snap(job)
        assert status == "loading" and meta["pending_sources"] == ["find_tender"]
        assert meta["totalExact"] is False
    finally:
        gate.set()
    wait_for(lambda: finished(job))


def test_snapshots_hide_private_bookkeeping():
    Fakes().everyone_answers()
    job = start("test snapshot")
    wait_for(lambda: finished(job))
    _, meta, _ = snap(job)
    assert not [k for k in meta if k.startswith("_")]
    assert {"portal_results", "pending_sources", "loading_sources", "total_sources_count", "source_progress", "errors"} <= set(meta)


def test_a_search_with_nothing_to_search_is_complete_at_once():
    job = ps.start_progressive_search("anything", "")
    assert job.status == "complete" and snap(job)[1]["portal_results"] == {}


def test_portal_results_from_hand_made_state():
    sources = {"a": {"label": "Alpha"}, "b": {"label": "Beta"}, "c": {"label": "Gamma"}, "d": {"label": "Delta"},
               "e": {"label": "Echo"}, "f": {"label": "Foxtrot"}, "g": {"label": "Golf"}}
    live = [{"source": "a", "title": "x"}, {"source": "a", "title": "y"}, {"source": "c", "title": "z"}, {"source": "e", "title": "w"}]
    meta = {
        "source_progress": {"a": "complete", "b": "complete", "c": "error", "d": "timeout", "e": "timeout", "f": "pending", "g": "fetching_all"},
        "errors": {"c": "no good", "d": "Delta did not respond in time", "e": "Echo stopped loading after the first 1 result"},
        "_portal_times": {"a": {"start": 100.0, "end": 102.5}, "g": {"start": 100.0}},
        "portal_notes": {"a": {"available": 120}},
        "_late": {"a": True},
    }
    r = ps.build_portal_results(meta, live, sources, now=110.0)
    assert (r["a"]["status"], r["a"]["count"], r["a"]["durationMs"], r["a"]["available"], r["a"]["late"]) == ("ok", 2, 2500, 120, True)
    assert (r["b"]["status"], r["b"]["count"], r["b"]["message"]) == ("empty", 0, None)
    assert (r["c"]["status"], r["c"]["partial"], r["c"]["message"]) == ("ok", True, "no good"), "rows plus an error: partial"
    assert (r["d"]["status"], r["d"]["count"], r["d"]["partial"]) == ("timeout", 0, False)
    assert (r["e"]["status"], r["e"]["count"], r["e"]["partial"]) == ("timeout", 1, True)
    assert (r["f"]["status"], r["f"]["durationMs"]) == ("pending", None)
    assert (r["g"]["status"], r["g"]["durationMs"]) == ("running", 10000), "a running portal reports its time so far"
    assert r["a"]["label"] == "Alpha" and list(r) == list(sources)
    meta["source_progress"]["b"] = "error"
    assert ps.build_portal_results(meta, live, sources, now=110.0)["b"]["status"] == "error", "an error with no message still has one"
    assert ps.build_portal_results(meta, live, sources, now=110.0)["b"]["message"]
    meta["source_progress"]["b"] = "complete"
    meta["errors"]["b"] = "Only part of the feed could be read"
    assert ps.build_portal_results(meta, live, sources, now=110.0)["b"]["status"] == "error", "a warning with no rows is not 'empty'"


def main() -> None:
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
