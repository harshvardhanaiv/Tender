"""SAM.gov Opportunities Public API Integration Module.
API Docs: https://sam.gov/data-services
"""
from __future__ import annotations

import os
import requests
from typing import Any
from datetime import datetime, timedelta

def search_sam_gov_api(keyword: str, timeout: int = 15) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch opportunities directly from official SAM.gov Public API with meta fallback."""
    kw_clean = (keyword or "").strip()
    api_key = os.getenv("SAM_GOV_API_KEY")
    rows = []

    if api_key:
        url = "https://api.sam.gov/prod/opportunities/v2/search"
        today = datetime.now()
        posted_from = (today - timedelta(days=90)).strftime("%m/%d/%Y")
        posted_to = today.strftime("%m/%d/%Y")

        params = {
            "api_key": api_key,
            "title": kw_clean,
            "postedFrom": posted_from,
            "postedTo": posted_to,
            "limit": 25,
            "offset": 0,
        }

        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                opps = data.get("opportunitiesData", []) or []
                for opp in opps:
                    notice_id = opp.get("noticeId") or opp.get("solicitationNumber") or ""
                    title = opp.get("title") or "USA SAM.gov Opportunity"
                    sol_num = opp.get("solicitationNumber") or ""
                    desc = opp.get("description") or f"Solicitation Number: {sol_num}"
                    deadline = opp.get("responseDeadLine") or opp.get("archiveDate") or "See official notice"
                    posted = opp.get("postedDate") or ""
                    naics = opp.get("naicsCode") or "N/A"
                    award_val = opp.get("award", {}).get("amount") if isinstance(opp.get("award"), dict) else "N/A"
                    link = opp.get("uiLink") or f"https://sam.gov/opp/{notice_id}/view"

                    rows.append({
                        "resource_id": str(notice_id),
                        "title": title,
                        "description": desc,
                        "contracting_authority": "US Federal Government (SAM.gov)",
                        "estimated_value_eur": str(award_val) if award_val != "N/A" else "N/A",
                        "cpv_codes": naics,
                        "procedure": "Public Opportunity",
                        "procurement_type": "Services",
                        "submission_deadline": str(deadline),
                        "date_published": str(posted),
                        "detail_url": link,
                        "source": "sam_gov",
                        "source_label": "USA (SAM.gov)",
                        "confidence": "high",
                        "source_type_label": "Direct Portal API",
                    })
        except Exception as exc:
            print(f"[SAM.gov API Error]: {exc}")

    return rows, None

