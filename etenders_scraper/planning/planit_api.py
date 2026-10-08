"""PlanIt (planit.org.uk) API client — UK planning applications, 420 authorities.

PlanIt is run by one person and is explicitly not a commercial service. Its FAQ asks
callers to make at most one /api/applics request per minute, run bulk work overnight
(18:00-06:00), honour Retry-After on 429, stay under ~300 requests a day, and send a
real User-Agent ("I will block requests without a valid user agent"). Every one of
those is enforced here rather than left to callers, because a caller that forgets gets
the whole product blocked.

Everything in this module is behind `fetch_applications`, so swapping to a paid feed
(PlanWire, PlanAPI, Searchland) later means writing one sibling module with the same
signature and flipping PLANNING_SOURCE.
"""
from __future__ import annotations

import threading
import time
from datetime import date
from typing import Any

import requests

PLANIT_APPLICS_URL = "https://www.planit.org.uk/api/applics/json"
PLANIT_AREAS_URL = "https://www.planit.org.uk/api/areas/json"

# Per PlanIt's FAQ. Do not lower these.
MIN_REQUEST_INTERVAL_SECS = 60.0
DAILY_REQUEST_CAP = 300
MAX_PAGE_SIZE = 300

# PlanIt caps the total result set per query, so a query that would match more than
# this must be split into narrower windows rather than paged further. See
# harvester.iter_windows().
MAX_RESULTS_PER_QUERY = 5000

# Same contact as scripts/contracts_finder_sync.py's DEFAULT_USER_AGENT, so PlanIt's
# operator can reach us rather than just block us.
USER_AGENT = (
    "TenderFlow/1.0 (+https://tender.civenta.co.uk; planning leads harvester; "
    "contact: data@tenderflow.co.uk)"
)

# PlanIt substitutes this string for fields the source LPA does not expose publicly.
# It is not data and must never be stored.
PLACEHOLDER_VALUES = {"see source", "not available", "n/a", "none", "-", ""}

_rate_lock = threading.Lock()
_last_request_at: float = 0.0


class PlanItRateLimited(Exception):
    """Raised when PlanIt returns 429 and the backoff budget is exhausted."""


def _throttle() -> None:
    """Block until at least MIN_REQUEST_INTERVAL_SECS has passed since the last call.

    Uses a monotonic clock so a system clock change cannot collapse the interval, and
    holds the lock across the sleep so concurrent callers queue rather than all wake
    at once and fire together.
    """
    global _last_request_at
    with _rate_lock:
        elapsed = time.monotonic() - _last_request_at
        wait = MIN_REQUEST_INTERVAL_SECS - elapsed
        if wait > 0 and _last_request_at > 0.0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _retry_after_secs(resp: requests.Response, fallback: float) -> float:
    raw = (resp.headers.get("Retry-After") or "").strip()
    if raw.isdigit():
        return min(float(raw), 600.0)
    return fallback


def _get(url: str, params: dict[str, Any], timeout: int, max_retries: int = 3) -> tuple[dict | None, str | None]:
    """Single throttled GET with adaptive backoff. Returns (json, error_or_None)."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    backoff = 120.0

    for attempt in range(max_retries):
        _throttle()
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            if attempt == max_retries - 1:
                return None, f"PlanIt request failed: {exc}"
            time.sleep(backoff)
            backoff *= 2
            continue

        if resp.status_code == 429:
            if attempt == max_retries - 1:
                raise PlanItRateLimited(
                    "PlanIt returned 429 after %d attempts; harvest aborted to stay "
                    "within its published limits" % max_retries
                )
            time.sleep(_retry_after_secs(resp, backoff))
            backoff *= 2
            continue

        if resp.status_code != 200:
            return None, f"PlanIt HTTP {resp.status_code} for {resp.url}"

        try:
            return resp.json(), None
        except ValueError as exc:
            return None, f"PlanIt returned non-JSON: {exc}"

    return None, "PlanIt request exhausted retries"


def _iso(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def fetch_applications(
    *,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    decided_start: date | str | None = None,
    decided_end: date | str | None = None,
    app_size: str | None = None,
    app_type: str | None = None,
    authority: str | None = None,
    page: int = 1,
    pg_sz: int = MAX_PAGE_SIZE,
    timeout: int = 60,
) -> tuple[list[dict[str, Any]], str | None, int | None]:
    """Fetch one page of planning applications.

    Returns (raw_records, error_or_None, total_or_None). Records are PlanIt's own
    shape — normalize.normalize_planit_record() converts them to our columns.

    `page` is 1-based (verified: pg_sz=2&page=2 returns offsets 2-3).

    Window on `start_date`/`end_date` (submission) or `decided_start`/`decided_end`.
    PlanIt's changed/different windows are deliberately not exposed: PlanIt bumps
    last_changed on every re-scrape, so a 3-day changed window for Large schemes alone
    matched 6,557 records (vs 13 new submissions) — over the per-query cap and many
    times the nightly budget. Measured 2026-09-17.
    """
    params: dict[str, Any] = {
        "pg_sz": min(int(pg_sz), MAX_PAGE_SIZE),
        "page": max(1, int(page)),
        "compress": "1",
        "sort": "-start_date",
    }
    if decided_start is not None:
        params["decided_start"] = _iso(decided_start)
    if decided_end is not None:
        params["decided_end"] = _iso(decided_end)
    if start_date is not None:
        params["start_date"] = _iso(start_date)
    if end_date is not None:
        params["end_date"] = _iso(end_date)
    if app_size:
        params["app_size"] = app_size
    if app_type:
        params["app_type"] = app_type
    if authority:
        params["auth"] = authority

    data, err = _get(PLANIT_APPLICS_URL, params, timeout)
    if err:
        return [], err, None

    records = (data or {}).get("records") or []
    if not isinstance(records, list):
        return [], "PlanIt response had no 'records' list", None

    total = (data or {}).get("total")
    return [r for r in records if isinstance(r, dict)], None, (int(total) if isinstance(total, int) else None)


# Area records include full boundary geometry by default, which makes the response
# ~5.7MB and PlanIt rejects it ("Response content too large"). Always select fields.
AREA_FIELDS = "area_id,area_name,long_name,area_type,gss_code,parent_name,is_planning"


def fetch_areas_page(page: int = 1, timeout: int = 60) -> tuple[list[dict[str, Any]], str | None, int | None]:
    """Fetch one page of the planning-authority list (~485 areas, so two pages).

    Used to resolve area_id -> country/region. The harvester stores the result in
    planning_areas and refreshes it weekly, so this is rarely called.
    """
    params = {"pg_sz": MAX_PAGE_SIZE, "page": int(page), "compress": "1", "select": AREA_FIELDS}
    data, err = _get(PLANIT_AREAS_URL, params, timeout)
    if err:
        return [], err, None
    records = (data or {}).get("records") or []
    total = (data or {}).get("total")
    return [r for r in records if isinstance(r, dict)], None, (int(total) if isinstance(total, int) else None)
