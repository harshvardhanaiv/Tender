"""Scraper for Contracts Finder (contractsfinder.service.gov.uk) using the official OCDS API."""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from dateutil import parser as date_parser
from ..client import SSLAdapter

_API_BASE = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"


def format_iso_date(iso_str: str) -> str:
    """Convert an ISO-8601 date string to standard format used by the application."""
    if not iso_str:
        return ""
    try:
        dt = date_parser.isoparse(iso_str)
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return iso_str


def _request_with_backoff(
    session: requests.Session,
    url: str,
    params: dict[str, Any],
    max_retries: int = 3,
    initial_delay: float = 2.0,
    timeout: int = 20,
) -> requests.Response:
    """Execute GET request with exponential backoff and explicit Retry-After header support."""
    last_resp: requests.Response | None = None
    for attempt in range(max_retries + 1):
        resp = session.get(url, params=params, timeout=timeout)
        last_resp = resp
        if resp.status_code == 429 or resp.status_code == 503:
            if attempt < max_retries:
                retry_after_str = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                sleep_time = 0.0
                if retry_after_str:
                    try:
                        sleep_time = float(retry_after_str)
                    except ValueError:
                        sleep_time = 0.0
                if sleep_time <= 0:
                    sleep_time = initial_delay * (2 ** attempt) + 0.2
                time.sleep(sleep_time)
                continue
        return resp
    return last_resp if last_resp is not None else resp


# How far back to look for tender notices; older ones are almost never still open.
_LOOKBACK_DAYS = 180
# Runaway-loop guard only (40,000 notices, far more than 180 days of tender notices). The feed
# is followed until it runs out; hitting this is logged.
_MAX_RELEASE_PAGES = 400
_PAGE_DELAY_SECS = 0.5  # polite spacing between pages
_PAGE_RETRIES = 2  # extra attempts at one page after a transient error (timeout, reset, bad JSON)
# The API has no keyword filter, so every search has to scan the whole feed: a few hundred
# sequential requests, roughly 6-9 minutes (~0.9s per request plus the polite delay above). The
# scanned feed is kept in a shared cache so searches for different keywords reuse one download:
#   - it is fresh for _FEED_CACHE_TTL_SECS;
#   - after that it is still served, up to _FEED_MAX_STALE_SECS old, while ONE background scan
#     refreshes it (stale-while-revalidate), so once the cache has been warmed at all a search
#     never has to wait for a scan;
#   - start_feed_prewarm() warms it at app start and keeps a snapshot of each completed scan in
#     the database, so a redeploy or restart doesn't start cold either. Before this, the first
#     search in any quiet 15-minute window (and every first search after a deploy) sat at
#     "1 of 3 finished" for minutes while this portal scanned -- the "Tender Search hang".
_FEED_CACHE_TTL_SECS = 15 * 60
_FEED_MAX_STALE_SECS = 6 * 60 * 60
_REFRESH_COOLDOWN_SECS = 5 * 60  # first back-off after a failed refresh; doubles per consecutive failure, capped at 1h
_WARM_STARTUP_DELAY_SECS = 20
_WARM_CHECK_SECS = 5 * 60
_SNAPSHOT_VERSION = 1  # bump when the entry/row shape changes so old snapshots are ignored
_MIN_FEED_ENTRIES = 200  # a real 180-day scan is thousands of notices; far fewer means a degraded API
_feed_cache: dict[str, Any] = {"key": None, "at": 0.0, "entries": []}
_feed_lock = threading.Lock()  # held for the length of a scan: one download at a time
_refresh_guard = threading.Lock()  # at most one background refresh thread
_refresh_state = {"failures": 0, "next_allowed": 0.0}
_snapshot_store: Any = None  # set by start_feed_prewarm(); has load(key, version) / save(key, version, scanned_at, entries)
_warm_started = False


def _new_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", SSLAdapter())
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def _fetch_release_page(session: requests.Session, params: dict[str, Any]) -> tuple[requests.Response, dict[str, Any] | None]:
    """One feed page, retrying transient errors. A scan is a few hundred sequential requests, and
    giving up on the first timeout or connection reset ended it early with only the pages so far
    (returned as if complete, never cached), so one hiccup meant partial results and a cold cache
    on the next search too. A 429 comes back as (response, None) for the caller to handle."""
    last_exc: Exception | None = None
    for attempt in range(_PAGE_RETRIES + 1):
        try:
            resp = _request_with_backoff(session, _API_BASE, params=params, max_retries=3, initial_delay=2.0, timeout=20)
            if resp.status_code == 429:
                return resp, None
            resp.raise_for_status()
            return resp, resp.json()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < _PAGE_RETRIES:
                time.sleep(1.0 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _iter_release_pages(session: requests.Session, state: dict[str, Any]):
    """Yield the latest tender notices one API page at a time, until the feed runs out.

    Sets state["complete"] only when the feed genuinely ended, so the caller can tell a full
    scan from one cut short by a rate limit or error (state["reason"] then says why, for the
    warning the user sees). A failure before any page arrives raises; after that, the pages
    already yielded are kept."""
    since_date = datetime.utcnow() - timedelta(days=_LOOKBACK_DAYS)
    params: dict[str, Any] = {
        "publishedFrom": since_date.strftime("%Y-%m-%dT%H:%M:%S"),
        "stages": "tender",
        "limit": 100,
    }
    cursor = None
    fetched_any = False

    for page_idx in range(_MAX_RELEASE_PAGES):
        if cursor:
            params["cursor"] = cursor
        if page_idx > 0:
            time.sleep(_PAGE_DELAY_SECS)

        try:
            resp, data = _fetch_release_page(session, params)
            if resp.status_code == 429:
                if not fetched_any:
                    raise RuntimeError("Contracts Finder is temporarily rate-limited (HTTP 429). Please try again in a few moments.")
                state["reason"] = f"rate-limited by Contracts Finder (HTTP 429) after {page_idx} pages"
                print(f"[contracts_finder] Rate-limited after {page_idx} pages; returning what was fetched.")
                return
        except Exception as exc:
            if not fetched_any:
                raise
            state["reason"] = f"a request failed after {page_idx} pages ({str(exc)[:100]})"
            print(f"[contracts_finder] Fetch failed after {page_idx} pages; returning what was fetched.")
            return

        page_releases = data.get("releases", [])
        if not page_releases:
            if not fetched_any:
                # 180 days of tender notices is tens of thousands, so an empty FIRST page means the
                # API isn't serving data (it answers HTTP 200 with no releases when throttled or in
                # maintenance), not that the feed is empty. Treating that as a finished scan cached
                # "no notices" over a good cache and made every search report Contracts Finder
                # complete with 0 rows and no error.
                raise RuntimeError("Contracts Finder returned no notices -- its API is not serving data right now. Try again shortly.")
            # The same throttling can start part way through. The real end of the feed is a page with
            # no next link (below), so an empty page before that is a cut-short scan: calling it
            # complete cached a truncated feed (e.g. only the newest few thousand notices) and
            # served it for hours.
            state["reason"] = f"Contracts Finder stopped returning notices after {page_idx} pages"
            print(f"[contracts_finder] Empty page after {page_idx} pages; treating the scan as incomplete.")
            return
        fetched_any = True
        yield page_releases

        next_url = data.get("links", {}).get("next", "")
        next_query = parse_qs(urlparse(next_url).query) if next_url else {}
        cursor = next_query.get("cursor", [None])[0]
        if not cursor:
            state["complete"] = True
            return
        # The API rejects a cursor (HTTP 400) unless the request repeats every parameter of its own
        # next link, including the publishedTo it adds. Sending only the cursor stopped the scan
        # after the first page, so follow the next link's parameters as given.
        params = {k: v[0] for k, v in next_query.items()}

    state["reason"] = f"it hit the {_MAX_RELEASE_PAGES}-page safety limit"
    print(f"[contracts_finder] Stopped at the {_MAX_RELEASE_PAGES}-page safety limit; results may be incomplete.")


def _release_to_entry(release: dict[str, Any], source_id: str, source_label: str) -> tuple[str, dict[str, Any]] | None:
    """Turn an OCDS release into (lower-cased search text, result row), or None if it has no ocid."""
    tender = release.get("tender", {})
    title = tender.get("title", "")
    description = tender.get("description", "")
    ocid = release.get("ocid", "")
    if not ocid:
        return None

    # Check items for classifications or descriptions
    items_text = ""
    cpv_list: list[str] = []
    for item in tender.get("items", []):
        classification = item.get("classification", {})
        items_text += f" {classification.get('id', '')} {classification.get('description', '')} {item.get('description', '')}"
        cpv_id = classification.get("id")
        cpv_desc = classification.get("description")
        if cpv_id:
            cpv_list.append(f"{cpv_id} - {cpv_desc}" if cpv_desc else cpv_id)

    # Extract awarded suppliers from release awards if present
    suppliers = []
    for award in release.get("awards", []):
        for s in award.get("suppliers", []):
            s_name = s.get("name")
            if s_name:
                suppliers.append(s_name)
    supplier_str = ", ".join(dict.fromkeys(suppliers))
    buyer_name = release.get("buyer", {}).get("name") or ""

    search_blob = f"{title} {description} {items_text} {buyer_name} {supplier_str} {ocid} {release.get('id', '')}".lower()

    # Extract notice UUID from release ID (usually format: {uuid}-{numeric_id} or just {uuid})
    rel_id = release.get("id") or ""
    notice_uuid = rel_id.rsplit("-", 1)[0] if "-" in rel_id else rel_id

    # Format estimate value string
    val_amount = tender.get("value", {}).get("amount")
    val_str = str(val_amount) if val_amount is not None else ""

    # Get duration
    duration_val = tender.get("contractPeriod", {}).get("durationInDays")
    duration_str = f"{duration_val} days" if duration_val is not None else ""

    # Format the row to match the app structure
    row = {
        "title": title,
        "resource_id": ocid,
        "ocid": ocid,
        "contracting_authority": buyer_name or "Unknown Buyer",
        "supplier_name": supplier_str,
        "awarded_supplier": supplier_str,
        "description": description,
        "estimated_value_eur": val_str,
        "submission_deadline": format_iso_date(tender.get("tenderPeriod", {}).get("endDate", "")),
        "procedure": tender.get("procurementMethodDetails", tender.get("procurementMethod", "")),
        "procurement_type": tender.get("mainProcurementCategory", ""),
        "date_published": format_iso_date(release.get("date", "")),
        "detail_url": f"https://www.contractsfinder.service.gov.uk/Notice/{notice_uuid}",
        "status": tender.get("status", "Active").title(),
        "source": source_id,
        "source_label": source_label,
        "cpv_codes": ", ".join(cpv_list),
        "contract_duration_in_months_or_years_including_any_options_and_renewals": duration_str,
        "contract_awarded_in_lots": "Yes" if tender.get("lots") else "No",
    }
    return search_blob, row


def _fresh_feed_entries(key: str) -> list[tuple[str, dict[str, Any]]] | None:
    if _feed_cache["key"] == key and (time.time() - _feed_cache["at"]) < _FEED_CACHE_TTL_SECS:
        return _feed_cache["entries"]
    return None


def _servable_feed_entries(key: str) -> list[tuple[str, dict[str, Any]]] | None:
    """Cached entries a search can use right now: fresh ones, or stale ones (up to
    _FEED_MAX_STALE_SECS old) with a background refresh kicked off for the next search."""
    if _feed_cache["key"] != key or not _feed_cache["entries"]:
        return None
    age = time.time() - _feed_cache["at"]
    if age < _FEED_CACHE_TTL_SECS:
        return _feed_cache["entries"]
    if age < _FEED_MAX_STALE_SECS:
        _start_background_refresh(key)
        return _feed_cache["entries"]
    return None


def _scan_feed(
    session: requests.Session,
    source_id: str,
    source_label: str,
    key: str,
    note: list[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Download the whole lookback window; the caller holds _feed_lock. Only a scan that reached
    the end of the feed is cached (and snapshotted); one cut short by a rate limit or an error is
    returned but not reused, so the next search tries again. Why a scan fell short is appended to
    `note` so the search can tell the user its results are partial instead of presenting them as
    complete."""
    state: dict[str, Any] = {"complete": False}
    entries: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for page in _iter_release_pages(session, state):
        for release in page:
            entry = _release_to_entry(release, source_id, source_label)
            if entry and entry[1]["ocid"] not in seen:
                seen.add(entry[1]["ocid"])
                entries.append(entry)
    cached = False
    if state["complete"]:
        cached = _worth_caching(key, entries)
        if cached:
            scanned_at = time.time()
            _feed_cache.update(key=key, at=scanned_at, entries=entries)
            _save_snapshot(key, scanned_at, entries)
    if not cached and note is not None:
        note.append(state.get("reason") or f"it returned only {len(entries)} notices")
    return entries


def _worth_caching(key: str, entries: list[tuple[str, dict[str, Any]]]) -> bool:
    """A finished scan is only cached (and snapshotted) if it looks like a real 180-day feed. A
    throttled or degraded API can finish "successfully" with a handful of notices, and caching
    that would replace a good copy -- in memory and in the database snapshot -- with a bad one."""
    if len(entries) < _MIN_FEED_ENTRIES:
        print(f"[contracts_finder] Not caching a scan of only {len(entries)} notices (the API looks degraded).")
        return False
    have = len(_feed_cache["entries"]) if _feed_cache["key"] == key else 0
    if have >= 2 * len(entries):
        print(f"[contracts_finder] Keeping the cached feed ({have} notices): this scan found only {len(entries)}.")
        return False
    return True


def _all_feed_entries(
    session: requests.Session,
    source_id: str,
    source_label: str,
    note: list[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Every entry in the lookback window, scanning only when there is nothing usable cached.

    Held under a lock so concurrent searches wait for one download rather than each starting
    their own (including one already running as a background refresh)."""
    key = f"{source_id}:{source_label}"
    with _feed_lock:
        cached = _fresh_feed_entries(key)
        if cached is not None:
            return cached
        return _scan_feed(session, source_id, source_label, key, note)


def _refresh_allowed() -> bool:
    return time.time() >= _refresh_state["next_allowed"]


def _note_refresh_result(ok: bool) -> None:
    """Back off exponentially (5, 10, 20, 40, then 60 minutes) after consecutive failed background
    refreshes. Contracts Finder rate-limits hard (a few dozen quick requests got a test machine
    429s, and a throttled API answers 200 with no notices), so retrying a failing scan every few
    minutes, around the clock, would keep the production server throttled."""
    if ok:
        _refresh_state["failures"] = 0
        _refresh_state["next_allowed"] = 0.0
        return
    _refresh_state["failures"] += 1
    delay = min(60 * 60.0, _REFRESH_COOLDOWN_SECS * 2 ** (_refresh_state["failures"] - 1))
    _refresh_state["next_allowed"] = time.time() + delay
    print(f"[contracts_finder] Feed refresh failed ({_refresh_state['failures']} in a row); next attempt in {int(delay // 60)} min.")


def _refresh_feed(key: str) -> None:
    source_id, source_label = key.split(":", 1)
    with _feed_lock:
        if _fresh_feed_entries(key) is not None:
            return  # another scan finished while this one waited for the lock
        before = _feed_cache["at"] if _feed_cache["key"] == key else 0.0
        try:
            _scan_feed(_new_session(), source_id, source_label, key)
        finally:
            _note_refresh_result(_feed_cache["key"] == key and _feed_cache["at"] > before)


def _start_background_refresh(key: str) -> None:
    """Refresh the shared cache in a background thread: at most one at a time, and not while
    backing off after failed attempts (see _note_refresh_result)."""
    if not _refresh_allowed():
        return
    if not _refresh_guard.acquire(blocking=False):
        return

    def _run() -> None:
        try:
            _refresh_feed(key)
        except Exception as exc:  # noqa: BLE001
            print(f"[contracts_finder] Background feed refresh failed: {exc}")
        finally:
            _refresh_guard.release()

    threading.Thread(target=_run, daemon=True, name="CFFeedRefresh").start()


def _save_snapshot(key: str, scanned_at: float, entries: list[tuple[str, dict[str, Any]]]) -> None:
    store = _snapshot_store
    if store is None:
        return
    try:
        store.save(key, _SNAPSHOT_VERSION, scanned_at, entries)
    except Exception as exc:  # noqa: BLE001
        print(f"[contracts_finder] Could not save the feed snapshot: {exc}")


def _restore_snapshot(key: str) -> bool:
    """Load the last saved scan into the shared cache if it is still usable (and newer than what
    is already cached)."""
    store = _snapshot_store
    if store is None:
        return False
    snap = store.load(key, _SNAPSHOT_VERSION)
    if not snap:
        return False
    scanned_at, entries = snap
    if not entries or time.time() - scanned_at >= _FEED_MAX_STALE_SECS:
        return False
    if len(entries) < _MIN_FEED_ENTRIES:
        # Saved by an older build that cached whatever a throttled API returned; serving it would
        # make every search look at a few notices for hours.
        print(f"[contracts_finder] Ignoring a saved feed snapshot of only {len(entries)} notices.")
        return False
    if _feed_cache["key"] == key and _feed_cache["at"] >= scanned_at:
        return False
    _feed_cache.update(key=key, at=scanned_at, entries=entries)
    return True


def start_feed_prewarm(source_id: str, source_label: str, store: Any = None) -> None:
    """Keep the shared feed cache warm in the background: restore the last saved scan at startup,
    refresh it if it is stale, and re-scan whenever it has gone half way to too old to serve, so
    the cache never goes cold on a running server even if nobody searches for hours. Production
    only (server.create_app starts it with the other schedulers): a scan is hundreds of requests
    to a government API."""
    global _snapshot_store, _warm_started
    if _warm_started:
        return
    _warm_started = True
    _snapshot_store = store
    key = f"{source_id}:{source_label}"

    def _run() -> None:
        time.sleep(_WARM_STARTUP_DELAY_SECS)
        try:
            if _restore_snapshot(key):
                print("[contracts_finder] Feed cache restored from the last saved snapshot.")
        except Exception as exc:  # noqa: BLE001
            print(f"[contracts_finder] Could not restore the feed snapshot: {exc}")
        threshold = _FEED_CACHE_TTL_SECS
        while True:
            try:
                has_cache = _feed_cache["key"] == key and bool(_feed_cache["entries"])
                age = time.time() - _feed_cache["at"] if has_cache else None
                if (age is None or age >= threshold) and _refresh_allowed():
                    _refresh_feed(key)
            except Exception as exc:  # noqa: BLE001
                print(f"[contracts_finder] Feed warm-up failed: {exc}")
            threshold = _FEED_MAX_STALE_SECS / 2
            time.sleep(_WARM_CHECK_SECS)

    threading.Thread(target=_run, daemon=True, name="CFFeedWarm").start()


def _entry_batches(
    session: requests.Session,
    source_id: str,
    source_label: str,
    *,
    preview: bool,
    notes: list[str] | None = None,
):
    """Yield lists of (search text, row) entries to match against.

    A preview (max_results set) scans lazily up to 5 pages so it can stop early and show results fast; a
    full search scans everything, or reuses the shared cache (stale entries included -- see
    _servable_feed_entries). If a full scan falls short, why goes into `notes`."""
    key = f"{source_id}:{source_label}"
    cached = _servable_feed_entries(key)
    if cached is not None:
        yield cached
    elif preview:
        max_preview_pages = 5
        page_count = 0
        for page in _iter_release_pages(session, {}):
            yield [e for e in (_release_to_entry(r, source_id, source_label) for r in page) if e]
            page_count += 1
            if page_count >= max_preview_pages:
                break
    else:
        yield _all_feed_entries(session, source_id, source_label, notes)


def search_contracts_finder(
    *,
    keyword: str,
    source_id: str,
    source_label: str,
    max_results: int | None = None,
    notes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Search UK Contracts Finder tender notices from the last 180 days.

    The OCDS API has no keyword parameter, so the notice feed is scanned in full (or, for a
    preview, until max_results matches are found) and filtered by keyword client-side. When the
    full scan could not finish (rate limit, errors), the rows found are still returned and the
    reason is appended to `notes` -- callers should show it, because the rows are then only the
    newest part of the feed."""
    session = _new_session()
    keyword_lower = keyword.strip().lower()
    matched_rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for entries in _entry_batches(session, source_id, source_label, preview=bool(max_results), notes=notes):
        for search_blob, row in entries:
            if row["ocid"] in seen:
                continue
            if keyword_lower and keyword_lower not in search_blob:
                continue
            seen.add(row["ocid"])
            matched_rows.append(dict(row))  # callers add fields to rows, so never hand out the cached dict
            if max_results and len(matched_rows) >= max_results:
                return matched_rows

    return matched_rows


def search_contracts_finder_first_page(
    *,
    keyword: str,
    source_id: str,
    source_label: str,
) -> list[dict[str, Any]]:
    """Fetch first page (100 results) and filter by keyword for fast initial dashboard preview."""
    return search_contracts_finder(
        keyword=keyword,
        source_id=source_id,
        source_label=source_label,
        max_results=20,  # limit to 20 on first page
    )


def fetch_contracts_finder_details(resource_id: str) -> dict[str, Any] | None:
    """Fetch the specific notice release package from OCDS API by Notice ID or OCID."""
    session = requests.Session()
    session.mount("https://", SSLAdapter())
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    # For Contracts Finder, single notice details can be fetched from the OCDS Record endpoint:
    # GET https://www.contractsfinder.service.gov.uk/Published/OCDS/Record/{ocid}
    # However, since some IDs are notice IDs and others are OCIDs, we can try to fetch by OCID or Notice ID format.
    url = f"https://www.contractsfinder.service.gov.uk/Published/OCDS/Record/{resource_id}"
    try:
        resp = session.get(url, timeout=20)
        # If record API fails (e.g. because it's not a valid OCID), fall back to standard record lookup
        if resp.status_code != 200:
            return None
        data = resp.json()
        
        # Check if record has compiledRelease
        compiled_release = data.get("compiledRelease", {})
        if not compiled_release:
            # Maybe it is just a package response
            releases = data.get("releases", [])
            if not releases:
                return None
            compiled_release = releases[0]
            
        tender = compiled_release.get("tender", {})

        parties = compiled_release.get("parties", [])
        contact_name = ""
        contact_email = ""
        contact_phone = ""
        buyer_party = None
        for party in parties:
            roles = party.get("roles", [])
            if "buyer" in roles or "procuringEntity" in roles:
                buyer_party = party
                break
        if not buyer_party and parties:
            for party in parties:
                if party.get("contactPoint"):
                    buyer_party = party
                    break
        if buyer_party:
            cp = buyer_party.get("contactPoint", {})
            contact_name = cp.get("name") or buyer_party.get("name") or ""
            contact_email = cp.get("email", "")
            contact_phone = cp.get("telephone", "")
        
        # Parse items into friendly CPV code list
        cpv_list: list[str] = []
        for item in tender.get("items", []):
            classification = item.get("classification", {})
            cpv_id = classification.get("id")
            cpv_desc = classification.get("description")
            if cpv_id:
                cpv_list.append(f"{cpv_id} - {cpv_desc}" if cpv_desc else cpv_id)
                
        # Format estimate value
        val_amount = tender.get("value", {}).get("amount")
        val_curr = tender.get("value", {}).get("currency", "GBP")
        val_str = f"{val_amount} {val_curr}" if val_amount is not None else ""
        
        # Get duration
        duration_val = tender.get("contractPeriod", {}).get("durationInDays")
        duration_str = f"{duration_val} days" if duration_val is not None else ""
        
        rel_id = compiled_release.get("id") or ""
        if "-" in rel_id:
            notice_uuid = rel_id.rsplit("-", 1)[0]
        else:
            notice_uuid = rel_id
            
        detail = {
            "resource_id": resource_id,
            "title": tender.get("title", ""),
            "description": tender.get("description", ""),
            "estimated_value_eur": val_str,
            "cpv_codes": ", ".join(cpv_list),
            "procedure": tender.get("procurementMethodDetails", tender.get("procurementMethod", "")),
            "procurement_type": tender.get("mainProcurementCategory", ""),
            "submission_deadline": format_iso_date(tender.get("tenderPeriod", {}).get("endDate", "")),
            "contract_duration_in_months_or_years_including_any_options_and_renewals": duration_str,
            "end_of_clarification_period": "",
            "allow_suppliers_to_make_an_online_expression_of_interest": "",
            "contract_awarded_in_lots": "Yes" if tender.get("lots") else "No",
            "eu_funding": "",
            "date_of_publication_invitation": format_iso_date(compiled_release.get("date", "")),
            "ted_links_for_published_notices": "",
            "detail_url": f"https://www.contractsfinder.service.gov.uk/Notice/{notice_uuid}",
            "contact_name": contact_name,
            "contact_email": contact_email,
            "contact_phone": contact_phone,
        }
        return detail
    except Exception:
        return None
