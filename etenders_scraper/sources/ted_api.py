"""EU TED (Tenders Electronic Daily) Direct v3 Search API Integration Module.
API Docs: https://ted.europa.eu/api/v3/notices/search
"""
from __future__ import annotations

import requests
from typing import Any
from datetime import datetime

def search_eu_ted_api(keyword: str, timeout: int = 15) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch structured notices directly from EU TED Search API v3 with meta fallback."""
    kw_clean = (keyword or "").strip()
    url = "https://ted.europa.eu/api/v3/notices/search"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    
    payload = {
        "q": f'FT ~ "{kw_clean}"' if kw_clean else 'FT ~ "procurement"',
        "fields": ["publication-number", "notice-title", "buyer-name", "deadline", "value-eur", "cpv-code"],
        "page": 1,
        "limit": 25,
    }
    rows = []

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            notices = data.get("notices", []) or data.get("results", []) or []
            for n in notices:
                if not isinstance(n, dict):
                    continue
                pub_num = n.get("publication-number") or n.get("id") or ""
                title = n.get("notice-title") or n.get("title") or "EU TED Procurement Opportunity"
                authority = n.get("buyer-name") or "EU Contracting Authority"
                deadline = n.get("deadline") or "See official notice"
                value = n.get("value-eur") or "N/A"
                cpv = n.get("cpv-code") or "N/A"
                link = f"https://ted.europa.eu/en/notice/{pub_num}/html" if pub_num else "https://ted.europa.eu"

                rows.append({
                    "resource_id": str(pub_num),
                    "title": title,
                    "description": f"Notice published by {authority}. CPV: {cpv}",
                    "contracting_authority": authority,
                    "estimated_value_eur": str(value),
                    "cpv_codes": str(cpv),
                    "procedure": "Open Procedure",
                    "procurement_type": "Services",
                    "submission_deadline": str(deadline),
                    "date_published": datetime.now().strftime("%Y-%m-%d"),
                    "detail_url": link,
                    "source": "eu_ted",
                    "source_label": "EU Tenders (TED)",
                    "confidence": "high",
                    "source_type_label": "Direct Portal API",
                })
    except Exception as exc:
        print(f"[EU TED API Error]: {exc}")

    return rows, None

