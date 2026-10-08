"""Scraper for UK Government Commercial Agency (GCA, formerly Crown Commercial Service / CCS) Frameworks and Agreements.

Site: https://www.gca.gov.uk/agreements
Scope: UK Central Government & Wider Public Sector National Framework Agreements and DPS.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from ..client import DEFAULT_HEADERS, SSLAdapter

_BASE_URL = "https://www.gca.gov.uk"
_SEARCH_URL = f"{_BASE_URL}/agreements"


def _format_date(date_str: str) -> str:
    """Format date string (e.g. 31/10/2019 or ISO) to standard TenderFlow date format."""
    if not date_str:
        return ""
    clean = date_str.strip()
    # If in dd/mm/yyyy format
    if re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", clean):
        parts = clean.split("/")
        return f"{int(parts[0]):02d}/{int(parts[1]):02d}/{parts[2]} 00:00:00"
    try:
        dt = date_parser.parse(clean, dayfirst=True)
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return clean


def _make_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", SSLAdapter())
    session.headers.update(DEFAULT_HEADERS)
    session.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": _BASE_URL,
    })
    return session


def _parse_results_page(html: str, source_id: str, source_label: str) -> list[dict[str, Any]]:
    """Parse HTML from GCA agreements search."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for li in soup.find_all("li"):
        h3 = li.find("h3")
        if not h3:
            continue
        a = h3.find("a")
        if not a:
            continue
        href = a.get("href", "").strip()
        if not href or not href.startswith("/agreements/"):
            continue

        title = a.get_text(separator=" ", strip=True)
        if not title:
            continue

        agreement_id = href.rstrip("/").split("/")[-1].strip()

        # Parse inline metadata list (Agreement ID, Start Date, End Date, Regulation)
        meta: dict[str, str] = {}
        meta_ul = li.find("ul", class_=lambda c: c and "list--inline" in c)
        if meta_ul:
            for m_li in meta_ul.find_all("li"):
                txt = m_li.get_text(" ", strip=True)
                if ":" in txt:
                    k, v = txt.split(":", 1)
                    meta[k.strip().lower()] = v.strip()

        if not agreement_id and "agreement id" in meta:
            agreement_id = meta["agreement id"]

        # Parse description paragraph
        desc_el = li.find("p", class_=lambda c: c and "govuk-body-s" in c)
        description = desc_el.get_text(" ", strip=True) if desc_el else ""

        # Dates
        start_date_raw = meta.get("start date", "")
        end_date_raw = meta.get("end date", "")
        start_date = _format_date(start_date_raw)
        end_date = _format_date(end_date_raw)
        regulation = meta.get("regulation", "")

        # Check status from tag or dates
        status = "Live"
        status_tag = li.find(class_=lambda c: c and ("tag" in c or "badge" in c or "status" in c))
        if status_tag:
            status = status_tag.get_text(strip=True).title()
        elif end_date_raw:
            try:
                dt_end = date_parser.parse(end_date_raw, dayfirst=True)
                if dt_end < datetime.now():
                    status = "Expired"
            except Exception:
                pass

        detail_url = urljoin(_BASE_URL, href)

        row = {
            "title": title,
            "resource_id": agreement_id or href,
            "ocid": agreement_id or href,
            "contracting_authority": "Government Commercial Agency (GCA / CCS)",
            "supplier_name": "",
            "awarded_supplier": "",
            "description": description,
            "estimated_value_eur": "",
            "submission_deadline": end_date,
            "procedure": f"Framework Agreement ({regulation})" if regulation else "Framework Agreement",
            "procurement_type": "Framework Agreement",
            "date_published": start_date,
            "detail_url": detail_url,
            "status": status,
            "source": source_id,
            "source_label": source_label,
            "cpv_codes": "",
            "contract_duration_in_months_or_years_including_any_options_and_renewals": "",
            "contract_awarded_in_lots": "Yes",
        }
        rows.append(row)

    return rows


def search_gca_agreements(
    *,
    keyword: str,
    source_id: str = "gca_agreements",
    source_label: str = "GCA Frameworks (UK)",
    max_results: int | None = None,
    max_pages: int = 3,
    timeout: int = 15,
) -> list[dict[str, Any]]:
    """Search GCA / CCS framework agreements by keyword."""
    session = _make_session()
    all_rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for page in range(1, max_pages + 1):
        params: dict[str, Any] = {"keyword": keyword.strip()}
        if page > 1:
            params["page"] = page

        try:
            resp = session.get(_SEARCH_URL, params=params, timeout=timeout)
            resp.raise_for_status()
        except Exception as exc:
            if not all_rows:
                raise RuntimeError(f"GCA Agreements search failed: {exc}") from exc
            break

        page_rows = _parse_results_page(resp.text, source_id, source_label)
        if not page_rows:
            break

        for r in page_rows:
            rid = r["resource_id"]
            if rid not in seen:
                seen.add(rid)
                all_rows.append(r)

        if max_results and len(all_rows) >= max_results:
            break

        # Check if next page exists
        soup = BeautifulSoup(resp.text, "html.parser")
        next_link = soup.find("a", string=re.compile(r"Next", re.I)) or soup.find("a", attrs={"aria-label": re.compile(r"next", re.I)})
        if not next_link:
            break

    if max_results:
        all_rows = all_rows[:max_results]

    return all_rows


def search_gca_agreements_first_page(
    *,
    keyword: str,
    source_id: str = "gca_agreements",
    source_label: str = "GCA Frameworks (UK)",
) -> list[dict[str, Any]]:
    """Fast initial preview of first page (12-20 agreements)."""
    return search_gca_agreements(
        keyword=keyword,
        source_id=source_id,
        source_label=source_label,
        max_results=20,
        max_pages=1,
        timeout=10,
    )


def fetch_gca_agreement_details(resource_id: str) -> dict[str, Any] | None:
    """Fetch full agreement details from GCA agreement page."""
    session = _make_session()
    url = f"{_BASE_URL}/agreements/{resource_id.strip()}"
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        title_el = soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else ""

        # Description and benefits
        desc_section = soup.find(id=re.compile(r"description", re.I)) or soup.find("h2", string=re.compile(r"Description", re.I))
        description = ""
        if desc_section:
            parent = desc_section.parent or desc_section
            paragraphs = parent.find_all("p")
            description = "\n\n".join(p.get_text(" ", strip=True) for p in paragraphs if p.get_text(strip=True))

        # Key facts / contact
        contact_email = ""
        mail_a = soup.find("a", href=lambda h: h and h.startswith("mailto:"))
        if mail_a:
            contact_email = mail_a.get("href", "").replace("mailto:", "").strip()

        return {
            "resource_id": resource_id,
            "title": title,
            "description": description,
            "estimated_value_eur": "",
            "cpv_codes": "",
            "procedure": "Framework Agreement",
            "procurement_type": "Framework Agreement",
            "submission_deadline": "",
            "contract_duration_in_months_or_years_including_any_options_and_renewals": "",
            "end_of_clarification_period": "",
            "allow_suppliers_to_make_an_online_expression_of_interest": "",
            "contract_awarded_in_lots": "Yes",
            "eu_funding": "",
            "date_of_publication_invitation": "",
            "ted_links_for_published_notices": "",
            "detail_url": url,
            "contact_name": "Government Commercial Agency Support",
            "contact_email": contact_email or "info@gca.gov.uk",
            "contact_phone": "0345 410 2222",
        }
    except Exception:
        return None
