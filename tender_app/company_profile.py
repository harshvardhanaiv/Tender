"""Company profile for a supplier: Companies House filings and owners, plus the Google rating.

Every figure comes straight from the source's API response; a source with no key, no confident match or a
failing call is reported as such and the rest still render. Matches need a name similarity of at least
MATCH_THRESHOLD (the same bar the supplier panel uses), so a rating is never attached to a look-alike company.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from tender_app import config
from tender_app.ch_matcher import calculate_name_similarity, normalize_ch_company_number

MATCH_THRESHOLD = 0.85
CH_API = "https://api.company-information.service.gov.uk"
CACHE_TTL_S = 24 * 3600
_CACHE: dict[tuple, tuple[float, Any]] = {}


def _cached(key: tuple, build: Callable[[], Any]) -> Any:
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    value = build()
    # Only remember definite answers; a timeout or API error is retried next time.
    if value.get("status") != "error":
        _CACHE[key] = (time.time(), value)
    return value


def _get_json(url: str, headers: dict[str, str], data: bytes | None = None) -> Any:
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=config.COMPANY_PROFILE_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── Companies House ─────────────────────────────────────────────────────────────────────────

def _ch_headers() -> dict[str, str]:
    token = base64.b64encode(f"{config.COMPANIES_HOUSE_API_KEY}:".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _ch_find_number(name: str) -> tuple[str | None, str | None]:
    q = urllib.parse.quote(name)
    data = _get_json(f"{CH_API}/search/companies?q={q}&items_per_page=8", _ch_headers())
    best = (0.0, None, None)
    for item in data.get("items") or []:
        score = calculate_name_similarity(name, item.get("title"))
        if score > best[0]:
            best = (score, item.get("company_number"), item.get("title"))
    return (best[1], best[2]) if best[0] >= MATCH_THRESHOLD else (None, None)


def companies_house(name: str, company_number: str | None) -> dict[str, Any]:
    if not config.COMPANIES_HOUSE_API_KEY:
        return {"status": "not_connected"}

    def build() -> dict[str, Any]:
        try:
            number = normalize_ch_company_number(company_number)
            matched_by = "number"
            if not number:
                found, _ = _ch_find_number(name)
                number, matched_by = normalize_ch_company_number(found), "name"
            if not number:
                return {"status": "not_found"}
            h = _ch_headers()
            co = _get_json(f"{CH_API}/company/{number}", h)
            out: dict[str, Any] = {
                "status": "ok",
                "matched_by": matched_by,
                "company_number": number,
                "name": co.get("company_name"),
                "company_status": co.get("company_status"),
                "incorporated": co.get("date_of_creation"),
                "type": co.get("type"),
                "url": f"https://find-and-update.company-information.service.gov.uk/company/{number}",
                "last_accounts": ((co.get("accounts") or {}).get("last_accounts") or {}).get("made_up_to"),
                "accounts_next_due": (co.get("accounts") or {}).get("next_due"),
                "last_confirmation_statement": (co.get("confirmation_statement") or {}).get("last_made_up_to"),
                "filings": [], "owners": [], "owners_error": False, "filings_error": False,
            }
            try:
                fh = _get_json(f"{CH_API}/company/{number}/filing-history?items_per_page=8", h)
                out["filings"] = [{"date": f.get("date"), "description": f.get("description"), "category": f.get("category")}
                                  for f in (fh.get("items") or [])[:8]]
            except Exception:
                out["filings_error"] = True
            try:
                psc = _get_json(f"{CH_API}/company/{number}/persons-with-significant-control?items_per_page=20", h)
                out["owners"] = [{"name": p.get("name"), "kind": (p.get("kind") or "").split("#")[-1],
                                  "control": [str(c).replace("-", " ") for c in (p.get("natures_of_control") or [])],
                                  "since": p.get("notified_on"), "ceased": p.get("ceased_on")}
                                 for p in (psc.get("items") or [])]
            except urllib.error.HTTPError as ex:
                out["owners_error"] = ex.code != 404  # 404: the company publishes no PSC register
            except Exception:
                out["owners_error"] = True
            return out
        except Exception:
            return {"status": "error"}

    return _cached(("ch", name.lower(), company_number or ""), build)


# ── Google ──────────────────────────────────────────────────────────────────────────────────

def google(name: str, region: str | None) -> dict[str, Any]:
    if not config.GOOGLE_PLACES_API_KEY:
        return {"status": "not_connected"}

    def build() -> dict[str, Any]:
        try:
            body = json.dumps({"textQuery": f"{name} {region or ''} UK".strip(), "regionCode": "GB", "pageSize": 5}).encode()
            data = _get_json("https://places.googleapis.com/v1/places:searchText", {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,
                "X-Goog-FieldMask": "places.displayName,places.rating,places.userRatingCount,places.googleMapsUri,places.formattedAddress",
            }, data=body)
            for pl in data.get("places") or []:
                title = (pl.get("displayName") or {}).get("text")
                if calculate_name_similarity(name, title) >= MATCH_THRESHOLD:
                    if pl.get("rating") is None:
                        return {"status": "no_reviews", "name": title, "url": pl.get("googleMapsUri")}
                    return {"status": "ok", "name": title, "rating": pl["rating"], "count": pl.get("userRatingCount"),
                            "url": pl.get("googleMapsUri"), "address": pl.get("formattedAddress")}
            return {"status": "not_found"}
        except Exception:
            return {"status": "error"}

    return _cached(("google", name.lower(), region or ""), build)


def build_profile(supplier: dict[str, Any]) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor
    name = supplier["name"]
    with ThreadPoolExecutor(max_workers=2) as ex:  # independent sources: wait for the slowest, not the sum
        ch = ex.submit(companies_house, name, supplier.get("company_number"))
        gg = ex.submit(google, name, supplier.get("region"))
        return {
            "supplier": {"id": supplier["id"], "name": name, "region": supplier.get("region")},
            "companies_house": ch.result(),
            "google": gg.result(),
        }
