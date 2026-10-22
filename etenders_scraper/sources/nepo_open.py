from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import requests

from ..client import DEFAULT_HEADERS

# Open (open-uk.org), the North East councils' shared procurement portal run by NEPO. Its public
# "Browse Opportunities" page is a single-page app that loads one JSON list from this gateway
# endpoint without a login. There is no server-side keyword search, so the whole list (about 100
# live notices) is fetched and filtered here. Confirmed against the live site, 2026-10-22.
SITE_ROOT = "https://www.open-uk.org"
_LIST_URL = "https://gateway-pro-03.open-uk.org/opportunity/browse/all-public"
_HEADERS = {**DEFAULT_HEADERS, "Origin": SITE_ROOT, "Referer": f"{SITE_ROOT}/", "Accept": "application/json"}

try:
    from zoneinfo import ZoneInfo

    _LONDON = ZoneInfo("Europe/London")
except Exception:  # no tz database in the image: show UTC rather than fail
    _LONDON = timezone.utc


def search_nepo_open(
    *,
    keyword: str,
    source_id: str = "nepo_open",
    source_label: str = "NEPO Open (North East)",
    timeout: int = 30,
    max_results: int | None = None,
) -> list[dict[str, Any]]:
    try:
        resp = requests.get(_LIST_URL, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        items = resp.json().get("items") or []
    except Exception as exc:
        raise RuntimeError(f"{source_label} search failed: {exc}") from exc

    terms = _terms(keyword)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        row = _row(item)
        if not row or row["resource_id"] in seen:
            continue
        if terms and not _matches(row, terms):
            continue
        seen.add(row["resource_id"])
        row["source"] = source_id
        row["source_label"] = source_label
        out.append(row)
        if max_results is not None and len(out) >= max_results:
            break
    return out


def _terms(keyword: str) -> list[str]:
    """Quoted phrases stay whole; every other word must appear somewhere in the notice."""
    keyword = (keyword or "").strip().lower()
    phrases = re.findall(r'"([^"]+)"', keyword)
    rest = re.sub(r'"[^"]*"', " ", keyword)
    return [p.strip() for p in phrases if p.strip()] + rest.split()


def _matches(row: dict[str, Any], terms: list[str]) -> bool:
    haystack = " ".join(
        str(row.get(k) or "") for k in ("title", "contracting_authority", "description", "reference", "procedure")
    ).lower()
    return all(t in haystack for t in terms)


def _when(iso: str | None, with_time: bool) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_LONDON)
    except ValueError:
        return ""
    return dt.strftime("%d/%m/%Y %H:%M" if with_time else "%d/%m/%Y")


def _row(item: dict[str, Any]) -> dict[str, Any] | None:
    rid = item.get("opportunityID")
    title = (item.get("opportunityName") or "").strip()
    if not rid or not title:
        return None
    row: dict[str, Any] = {
        "resource_id": rid,
        "title": title,
        "detail_url": f"{SITE_ROOT}/opportunities/overview/public/{rid}",
        "contracting_authority": ((item.get("account") or {}).get("companyName") or "").strip(),
        "description": (item.get("description") or "").strip(),
        "reference": item.get("projectReference") or "",
        "procedure": item.get("eventName") or "",
        "date_published": _when(item.get("createOn"), False),
        "submission_deadline": _when(item.get("submissionEndDate"), True),
    }
    try:
        value = float(item.get("estimatedValue") or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value > 0:
        row["estimated_value_eur"] = f"£{value:,.2f}"
    return row
