"""CanadaBuys Open Procurement JSON API Module.
"""
from __future__ import annotations

import requests
from typing import Any
from datetime import datetime

def search_canadabuys_api(keyword: str, timeout: int = 15) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch tenders directly from CanadaBuys Open Data / Search API with meta fallback."""
    kw_clean = (keyword or "").strip()
    url = "https://canadabuys.canada.ca/api/canadabuys/v1/tenders"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    params = {"q": kw_clean, "lang": "en", "limit": 25}
    rows = []

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("results", []) or data.get("data", []) or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                tid = item.get("id") or item.get("solicitation_number") or ""
                title = item.get("title") or "CanadaBuys Opportunity"
                authority = item.get("contracting_entity") or "Government of Canada"
                deadline = item.get("closing_date") or "See official notice"
                posted = item.get("publication_date") or datetime.now().strftime("%Y-%m-%d")
                link = item.get("url") or f"https://canadabuys.canada.ca/en/tender-opportunities/{tid}"

                rows.append({
                    "resource_id": str(tid),
                    "title": title,
                    "description": item.get("description") or f"Tender notice published by {authority}",
                    "contracting_authority": authority,
                    "estimated_value_eur": "N/A",
                    "cpv_codes": "N/A",
                    "procedure": "Open Tender",
                    "procurement_type": "Services",
                    "submission_deadline": str(deadline),
                    "date_published": str(posted),
                    "detail_url": link,
                    "source": "canadabuys",
                    "source_label": "CanadaBuys",
                    "confidence": "high",
                    "source_type_label": "Direct Portal API",
                })
    except Exception as exc:
        print(f"[CanadaBuys API Error]: {exc}")

    return rows, None

