"""France BOAMP Direct Portal Integration Module.
Portal URL: https://www.boamp.fr
OpenData API: https://boamp-datadila.opendatasoft.com/api/records/1.0/search/?dataset=boamp
"""
from __future__ import annotations

import requests
import hashlib
from typing import Any
from datetime import datetime

def search_boamp_api(keyword: str, timeout: int = 15) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch structured public procurement notices directly from France BOAMP portal / OpenData API."""
    kw_clean = (keyword or "").strip()
    url = "https://boamp-datadila.opendatasoft.com/api/records/1.0/search/"
    params = {
        "dataset": "boamp",
        "rows": 50,
        "sort": "dateparution",
    }
    if kw_clean:
        params["q"] = kw_clean

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code == 429:
            return [], "BOAMP rate limit exceeded (HTTP 429)."
        resp.raise_for_status()

        data = resp.json()
        records = data.get("records", [])
        rows = []

        for idx, rec in enumerate(records):
            fields = rec.get("fields", {})
            title = fields.get("objet") or fields.get("titre") or fields.get("famille_libelle") or "Avis de Marché BOAMP"
            authority = fields.get("nomacheteur") or fields.get("denomination") or "République Française (BOAMP)"
            pub_date = fields.get("dateparution") or datetime.now().strftime("%Y-%m-%d")
            idweb = fields.get("idweb") or rec.get("recordid") or f"boamp_{idx}"
            
            detail_url = fields.get("url_avis") or f"https://www.boamp.fr/pages/avis/?q=idweb:{idweb}"
            desc = fields.get("description") or fields.get("resume") or f"Avis de marché publié au BOAMP par {authority}."
            cpv = fields.get("code_cpv") or "N/A"

            res_id = str(idweb).upper()

            rows.append({
                "resource_id": res_id,
                "title": title,
                "description": desc,
                "contracting_authority": authority,
                "estimated_value_eur": "N/A",
                "cpv_codes": str(cpv),
                "procedure": "Avis de Marché (BOAMP)",
                "procurement_type": "Services",
                "submission_deadline": "Voir avis officiel",
                "date_published": str(pub_date)[:10],
                "detail_url": detail_url,
                "source": "boamp",
                "source_label": "France (BOAMP)",
                "confidence": "high",
                "source_type_label": "Direct Portal API",
            })

        return rows, None

    except Exception as exc:
        return [], f"BOAMP portal error: {exc}"
