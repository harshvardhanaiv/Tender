"""Germany Bund.de / e-Vergabe Direct Portal Integration Module.
Portal URL: https://www.evergabe-online.de
"""
from __future__ import annotations

import requests
import hashlib
from typing import Any
from datetime import datetime
from bs4 import BeautifulSoup

def search_bund_api(keyword: str, timeout: int = 15) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch structured public procurement notices directly for Germany Bund.de / e-Vergabe."""
    kw_clean = (keyword or "").strip()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    rows = []
    try:
        url = "https://www.evergabe-online.de/search.html"
        params = {"searchString": kw_clean} if kw_clean else {}
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.content, "html.parser")
            items = soup.find_all(["tr", "div", "li"], class_=lambda c: c and any(k in c.lower() for k in ["bekanntmachung", "ausschreibung", "result", "item", "row"]))
            for idx, item in enumerate(items):
                text_content = item.get_text(separator=" ", strip=True)
                if not text_content or len(text_content) < 20:
                    continue

                title_el = item.find(["h1", "h2", "h3", "h4", "a", "strong"])
                title = title_el.get_text(strip=True) if title_el else text_content[:80]
                if len(title) < 5:
                    continue
                if any(p in title.lower() for p in ["passwort vergessen", "benutzername vergessen", "anmelden mit", "login", "anmeldung", "registrieren"]):
                    continue
                if kw_clean and kw_clean != "all" and kw_clean.lower() not in text_content.lower() and kw_clean.lower() not in title.lower():
                    continue

                link_el = item.find("a", href=True)
                link = "https://www.evergabe-online.de/" + link_el["href"].lstrip("/") if link_el and not link_el["href"].startswith("http") else (link_el["href"] if link_el else "https://www.evergabe-online.de")
                res_id = hashlib.md5(f"bund_{title}_{idx}".encode("utf-8")).hexdigest()[:12].upper()

                rows.append({
                    "resource_id": res_id,
                    "title": title,
                    "description": text_content[:250],
                    "contracting_authority": "Bundesrepublik Deutschland (e-Vergabe)",
                    "estimated_value_eur": "N/A",
                    "cpv_codes": "N/A",
                    "procedure": "Öffentliche Ausschreibung",
                    "procurement_type": "Services",
                    "submission_deadline": "Siehe Bekanntmachung",
                    "date_published": datetime.now().strftime("%Y-%m-%d"),
                    "detail_url": link,
                    "source": "bund",
                    "source_label": "Germany (Bund.de)",
                    "confidence": "high",
                    "source_type_label": "Direct Portal API",
                })
    except Exception as e:
        return rows, None

    return rows, None
