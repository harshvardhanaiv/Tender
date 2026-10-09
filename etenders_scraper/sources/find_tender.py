"""Scraper for Find a Tender (find-tender.service.gov.uk).

Strategy
--------
The OCDS API at /api/1.0/ocdsReleasePackages only supports filtering by
date-range and stage — it does NOT support free-text keyword search.  The
website's search form DOES perform full-text keyword search (titles,
descriptions, identifiers, organisation names).  We therefore:

  1.  POST to https://www.find-tender.service.gov.uk/Search/Results with the
      `keywords` field and parse the HTML result rows with BeautifulSoup.
  2.  Keep the OCDS API only for fetching a single notice's full detail.

How the search form behaves (checked against the live site, October 2026)
-------------------------------------------------------------------------
  * GET /Search/Results returns the form with a `form_token`. The token is SINGLE USE: a second POST
    with the same token is answered with HTTP 200 and a "Something went wrong" page that holds no
    notices. An earlier version cached one token for 30 minutes and reused it for every search and
    every results page, so every search after the first came back as a clean, silent zero (and a
    search's own page 2 failed the same way). Each search now gets its own session and its own token.
  * The server keeps the search in the session, so further pages are plain GETs of
    /Search/Results?page=N, the links the site itself uses, not repeat POSTs.
  * A genuine zero is the title "Search results" with "We've found 0 notices". The error page is
    titled "Something went wrong". Only the first is an empty result: the second, and any page this
    module cannot read, raises FindTenderError so a search can say the portal failed instead of
    presenting nothing as if it were an answer.
  * The site answers a burst of requests with HTTP 429, so every request goes through one
    process-wide throttle and honours Retry-After.
"""
from __future__ import annotations

import math
import re
import threading
import time
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from ..client import SSLAdapter

_BASE = "https://www.find-tender.service.gov.uk"
_SEARCH_URL = f"{_BASE}/Search/Results"
_OCDS_BASE = f"{_BASE}/api/1.0/ocdsReleasePackages"
_PAGE_SIZE = 20  # notices on one results page

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": _BASE,
}


class FindTenderError(RuntimeError):
    """Find a Tender could not be searched (rate limit, error page, unreadable page, no answer).

    Raised instead of returning an empty list, so a failed search is never mistaken for "no results"."""


# ---------------------------------------------------------------------------
# Politeness: one request at a time slot, process-wide
# ---------------------------------------------------------------------------

_MIN_GAP_SEC = 1.0
# Every HTTP 429 widens the gap (x2, x3 ... up to x5 of _MIN_GAP_SEC) and each good response narrows it again,
# so a busy day slows the crawl down instead of hammering a site that has already said "too fast".
_MAX_BOOST = 4.0
_boost = 0.0
_sleep = time.sleep  # tests replace this so they do not wait
_throttle_lock = threading.Lock()
_next_slot = 0.0


def _throttle() -> None:
    """Space requests at least _MIN_GAP_SEC apart across every thread (without sleeping under the lock)."""
    global _next_slot
    with _throttle_lock:
        now = time.monotonic()
        wait = max(0.0, _next_slot - now)
        _next_slot = max(now, _next_slot) + _MIN_GAP_SEC * (1.0 + _boost)
    if wait > 0:
        _sleep(wait)


def _retry_after(resp: requests.Response) -> float:
    raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _make_session() -> requests.Session:
    s = requests.Session()
    s.mount("https://", SSLAdapter())
    s.headers.update(_HEADERS)
    return s


def _note_response(status: int) -> None:
    global _boost
    with _throttle_lock:
        _boost = min(_MAX_BOOST, _boost + 1.0) if status == 429 else max(0.0, _boost - 0.25)


def _request(session: requests.Session, method: str, url: str, *, retries: int = 4, timeout: int = 20, **kwargs: Any) -> requests.Response:
    """One throttled request; 429/503 and network errors are retried a few times, waiting out Retry-After."""
    last: requests.Response | None = None
    for attempt in range(retries + 1):
        _throttle()
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            if attempt < retries:
                _sleep(1.5 * (attempt + 1))
                continue
            raise FindTenderError(f"Find a Tender did not answer ({type(exc).__name__})") from exc
        _note_response(resp.status_code)
        if resp.status_code in (429, 503) and attempt < retries:
            last = resp
            _sleep(min(30.0, _retry_after(resp) or 3.0 * (attempt + 1)))
            continue
        return resp
    assert last is not None
    return last


def _check_status(resp: requests.Response, what: str) -> None:
    if resp.status_code == 429:
        raise FindTenderError("Find a Tender is rate-limiting requests (HTTP 429). Try again in a minute.")
    if resp.status_code >= 400:
        raise FindTenderError(f"Find a Tender returned HTTP {resp.status_code} for {what}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(iso_str: str) -> str:
    """Convert ISO-8601 to dd/mm/yyyy HH:MM:SS."""
    if not iso_str:
        return ""
    try:
        dt = date_parser.isoparse(iso_str)
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return iso_str


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


_FAT_DATE_FORMATS = ("%d %B %Y, %I:%M%p", "%d %B %Y %I:%M%p", "%d %B %Y")


def _fat_datetime(text: str) -> str:
    """"3 January 2023, 3:29pm" -> "03/01/2023 15:29:00" (the format the other portals use); unreadable text is kept."""
    text = _clean(text)
    for fmt in _FAT_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%d/%m/%Y %H:%M:%S")
        except ValueError:
            continue
    return text


_MONEY = re.compile(r"£\s*([\d,]+(?:\.\d+)?)")
_VALUE_LABELS = (
    "Total value excluding VAT",
    "Total value including VAT",
    "Contract value excluding VAT",
    "Contract values excluding VAT",
    "Lot values including VAT",
)


def _best_value(entries: dict[str, str]) -> str:
    """One figure for the value column: the largest amount (a framework's range reads as its ceiling)."""
    for label in _VALUE_LABELS:
        amounts = [float(m.replace(",", "")) for m in _MONEY.findall(entries.get(label, ""))]
        if amounts:
            return f"£{max(amounts):,.0f}"
    return ""


def _status_for(notice_type: str) -> str:
    low = notice_type.lower()
    if "award" in low or "contract details" in low or "termination" in low:
        return "Awarded"
    return "Active"


def _row(notice_id: str, title: str, source_id: str, source_label: str, **fields: str) -> dict[str, Any]:
    return {
        "title": title,
        "resource_id": notice_id,
        "ocid": "",
        "contracting_authority": fields.get("authority", ""),
        "supplier_name": fields.get("suppliers", ""),
        "awarded_supplier": fields.get("suppliers", ""),
        "description": fields.get("description", ""),
        "estimated_value_eur": fields.get("value", ""),
        "submission_deadline": fields.get("deadline", ""),
        "procedure": "",
        "procurement_type": "",
        "date_published": fields.get("published", ""),
        "detail_url": f"{_BASE}/Notice/{notice_id}",
        "status": fields.get("status") or "Active",
        "source": source_id,
        "source_label": source_label,
        "cpv_codes": "",
        "location": fields.get("location", ""),
        "notice_type": fields.get("notice_type", ""),
        "contract_duration_in_months_or_years_including_any_options_and_renewals": "",
        "contract_awarded_in_lots": "",
    }


def _rows_from_blocks(soup: BeautifulSoup, source_id: str, source_label: str) -> list[dict[str, Any]]:
    """The current results markup: one div.search-result per notice."""
    rows: list[dict[str, Any]] = []
    for block in soup.select("div.search-result"):
        link = block.select_one(".search-result-header a[href]") or block.find("a", href=re.compile(r"/Notice/"))
        m = re.search(r"/Notice/(\d{6}-\d{4})", link.get("href", "")) if link else None
        if not link or not m:
            continue
        sub = block.select_one(".search-result-sub-header")
        desc = block.select_one("div[id$='-description']") or block.select_one("div.wrap-text:not(.search-result-sub-header)")
        entries: dict[str, str] = {}
        for entry in block.select("dl .search-result-entry"):
            dt, dd = entry.find("dt"), entry.find("dd")
            if dt and dd:
                entries[_clean(dt.get_text(" "))] = _clean(dd.get_text(" "))
        notice_type = entries.get("Notice type", "")
        location = entries.get("Contract location") or entries.get("Contract locations") or ""
        deadline = entries.get("Submission deadline") or entries.get("Engagement deadline") or ""
        rows.append(
            _row(
                m.group(1),
                _clean(link.get_text()),
                source_id,
                source_label,
                authority=_clean(sub.get_text(" ")) if sub else "",
                description=_clean(desc.get_text(" ")) if desc else "",
                value=_best_value(entries),
                deadline=_fat_datetime(deadline) if deadline else "",
                published=_fat_datetime(entries["Publication date"]) if entries.get("Publication date") else "",
                suppliers=entries.get("Suppliers", "")[:300],
                location=location[:200],
                notice_type=notice_type,
                status=_status_for(notice_type),
            )
        )
    return rows


def _rows_from_anchors(soup: BeautifulSoup, source_id: str, source_label: str) -> list[dict[str, Any]]:
    """Fallback for an unfamiliar layout: take what can be read around each notice link."""
    rows: list[dict[str, Any]] = []
    for a in soup.find_all("a", href=re.compile(r"/Notice/\d{6}-\d{4}")):
        m = re.search(r"/Notice/(\d{6}-\d{4})", a.get("href", ""))
        if not m:
            continue
        container = a
        for _ in range(8):
            container = container.parent
            if container is None:
                break
            cls = " ".join(container.get("class", []))
            if any(k in cls for k in ("search-result", "notice-item", "govuk-summary", "result")):
                break
        authority = ""
        if container is not None:
            sub = container.select_one(".search-result-sub-header")
            if sub:
                authority = _clean(sub.get_text(" "))
        rows.append(_row(m.group(1), _clean(a.get_text(" ")), source_id, source_label, authority=authority))
    return rows


def _parse_page(html: str, source_id: str, source_label: str) -> tuple[str, int | None, str, list[dict[str, Any]]]:
    """(kind, notices found, page title, rows) where kind is results | empty | error | unknown."""
    soup = BeautifulSoup(html, "html.parser")
    title = _clean(soup.title.get_text(" ")) if soup.title else ""
    if "something went wrong" in title.lower():
        return "error", None, title, []
    found = re.search(r"We.ve found ([\d,]+) notices?", soup.get_text(" ", strip=True))
    total = int(found.group(1).replace(",", "")) if found else None
    rows = _rows_from_blocks(soup, source_id, source_label) or _rows_from_anchors(soup, source_id, source_label)
    seen: set[str] = set()
    rows = [r for r in rows if r["resource_id"] not in seen and not seen.add(r["resource_id"])]
    if rows:
        return "results", total, title, rows
    if total == 0:
        return "empty", 0, title, []
    return "unknown", total, title, []


def _parse_results_page(html: str, source_id: str, source_label: str) -> list[dict[str, Any]]:
    """Rows of one results page (kept for callers that only want the rows)."""
    return _parse_page(html, source_id, source_label)[3]


# ---------------------------------------------------------------------------
# Public search functions
# ---------------------------------------------------------------------------

def _fetch_form_token(session: requests.Session) -> str:
    resp = _request(session, "GET", _SEARCH_URL, retries=2, timeout=15)
    _check_status(resp, "its search page")
    soup = BeautifulSoup(resp.text, "html.parser")
    el = soup.find("input", attrs={"name": "form_token"})
    token = (el.get("value") or "") if el else ""
    if not token:
        title = _clean(soup.title.get_text(" ")) if soup.title else "no title"
        raise FindTenderError(f"Find a Tender's search page did not include its search token (page title: {title!r})")
    return token


def _search_form(token: str, keyword: str) -> dict[str, Any]:
    form: dict[str, Any] = {
        "form_token": token,
        "keywords": keyword,
        "adv_search": "Update results",
        # include all stages so we don't miss anything
        "stage[1]": "1",  # Planning
        "stage[2]": "1",  # Tender
        "stage[4]": "1",  # Pipeline
        "stage[5]": "1",  # Award
        "stage[3]": "1",  # Contract
        "stage[6]": "1",  # Termination
        "payments_compliance": "1",
    }
    for i in range(1, 45):
        form[f"form_type[{i}]"] = "1"
    return form


def _open_search(keyword: str, source_id: str, source_label: str) -> tuple[requests.Session, str, int | None, str, list[dict[str, Any]]]:
    """Start a search in a fresh session. An error page means that session's search did not start, so
    one more attempt is made from scratch; a second error page is raised, never returned as no results."""
    for _attempt in range(2):
        session = _make_session()
        token = _fetch_form_token(session)
        resp = _request(session, "POST", _SEARCH_URL, retries=1, timeout=25, data=_search_form(token, keyword))
        _check_status(resp, "the search")
        kind, total, title, rows = _parse_page(resp.text, source_id, source_label)
        if kind != "error":
            return session, kind, total, title, rows
    raise FindTenderError('Find a Tender answered the search with its "Something went wrong" page twice in a row')


def search_find_tender(
    *,
    keyword: str,
    source_id: str,
    source_label: str,
    max_results: int | None = None,
    max_pages: int = 6,
    info: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Search Find a Tender by keyword via the website's full-text search.

    Returns [] only when the portal answered "0 notices". Anything else that goes wrong before the first
    page is in hand raises FindTenderError. If a later page fails the rows already read are returned and
    `info["note"]` says what was missed. `info` also receives `total` (notices the portal reports)."""
    info = info if info is not None else {}
    session, kind, total, title, rows = _open_search(keyword, source_id, source_label)
    info["total"] = total
    if kind == "empty":
        return []
    if kind != "results":
        raise FindTenderError(f"Find a Tender returned a page this app could not read (page title: {title!r})")

    all_rows = list(rows)
    page = 1
    pages_available = math.ceil(total / _PAGE_SIZE) if total else None
    while page < max_pages and not (max_results and len(all_rows) >= max_results):
        if pages_available is not None and page >= pages_available:
            break
        page += 1
        try:
            resp = _request(session, "GET", _SEARCH_URL, retries=1, timeout=20, params={"page": page})
            _check_status(resp, f"results page {page}")
        except FindTenderError as exc:
            info["note"] = f"results page {page} failed: {exc}"
            break
        page_kind, _total, _title, page_rows = _parse_page(resp.text, source_id, source_label)
        if page_kind != "results":
            info["note"] = f"results page {page} could not be read"
            break
        known = {r["resource_id"] for r in all_rows}
        all_rows.extend(r for r in page_rows if r["resource_id"] not in known)

    if max_results:
        all_rows = all_rows[:max_results]
    return all_rows


def search_find_tender_first_page(
    *,
    keyword: str,
    source_id: str,
    source_label: str,
    info: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch first page results for fast initial dashboard preview."""
    return search_find_tender(
        keyword=keyword,
        source_id=source_id,
        source_label=source_label,
        max_results=20,
        max_pages=2,
        info=info,
    )


# ---------------------------------------------------------------------------
# Detail fetch (still uses OCDS API — it works well for single notices)
# ---------------------------------------------------------------------------

def fetch_find_tender_details(resource_id: str) -> dict[str, Any] | None:
    """Fetch full notice detail via the OCDS API by Notice ID."""
    session = _make_session()
    session.headers["Accept"] = "application/json, */*"

    url = f"{_OCDS_BASE}/{resource_id}"
    try:
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        releases = data.get("releases", [])
        if not releases:
            return None
        release = releases[0]
        tender = release.get("tender", {})

        parties = release.get("parties", [])
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

        cpv_list: list[str] = []
        for item in tender.get("items", []):
            classification = item.get("classification", {})
            cpv_id = classification.get("id")
            cpv_desc = classification.get("description")
            if cpv_id:
                cpv_list.append(f"{cpv_id} - {cpv_desc}" if cpv_desc else cpv_id)

        val_amount = tender.get("value", {}).get("amount")
        val_curr = tender.get("value", {}).get("currency", "GBP")
        val_str = f"{val_amount} {val_curr}" if val_amount is not None else ""

        duration_val = tender.get("contractPeriod", {}).get("durationInDays")
        duration_str = f"{duration_val} days" if duration_val is not None else ""

        def _fmt_iso(s: str) -> str:
            if not s:
                return ""
            try:
                return date_parser.isoparse(s).strftime("%d/%m/%Y %H:%M:%S")
            except Exception:
                return s

        return {
            "resource_id": resource_id,
            "title": tender.get("title", ""),
            "description": tender.get("description", ""),
            "estimated_value_eur": val_str,
            "cpv_codes": ", ".join(cpv_list),
            "procedure": tender.get("procurementMethodDetails", tender.get("procurementMethod", "")),
            "procurement_type": tender.get("mainProcurementCategory", ""),
            "submission_deadline": _fmt_iso(tender.get("tenderPeriod", {}).get("endDate", "")),
            "contract_duration_in_months_or_years_including_any_options_and_renewals": duration_str,
            "end_of_clarification_period": "",
            "allow_suppliers_to_make_an_online_expression_of_interest": "",
            "contract_awarded_in_lots": "Yes" if tender.get("lots") else "No",
            "eu_funding": "",
            "date_of_publication_invitation": _fmt_iso(release.get("date", "")),
            "ted_links_for_published_notices": "",
            "detail_url": f"{_BASE}/Notice/{resource_id}",
            "contact_name": contact_name,
            "contact_email": contact_email,
            "contact_phone": contact_phone,
        }
    except Exception:
        return None
