"""New Zealand GETS RSS/XML Feed Integration Module.
Feed URL: https://www.gets.govt.nz/ExternalRSSFeed.htm
"""
from __future__ import annotations

import requests
import xml.etree.ElementTree as ET
import hashlib
from typing import Any
from datetime import datetime

def search_gets_api(keyword: str, timeout: int = 15) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch structured public tender notices directly from GETS RSS feed."""
    kw_clean = (keyword or "").strip()
    url = "https://www.gets.govt.nz/ExternalRSSFeed.htm"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }

    rows = []
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            items = root.findall(".//item")
            kw_lower = kw_clean.lower()

            for item in items:
                title = item.findtext("title") or "GETS Notice"
                desc = item.findtext("description") or ""
                link = item.findtext("link") or "https://www.gets.govt.nz"
                guid = item.findtext("guid") or link
                pub_date = item.findtext("pubDate") or datetime.now().strftime("%Y-%m-%d")

                if kw_lower and kw_lower not in title.lower() and kw_lower not in desc.lower():
                    continue

                res_id = hashlib.md5(f"gets_{guid}".encode("utf-8")).hexdigest()[:12].upper()

                rows.append({
                    "resource_id": res_id,
                    "title": title,
                    "description": desc,
                    "contracting_authority": "New Zealand Government (GETS)",
                    "estimated_value_eur": "N/A",
                    "cpv_codes": "N/A",
                    "procedure": "Public Opportunity",
                    "procurement_type": "Services",
                    "submission_deadline": "See official notice",
                    "date_published": str(pub_date)[:16],
                    "detail_url": link,
                    "source": "gets_nz",
                    "source_label": "New Zealand (GETS)",
                    "confidence": "high",
                    "source_type_label": "Direct Portal API",
                })
    except Exception as exc:
        return rows, None

    return rows, None
