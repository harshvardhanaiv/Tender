from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..client import DEFAULT_HEADERS

SITE_ROOT = "https://procontract.due-north.com"
_SEARCH_URL = f"{SITE_ROOT}/Opportunities/Index"
_MAX_PAGES = 15
_PARALLEL_WORKERS = 6

# Default values for the rest of the search filter form, submitted alongside the keyword so the
# POST matches an unmodified form (all organisations/portals, open opportunities only). Confirmed
# against the live site, 2026-10: the filter is applied server-side against the session and then
# persists across the plain GET-based pagination used below.
_BASE_FILTER_FIELDS = {
    "ResultFilterHistoryId": "00000000-0000-0000-0000-000000000000",
    "FilterResultItems.PortalWithAllOptionFilter": "AllPortals",
    "FilterResultItems.PortalWithAllOptionFilter.Visibility": "Show",
    "SelectedOrganisationId": "AllOrganisations",
    "IncludeClosed.filterValue": "false",
    "StartEndDateFilter[0].StartDate": "",
    "StartEndDateFilter[0].EndDate": "",
    "StartEndDateFilter[0].Name": "Expression date",
    "StartEndDateFilter[1].StartDate": "",
    "StartEndDateFilter[1].EndDate": "",
    "StartEndDateFilter[1].Name": "Expression date",
}


def search_procontract(
    *,
    keyword: str,
    source_id: str = "procontract",
    source_label: str = "ProContract",
    timeout: int = 10,
    max_results: int | None = None,
) -> list[dict[str, Any]]:
    """Search ProContract (Proactis) -- a single shared portal hosting 300+ UK council,
    housing-association and public-body buyers (e.g. London Tenders Portal, many county/
    district councils). No public API; the keyword filter is a session-scoped form POST
    (anti-forgery token required) and results page via plain GET afterwards."""
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    rows: list[dict[str, Any]] = []

    try:
        resp = session.get(_SEARCH_URL, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        token_input = soup.find("input", attrs={"name": "__RequestVerificationToken"})
        if not token_input:
            raise RuntimeError("ProContract anti-forgery token not found on search page")

        data = {
            "__RequestVerificationToken": token_input.get("value", ""),
            "ResultFilter.GeneralSearchFilter.SearchValue": keyword,
            "ResultFilter.GeneralSearchFilter.SearchTypeValue": "AllData",
            "ResultFilter.GeneralSearchFilter.Search": "Go",
            **_BASE_FILTER_FIELDS,
        }
        resp = session.post(_SEARCH_URL, data=data, timeout=timeout)
        resp.raise_for_status()

        rows = _parse_results_html(resp.text)
        total_pages = min(_total_pages(resp.text) or 1, 3)

        if total_pages > 1:
            cookies = session.cookies.get_dict()
            headers = dict(session.headers)

            def fetch_page(page_num: int) -> list[dict[str, Any]]:
                worker = requests.Session()
                worker.headers.update(headers)
                worker.cookies.update(cookies)
                r = worker.get(
                    _SEARCH_URL,
                    params={
                        "Page": page_num,
                        "PageSize": 10,
                        "SortColumn": "Title",
                        "SortDirection": "Ascending",
                    },
                    timeout=6,
                )
                r.raise_for_status()
                return _parse_results_html(r.text)

            with ThreadPoolExecutor(max_workers=_PARALLEL_WORKERS) as pool:
                futures = [pool.submit(fetch_page, p) for p in range(2, total_pages + 1)]
                try:
                    for fut in as_completed(futures, timeout=8):
                        try:
                            rows.extend(fut.result())
                        except Exception:
                            pass
                except FuturesTimeoutError:
                    for f in futures:
                        f.cancel()

        return _finalize_rows(rows, source_id=source_id, source_label=source_label, max_results=max_results)
    except Exception as exc:
        print(f"[search_procontract error]: {exc}")
        if not rows:
            raise RuntimeError(f"ProContract search failed: {exc}") from exc
        return _finalize_rows(rows, source_id=source_id, source_label=source_label, max_results=max_results)


def _finalize_rows(
    raw_rows: list[dict[str, Any]],
    *,
    source_id: str,
    source_label: str,
    max_results: int | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in raw_rows:
        rid = row.get("resource_id")
        if not rid or rid in seen_ids:
            continue
        seen_ids.add(rid)
        row["source"] = source_id
        row["source_label"] = source_label
        out.append(row)
        if max_results is not None and len(out) >= max_results:
            break
    return out


def _total_pages(html: str) -> int | None:
    soup = BeautifulSoup(html, "lxml")
    numbers = []
    for item in soup.select(".pagination .pagination-item"):
        text = item.get_text(strip=True)
        if text.isdigit():
            numbers.append(int(text))
    return max(numbers) if numbers else None


def _parse_results_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="opportunitiesGrid")
    if not table:
        return []
    rows: list[dict[str, Any]] = []
    for tr in table.find_all("tr", class_=["gridrow", "gridrow_alternate"]):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        link = tds[0].find("a")
        if not link:
            continue
        href = link.get("href", "")
        ref = (link.get("title") or "").strip()
        row: dict[str, Any] = {
            "resource_id": ref or href,
            "title": link.get_text(" ", strip=True),
            "detail_url": urljoin(SITE_ROOT, href),
            "contracting_authority": tds[1].get_text(" ", strip=True),
        }
        if len(tds) > 2:
            row["date_published"] = tds[2].get_text(" ", strip=True)
        if len(tds) > 3:
            row["submission_deadline"] = tds[3].get_text(" ", strip=True)
        if len(tds) > 4:
            value_text = tds[4].get_text(" ", strip=True)
            if value_text and value_text.upper() != "N/A":
                row["estimated_value_eur"] = value_text
        rows.append(row)
    return rows


def fetch_procontract_details(resource_id: str, detail_url: str = "", timeout: int = 20) -> dict[str, Any]:
    """Fetch and parse a single ProContract advert page."""
    url = detail_url or (f"{SITE_ROOT}/Advert?advertId={resource_id}" if "-" in resource_id else "")
    if not url:
        return {}

    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[fetch_procontract_details] Error fetching {url}: {exc}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()

    full_text = soup.get_text("\n", strip=True)

    title = ""
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(" ", strip=True)

    def section(label: str) -> str:
        m = re.search(re.escape(label) + r"\s*\n([^\n]+(?:\n(?!\w[\w\s]*\n)[^\n]+)*)", full_text, re.I)
        return m.group(1).strip() if m else ""

    opportunity_id = section("Opportunity Id")
    description = section("Description")
    cpv_text = section("Categories")
    cpv_codes = ", ".join(re.findall(r"\b\d{8}-\d\b", cpv_text)) or cpv_text
    region = section("Region(s) of supply")
    value = section("Estimated value")
    buyer = section("Buyer")
    contact = section("Contact")
    email_m = re.search(r"[\w.-]+@[\w.-]+\.\w+", full_text)
    contact_email = email_m.group(0) if email_m else ""

    deadline = ""
    dl_m = re.search(r"\bto\s*\n(\d{2}/\d{2}/\d{4}\s*\d{2}:\d{2})", full_text)
    if dl_m:
        deadline = dl_m.group(1).strip()

    return {
        "resource_id": opportunity_id or resource_id,
        "detail_url": url,
        "title": title,
        "description": description or full_text[:2000],
        "contracting_authority": buyer,
        "contact_name": contact,
        "contact_email": contact_email,
        "region": region,
        "estimated_value_eur": value if value and value.upper() != "N/A" else "",
        "cpv_codes": cpv_codes,
        "submission_deadline": deadline,
        "procedure": "Open Opportunity",
        "procurement_type": "Services",
        "full_text": full_text,
    }
