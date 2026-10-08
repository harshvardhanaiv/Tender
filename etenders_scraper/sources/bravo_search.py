from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from ..client import DEFAULT_HEADERS

_LABEL_MAP = {
    "reference no": "resource_id",
    "reference no.": "resource_id",
    "reference number": "resource_id",
    "ocid": "ocid",
    "published by": "contracting_authority",
    "publication date": "date_published",
    "deadline date": "submission_deadline",
    "notice type": "notice_type",
    "location": "location",
    "value": "estimated_value_eur",
    "estimated value": "estimated_value_eur",
    "budget": "estimated_value_eur",
    "contract value": "estimated_value_eur",
    "status": "status",
    "awarded to": "awarded_supplier",
    "awarded supplier": "awarded_supplier",
    "supplier": "awarded_supplier",
    "contractor": "awarded_supplier",
    "winning bidder": "awarded_supplier",
    "winner": "awarded_supplier",
    "cpv": "cpv_codes",
    "cpv codes": "cpv_codes",
    "cpv code": "cpv_codes",
    "procedure": "procedure",
    "procedure type": "procedure",
    "type of procedure": "procedure",
    "contract type": "procurement_type",
    "procurement type": "procurement_type",
    "contract duration": "contract_duration_in_months_or_years_including_any_options_and_renewals",
    "duration": "contract_duration_in_months_or_years_including_any_options_and_renewals",
}

_WALES_PAGER = "ctl00$MainBody$rpNoticeResultsPager"
_PCS_PAGE_SELECT = "ctl00$maincontent$PagingHelperTop$ddPageSelect"
_PCS_PAGE_TARGET = "ctl00$maincontent$PagingHelperTop$ddPageSelect"
_PCS_PARALLEL_WORKERS = 4
_PCS_MAX_PAGES = 5      # 5 pages = 50 tenders (PCS pages have ~2MB ViewState and high latency; 5 pages keeps PCS fast)
_WALES_PARALLEL_WORKERS = 15
_WALES_MAX_PAGES = 30   # 30 pages = 300 tenders (Sell2Wales is lightweight and fast)
_BRAVO_MAX_PAGES = 30


def search_bravo_first_page(
    *,
    site_root: str,
    keyword: str,
    source_id: str,
    source_label: str,
    timeout: int = 120,
) -> list[dict[str, Any]]:
    """First results pages (fast preview up to 3 pages / 30 tenders)."""
    site_root = site_root.rstrip("/")
    search_url = f"{site_root}/Search/Search_MainPage.aspx"
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    try:
        if "sell2wales" in site_root.lower():
            html, get_params = _wales_initial_search(session, search_url, keyword, timeout)
            rows = _wales_collect_all_pages(session, search_url, html, get_params, site_root, timeout, max_pages=3)
        else:
            html = _pcs_initial_search(session, search_url, keyword, timeout)
            # Fast preview for PCS: immediately parse page 1 without waiting for extra slow PCS pages
            rows = _parse_results_html(html, site_root)

        return _finalize_bravo_rows(
            rows,
            site_root=site_root,
            source_id=source_id,
            source_label=source_label,
            max_results=None,
            keyword=keyword,
        )
    except Exception as exc:
        print(f"[search_bravo_first_page error for {source_id}]: {exc}")
        return []


def search_bravo_portal(
    *,
    site_root: str,
    keyword: str,
    source_id: str,
    source_label: str,
    timeout: int = 120,
    max_results: int | None = None,
) -> list[dict[str, Any]]:
    """Complete search across all result pages."""
    site_root = site_root.rstrip("/")
    search_url = f"{site_root}/Search/Search_MainPage.aspx"
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    try:
        if "sell2wales" in site_root.lower():
            html, get_params = _wales_initial_search(session, search_url, keyword, timeout)
            rows = _wales_collect_all_pages(
                session, search_url, html, get_params, site_root, timeout, max_pages=_WALES_MAX_PAGES
            )
        else:
            html = _pcs_initial_search(session, search_url, keyword, timeout)
            rows = _pcs_collect_all_pages(session, search_url, html, site_root, timeout, max_pages=_PCS_MAX_PAGES)

        return _finalize_bravo_rows(
            rows,
            site_root=site_root,
            source_id=source_id,
            source_label=source_label,
            max_results=max_results,
            keyword=keyword,
        )
    except Exception as exc:
        print(f"[search_bravo_portal error for {source_id}]: {exc}")
        return []


def _finalize_bravo_rows(
    raw_rows: list[dict[str, Any]],
    *,
    site_root: str,
    source_id: str,
    source_label: str,
    max_results: int | None,
    keyword: str = "",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    kw_clean = (keyword or "").strip().lower()
    prefix_match = re.match(r"^(?:supplier|winner|contractor|buyer|authority|title):\s*(.*)$", kw_clean, re.I)
    if prefix_match:
        kw_clean = prefix_match.group(1).strip()
    filter_by_keyword = bool(kw_clean and kw_clean != "all" and len(kw_clean) >= 2)

    for row in raw_rows:
        title = (row.get("title") or "").strip()
        if not title or len(title) < 8:
            continue
        if "technical solutions contract" in title.lower() or re.search(r"contract\s*#\s*0?\d+", title, re.I):
            continue
        if "bad page" in title.lower() or "page you requested" in title.lower() or "login required" in title.lower():
            continue

        if filter_by_keyword:
            text_corpus = f"{title} {row.get('description', '')} {row.get('contracting_authority', '')} {row.get('supplier_name', '')} {row.get('awarded_supplier', '')}".lower()
            if kw_clean not in text_corpus:
                continue

        rid = row.get("resource_id") or row.get("detail_url") or row.get("title")
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        row["source"] = source_id
        row["source_label"] = source_label
        if row.get("awarded_supplier") and not row.get("supplier_name"):
            row["supplier_name"] = row["awarded_supplier"]
        if not row.get("resource_id") and row.get("detail_url"):
            m = re.search(r"ID=([^&]+)", row["detail_url"], re.I)
            if m:
                row["resource_id"] = m.group(1)
        out.append(row)
        if max_results is not None and len(out) >= max_results:
            break
    return out


def _pcs_collect_all_pages(
    session: requests.Session,
    search_url: str,
    first_html: str,
    site_root: str,
    timeout: int,
    max_pages: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch PCS result pages (up to _PCS_MAX_PAGES in parallel using essential form state)."""
    rows = _parse_results_html(first_html, site_root)
    limit = max_pages if max_pages is not None else _PCS_MAX_PAGES
    total_pages = min(_total_pages(first_html) or 1, limit)
    if total_pages <= 1:
        return rows

    base_fields = _essential_asp_fields(first_html)
    cookies = session.cookies.get_dict()
    headers = dict(session.headers)

    def fetch_page(page_num: int) -> list[dict[str, Any]]:
        worker = requests.Session()
        worker.headers.update(headers)
        worker.cookies.update(cookies)
        data = {
            **base_fields,
            "__EVENTTARGET": _PCS_PAGE_TARGET,
            "__EVENTARGUMENT": "",
            _PCS_PAGE_SELECT: str(page_num),
        }
        req_timeout = max(timeout, 35) if timeout else 35
        response = worker.post(search_url, data=data, timeout=req_timeout)
        response.raise_for_status()
        return _parse_results_html(response.text, site_root)

    with ThreadPoolExecutor(max_workers=_PCS_PARALLEL_WORKERS) as pool:
        futures = [pool.submit(fetch_page, p) for p in range(2, total_pages + 1)]
        for fut in as_completed(futures):
            try:
                rows.extend(fut.result())
            except Exception:
                pass
    return rows


def _wales_collect_all_pages(
    session: requests.Session,
    search_url: str,
    first_html: str,
    get_params: dict[str, str],
    site_root: str,
    timeout: int,
    max_pages: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch Sell2Wales result pages; page 2+ in parallel using page-1 form state (up to 30 pages / 300 tenders)."""
    rows = _parse_results_html(first_html, site_root)
    limit = max_pages if max_pages is not None else _WALES_MAX_PAGES
    total_pages = min(_total_pages(first_html) or 1, limit)
    if total_pages <= 1:
        return rows

    base_fields = _asp_form_fields(first_html)
    cookies = session.cookies.get_dict()
    headers = dict(session.headers)

    def fetch_page(page_num: int) -> list[dict[str, Any]]:
        worker = requests.Session()
        worker.headers.update(headers)
        worker.cookies.update(cookies)
        data = {
            **base_fields,
            "__EVENTTARGET": _WALES_PAGER,
            "__EVENTARGUMENT": str(page_num),
        }
        req_timeout = max(timeout, 35) if timeout else 35
        response = worker.post(search_url, params=get_params, data=data, timeout=req_timeout)
        response.raise_for_status()
        return _parse_results_html(response.text, site_root)

    with ThreadPoolExecutor(max_workers=_WALES_PARALLEL_WORKERS) as pool:
        futures = [pool.submit(fetch_page, p) for p in range(2, total_pages + 1)]
        for fut in as_completed(futures):
            try:
                rows.extend(fut.result())
            except Exception:
                pass
    return rows


_WALES_CONTRACT_RESULTS_CHECKBOX = "ctl00$MainBody$pickerNoticeType$rptLocations$ctl03$chkBox"


def search_sell2wales_contract_results(
    *,
    source_id: str = "sell2wales",
    source_label: str = "Sell2Wales",
    site_root: str = "https://www.sell2wales.gov.wales",
    max_results: int = 30,
    timeout: int = 35,
) -> list[dict[str, Any]]:
    """Fetches Sell2Wales notices filtered to the "Contract Results" category —
    Sell2Wales exposes this as a checkbox-picker widget rather than a query
    parameter, so unlike the rest of this module this issues its own scoped
    request rather than reusing search_bravo_portal's generic keyword search
    (confirmed against the live site, 2026-09: a plain full postback with that
    checkbox checked returns only Contract Results notices)."""
    site_root = site_root.rstrip("/")
    search_url = f"{site_root}/Search/Search_MainPage.aspx"
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    try:
        resp = session.get(search_url, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        form = soup.find("form", id="aspnetForm")
        if form is None:
            return []

        data: dict[str, str] = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            field_type = (inp.get("type") or "text").lower()
            if field_type in ("submit", "button", "image", "checkbox"):
                continue
            data[name] = inp.get("value", "")
        for sel in form.find_all("select"):
            name = sel.get("name")
            if not name:
                continue
            selected = sel.find("option", selected=True) or sel.find("option")
            data[name] = selected.get("value", "") if selected else ""

        data[_WALES_CONTRACT_RESULTS_CHECKBOX] = "on"
        data["ctl00$MainBody$btnSearch"] = "Search"

        post_resp = session.post(search_url, data=data, timeout=timeout)
        post_resp.raise_for_status()
        rows = _parse_results_html(post_resp.text, site_root)

        return _finalize_bravo_rows(
            rows,
            site_root=site_root,
            source_id=source_id,
            source_label=source_label,
            max_results=max_results,
        )
    except Exception as exc:
        print(f"[search_sell2wales_contract_results error]: {exc}")
        return []


def _wales_initial_search(
    session: requests.Session,
    search_url: str,
    keyword: str,
    timeout: int,
) -> tuple[str, dict[str, str]]:
    params = {"noticeType": "-1", "keywords": keyword, "location": "100"}
    req_timeout = max(timeout, 35) if timeout else 35
    response = session.get(search_url, params=params, timeout=req_timeout)
    response.raise_for_status()
    return response.text, params


def _wales_fetch_page(
    session: requests.Session,
    search_url: str,
    params: dict[str, str],
    page: int,
    previous_html: str,
    timeout: int,
) -> str:
    data = _asp_form_fields(previous_html)
    data["__EVENTTARGET"] = _WALES_PAGER
    data["__EVENTARGUMENT"] = str(page)
    req_timeout = max(timeout, 35) if timeout else 35
    response = session.post(search_url, params=params, data=data, timeout=req_timeout)
    response.raise_for_status()
    return response.text


def _pcs_initial_search(
    session: requests.Session,
    search_url: str,
    keyword: str,
    timeout: int,
) -> str:
    req_timeout = max(timeout, 35) if timeout else 35
    params = {"noticeType": "-1", "keywords": keyword}
    try:
        response = session.get(search_url, params=params, timeout=req_timeout)
        response.raise_for_status()
        if "search-result" in response.text or "ctl00$maincontent$btnSearch" in response.text:
            return response.text
    except Exception:
        pass

    # Fallback to GET + POST
    response = session.get(search_url, timeout=req_timeout)
    response.raise_for_status()
    data = _essential_asp_fields(response.text)
    data["ctl00$maincontent$txtKeywords"] = keyword
    data["ctl00$maincontent$btnSearch"] = "Search"
    response = session.post(search_url, data=data, timeout=req_timeout)
    response.raise_for_status()
    return response.text


def _essential_asp_fields(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, str] = {}
    for fid in ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION", "__VIEWSTATEENCRYPTED"]:
        tag = soup.find("input", id=fid) or soup.find("input", {"name": fid})
        if tag and tag.get("value") is not None:
            out[tag.get("name", fid)] = tag.get("value", "")
    return out


def _asp_form_fields(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form", id="aspnetForm")
    if form is None:
        return {}
    out: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        field_type = (inp.get("type") or "text").lower()
        if field_type in ("submit", "button", "image"):
            continue
        out[name] = inp.get("value", "")
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        selected = sel.find("option", selected=True) or sel.find("option")
        out[name] = selected.get("value", "") if selected else ""
    return out


def _total_pages(html: str) -> int | None:
    match = re.search(r"Page\s+\d+\s+of\s+(\d+)", html, re.I)
    if match:
        return int(match.group(1))
    soup = BeautifulSoup(html, "lxml")
    select = soup.select_one("select[id*='ddPageSelect']")
    if select:
        options = select.find_all("option")
        if options:
            return int(options[-1].get("value", len(options)))
    return None


def _parse_results_html(html: str, site_root: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select(".search-result")
    if cards:
        return [_parse_search_result_card(card, site_root) for card in cards]
    return _parse_search_view_links(soup, site_root)


def _parse_search_result_card(card: Tag, site_root: str) -> dict[str, Any]:
    row: dict[str, Any] = {}
    title_link = card.select_one("a.notice-title") or card.find("a", href=True)
    if title_link:
        row["title"] = title_link.get_text(" ", strip=True)
        row["detail_url"] = urljoin(site_root, title_link["href"])

    abstract = card.select_one(".notice-abstract span[aria-label], .notice-abstract span")
    if abstract:
        row["description"] = abstract.get("aria-label") or abstract.get_text(" ", strip=True)

    for prop in card.select(".notice-property"):
        label_el = prop.select_one(".notice-refno, span:first-child")
        if not label_el:
            continue
        label = label_el.get_text(" ", strip=True).rstrip(":").lower()
        spans = prop.find_all("span")
        value = spans[-1].get_text(" ", strip=True) if len(spans) >= 2 else ""
        key = _LABEL_MAP.get(label)
        if key:
            row[key] = value
    return row


def _parse_search_view_links(soup: BeautifulSoup, site_root: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "search_view.aspx" not in href.lower():
            continue
        rid_match = re.search(r"ID=([^&]+)", href, re.I)
        if not rid_match:
            continue
        rid = rid_match.group(1)
        if rid in seen:
            continue
        seen.add(rid)

        title = re.sub(r"\(Opens in new tab\)", "", link.get_text(" ", strip=True), flags=re.I).strip()
        container = link.find_parent("td") or link.find_parent("div") or link.parent
        row: dict[str, Any] = {
            "title": title,
            "resource_id": rid,
            "detail_url": urljoin(site_root, href),
        }
        if container:
            _apply_label_text(row, container.get_text("\n", strip=True))
        rows.append(row)
    return rows


def _apply_label_text(row: dict[str, Any], text: str) -> None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    i = 0
    while i < len(lines) - 1:
        label = lines[i].rstrip(":").lower()
        key = _LABEL_MAP.get(label)
        if key:
            row[key] = lines[i + 1]
            i += 2
            continue
        i += 1


_AWARD_SECTION_LABELS = {
    "official name": "name",
    "postal address": "address",
    "town": "town",
    "postal code": "postcode",
    "country": "country",
    "e-mail": "email",
    "telephone": "phone",
}


def _extract_award_of_contract_winner(full_text: str) -> tuple[str, str]:
    """Parses the "4 Award of Contract" / "Successful Bidders" structured section
    for the winner's official name and address. Returns ("", "") if the notice
    genuinely doesn't publish one (open/live tenders, or a portal quirk) — never
    guesses a name."""
    lines = [ln.strip() for ln in full_text.split("\n") if ln.strip()]
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^(Award of Contract|Successful Bidders)$", ln, re.I):
            start = i
            break
    if start is None:
        return "", ""

    section = lines[start:start + 200]
    fields: dict[str, str] = {}
    i = 0
    while i < len(section) - 1:
        label = section[i].rstrip(":").lower()
        key = _AWARD_SECTION_LABELS.get(label)
        if key and key not in fields:
            nxt = section[i + 1]
            # A value line is real only if it isn't itself another known label
            # (labels with no value are followed directly by the next label).
            if nxt.rstrip(":").lower() not in _AWARD_SECTION_LABELS:
                fields[key] = nxt
        i += 1

    name = fields.get("name", "")
    if not name:
        return "", ""
    address_parts = [fields.get(k) for k in ("address", "town", "postcode", "country") if fields.get(k)]
    return name, ", ".join(address_parts)


def fetch_bravo_details(
    source: str,
    resource_id: str,
    site_root: str = "https://www.sell2wales.gov.wales",
    timeout: int = 20,
) -> dict[str, Any]:
    """Fetch and deeply parse full detail page for Bravo portals (Sell2Wales, Public Contracts Scotland)."""
    site_root = site_root.rstrip("/")
    if "ID=" in str(resource_id):
        detail_url = f"{site_root}/Search/show/search_view.aspx?{resource_id}"
    else:
        detail_url = f"{site_root}/Search/show/search_view.aspx?ID={resource_id}"

    headers = dict(DEFAULT_HEADERS)
    headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    headers["Accept-Language"] = "en-GB,en;q=0.9"

    try:
        resp = requests.get(detail_url, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[fetch_bravo_details] Error fetching {detail_url}: {exc}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()

    full_text = soup.get_text("\n", strip=True)

    title = ""
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(" ", strip=True)
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()

    def get_section(label_pattern: str, max_chars: int = 4000) -> str:
        m = re.search(label_pattern, full_text, re.I)
        if not m:
            return ""
        start_pos = m.end()
        sub = full_text[start_pos:start_pos + max_chars]
        stop_m = re.search(
            r"\n(Contents|Summary|Full notice text|Coding|About the buyer|Scope|Procedure|Lots|Submission|Award criteria)\n",
            sub,
            re.I,
        )
        if stop_m:
            sub = sub[:stop_m.start()]
        return sub.strip()

    abstract = get_section(r"Abstract\s*\n")
    proc_desc = get_section(r"Procurement description\s*\n")

    desc_parts = []
    if abstract:
        desc_parts.append(abstract)
    if proc_desc and proc_desc.lower() not in (abstract.lower() if abstract else ""):
        desc_parts.append(proc_desc)
    description = "\n\n".join(desc_parts)

    auth_name = ""
    auth_m = re.search(r"Published by:\s*\n?([^\n]+)", full_text, re.I)
    if auth_m:
        auth_name = auth_m.group(1).strip()
    if not auth_name:
        auth_m2 = re.search(r"Contracting authority\s*\n?([^\n]+)", full_text, re.I)
        if auth_m2:
            auth_name = auth_m2.group(1).strip()

    email_m = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", full_text)
    contact_email = email_m.group(0) if email_m else ""

    addr_sec = get_section(r"Contracting authority\s*\n", 1000)

    value = ""
    val_m = re.search(r"Total value[^\n]*\n?([^\n]+)", full_text, re.I)
    if val_m:
        value = val_m.group(1).strip()
    if not value:
        val_m2 = re.search(r"Estimated value[^\n]*\n?([^\n]+)", full_text, re.I)
        if val_m2:
            value = val_m2.group(1).strip()
    if not value:
        # PCS's "Quick Quote Award" notice template uses a distinct
        # "Currency: X" / "Price: N" pair instead (confirmed live, 2026-09).
        cur_m = re.search(r"Currency:\s*\n?(\w+)\s*\n?Price:\s*\n?([\d,.]+)", full_text, re.I)
        if cur_m:
            value = f"{cur_m.group(2)} {cur_m.group(1)}"

    pub_date = ""
    pub_m = re.search(r"(?:Publication date|First published):\s*\n?([^\n]+)", full_text, re.I)
    if pub_m:
        pub_date = pub_m.group(1).strip()

    sub_deadline = ""
    dl_m = re.search(r"Tender submission deadline\s*\n?([^\n]+)", full_text, re.I)
    if dl_m:
        sub_deadline = dl_m.group(1).strip()
    if not sub_deadline:
        dl_m2 = re.search(r"Deadline date:\s*\n?([^\n]+)", full_text, re.I)
        if dl_m2:
            sub_deadline = dl_m2.group(1).strip()

    enq_deadline = ""
    enq_m = re.search(r"Enquiry deadline\s*\n?([^\n]+)", full_text, re.I)
    if enq_m:
        enq_deadline = enq_m.group(1).strip()

    award_date = ""
    aw_m = re.search(r"Date of (?:award of contract|Contract Award|Award)\s*\n?([^\n]+)", full_text, re.I)
    if aw_m:
        award_date = aw_m.group(1).strip()

    proc_type = ""
    pt_m = re.search(r"Procedure type\s*\n?([^\n]+)", full_text, re.I)
    if pt_m:
        proc_type = pt_m.group(1).strip()

    is_framework = "Yes" if re.search(r"Is a framework being established\?\s*\n?Yes", full_text, re.I) else ""

    awarded_supplier = ""
    awarded_supplier_address = ""
    sup_m = re.search(r"(?:Awarded to|Contractor|Winning bidder|Successful supplier|Supplier):\s*\n?([^\n]+)", full_text, re.I)
    if sup_m:
        awarded_supplier = sup_m.group(1).strip()
    if not awarded_supplier:
        # Real Sell2Wales/PCS "Contract award notice" pages don't use any of the
        # labels above — the winner is published under a structured
        # "4 Award of Contract" / "Successful Bidders" section instead, as
        # "Official Name:" (+ "Postal Address:"/"Town:"/"Postal Code:"/"Country:")
        # on the line(s) following each label. Confirmed against a real notice
        # (Sell2Wales SEP658453, 2026-09) — see fetch_bravo_details docstring.
        awarded_supplier, awarded_supplier_address = _extract_award_of_contract_winner(full_text)

    lots_text = get_section(r"Lots\s*\n", 6000)
    sub_instructions = get_section(r"Submission address and any special instructions\s*\n", 4000)

    cpvs = re.findall(r"\b\d{8}\b[^\n]*", full_text)
    cpv_str = ", ".join(dict.fromkeys(cpvs))

    return {
        "source": source,
        "resource_id": resource_id,
        "detail_url": detail_url,
        "title": title,
        "description": description or full_text[:2000],
        "abstract": abstract,
        "procurement_description": proc_desc,
        "contracting_authority": auth_name,
        "contact_email": contact_email,
        "authority_address": addr_sec,
        "date_published": pub_date,
        "estimated_value_eur": value,
        "submission_deadline": sub_deadline,
        "clarification_deadline": enq_deadline,
        "award_date": award_date,
        "procedure": proc_type,
        "is_framework": is_framework,
        "awarded_supplier": awarded_supplier,
        "awarded_supplier_address": awarded_supplier_address,
        "supplier_name": awarded_supplier,
        "cpv_codes": cpv_str,
        "lots_breakdown": lots_text,
        "submission_instructions": sub_instructions,
        "full_text": full_text,
    }

