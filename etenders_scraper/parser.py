from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .fields import parse_cft_workspace_text

DISPLAY_TAG_RE = re.compile(r"d-(\d+)-([a-z])")
DETAIL_URL_TEMPLATE = "cft/prepareViewCfTWS.do?resourceId={resource_id}"
RESULTS_SUMMARY_RE = re.compile(
    r"Displaying:\s*([\d,]+)-([\d,]+)\s*\|\s*([\d,]+)\s+results(?:\s+in\s+total)?",
    re.IGNORECASE,
)


def extract_pagination_token(html: str) -> str | None:
    """Return displaytag table id prefix like '3680175' from pagination links."""
    match = DISPLAY_TAG_RE.search(html)
    return match.group(1) if match else None


def parse_results_summary(html: str) -> dict[str, int] | None:
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    match = RESULTS_SUMMARY_RE.search(text)
    if not match:
        return None
    start, end, total = (int(x.replace(",", "")) for x in match.groups())
    return {"start": start, "end": end, "total": total}


def parse_search_rows(html: str, base_url: str = "https://www.etenders.gov.ie/epps/") -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    table = _find_results_table(soup)
    if table is None:
        return []

    headers = _header_labels(table)
    rows: list[dict[str, Any]] = []
    body = table.find("tbody") or table
    for tr in body.find_all("tr", recursive=False):
        cells = tr.find_all(["td", "th"])
        if not cells or len(cells) < 4:
            continue
        if all(cell.name == "th" for cell in cells):
            continue
        row = _parse_row(cells, headers, base_url)
        if row.get("title") or row.get("resource_id"):
            rows.append(row)
    return rows


def _find_results_table(soup: BeautifulSoup) -> Tag | None:
    candidates: list[tuple[int, Tag]] = []
    for table in soup.find_all("table"):
        tbody = table.find("tbody")
        data_rows = 0
        if tbody:
            for tr in tbody.find_all("tr", recursive=False):
                if tr.find("td"):
                    data_rows += 1
        else:
            for tr in table.find_all("tr"):
                if tr.find("td") and not tr.find("th"):
                    data_rows += 1
        if data_rows:
            candidates.append((data_rows, table))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _header_labels(table: Tag) -> list[str]:
    labels: list[str] = []
    thead = table.find("thead")
    if thead:
        for th in thead.find_all("th"):
            labels.append(_normalize_header(th.get_text(" ", strip=True)))
    if labels:
        return labels

    first_row = table.find("tr")
    if first_row:
        for cell in first_row.find_all("th"):
            labels.append(_normalize_header(cell.get_text(" ", strip=True)))
    return labels


def _normalize_header(text: str) -> str:
    mapping = {
        "#": "row_number",
        "title": "title",
        "resource id": "resource_id",
        "ca": "contracting_authority",
        "date published": "date_published",
        "tenders submission deadline": "submission_deadline",
        "procedure": "procedure",
        "status": "status",
        "award date": "award_date",
        "cycle": "cycle",
        "estimated value": "estimated_value_eur",
        "info": "info",
        "notice pdf": "notice_pdf",
    }
    key = re.sub(r"\s+", " ", text.strip().lower())
    return mapping.get(key, re.sub(r"[^a-z0-9]+", "_", key).strip("_") or "column")


def _parse_row(cells: list[Tag], headers: list[str], base_url: str) -> dict[str, Any]:
    values: list[str] = []
    for cell in cells:
        values.append(cell.get_text(" ", strip=True))

    row: dict[str, Any] = {}
    for index, value in enumerate(values):
        key = headers[index] if index < len(headers) else f"col_{index}"
        row[key] = value

    title_link = None
    for cell in cells:
        link = cell.find("a", href=True)
        if link and link.get_text(strip=True):
            href = link["href"]
            text = link.get_text(" ", strip=True)
            if len(text) > 10 or "cft" in href.lower() or "resource" in href.lower():
                title_link = link
                break
    if title_link is None:
        for cell in cells:
            link = cell.find("a", href=True)
            if link:
                title_link = link
                break

    if title_link:
        row["title"] = title_link.get_text(" ", strip=True)
        row["detail_url"] = urljoin(base_url, title_link["href"])
        resource_id = _resource_id_from_url(title_link["href"])
        if resource_id:
            row["resource_id"] = resource_id

    if not row.get("resource_id"):
        row["resource_id"] = _find_resource_id(values)

    if row.get("resource_id") and not row.get("detail_url"):
        row["detail_url"] = urljoin(
            base_url,
            DETAIL_URL_TEMPLATE.format(resource_id=row["resource_id"]),
        )

    row["notice_pdf_url"] = _first_pdf_url(cells, base_url)
    return row


def _resource_id_from_url(href: str) -> str | None:
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    for key in ("resourceId", "resourceID", "id", "cftId", "cftID"):
        if key in query and query[key]:
            return query[key][0]
    match = re.search(r"/(\d{5,})(?:[/?#]|$)", href)
    return match.group(1) if match else None


def _find_resource_id(values: list[str]) -> str | None:
    for value in values:
        if re.fullmatch(r"\d{5,}", value.replace(",", "").strip()):
            return value.replace(",", "").strip()
    return None


def _first_pdf_url(cells: list[Tag], base_url: str) -> str | None:
    for cell in cells:
        for link in cell.find_all("a", href=True):
            href = link["href"]
            if "pdf" in href.lower() or "download" in href.lower() or "notice" in href.lower():
                return urljoin(base_url, href)
    return None


def parse_form_fields(html: str) -> tuple[str, str, dict[str, str]]:
    """Return (method, action_url, default_field_values) for the advanced search form."""
    soup = BeautifulSoup(html, "lxml")
    form = _find_advanced_search_form(soup)
    if form is None:
        raise ValueError("No form found on advanced search page")

    method = (form.get("method") or "get").lower()
    action = form.get("action") or ""
    fields: dict[str, str] = {}

    for element in form.find_all(["input", "select", "textarea"]):
        name = element.get("name")
        if not name or element.get("type") == "button":
            continue
        tag = element.name
        if tag == "select":
            selected = element.find("option", selected=True) or element.find("option")
            fields[name] = selected.get("value", "") if selected else ""
        elif tag == "textarea":
            fields[name] = element.get_text()
        else:
            input_type = (element.get("type") or "text").lower()
            if input_type in {"submit", "image", "reset"}:
                continue
            fields[name] = element.get("value", "")

    return method, action, fields


def _find_advanced_search_form(soup: BeautifulSoup) -> Tag | None:
    forms = soup.find_all("form", attrs={"method": True})
    for candidate in forms:
        action = (candidate.get("action") or "").lower()
        if any(token in action for token in ("advanced", "cft", "search", "viewcfts")):
            return candidate
    return forms[0] if forms else None


def parse_detail_fields(html: str) -> dict[str, str]:
    """Extract structured CfT workspace fields from a tender detail page."""
    soup = BeautifulSoup(html, "lxml")
    result: dict[str, str] = {}

    workspace = soup.find(string=re.compile(r"View CfT Workspace|Show CfT Menu", re.I))
    container = workspace.find_parent(["div", "section", "article"]) if workspace else None
    text = (container or soup).get_text(" ", strip=True)
    parsed = parse_cft_workspace_text(text)
    if parsed:
        return parsed

    # Fallback: generic "Label : Value" pairs in table rows.
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        key = _clean_key(cells[0].get_text(" ", strip=True))
        value = cells[1].get_text(" ", strip=True)
        if key and value:
            result[key] = value

    # Generic "Label Value" blocks in definition lists.
    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt", recursive=False)
        dds = dl.find_all("dd", recursive=False)
        for dt, dd in zip(dts, dds):
            key = _clean_key(dt.get_text(" ", strip=True))
            value = dd.get_text(" ", strip=True)
            if key and value:
                result[key] = value

    # Description fallback: capture the largest non-trivial paragraph block.
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "div"])]
    paragraphs = [p for p in paragraphs if len(p) > 120]
    if paragraphs and "description" not in result:
        result["description_full"] = max(paragraphs, key=len)

    return result


def _clean_key(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip().rstrip(":").lower())
    if not cleaned:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", cleaned).strip("_")
