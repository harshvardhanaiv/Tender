from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..client import DEFAULT_HEADERS

# Mercell CTM (formerly eu-supply) public tender lists, used by regional portals such as
# YORtender. The "Ongoing public tenders" page needs no login: it is a server-rendered table
# and a plain form POST filters it. Confirmed against the live site, 2026-10-22. The notice
# page behind each row (rwlentrance_s.asp) is not fetched, so rows carry list fields only.
_PAGE_SIZE = 100
_MAX_PAGES = 3


def _list_url(site_root: str) -> str:
    return f"{site_root.rstrip('/')}/ctm/Supplier/PublicTenders"


def search_eu_supply_ctm(
    *,
    keyword: str,
    site_root: str,
    branding: str,
    source_id: str,
    source_label: str,
    timeout: int = 20,
    max_results: int | None = None,
) -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    url = _list_url(site_root)
    try:
        resp = session.get(url, params={"B": branding}, timeout=timeout)
        resp.raise_for_status()
        form = _form_defaults(resp.text)
        if not form:
            raise RuntimeError("search form not found")
        form["SearchFilter.PagingInfo.PageSize"] = str(_PAGE_SIZE)
        if keyword:
            # The page's own "Name" text filter; the portal wraps the keyword in wildcards.
            form["SearchFilter.ShortDescription"] = keyword

        rows: list[dict[str, Any]] = []
        for page in range(1, _MAX_PAGES + 1):
            form["SearchFilter.PagingInfo.PageNumber"] = str(page)
            resp = session.post(url, params={"B": branding}, data=form, timeout=timeout)
            resp.raise_for_status()
            page_rows = _parse_rows(resp.text, site_root)
            rows.extend(page_rows)
            if len(page_rows) < _PAGE_SIZE:
                break
    except Exception as exc:
        raise RuntimeError(f"{source_label} search failed: {exc}") from exc

    return _finalize_rows(rows, source_id=source_id, source_label=source_label, max_results=max_results)


def _form_defaults(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form", id="searchForm")
    if not form:
        return {}
    data: dict[str, str] = {}
    for field in form.find_all(["input", "select"]):
        name = field.get("name")
        if not name or field.get("type") == "checkbox":
            continue
        if field.name == "select":
            option = field.find("option", selected=True) or field.find("option")
            data[name] = option.get("value", "") if option else ""
        else:
            data[name] = field.get("value", "")
    return data


def _parse_rows(html: str, site_root: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, Any]] = []
    for link in soup.select("a[href*='rwlentrance_s']"):
        tr = link.find_parent("tr")
        if not tr:
            continue
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue
        href = link.get("href", "")
        pid = (parse_qs(urlparse(href).query).get("PID") or [""])[0]
        if not pid:
            continue

        def cell(i: int) -> str:
            return tds[i].get_text(" ", strip=True) if i < len(tds) else ""

        row: dict[str, Any] = {
            "resource_id": pid,
            "title": link.get_text(" ", strip=True),
            "detail_url": urljoin(site_root, href),
            "contracting_authority": cell(6),
            "date_published": cell(3),
            "submission_deadline": cell(4),
            "procedure": cell(5),
            "reference": cell(1),
        }
        rows.append(row)
    return rows


def _finalize_rows(
    raw_rows: list[dict[str, Any]],
    *,
    source_id: str,
    source_label: str,
    max_results: int | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw_rows:
        rid = row.get("resource_id")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        row["source"] = source_id
        row["source_label"] = source_label
        out.append(row)
        if max_results is not None and len(out) >= max_results:
            break
    return out
