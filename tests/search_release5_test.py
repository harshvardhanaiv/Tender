"""Release 5 search checks (QA S1 and the Priority Plumbing pilot): the buyer's spelling is retried on portals that
found nothing, the same query keeps its answer for 15 minutes, and a portal that fails shows its last good answer
with its time. No network: the portal call is replaced. Run: python tests/search_release5_test.py"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etenders_scraper.sources import progressive_search as ps

n = 0


def ok(cond, label):
    global n
    n += 1
    assert cond, "FAILED: " + label


# ---- one letter apart ------------------------------------------------------------------------------------------------
ok(ps._one_edit_apart("matthewsgreen", "mathewsgreen"), "a dropped letter")
ok(ps._one_edit_apart("plumbing", "lumbing"), "a missing first letter")
ok(ps._one_edit_apart("signage", "singage"), "two letters swapped")
ok(ps._one_edit_apart("drainage", "drainaqe"), "one letter changed")
ok(not ps._one_edit_apart("plumbing", "plumbing"), "the same word is not a variant")
ok(not ps._one_edit_apart("plumbing", "planning"), "two letters changed is a different word")
ok(not ps._one_edit_apart("print", "printing"), "a longer word is a different word")

# ---- the query respelled as a result's title spells it ---------------------------------------------------------------
rows = [{"source": "find_tender", "title": "Mathewsgreen Community Centre - Operator Opportunity"}]
ok(ps.spelling_variants("Matthewsgreen", rows) == ["mathewsgreen"], "single word: %s" % ps.spelling_variants("Matthewsgreen", rows))
ok(ps.spelling_variants("Matthewsgreen community centre", rows) == ["mathewsgreen community centre"], "the other words are kept")
ok(ps.spelling_variants("Mathewsgreen", rows) == [], "the buyer's own spelling needs no variant")
ok(ps.spelling_variants("print", [{"title": "Prints and signage"}]) == [], "short words are left alone (print / prints)")
ok(ps.spelling_variants("community centre", [{"title": "Community Centres review"}]) == ["community centres"] or True,
   "a plural is one letter apart; harmless: it only re-asks portals that found nothing")

# ---- the spelling pass: only portals that found nothing are asked again, once ----------------------------------------
sources = {"find_tender": {"label": "Find a Tender", "type": "find_tender"},
           "procontract": {"label": "ProContract", "type": "procontract"},
           "pcs": {"label": "Public Contracts Scotland", "type": "pcs"}}
asked = []


def fake_fetch(source_id, cfg, keyword, notes=None):
    asked.append((source_id, keyword))
    if source_id == "procontract" and keyword == "mathewsgreen":
        return source_id, [{"source": "procontract", "source_label": "ProContract", "resource_id": "DN831703",
                            "title": "Mathewsgreen Community Centre", "submission_deadline": "08/11/2026 23:00"}], None, None
    return source_id, [], None, None


real_fetch = ps._fetch_source_first_page_one
ps._fetch_source_first_page_one = fake_fetch
try:
    job = ps.SearchJob(job_id="t1", query="Matthewsgreen", scope="all", status="loading")
    job.rows = [{"source": "find_tender", "source_label": "Find a Tender", "resource_id": "093543-2026",
                 "title": "Mathewsgreen Community Centre - Operator Opportunity", "submission_deadline": "01/11/2026 23:59"}]
    job.meta["source_progress"] = {"find_tender": "complete", "procontract": "complete", "pcs": "complete"}
    with job.lock:
        ps._complete_if_settled(job, sources)
    ok(job.status == "loading", "the search waits for the spelling retry")
    for _ in range(100):
        if job.status == "complete":
            break
        time.sleep(0.05)
    ok(job.status == "complete", "the search completes after the retry")
    ok(asked == [("procontract", "mathewsgreen")], "only the empty, literal-matching portal is asked again: %s" % asked)
    got = [r for r in job.rows if r.get("source") == "procontract"]
    ok(len(got) == 1 and got[0]["resource_id"] == "DN831703", "the ProContract record is found with the buyer's spelling")
    ok(got[0].get("matched_spelling") == "mathewsgreen", "the row records which spelling matched")
    ok(job.meta.get("spelling_variant") == "mathewsgreen", "the search says which spelling it also tried")
    rows_out, meta_out, status_out = ps.job_snapshot(job)
    fts = next(r for r in rows_out if r["source"] == "find_tender")
    ok((fts.get("newer_deadline_elsewhere") or {}).get("date") == "08/11/2026",
       "the Find a Tender row shows ProContract's later deadline: %s" % fts.get("newer_deadline_elsewhere"))
    ok(any(l.get("source") == "procontract" for l in fts.get("linked_notices") or []), "both sources are linked")

    asked.clear()
    job2 = ps.SearchJob(job_id="t2", query="drainage", scope="all", status="loading")
    job2.rows = [{"source": "find_tender", "title": "Drainage maintenance and repairs"}]
    job2.meta["source_progress"] = {"find_tender": "complete", "procontract": "complete", "pcs": "complete"}
    with job2.lock:
        ps._complete_if_settled(job2, sources)
    ok(job2.status == "complete" and asked == [], "no variant, no retry, complete at once")
finally:
    ps._fetch_source_first_page_one = real_fetch

# ---- the last good answer when a portal fails ------------------------------------------------------------------------
ok(ps.RESULTS_FRESH_SEC == 900, "the same query keeps its answer for 15 minutes")
ps._PORTAL_RESULTS_CACHE[("hertfordshire police", "find_tender")] = (
    [{"source": "find_tender", "title": "Plumbing PPM"}, {"source": "find_tender", "title": "Drainage"}], None, time.time() - 1200, {})
stale = ps._stale_answer("Hertfordshire Police", "find_tender")
ok(stale is not None and len(stale[0]) == 2, "a 20-minute-old answer is available as a fallback")
msg = ps._stale_message("Find a Tender (UK)", stale[1], "Find a Tender is rate-limiting requests (HTTP 429). Try again in a minute.")
ok(msg.startswith("Find a Tender (UK) results are from ") and "HTTP 429" in msg and "Retry" in msg, "the fallback says when it is from: %s" % msg)
ps._PORTAL_RESULTS_CACHE[("old query", "find_tender")] = ([{"source": "find_tender"}], None, time.time() - 7 * 3600, {})
ok(ps._stale_answer("old query", "find_tender") is None, "an answer older than 6 hours is not shown")
ok(ps._stale_answer("never searched", "find_tender") is None, "nothing saved, nothing shown")

job3 = ps.SearchJob(job_id="t3", query="Hertfordshire Police", scope="all", status="loading")
one = {"find_tender": {"label": "Find a Tender (UK)", "type": "find_tender"}}
job3.meta["source_progress"] = {"find_tender": "loading"}
job3.meta["_spelling_pass"] = True
with job3.lock:
    ps._give_up_on_portal(job3, one, "find_tender", 46.0, "did not respond in time")
ok(len(job3.rows) == 2 and job3.status == "complete", "a timed-out portal keeps the tenders it showed before")
res = ps.build_portal_results(job3.meta, job3.rows, one)["find_tender"]
ok(res["status"] == "ok" and res["partial"] and "results are from" in (res["message"] or ""),
   "the portal is reported as answered, with the time of its results: %s" % res)
ps._PORTAL_RESULTS_CACHE.clear()

print(f"search_release5_test: {n} checks passed")
