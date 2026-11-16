import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..deadline import enrich_deadline_fields
from ..scraper import EtendersScraper
from .bravo_search import search_bravo_first_page
from .registry import (
    SOURCES,
    _fetch_ie_batch,
    _fetch_ni_portal,
    _fetch_source_batch,
    _interleave_sources,
    fan_out_terms,
    merge_term_rows,
    _sort_key,
)

_FIRST_PAGE_SIZE = 100

# A portal's progress, as the job records it. These are the states in which a portal is finished with,
# whether or not it succeeded; every other state means the search is still waiting for it.
SETTLED_STATES = frozenset({"complete", "error", "stopped", "timeout"})
UNSETTLED_STATES = frozenset({"pending", "loading", "first_page", "fetching_all"})

# How long a search waits for one portal. A portal that has produced nothing at all within
# FIRST_PAGE_BUDGET_SEC is marked "timeout"; one that is still adding rows after FULL_LIST_BUDGET_SEC is
# marked "timeout" too, keeping the rows it had. Without a limit a single stalled portal held the whole
# search at "2 of 3 finished" for minutes (Round 27). Portals that fetch everything in one go or have to
# solve a captcha get longer. The worker thread is not killed: if it finishes late its rows are kept.
FIRST_PAGE_BUDGET_SEC = float(os.environ.get("SEARCH_PORTAL_TIMEOUT_SEC", "45"))
FULL_LIST_BUDGET_SEC = float(os.environ.get("SEARCH_PORTAL_FULL_TIMEOUT_SEC", "150"))
_FIRST_PAGE_BUDGET_OVERRIDES = {"procontract": 75.0, "etenders_ni": 100.0, "contracts_finder": 60.0, "pcs": 180.0}
# Public Contracts Scotland answers slowly (2.5 to 3 minutes for a broad word on live, ~2 MB of page state per result page),
# so it gets 3 minutes for its first page and 10 minutes to finish listing (every other portal has 150 s), and keeps the rows
# it had if it still runs out.
# Contracts Finder has no keyword filter, so a cold search downloads its whole 180-day feed (about 400 pages) in three
# parallel windows, roughly 3 to 4 minutes, streaming matches into the results as they arrive.
_FULL_LIST_BUDGET_OVERRIDES = {"pcs": 600.0, "contracts_finder": 420.0}
WATCHDOG_TICK_SEC = 0.5


def first_page_budget(source_id: str) -> float:
    return max(FIRST_PAGE_BUDGET_SEC, _FIRST_PAGE_BUDGET_OVERRIDES.get(source_id, 0.0))


def full_list_budget(source_id: str) -> float:
    return max(FULL_LIST_BUDGET_SEC, _FULL_LIST_BUDGET_OVERRIDES.get(source_id, 0.0), first_page_budget(source_id))


_INVALID_ROW_PATTERNS = [
    "bad page parameters",
    "bad page",
    "log-in bad page",
    "page you requested has not been supplied",
    "page not found",
    "404 not found",
    "500 internal server error",
    "service unavailable",
    "access denied",
    "the page you requested has not been supplied with the correct parameters",
    "no actual tender",
    "no actual requirements",
    "please log in",
    "login required",
    "session expired",
    "you must be logged in",
    "javascript is required",
    "enable cookies",
    "website error",
    "this page is not available",
    "403 forbidden",
    "gateway timeout",
    "passwort vergessen",
    "benutzername vergessen",
    "renewsession",
    "cookiecheck",
    "anmelden mit",
    "bezeichnung",
]

_SYNTHETIC_ROW_PATTERNS = [
    "technical solutions contract",
    "contract #0",
    "supply, delivery and implementation of",
    "provision of digital infrastructure, cloud &",
    "indexed portal notice",
]

def is_invalid_tender_row(row: dict, query: str = "") -> bool:
    """Return True if row is an error page, invalid page, or synthetic fake tender."""
    if not isinstance(row, dict):
        return True
    title = str(row.get("title") or "").strip()
    desc = str(row.get("description") or "").strip()
    auth = str(row.get("contracting_authority") or "").strip()
    title_lower = title.lower()
    desc_lower = desc.lower()
    auth_lower = auth.lower()

    if not title_lower or len(title_lower) < 8:
        return True

    if any(p in title_lower or p in desc_lower for p in _INVALID_ROW_PATTERNS):
        return True

    if any(p in title_lower for p in _SYNTHETIC_ROW_PATTERNS):
        return True

    if re.search(r"contract\s*#\s*0?\d+", title_lower):
        return True

    if re.search(r"&\s*technical\s+solutions", title_lower):
        return True

    if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", title_lower):
        return True

    if query and query.strip() and query.strip().lower() != "all":
        kw = query.strip().lower()
        prefix_match = re.match(r"^(?:supplier|winner|contractor|buyer|authority|title):\s*(.*)$", kw, re.I)
        if prefix_match:
            kw = prefix_match.group(1).strip()
        if kw and len(kw) >= 3:
            if kw in auth_lower and any(pat in auth_lower for pat in ["department of", "ministry of", "authority", "directorate", "agency"]):
                if any(phrase in auth_lower for phrase in ["housing, local government &", "& technology", "executive agency for", "government digital &", "infrastructure &"]):
                    return True
            if kw in title_lower and any(p in title_lower for p in ["technical solutions", "contract #", "implementation of", "provision of digital infrastructure"]):
                return True

    return False



# Portals whose own keyword search returns loosely related notices (fix plan H5: 50 unrelated Public Contracts
# Scotland results for "Matthewsgreen", 112 for EEF's title words). Their rows must contain a query word.
LOOSE_MATCH_SOURCES = {"pcs"}
_QUERY_STOP = {"and", "the", "for", "with", "from", "into", "of", "to", "in", "on", "at", "by", "a", "an", "or",
               "services", "service", "contract", "contracts", "tender", "tenders", "provision", "supply", "framework",
               "lot", "dps", "uk", "new"}


def query_terms(query: str) -> list[str]:
    q = (query or "").strip().lower()
    if not q or q == "all":
        return []
    q = re.sub(r"^(?:supplier|winner|contractor|buyer|authority|title):\s*", "", q)
    q = q.replace("&", " and ")
    return [w for w in re.findall(r"[a-z0-9]+", q) if len(w) >= 3 and w not in _QUERY_STOP]


def matches_query(row: dict, query: str, strict: bool = False) -> bool:
    """True when the row mentions a meaningful word of the query (or the query has none). "&" counts as "and".
    strict (portals that ignore the query): at least half of the words must appear, so "Thetford Activity
    Interpretation" no longer keeps every notice that says "activity" (release 5: 60 and 102 results for one real one)."""
    terms = query_terms(query)
    if not terms:
        return True
    hay = " ".join(str(row.get(k) or "") for k in ("title", "description", "contracting_authority", "cpv", "cpv_codes",
                                                   "reference", "resource_id")).lower().replace("&", " and ")
    words = set(re.findall(r"[a-z0-9]+", hay))
    hits = sum(1 for t in terms if t in words or (len(t) >= 5 and t in hay))
    return hits >= (max(1, -(-len(terms) // 2)) if strict else 1)


@dataclass
class SearchJob:
    job_id: str
    query: str
    scope: str
    status: str  # loading | complete | error
    rows: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    started_at: float = field(default_factory=time.monotonic)
    cancelled: bool = False
    cancelled_sources: set[str] = field(default_factory=set)
    # Tender ids already written to the master store for this job, so repeated polls of a
    # growing result list only store the new rows.
    upserted_ids: set[str] = field(default_factory=set)
    # True while a watchdog thread is timing this job's portals (see _watch_job)
    watchdog_running: bool = False


_JOBS: dict[str, SearchJob] = {}
_JOBS_LOCK = threading.Lock()
# (keyword, portal) -> (rows, warning, saved at, the portal's notes). Only answers worth reusing go in:
# a portal that failed outright is not remembered, so searching again tries it again.
_PORTAL_RESULTS_CACHE: dict[tuple[str, str], tuple[list[dict[str, Any]], str | None, float, dict[str, Any]]] = {}
# The same query gives the same answer for 15 minutes (pilot, 13 Nov: "Hertfordshire Police" returned 7 tenders, then
# 6 a few minutes later, because Find a Tender rate-limited the second search). "Retry" on a portal still refetches.
RESULTS_FRESH_SEC = 900
# When a portal fails or times out, its last good answer to this query (up to 6 hours old) is shown and labelled
# with its time, rather than dropping tenders the user saw a moment ago.
RESULTS_STALE_SEC = 6 * 3600


def _stale_answer(keyword: str, source_id: str):
    hit = _PORTAL_RESULTS_CACHE.get(((keyword or "").strip().lower(), source_id))
    if not hit:
        return None
    rows, _err, saved, notes = hit
    if not rows or time.time() - saved > RESULTS_STALE_SEC:
        return None
    return rows, saved, notes


def _stale_message(label: str, saved: float, why: str) -> str:
    why = (why or "the portal did not answer").strip().rstrip(".")
    return (f"{label} results are from {time.strftime('%H:%M', time.localtime(saved))} ({why[:140]}). "
            "Retry this portal to refresh them.")


def get_job(job_id: str) -> SearchJob | None:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def _log(job: SearchJob, message: str) -> None:
    """One server-log line per portal milestone and one when the search finishes, so a report like
    "it completed but a portal's rows are missing" can be traced to what each portal returned."""
    print(f"[search {job.job_id[:8]} {job.query[:40]!r}] {message}", flush=True)


def _sources_for_scope(scope: str) -> dict[str, dict[str, str]]:
    if scope == "all":
        return SOURCES
    # Support comma-separated list of portal IDs
    ids = [s.strip() for s in scope.split(",") if s.strip()]
    if len(ids) == 1:
        return {ids[0]: SOURCES[ids[0]]} if ids[0] in SOURCES else {}
    return {sid: SOURCES[sid] for sid in ids if sid in SOURCES}


def _fetch_source_first_page(
    source_id: str, cfg: dict[str, str], keyword: str, notes: dict[str, Any] | None = None
) -> tuple[str, list[dict[str, Any]], str | None, int | None]:
    """First page per portal. A comma-separated keyword is searched term by term and merged (Contracts
    Finder checks all terms against one feed instead)."""
    if cfg["type"] != "contracts_finder":
        sub_notes: dict[str, dict[str, Any]] = {}

        def one(term: str):
            n: dict[str, Any] = {}
            sub_notes[term] = n
            return _fetch_one_with_variant(source_id, cfg, term, n)

        fanned = fan_out_terms(keyword, one)
        if fanned is not None:
            good = [(t, r) for t, r in fanned if not isinstance(r, Exception)]
            if not good:
                raise next(r for _, r in fanned if isinstance(r, Exception))
            if notes is not None:
                avail = [n.get("available") for n in sub_notes.values() if n.get("available") is not None]
                if avail:
                    notes["available"] = sum(avail)
            errs = [r[2] for _, r in good if r[2]] + [str(r) for _, r in fanned if isinstance(r, Exception)]
            hints = [r[3] for _, r in good if r[3] is not None]
            return source_id, merge_term_rows([(t, r[1]) for t, r in good]), (errs[0] if errs and not any(r[1] for _, r in good) else None), (sum(hints) if hints else None)
    return _fetch_one_with_variant(source_id, cfg, keyword, notes)


def ampersand_variant(term: str) -> str | None:
    """"Print and Signage" <-> "Print & Signage": portals match titles literally (fix plan R11)."""
    t = term or ""
    if "&" in t:
        return re.sub(r"\s*&\s*", " and ", t).strip()
    if re.search(r"\band\b", t, re.I):
        return re.sub(r"\s+and\s+", " & ", t, flags=re.I).strip()
    return None


def _fetch_one_with_variant(source_id: str, cfg: dict[str, str], keyword: str, notes: dict[str, Any] | None = None):
    """One portal query; if it finds nothing and the keyword has "&" or "and", try the other spelling once."""
    sid, rows, err, hint = _fetch_source_first_page_one(source_id, cfg, keyword, notes)
    alt = ampersand_variant(keyword) if not rows else None
    if alt and alt != keyword:
        try:
            sid2, rows2, err2, hint2 = _fetch_source_first_page_one(source_id, cfg, alt, notes)
            if rows2:
                return sid2, rows2, err2, hint2
        except Exception:  # noqa: BLE001 - the first answer stands
            pass
    return sid, rows, err, hint


def _fetch_source_first_page_one(
    source_id: str, cfg: dict[str, str], keyword: str, notes: dict[str, Any] | None = None
) -> tuple[str, list[dict[str, Any]], str | None, int | None]:
    """One page per portal. `notes`, when given, receives extra facts about the answer (`available`:
    how many notices the portal says match, where it tells us)."""
    if cfg["type"] == "etenders":
        if source_id == "etenders_ie":
            scraper = EtendersScraper(
                delay_seconds=0.15,
                page_size=_FIRST_PAGE_SIZE,
                base_url=cfg["base_url"],
            )
            rows, summary = scraper.search_description_page(keyword, page=1)
            for row in rows:
                row["source"] = source_id
                row["source_label"] = cfg["label"]
            total = summary["total"] if summary else None
            return source_id, rows, None, total
        batch, err = _fetch_ni_portal(source_id, cfg, keyword)
        return source_id, batch, err, None
    elif cfg["type"] == "find_tender":
        from .find_tender import search_find_tender_first_page
        info: dict[str, Any] = {}
        batch = search_find_tender_first_page(
            keyword=keyword,
            source_id=source_id,
            source_label=cfg["label"],
            info=info,
        )
        if notes is not None and info.get("total") is not None:
            notes["available"] = info["total"]
        return source_id, batch, None, None
    elif cfg["type"] == "contracts_finder":
        from .contracts_finder import search_contracts_finder_first_page
        batch = search_contracts_finder_first_page(
            keyword=keyword,
            source_id=source_id,
            source_label=cfg["label"],
        )
        return source_id, batch, None, None
    elif cfg["type"] == "procontract":
        # No dedicated first-page-only fetcher -- ProContract is capped at 15 pages / 150
        # results (see procontract._MAX_PAGES), small enough that running the full fetch here
        # and skipping phase 2 below (see the etenders_ni/meta skip a few lines down in
        # _portal_worker) is simpler and cheaper than a second, duplicate full pass. Without
        # this branch, cfg["type"] "procontract" matched none of the elifs above and fell
        # through to the Bravo/PCS fallback at the bottom of this function, which built a
        # search URL against the wrong site (PCS's Search_MainPage.aspx, not ProContract's
        # Opportunities/Index) and could take its full 120s per-request timeout to fail --
        # this was the Round 20 "3rd of 3 portals never completes" hang (procontract was the
        # 3rd England portal; see REGION_PORTAL_IDS.England in app.js).
        from .procontract import search_procontract
        try:
            batch = search_procontract(
                keyword=keyword,
                source_id=source_id,
                source_label=cfg["label"],
            )
            return source_id, batch, None, None
        except Exception as exc:
            return source_id, [], str(exc), None
    elif cfg["type"] == "nepo_open":
        # One request for the whole public list, filtered here, so phase 2 is skipped for it below.
        from .nepo_open import search_nepo_open
        try:
            batch = search_nepo_open(
                keyword=keyword,
                source_id=source_id,
                source_label=cfg["label"],
            )
            return source_id, batch, None, None
        except Exception as exc:
            return source_id, [], str(exc), None
    elif cfg["type"] == "eu_supply_ctm":
        # A complete fetch (one to three pages of 100), so phase 2 is skipped for it below.
        from .eu_supply_ctm import search_eu_supply_ctm
        try:
            batch = search_eu_supply_ctm(
                keyword=keyword,
                site_root=cfg["site_root"],
                branding=cfg["branding"],
                source_id=source_id,
                source_label=cfg["label"],
            )
            return source_id, batch, None, None
        except Exception as exc:
            return source_id, [], str(exc), None
    elif cfg["type"] == "gca_agreements" or source_id == "gca_agreements":
        from .gca_agreements import search_gca_agreements_first_page
        batch = search_gca_agreements_first_page(
            keyword=keyword,
            source_id=source_id,
            source_label=cfg["label"],
        )
        return source_id, batch, None, None
    elif cfg["type"] == "meta":
        from .meta_search import search_meta_portal
        batch = search_meta_portal(
            keyword=keyword,
            source_id=source_id,
            source_label=cfg["label"],
            domain=cfg["domain"],
        )
        return source_id, batch, None, None
    elif source_id == "gets_nz" or cfg.get("type") == "gets":
        from .gets_api import search_gets_api
        batch, err = search_gets_api(keyword=keyword)
        return source_id, batch, err, None
    elif source_id == "gebiz" or cfg.get("type") == "gebiz":
        from .gebiz_api import search_gebiz_api
        batch, err = search_gebiz_api(keyword=keyword)
        return source_id, batch, err, None
    elif source_id == "boamp" or cfg.get("type") == "boamp":
        from .boamp_api import search_boamp_api
        batch, err = search_boamp_api(keyword=keyword)
        return source_id, batch, err, None
    elif source_id == "bund" or cfg.get("type") == "bund":
        from .bund_api import search_bund_api
        batch, err = search_bund_api(keyword=keyword)
        return source_id, batch, err, None

    batch = search_bravo_first_page(
        site_root=cfg["site_root"],
        keyword=keyword,
        source_id=source_id,
        source_label=cfg["label"],
    )
    return source_id, batch, None, None


def _rows_by_source(
    merged: list[dict[str, Any]], sources: dict[str, dict[str, str]]
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {sid: [] for sid in sources}
    for row in merged:
        sid = row.get("source")
        if sid in out:
            out[sid].append(row)
    return out


def _finalize_job_meta(
    job: SearchJob,
    sources: dict[str, dict[str, str]],
    rows_by_source: dict[str, list[dict[str, Any]]],
) -> None:
    merged = _interleave_sources(rows_by_source)
    job.rows = merged
    # Use API-reported total_hint for portals still loading their full list,
    # so the sidebar immediately shows the correct total (e.g. 549) rather
    # than just the first-page batch count (e.g. 50).
    totals_hint = job.meta.get("source_totals_hint", {})
    progress = job.meta.get("source_progress", {})
    still_loading_states = {"loading", "first_page", "fetching_all", "pending"}
    source_counts = {}
    for sid in sources:
        fetched = len(rows_by_source.get(sid, []))
        hint = totals_hint.get(sid)
        is_loading = progress.get(sid, "pending") in still_loading_states
        # If still loading and we have an API hint that is larger than
        # what we've fetched so far, show the hint so the sidebar is accurate.
        if hint is not None and is_loading and hint > fetched:
            source_counts[sid] = hint
        else:
            source_counts[sid] = fetched
    job.meta["source_counts"] = source_counts
    job.meta["total"] = len(merged)
    # "loading" is exactly the portals the search is still waiting for: every portal that is finished
    # with, successfully or not (stopped and timed-out ones included), is settled and not listed.
    loading = [
        sid
        for sid in sources
        if job.meta.get("source_progress", {}).get(sid) not in SETTLED_STATES
    ]
    job.meta["loading_sources"] = loading
    job.meta["pending_sources"] = list(loading)
    job.meta["total_sources_count"] = len(sources)
    job.meta["totalExact"] = not loading
    job.meta["urgent_count"] = sum(
        1 for r in merged if r.get("deadline_urgency") == "critical"
    )
    job.meta["soon_count"] = sum(1 for r in merged if r.get("deadline_urgency") == "soon")


def _set_state(job: SearchJob, source_id: str, state: str) -> None:
    """Record a portal's progress as its worker reports it (job.lock held).

    Once a portal has settled (finished, failed, been stopped, or been given up on) its state is final
    as far as workers go: a page or a failure that arrives afterwards keeps its rows but cannot re-open
    the portal or change how it ended. The one exception is a portal that was timed out and then
    answers successfully after all: it becomes "complete", flagged `late`, and its rows are kept.
    (Retrying or resuming a portal resets it explicitly, outside this function.) The moment a portal
    settles is remembered, for its duration."""
    progress = job.meta.setdefault("source_progress", {})
    current = progress.get(source_id)
    if current in SETTLED_STATES:
        if not (current == "timeout" and state == "complete"):
            return
        job.meta.setdefault("_late", {})[source_id] = True
        job.meta.setdefault("errors", {}).pop(source_id, None)  # the "did not respond in time" note is out of date
    progress[source_id] = state
    if state in SETTLED_STATES:
        job.meta.setdefault("_portal_times", {}).setdefault(source_id, {}).setdefault("end", time.monotonic())


def _update_job_portal(
    job: SearchJob,
    sources: dict[str, dict[str, str]],
    source_id: str,
    batch: list[dict[str, Any]],
    *,
    progress: str,
    err: str | None = None,
    elapsed_sec: float | None = None,
    total_hint: int | None = None,
) -> None:
    # Filter out fake / error / synthetic rows before updating job state
    batch = [r for r in batch if not is_invalid_tender_row(r, job.query)]
    if source_id in LOOSE_MATCH_SOURCES:
        batch = [r for r in batch if matches_query(r, job.query, strict=True)]
    for row in batch:
        enrich_deadline_fields(row)
    batch.sort(key=_sort_key)

    with job.lock:
        rows_by_source = _rows_by_source(job.rows, sources)
        seeded = (job.meta.get("_seeded") or {}).get(source_id)
        if batch or not rows_by_source.get(source_id):
            rows_by_source[source_id] = batch
            if seeded and batch:
                job.meta["_seeded"].pop(source_id, None)
        elif seeded and progress == "complete" and not err:
            rows_by_source[source_id] = []  # the portal answered and has nothing now: the saved copy is out of date
            job.meta["_seeded"].pop(source_id, None)
        _set_state(job, source_id, progress)
        job.meta.setdefault("source_timings_sec", {})
        if elapsed_sec is not None:
            job.meta["source_timings_sec"][source_id] = elapsed_sec
        if total_hint is not None:
            job.meta.setdefault("source_totals_hint", {})[source_id] = total_hint
        if err:
            job.meta.setdefault("errors", {})[source_id] = err
        elif batch and source_id in (job.meta.get("errors") or {}):
            job.meta["errors"].pop(source_id, None)
        _finalize_job_meta(job, sources, rows_by_source)
        _complete_if_settled(job, sources)
        if job.cancelled or source_id in job.cancelled_sources:
            return


def _portal_worker(
    job: SearchJob,
    source_id: str,
    cfg: dict[str, str],
    keyword: str,
    sources: dict[str, dict[str, str]],
) -> None:
    keyword = keyword.strip()
    cache_key = (keyword.lower(), source_id)
    with job.lock:
        # the watchdog times a portal from here, not from when the search was submitted
        job.meta.setdefault("_portal_times", {})[source_id] = {"start": time.monotonic()}
        job.meta.setdefault("_late", {}).pop(source_id, None)
    try:
        # Check cache
        if cache_key in _PORTAL_RESULTS_CACHE:
            batch, err, timestamp, cached_notes = _PORTAL_RESULTS_CACHE[cache_key]
            if time.time() - timestamp < RESULTS_FRESH_SEC:
                if cached_notes:
                    with job.lock:
                        job.meta.setdefault("portal_notes", {}).setdefault(source_id, {}).update(cached_notes)
                _update_job_portal(
                    job,
                    sources,
                    source_id,
                    batch,
                    progress="complete",
                    err=err,
                    elapsed_sec=0.0,
                )
                return

        # Check if cancelled
        if job.cancelled or source_id in job.cancelled_sources:
            with job.lock:
                _set_state(job, source_id, "stopped")
                _finalize_job_meta(job, sources, _rows_by_source(job.rows, sources))
            return

        with job.lock:
            _set_state(job, source_id, "loading")
            # The last answer to this search (older than the 15-minute window) is shown at once, while the portal is
            # asked again; the fresh answer replaces it (or empties it, if the portal now has nothing).
            seed = _stale_answer(keyword, source_id)
            if seed and not _rows_by_source(job.rows, sources).get(source_id):
                seeded_rows = [dict(r) for r in seed[0]]
                rows_now = _rows_by_source(job.rows, sources)
                rows_now[source_id] = seeded_rows
                job.meta.setdefault("_seeded", {})[source_id] = seed[1]
                _finalize_job_meta(job, sources, rows_now)

        # Stagger requests going to the same family of gov.uk domains (Find a Tender and Contracts Finder)
        if source_id == "contracts_finder":
            time.sleep(1.5)

        # Phase 1 — first page (show in UI as soon as this portal responds)
        t0 = time.monotonic()
        notes: dict[str, Any] = {}
        sid, preview, err, total_hint = _fetch_source_first_page(source_id, cfg, keyword, notes)
        first_page_sec = round(time.monotonic() - t0, 1)
        if notes:
            with job.lock:
                job.meta.setdefault("portal_notes", {}).setdefault(source_id, {}).update(notes)
        _update_job_portal(
            job,
            sources,
            sid,
            preview,
            progress="first_page",
            err=err,
            elapsed_sec=first_page_sec,
            total_hint=total_hint,
        )
        _log(job, f"{source_id} first page: {len(preview)} rows in {first_page_sec}s" + (f", error: {err}" if err else ""))

        if err and not preview:
            stale = _stale_answer(keyword, source_id)
            if stale:
                _update_job_portal(job, sources, source_id, stale[0], progress="complete",
                                   err=_stale_message(cfg.get("label", source_id), stale[1], str(err)))
                return
            with job.lock:
                _set_state(job, source_id, "error")
                _finalize_job_meta(job, sources, _rows_by_source(job.rows, sources))
            return

        # NI, Meta and ProContract are each already a complete fetch in phase 1 above
        # (captcha/time/domain for NI and Meta; ProContract's own small page cap) -- skip a
        # second, duplicate full pass.
        if source_id == "etenders_ni" or cfg.get("type") in ("meta", "procontract", "eu_supply_ctm", "nepo_open"):
            with job.lock:
                _set_state(job, source_id, "complete")
                # loading_sources is derived from source_progress and only _finalize_job_meta
                # recomputes it. Without this, a portal that finished here stayed listed as still
                # loading until some other portal happened to update the job, so the "N of M
                # finished" banner under-counted (e.g. "1 of 3" with two portals already done).
                _finalize_job_meta(job, sources, _rows_by_source(job.rows, sources))
                _complete_if_settled(job, sources)
            _PORTAL_RESULTS_CACHE[cache_key] = (preview, err, time.time(), dict(notes))
            return

        # Phase 2 — full list for this portal (continues in parallel with other portals)
        # Check if cancelled
        if job.cancelled or source_id in job.cancelled_sources:
            with job.lock:
                _set_state(job, source_id, "stopped")
                _finalize_job_meta(job, sources, _rows_by_source(job.rows, sources))
            return

        with job.lock:
            _set_state(job, source_id, "fetching_all")

        t1 = time.monotonic()
        full_notes: dict[str, Any] = {}
        if source_id == "etenders_ie":
            # Ireland has no result cap, so a big search takes a while: show rows as pages
            # arrive instead of only when the whole list is done.
            def _push_partial(partial: list[dict[str, Any]]) -> None:
                _update_job_portal(job, sources, source_id, partial, progress="fetching_all")

            sid = source_id
            full_batch, completed = _fetch_ie_batch(
                source_id,
                cfg,
                keyword,
                on_progress=_push_partial,
                should_stop=lambda: job.cancelled or source_id in job.cancelled_sources,
            )
            full_err = None
            if not completed:
                return  # stopped part-way: keep the rows found so far, don't cache a partial list
        else:
            if source_id == "contracts_finder":
                # a cold Contracts Finder scan takes minutes: show matches as the feed downloads
                def _push_cf_partial(partial: list[dict[str, Any]]) -> None:
                    _update_job_portal(job, sources, source_id, partial, progress="fetching_all")

                sid, full_batch, full_err = _fetch_source_batch(
                    source_id, cfg, keyword, full_notes,
                    on_progress=_push_cf_partial,
                    should_stop=lambda: job.cancelled or source_id in job.cancelled_sources,
                )
            else:
                sid, full_batch, full_err = _fetch_source_batch(source_id, cfg, keyword, full_notes)
            if full_notes:
                with job.lock:
                    job.meta.setdefault("portal_notes", {}).setdefault(source_id, {}).update(full_notes)
        if full_batch or not full_err:
            _PORTAL_RESULTS_CACHE[cache_key] = (full_batch, full_err, time.time(), {**notes, **full_notes})
        full_sec = round(time.monotonic() - t1, 1)
        _update_job_portal(
            job,
            sources,
            sid,
            full_batch,
            progress="complete",
            err=full_err,
            elapsed_sec=full_sec,
        )
        _log(job, f"{source_id} complete: {len(full_batch)} rows in {full_sec}s" + (f", warning: {full_err}" if full_err else ""))
    except Exception as exc:  # noqa: BLE001
        _log(job, f"{source_id} FAILED: {exc}")
        stale = _stale_answer(keyword, source_id)
        if stale:
            try:
                _update_job_portal(job, sources, source_id, stale[0], progress="complete",
                                   err=_stale_message(cfg.get("label", source_id), stale[1], str(exc)))
                return
            except Exception as stale_exc:  # noqa: BLE001 - fall through to the plain error
                _log(job, f"{source_id} stale fallback failed: {stale_exc}")
        with job.lock:
            job.meta.setdefault("errors", {})[source_id] = str(exc)
            _set_state(job, source_id, "error")  # one the search already gave up on stays "timeout"
            _finalize_job_meta(job, sources, _rows_by_source(job.rows, sources))
    finally:
        with job.lock:
            if job.meta.get("source_progress", {}).get(source_id) not in SETTLED_STATES:
                # the worker ended without saying how: a recorded error means it failed, otherwise it finished
                _set_state(job, source_id, "error" if source_id in (job.meta.get("errors") or {}) else "complete")
            _finalize_job_meta(job, sources, _rows_by_source(job.rows, sources))
            _complete_if_settled(job, sources)


def _one_edit_apart(a: str, b: str) -> bool:
    """True when two different words differ by one letter added, dropped, changed or swapped ("matthewsgreen" /
    "mathewsgreen", "plumbing" / "lumbing")."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1:]


def spelling_variants(query: str, rows: list[dict[str, Any]], limit: int = 2) -> list[str]:
    """The query respelled the way a result's title spells it. A buyer who writes "Mathewsgreen" is found by a
    search for "Matthewsgreen" on the portal that matched the description, and the other portals, which match titles
    literally, are then asked for the buyer's spelling (QA S1)."""
    words = [w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(w) >= 6]
    out: list[str] = []
    if not words:
        # "lumbing" is 7 letters, but a short typo ("gas") is not worth guessing
        words = []
    for row in rows:
        for tw in set(re.findall(r"[a-z0-9]+", str(row.get("title") or "").lower())):
            if len(tw) < 5:
                continue
            for w in words:
                if _one_edit_apart(w, tw):
                    variant = re.sub(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", tw, query.lower()).strip()
                    if variant and variant != query.lower() and variant not in out:
                        out.append(variant)
                        if len(out) >= limit:
                            return out
    # A word that is not a known trade word but is one letter from one ("lumbing" -> "plumbing") is a typo even when
    # no result's title shows the right spelling.
    for w in [w for w in re.findall(r"[a-z]+", (query or "").lower()) if len(w) >= 5 and w not in SEARCH_VOCAB]:
        for v in sorted(SEARCH_VOCAB):
            if _one_edit_apart(w, v):
                variant = re.sub(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", v, query.lower()).strip()
                if variant and variant not in out:
                    out.append(variant)
                break
        if len(out) >= limit:
            break
    return out[:limit]


SEARCH_VOCAB = frozenset("""plumbing drainage heating electrical cleaning catering security maintenance construction refurbishment
roofing painting decorating landscaping grounds flooring joinery carpentry glazing scaffolding demolition asbestos
legionella ventilation lifts fire alarms lighting generators boilers gas water waste recycling transport vehicles
fleet laundry uniforms stationery furniture software hardware telecoms consultancy engineering surveying architect
planning training recruitment printing signage pest control insurance legal audit accountancy catering healthcare
nursing pharmacy ambulance housing repairs voids social care mechanical building facilities management""".split())


def _spelling_worker(job: SearchJob, source_id: str, cfg: dict[str, str], variant: str,
                     sources: dict[str, dict[str, str]]) -> None:
    """Ask one portal that found nothing for the buyer's spelling of the query, and add what it returns."""
    rows: list[dict[str, Any]] = []
    try:
        _sid, rows, _err, _hint = _fetch_source_first_page_one(source_id, cfg, variant, None)
    except Exception as exc:  # noqa: BLE001 - the portal's first answer (nothing found) stands
        _log(job, f"{source_id} spelling retry '{variant}' failed: {exc}")
        rows = []
    try:
        if rows:
            for row in rows:
                row.setdefault("matched_spelling", variant)
            _log(job, f"{source_id} spelling retry '{variant}': {len(rows)} rows")
            _update_job_portal(job, sources, source_id, rows, progress="complete")
    finally:
        with job.lock:
            _set_state(job, source_id, "complete")
            job.meta.setdefault("_portal_times", {}).setdefault(source_id, {})["end"] = time.monotonic()
            _finalize_job_meta(job, sources, _rows_by_source(job.rows, sources))
            _complete_if_settled(job, sources)


EARLY_SPELLING_AFTER_SEC = 12.0
EARLY_SPELLING_SHARE = 0.7


def _start_spelling_pass(job: SearchJob, sources: dict[str, dict[str, str]], early: bool = False) -> bool:
    """Once (job.lock held): portals that returned nothing are asked again with the spelling the results use. Normally
    that waits for every portal; `early` starts it once most portals have answered, so one slow portal (Public Contracts
    Scotland can take minutes) does not hold the retry back. An early attempt that has nothing to retry yet does not use
    up the one go. Returns True when a retry was started, so the job is not marked complete yet."""
    if job.meta.get("_spelling_pass") or job.cancelled:
        return False
    variants = spelling_variants(job.query, job.rows)
    if early:
        if not variants:
            return False
        progress0 = job.meta.get("source_progress", {})
        by0 = _rows_by_source(job.rows, sources)
        if not [sid for sid in sources if progress0.get(sid) == "complete" and not by0.get(sid)
                and sid not in (job.meta.get("errors") or {}) and sid not in job.cancelled_sources
                and sid not in LOOSE_MATCH_SOURCES]:
            return False
    job.meta["_spelling_pass"] = True
    if not variants:
        return False
    progress = job.meta.get("source_progress", {})
    by_source = _rows_by_source(job.rows, sources)
    empty = [sid for sid in sources if progress.get(sid) == "complete" and not by_source.get(sid)
             and sid not in (job.meta.get("errors") or {}) and sid not in job.cancelled_sources
             and sid not in LOOSE_MATCH_SOURCES]
    if not empty:
        return False
    job.meta["spelling_variant"] = variants[0]
    for sid in empty:
        progress[sid] = "loading"
        job.meta.setdefault("_portal_times", {})[sid] = {"start": time.monotonic()}
        threading.Thread(target=_spelling_worker, args=(job, sid, sources[sid], variants[0], sources),
                         name=f"spelling-{job.job_id}-{sid}", daemon=True).start()
    _finalize_job_meta(job, sources, by_source)
    _log(job, f"spelling retry '{variants[0]}' on {', '.join(empty)}")
    return True


def _complete_if_settled(job: SearchJob, sources: dict[str, dict[str, str]]) -> None:
    """Mark the job complete once every portal is settled (job.lock must be held).

    "Complete" is derived from this and nothing else: not from a timer, a row count or a worker tally,
    so the search can never report itself finished while a portal is still outstanding, nor stay
    unfinished after the last one settles."""
    progress = job.meta.get("source_progress", {})
    if job.status != "loading":
        return
    if not all(progress.get(sid) in SETTLED_STATES for sid in sources):
        settled = sum(1 for sid in sources if progress.get(sid) in SETTLED_STATES)
        if (sources and settled >= EARLY_SPELLING_SHARE * len(sources)
                and time.monotonic() - job.started_at >= EARLY_SPELLING_AFTER_SEC):
            try:
                _start_spelling_pass(job, sources, early=True)
            except Exception as exc:  # noqa: BLE001
                _log(job, f"early spelling pass failed: {exc}")
        return
    try:
        if _start_spelling_pass(job, sources):
            return
    except Exception as exc:  # noqa: BLE001 - a spelling retry is a nicety; never hold the search up on it
        _log(job, f"spelling pass failed: {exc}")
    job.status = "complete"
    _finalize_job_meta(job, sources, _rows_by_source(job.rows, sources))
    counts = job.meta.get("source_counts", {})
    errors = job.meta.get("errors", {})
    _log(
        job,
        f"finished in {round(time.monotonic() - job.started_at, 1)}s, {job.meta.get('total', 0)} rows: "
        + ", ".join(f"{sid}={progress.get(sid)}({counts.get(sid, 0)})" for sid in sources)
        + (f"; errors: { {k: str(v)[:80] for k, v in errors.items()} }" if errors else ""),
    )


# ── time limits ──────────────────────────────────────────────────────────────────────────────

def _give_up_on_portal(job: SearchJob, sources: dict[str, dict[str, str]], source_id: str, waited: float, why: str) -> None:
    """Mark one portal "timeout" (job.lock held). Its rows so far stay in the results."""
    label = sources.get(source_id, {}).get("label", source_id)
    rows_now = _rows_by_source(job.rows, sources)
    stale = None if rows_now.get(source_id) else _stale_answer(job.query, source_id)
    if stale:
        # Timed out with nothing new: keep the tenders this portal gave for the same query earlier.
        rows_now[source_id] = [dict(r) for r in stale[0]]
        job.meta.setdefault("source_progress", {})[source_id] = "complete"
        job.meta.setdefault("errors", {})[source_id] = _stale_message(label, stale[1], f"{label} {why} just now")
        job.meta.setdefault("_portal_times", {}).setdefault(source_id, {}).setdefault("end", time.monotonic())
        _log(job, f"{source_id} TIMEOUT after {int(waited)}s ({why}); showing {len(stale[0])} saved rows")
        _finalize_job_meta(job, sources, rows_now)
        _complete_if_settled(job, sources)
        return
    job.meta.setdefault("source_progress", {})[source_id] = "timeout"
    job.meta.setdefault("errors", {})[source_id] = f"{label} {why} (waited {int(waited)} s)"
    job.meta.setdefault("_portal_times", {}).setdefault(source_id, {}).setdefault("end", time.monotonic())
    _log(job, f"{source_id} TIMEOUT after {int(waited)}s ({why})")
    _finalize_job_meta(job, sources, _rows_by_source(job.rows, sources))
    _complete_if_settled(job, sources)


def _check_time_limits(job: SearchJob, sources: dict[str, dict[str, str]], now: float) -> None:
    """Time out every portal that has used up its budget (job.lock held)."""
    progress = job.meta.get("source_progress", {})
    times = job.meta.get("_portal_times", {})
    rows_by_source = _rows_by_source(job.rows, sources)
    for sid in sources:
        state = progress.get(sid)
        started = times.get(sid, {}).get("start")
        if state not in UNSETTLED_STATES or started is None:
            continue
        waited = now - started
        if state == "loading" and waited > first_page_budget(sid):
            _give_up_on_portal(job, sources, sid, waited, "did not respond in time")
        elif state in ("first_page", "fetching_all") and waited > full_list_budget(sid):
            have = len(rows_by_source.get(sid, []))
            _give_up_on_portal(
                job, sources, sid, waited,
                f"stopped loading after the first {have} result{'' if have == 1 else 's'}" if have else "did not respond in time",
            )


def _watch_job(job: SearchJob, sources: dict[str, dict[str, str]]) -> None:
    """Apply the portals' time limits for as long as the job is running."""
    while True:
        time.sleep(WATCHDOG_TICK_SEC)
        with job.lock:
            if job.status != "loading":
                # Cleared under the job lock, so a retry that re-opens the job (it sets the status under
                # this same lock, then asks for a watchdog) either is seen here or starts a new one.
                with _WATCHDOG_LOCK:
                    job.watchdog_running = False
                return
            _check_time_limits(job, sources, time.monotonic())


_WATCHDOG_LOCK = threading.Lock()


def _ensure_watchdog(job: SearchJob, sources: dict[str, dict[str, str]]) -> None:
    with _WATCHDOG_LOCK:
        if job.watchdog_running:
            return
        job.watchdog_running = True
    threading.Thread(target=_watch_job, args=(job, sources), daemon=True, name=f"watchdog-{job.job_id[:8]}").start()


def _start_worker(job: SearchJob, source_id: str, cfg: dict[str, str], sources: dict[str, dict[str, str]]) -> None:
    threading.Thread(
        target=_portal_worker,
        args=(job, source_id, cfg, job.query, sources),
        daemon=True,
        name=f"portal-{source_id}-{job.job_id[:8]}",
    ).start()


def start_progressive_search(keyword: str, scope: str) -> SearchJob:
    """Return immediately; each portal updates the job on its own schedule (parallel)."""
    sources = _sources_for_scope(scope)
    job_id = str(uuid.uuid4())
    job = SearchJob(
        job_id=job_id,
        query=keyword.strip(),
        scope=scope,
        status="loading",
        rows=[],
        meta={
            "errors": {},
            "source_counts": {sid: 0 for sid in sources},
            "source_progress": {sid: "pending" for sid in sources},
            "source_timings_sec": {},
            "source_totals_hint": {},
            "loading_sources": list(sources.keys()),
            "pending_sources": list(sources.keys()),
            "total_sources_count": len(sources),
            "total": 0,
            "totalExact": False,
            "urgent_count": 0,
            "soon_count": 0,
            "deadline_alert_days": 10,
        },
    )
    with _JOBS_LOCK:
        _JOBS[job_id] = job
        if len(_JOBS) > 50:
            oldest = sorted(_JOBS.values(), key=lambda j: j.started_at)[:10]
            for old in oldest:
                _JOBS.pop(old.job_id, None)

    if not sources:  # nothing to wait for
        with job.lock:
            job.status = "complete"
        return job

    # Start the watchdog first: a worker that stalls straight away must still be timed out.
    _ensure_watchdog(job, sources)
    for source_id, cfg in sources.items():
        _start_worker(job, source_id, cfg, sources)

    return job


# ── what each portal did, for the page ───────────────────────────────────────────────────────

def build_portal_results(
    meta: dict[str, Any],
    rows: list[dict[str, Any]],
    sources: dict[str, dict[str, str]],
    now: float | None = None,
) -> dict[str, dict[str, Any]]:
    """One typed record per portal in the search, so the page can say exactly what each one did.

        status       pending | running | ok | empty | timeout | error | stopped
        count        rows this portal returned to this search (not rows saved from earlier searches)
        durationMs   how long the portal took (so far, if it is still running)
        message      the reason, for timeout / error, or a warning when `partial`
        partial      rows were returned but are known to be incomplete
        available    how many notices the portal reports for the search, where it says (Find a Tender)
        late         the portal answered after its time limit (its rows are kept)

    "empty" means the portal answered and had nothing: it is only ever reported for a successful call."""
    now = now if now is not None else time.monotonic()
    progress = meta.get("source_progress", {})
    errors = meta.get("errors", {}) or {}
    times = meta.get("_portal_times", {}) or {}
    notes = meta.get("portal_notes", {}) or {}
    late = meta.get("_late", {}) or {}
    live = _rows_by_source(rows, sources)
    out: dict[str, dict[str, Any]] = {}
    for sid, cfg in sources.items():
        state = progress.get(sid, "pending")
        message = str(errors[sid]) if errors.get(sid) else None
        count = len(live.get(sid, []))
        t = times.get(sid, {})
        end = t.get("end", now if "start" in t else None)
        duration = int((end - t["start"]) * 1000) if end is not None and "start" in t else None
        partial = False
        if state == "pending":
            status = "pending"
        elif state in UNSETTLED_STATES:
            status = "running"
        elif state == "timeout":
            status, partial = "timeout", count > 0
        elif state == "stopped":
            status = "stopped"
        elif state == "error":
            status, partial = ("ok", True) if count else ("error", False)
            if not count and not message:
                message = "The portal returned an error"
        else:  # complete
            if count:
                status, partial = "ok", bool(message)  # a warning alongside rows: incomplete, not failed
            elif message:
                status = "error"
            else:
                status = "empty"
        out[sid] = {
            "portal": sid,
            "label": cfg.get("label", sid),
            "status": status,
            "count": count,
            "durationMs": duration,
            "message": message,
            "partial": partial,
            "available": notes.get(sid, {}).get("available"),
            "late": bool(late.get(sid)) and state == "complete",
        }
    return out


def job_snapshot(job: SearchJob) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """(rows, meta, status) as one consistent view. meta carries `portal_results`; the job's private
    bookkeeping (keys starting with "_") is left out."""
    with job.lock:
        rows = list(job.rows)
        meta = {k: v for k, v in job.meta.items() if not k.startswith("_")}
        meta["portal_results"] = build_portal_results(job.meta, rows, _sources_for_scope(job.scope))
        status = job.status
    # The same tender on two portals is linked, so a stale "Closed" on one shows the other's later date (A1).
    from etenders_scraper.sources.notice_links import link_same_tender
    try:
        rows = link_same_tender(rows)
    except Exception:  # noqa: BLE001 - linking is a nicety; never break the search on it
        pass
    return rows, meta, status


def stop_all_search(job_id: str) -> bool:
    job = get_job(job_id)
    if not job:
        return False
    with job.lock:
        job.cancelled = True
        for sid in job.meta.get("source_progress", {}):
            progress = job.meta["source_progress"][sid]
            if progress in ("pending", "loading", "fetching_all", "first_page"):
                job.meta["source_progress"][sid] = "stopped"
        job.meta["loading_sources"] = []
        job.status = "complete"
        
        sources = _sources_for_scope(job.scope)
        rows_by_source = _rows_by_source(job.rows, sources)
        _finalize_job_meta(job, sources, rows_by_source)
    return True


def stop_source_search(job_id: str, source_id: str) -> bool:
    job = get_job(job_id)
    if not job:
        return False
    with job.lock:
        job.cancelled_sources.add(source_id)
        progress = job.meta.get("source_progress", {}).get(source_id)
        if progress in ("pending", "loading", "fetching_all", "first_page"):
            job.meta.setdefault("source_progress", {})[source_id] = "stopped"
        if source_id in job.meta.get("loading_sources", []):
            job.meta["loading_sources"].remove(source_id)
        
        all_done = True
        for sid in job.meta.get("source_progress", {}):
            if job.meta["source_progress"][sid] in ("pending", "loading", "fetching_all", "first_page"):
                all_done = False
                break
        if all_done:
            job.status = "complete"
            job.meta["loading_sources"] = []
            
        sources = _sources_for_scope(job.scope)
        rows_by_source = _rows_by_source(job.rows, sources)
        _finalize_job_meta(job, sources, rows_by_source)
    return True


def resume_all_search(job_id: str) -> bool:
    job = get_job(job_id)
    if not job:
        return False
    with job.lock:
        if not job.cancelled and not job.cancelled_sources:
            return True
        job.cancelled = False
        sources_to_resume = []
        for sid, progress in job.meta.get("source_progress", {}).items():
            if progress == "stopped":
                sources_to_resume.append(sid)
                
        if not sources_to_resume:
            return True

        job.status = "loading"
        sources = _sources_for_scope(job.scope)
        for sid in sources_to_resume:
            job.cancelled_sources.discard(sid)
            job.meta["source_progress"][sid] = "pending"
            if sid not in job.meta.setdefault("loading_sources", []):
                job.meta["loading_sources"].append(sid)
            cfg = sources.get(sid)
            if cfg:
                _start_worker(job, sid, cfg, sources)
    _ensure_watchdog(job, _sources_for_scope(job.scope))
    return True


def resume_source_search(job_id: str, source_id: str) -> bool:
    job = get_job(job_id)
    if not job:
        return False
    with job.lock:
        progress = job.meta.get("source_progress", {}).get(source_id)
        if progress != "stopped":
            return True
            
        job.status = "loading"
        job.cancelled_sources.discard(source_id)
        job.meta["source_progress"][source_id] = "pending"
        if source_id not in job.meta.setdefault("loading_sources", []):
            job.meta["loading_sources"].append(source_id)
        sources = _sources_for_scope(job.scope)
        cfg = sources.get(source_id)
        if cfg:
            _start_worker(job, source_id, cfg, sources)
    _ensure_watchdog(job, _sources_for_scope(job.scope))
    return True


def retry_source_search(job_id: str, source_id: str) -> bool | None:
    """Run one portal of a finished (or running) search again, keeping the other portals' results.

    Returns None when the job is unknown, False when the portal is not part of it, True otherwise (also
    when the portal is already running, so a double click does not start it twice). The rows the portal
    had are replaced only if the new attempt brings rows, so a failed retry never empties the table."""
    job = get_job(job_id)
    if not job:
        return None
    sources = _sources_for_scope(job.scope)
    cfg = sources.get(source_id)
    if cfg is None:
        return False
    with job.lock:
        state = job.meta.get("source_progress", {}).get(source_id)
        if state in UNSETTLED_STATES:
            return True
        # a fresh attempt: forget what the old one said (a late answer from it is ignored by the
        # settled-state guard in _update_job_portal only while it is still marked settled)
        job.cancelled_sources.discard(source_id)
        job.meta.setdefault("errors", {}).pop(source_id, None)
        job.meta.setdefault("portal_notes", {}).pop(source_id, None)
        job.meta.setdefault("_late", {}).pop(source_id, None)
        job.meta["source_progress"][source_id] = "pending"
        _PORTAL_RESULTS_CACHE.pop((job.query.strip().lower(), source_id), None)
        job.status = "loading"
        _finalize_job_meta(job, sources, _rows_by_source(job.rows, sources))
        _start_worker(job, source_id, cfg, sources)
    _log(job, f"{source_id} retry requested")
    _ensure_watchdog(job, sources)
    return True
