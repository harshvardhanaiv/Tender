"""Singapore GeBIZ Direct Portal Integration Module.
Portal URL: https://www.gebiz.gov.sg
"""
from __future__ import annotations

import requests
import hashlib
import re
from typing import Any
from datetime import datetime
from bs4 import BeautifulSoup

def search_gebiz_api(keyword: str, timeout: int = 15) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch structured public notices directly from Singapore GeBIZ portal."""
    kw_clean = (keyword or "").strip()
    url = "https://www.gebiz.gov.sg/ptn/opportunity/BOListing.xhtml"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    rows = []
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.content, "html.parser")
            kw_lower = kw_clean.lower()

            items = soup.find_all("div", class_=re.compile(r"boListing|opportunity|tender", re.I)) or soup.find_all("tr")
            for idx, item in enumerate(items):
                text_content = item.get_text(separator=" ", strip=True)
                if not text_content or len(text_content) < 20:
                    continue

                if kw_lower and kw_lower not in text_content.lower():
                    continue

                title_el = item.find(["h1", "h2", "h3", "h4", "a", "strong"])
                title = title_el.get_text(strip=True) if title_el else text_content[:80]
                if len(title) < 5:
                    continue

                link_el = item.find("a", href=True)
                link = "https://www.gebiz.gov.sg" + link_el["href"] if link_el and link_el["href"].startswith("/") else (link_el["href"] if link_el else "https://www.gebiz.gov.sg")
                res_id = hashlib.md5(f"gebiz_{title}_{idx}".encode("utf-8")).hexdigest()[:12].upper()

                rows.append({
                    "resource_id": res_id,
                    "title": title,
                    "description": text_content[:250],
                    "contracting_authority": "Government of Singapore (GeBIZ)",
                    "estimated_value_eur": "N/A",
                    "cpv_codes": "N/A",
                    "procedure": "Public Tender",
                    "procurement_type": "Services",
                    "submission_deadline": "See official notice",
                    "date_published": datetime.now().strftime("%Y-%m-%d"),
                    "detail_url": link,
                    "source": "gebiz",
                    "source_label": "Singapore (GeBIZ)",
                    "confidence": "high",
                    "source_type_label": "Direct Portal API",
                })
    except Exception as exc:
        return rows, None

    return rows, None
