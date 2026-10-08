"""Convert raw PlanIt records into planning_applications rows.

Field behaviour below was checked against live PlanIt data (2026-09), not just its data
dictionary — the two differ in ways that matter:

* applicant_name and agent_name are almost always the literal "See source" (PlanIt does
  not republish individuals' names). agent_company, however, is usually real — that is
  the actual lead: the planning consultancy or architect running the scheme.
* Roughly a third of records have no top-level location_x/location_y but do carry
  lat/lng inside other_fields.
* app_type and app_state can be null.
* "Conditions" (discharge of conditions) is the most common app_type among Large
  schemes. It is follow-up paperwork on an already-approved development, not a new
  opportunity — scoring.py weights it to zero and the API excludes it by default.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from etenders_scraper.planning.planit_api import PLACEHOLDER_VALUES
from etenders_scraper.planning.scoring import low_value_reason

# ONS GSS code prefix -> nation. Authoritative; used instead of guessing from names.
GSS_PREFIX_COUNTRY = {
    "E": "England",
    "W": "Wales",
    "S": "Scotland",
    "N": "Northern Ireland",
}

# Fallback when an authority has no GSS code: PlanIt's area_type is e.g.
# "English District", "Welsh Unitary Authority", "Scottish Council Area".
AREA_TYPE_COUNTRY = (
    ("northern ireland", "Northern Ireland"),
    ("english", "England"),
    ("welsh", "Wales"),
    ("scottish", "Scotland"),
)


def country_for_area(gss_code: str | None, area_type: str | None) -> str | None:
    code = (gss_code or "").strip().upper()
    if code[:1] in GSS_PREFIX_COUNTRY:
        return GSS_PREFIX_COUNTRY[code[:1]]
    kind = (area_type or "").lower()
    for needle, country in AREA_TYPE_COUNTRY:
        if needle in kind:
            return country
    # Unknown stays unknown. A wrong nation silently mis-files a lead under a filter.
    return None


def clean_text(value: Any, max_len: int | None = None) -> str | None:
    """Strip PlanIt placeholders ("See source" etc.) and blank strings to None."""
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in PLACEHOLDER_VALUES:
        return None
    if max_len is not None:
        text = text[:max_len]
    return text or None


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None


def parse_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def parse_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coords(rec: dict[str, Any], other: dict[str, Any]) -> tuple[float | None, float | None]:
    lat = parse_float(rec.get("location_y"))
    lng = parse_float(rec.get("location_x"))
    if lat is None or lng is None:
        lat = parse_float(other.get("lat") if other.get("lat") is not None else other.get("latitude"))
        lng = parse_float(other.get("lng") if other.get("lng") is not None else other.get("longitude"))
    # PlanIt's own documented UK bounds; anything outside is a geocoding error.
    if lat is None or lng is None or not (48.0 <= lat <= 62.0) or not (-11.0 <= lng <= 4.0):
        return None, None
    return lat, lng


def normalize_planit_record(
    rec: dict[str, Any],
    areas: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Map one PlanIt record to a planning_applications row, or None if unusable.

    `areas` maps area_id -> planning_areas row, used to resolve country/region.
    Deliberately never produces a monetary value: planning registers do not carry one.
    """
    name = clean_text(rec.get("name"), 255)
    if not name:
        return None

    other = rec.get("other_fields") if isinstance(rec.get("other_fields"), dict) else {}
    area_id = parse_int(rec.get("area_id"))
    area = (areas or {}).get(area_id) if area_id is not None else None
    lat, lng = _coords(rec, other)
    description = clean_text(rec.get("description"))
    n_dwellings = parse_int(other.get("n_dwellings"))
    app_type = clean_text(rec.get("app_type"), 32)

    return {
        "id": name,
        "uid": clean_text(rec.get("uid"), 255),
        "planning_portal_id": clean_text(other.get("planning_portal_id"), 64),
        "authority": clean_text(rec.get("area_name"), 255),
        "authority_id": area_id,
        "country": (area or {}).get("country"),
        "region": (area or {}).get("region"),
        "description": description,
        "address": clean_text(rec.get("address")),
        "postcode": clean_text(rec.get("postcode"), 16),
        "latitude": lat,
        "longitude": lng,
        "app_size": clean_text(rec.get("app_size"), 16),
        "app_state": clean_text(rec.get("app_state"), 32),
        "app_type": app_type,
        "n_dwellings": n_dwellings,
        "low_value_reason": low_value_reason({"description": description, "n_dwellings": n_dwellings, "app_type": app_type}),
        "applicant_name": clean_text(other.get("applicant_name")),
        "applicant_company": clean_text(other.get("applicant_company")),
        "agent_name": clean_text(other.get("agent_name")),
        "agent_company": clean_text(other.get("agent_company")),
        "agent_address": clean_text(other.get("agent_address")),
        "case_officer": clean_text(other.get("case_officer")),
        "ward_name": clean_text(other.get("ward_name"), 255),
        "start_date": parse_date(rec.get("start_date")),
        "decided_date": parse_date(rec.get("decided_date")),
        "consultation_end_date": parse_date(other.get("consultation_end_date")),
        "target_decision_date": parse_date(other.get("target_decision_date")),
        "decision": clean_text(other.get("decision")),
        "docs_url": clean_text(other.get("docs_url")),
        "detail_url": clean_text(rec.get("url")),
        "planit_url": clean_text(rec.get("link")),
        "raw_json": json.dumps(_strip_personal(rec), default=str, separators=(",", ":")),
        "last_changed": parse_timestamp(rec.get("last_changed")),
    }


# other_fields keys that are personal data of private individuals and that we have no
# use for. Removed from raw_json too, otherwise the column-level omission is cosmetic.
_PERSONAL_OTHER_FIELDS = ("applicant_address", "agent_tel", "applicant_tel", "applicant_email", "agent_email")


def _strip_personal(rec: dict[str, Any]) -> dict[str, Any]:
    other = rec.get("other_fields")
    if not isinstance(other, dict):
        return rec
    cleaned = {k: v for k, v in other.items() if k not in _PERSONAL_OTHER_FIELDS}
    return {**rec, "other_fields": cleaned}


NATION_NAMES = frozenset(GSS_PREFIX_COUNTRY.values())
# Jersey, Guernsey, Alderney, Sark and the Isle of Man: PlanIt covers them, but they are
# not part of the UK, so they get their own label rather than a wrong nation.
CROWN_DEPENDENCY_LABEL = "Crown Dependencies"


def resolve_area_countries(areas: list[dict[str, Any]]) -> None:
    """Fill `country` in place for areas that have no GSS code, by walking up parents.

    Checked against live PlanIt data: the areas with no GSS code are combined planning
    authorities ("Mid Kent" -> Kent) and national bodies ("NSIP Wales" -> Wales, "Energy
    Consents Unit" -> Scotland). The national bodies decide the largest schemes in the
    dataset, so leaving them without a country would drop the most valuable leads from
    every country-filtered search.
    """
    by_name = {a["area_name"]: a for a in areas if a.get("area_name")}
    for area in areas:
        if area.get("country"):
            continue
        if (area.get("area_type") or "").lower() == "crown dependency":
            area["country"] = CROWN_DEPENDENCY_LABEL
            continue
        parent_name = area.get("region")
        seen: set[str] = set()
        while parent_name and parent_name not in seen:
            seen.add(parent_name)
            if parent_name in NATION_NAMES:
                area["country"] = parent_name
                break
            parent = by_name.get(parent_name)
            if parent is None:
                break
            if parent.get("country") and parent["country"] != CROWN_DEPENDENCY_LABEL:
                area["country"] = parent["country"]
                break
            parent_name = parent.get("region")


def normalize_planit_area(rec: dict[str, Any]) -> dict[str, Any] | None:
    area_id = parse_int(rec.get("area_id"))
    if area_id is None:
        return None
    return {
        "area_id": area_id,
        "area_name": clean_text(rec.get("area_name"), 255),
        "long_name": clean_text(rec.get("long_name"), 255),
        "area_type": clean_text(rec.get("area_type"), 128),
        "gss_code": clean_text(rec.get("gss_code"), 16),
        "country": country_for_area(rec.get("gss_code"), rec.get("area_type")),
        "region": clean_text(rec.get("parent_name"), 128),
        "is_planning": bool(rec.get("is_planning")),
    }
