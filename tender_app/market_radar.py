"""Market Radar: peer, supplier and price research over published contract awards.

Built for public sector buyers who are about to write a specification: given a category (a CPV
prefix and/or keywords), who else has bought it, from whom, how, and for roughly what. Everything
is derived from `contract_awards`, the same table Buyer and Supplier Intelligence read, with the
same data-quality rules so the numbers agree with those pages:

  * recurring notices are collapsed (same supplier + authority + date signed -> the highest value),
    as DEDUPED_AWARDS_CTE_SQL does -- applied here after the category filter, which is equivalent
    for this purpose and much cheaper than deduplicating the whole table per request;
  * a framework / call-off row's value is a ceiling shared by every appointed supplier, so it is
    counted as a framework appointment and never enters a spend, average or price figure;
  * a single award above SINGLE_AWARD_STATS_CEILING_GBP is untrustworthy source noise, so it is
    counted but its value is ignored;
  * only GBP awards from the UK/Ireland portals are used.

What is deliberately NOT here, because the data does not support it:

  * authority population: nothing in the database holds it, so "size" is the authority TYPE
    (London borough, county, district ...) rather than a population band;
  * licence-versus-support splits and total cost of ownership: award notices publish one total value;
  * ratings and reviews: there is no source for them.

Division of labour: SQL only filters and de-duplicates (one scan of the category's awards); every
metric is computed in plain Python from those rows (`build_analysis`), so it is deterministic,
unit-testable without a database, and cached per (category, authority type, window).

Contract values here are notice values, not invoiced spend, and "annual value" is the notice value
divided by the stated term when both start and end dates (or a stated term) are published.
"""
from __future__ import annotations

import math
import re
import threading
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable

from etenders_scraper.awards import (
    SHARED_VALUE_N_SQL,
    SINGLE_AWARD_STATS_CEILING_GBP,
    UK_IE_SOURCE_PORTALS,
    classify_buyer_type,
    looks_like_description,
)

LOCAL_GOVERNMENT = "Local Government / Council"

# More rows than this and the list is cut to the most recent (and the response says so): a
# division-level CPV prefix can match tens of thousands of awards.
MAX_ROWS = 60000
CACHE_TTL_SECONDS = 15 * 60
CACHE_MAX_ENTRIES = 16
MIN_RANGE_SAMPLE = 5  # fewer valued awards than this and no price range is shown


class CategoryError(ValueError):
    """The category the caller asked for is malformed (maps to HTTP 400)."""


# ───────────────────────────────────────── categories ──────────────────────────────────────────

# A preset matches an award when its CPV code starts with any `cpv` prefix OR its title contains any
# `keywords` entry (substring, case-insensitive). CPV is a hierarchy, so "5072" means "everything
# under 5072xxxx". Keywords catch notices whose CPV is missing or too generic. Presets were checked
# against the award data (see tests/test_market_radar_api.py::preset coverage): none of them should
# be edited without re-running that.
CATEGORY_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "housing-repairs-gas",
        "label": "Housing repairs & gas servicing",
        "hint": "Responsive repairs, planned maintenance, heating and gas servicing",
        "cpv": ("5070", "5071", "5072", "45331", "45333"),
        "keywords": ("gas servicing", "gas safety", "boiler", "responsive repair", "heating maintenance", "planned maintenance"),
    },
    {
        "id": "crm-case-management",
        "label": "CRM & case management software",
        "hint": "Customer, case and contact management systems",
        "cpv": ("48445", "72212445"),
        "keywords": ("crm", "customer relationship", "case management", "customer service platform"),
        "keyword_cpv": ("48", "72", "79", "85"),
    },
    {
        "id": "highways-maintenance",
        "label": "Highways maintenance",
        "hint": "Road surfacing and maintenance, street lighting, winter service",
        "cpv": ("45233", "5023", "349285", "90620"),
        "keywords": ("highway", "road maintenance", "street lighting", "carriageway"),
    },
    {
        "id": "grounds-tree-works",
        "label": "Grounds & tree works",
        "hint": "Grounds maintenance, horticulture, forestry and tree surgery",
        "cpv": ("773", "7720", "77211"),
        "keywords": ("grounds maintenance", "tree work", "tree surgery", "arboricultur"),
    },
    {
        "id": "energy-utilities",
        "label": "Energy & utilities",
        "hint": "Electricity, gas, fuel, solar and water supply",
        "cpv": ("09", "65"),
        "keywords": ("electricity supply", "gas supply", "energy supply", "solar"),
    },
    {
        "id": "waste-recycling",
        "label": "Waste & recycling",
        "hint": "Refuse collection, disposal, treatment and recycling",
        "cpv": ("905",),
        "keywords": ("waste collection", "recycling", "refuse collection", "household waste"),
    },
    {
        "id": "social-care",
        "label": "Social care & support services",
        "hint": "Care, support, advocacy and related services",
        "cpv": ("853",),
        "keywords": ("domiciliary care", "supported living", "residential care", "social care"),
    },
    {
        "id": "cleaning-facilities",
        "label": "Cleaning & facilities management",
        "hint": "Building cleaning, caretaking and integrated facilities services",
        "cpv": ("9091", "79993", "90919"),
        "keywords": ("cleaning services", "facilities management", "janitorial"),
    },
    {
        "id": "it-services-software",
        "label": "IT services & software",
        "hint": "Software, hosting, development and IT support",
        "cpv": ("72", "48"),
        "keywords": (),
    },
    {
        "id": "construction-works",
        "label": "Construction & building works",
        "hint": "New build, refurbishment and civil engineering works",
        "cpv": ("45",),
        "keywords": (),
    },
)
_PRESETS_BY_ID = {p["id"]: p for p in CATEGORY_PRESETS}


@dataclass(frozen=True)
class Category:
    """What to search for. `key` is a stable string (used in URLs, watchlists and cache keys)."""

    key: str
    label: str
    cpv: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()       # preset: matched with OR alongside `cpv`
    keyword_cpv: tuple[str, ...] = ()    # allowed CPV prefixes when a keyword matches
    text_tokens: tuple[str, ...] = ()    # custom search: every token must appear
    preset_id: str | None = None

    def to_public(self) -> dict[str, Any]:
        out: dict[str, Any] = {"key": self.key, "label": self.label}
        if self.preset_id:
            out["preset"] = self.preset_id
        else:
            if self.cpv:
                out["cpv"] = self.cpv[0]
            if self.text_tokens:
                out["q"] = " ".join(self.text_tokens)
        return out


def _clean_cpv(raw: str) -> str:
    """CPV prefix for a code or branch: "50720000-8" and "5072" both mean everything under 5072."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 9:  # 8 digits plus the check digit
        digits = digits[:8]
    if not 2 <= len(digits) <= 8:
        raise CategoryError("cpv must be 2 to 8 digits")
    if len(digits) == 8:
        # A full code ending in zeros stands for its whole branch (50720000 -> "5072").
        digits = digits.rstrip("0") if len(digits.rstrip("0")) >= 2 else digits[:2]
    return digits


def _clean_text(raw: str) -> tuple[str, ...]:
    text = re.sub(r"[^\w&/-]+", " ", (raw or "").lower(), flags=re.UNICODE)
    tokens = [t.strip("-/&") for t in text.split()]
    tokens = [t for t in tokens if len(t) >= 2][:6]
    if any(len(t) > 40 for t in tokens):
        raise CategoryError("search text is too long")
    return tuple(tokens)


def resolve_category(
    preset: str | None = None,
    cpv: str | None = None,
    q: str | None = None,
    cpv_labels: dict[str, str] | None = None,
) -> Category:
    """Build a Category from request arguments (a preset id, or a CPV prefix and/or search text)."""
    preset = (preset or "").strip()
    if preset:
        spec = _PRESETS_BY_ID.get(preset)
        if not spec:
            raise CategoryError(f"unknown category preset: {preset}")
        return Category(
            key=f"preset:{spec['id']}",
            label=spec["label"],
            cpv=tuple(spec["cpv"]),
            keywords=tuple(spec["keywords"]),
            keyword_cpv=tuple(spec.get("keyword_cpv", ())),
            preset_id=spec["id"],
        )
    prefix = _clean_cpv(cpv) if (cpv or "").strip() else ""
    tokens = _clean_text(q or "")
    if not prefix and not tokens:
        raise CategoryError("choose a category: a preset, a CPV code or some search text")
    parts = []
    if prefix:
        parts.append(f"cpv:{prefix}")
    if tokens:
        parts.append("q:" + " ".join(tokens))
    label_bits = []
    if prefix:
        label_bits.append((cpv_labels or {}).get(prefix) or f"CPV {prefix}")
    if tokens:
        label_bits.append(f"“{' '.join(tokens)}” in award titles")
    return Category(
        key="|".join(parts),
        label=" — ".join(label_bits),
        cpv=(prefix,) if prefix else (),
        text_tokens=tokens,
    )


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def category_where(cat: Category, alias: str = "a") -> tuple[str, list[Any]]:
    """SQL predicate (and its parameters) selecting the awards in a category.

    Every user-supplied value is a bound parameter; `LIKE ... ESCAPE` characters are escaped. The
    SQL text contains no literal percent sign other than the %s placeholders (psycopg2 treats any
    other as a format specifier)."""
    a = alias
    parts: list[str] = []
    params: list[Any] = []
    if cat.preset_id:
        for prefix in cat.cpv:
            parts.append(f"{a}.cpv_code LIKE %s")
            params.append(prefix + "%")
        if cat.keywords:
            kw_regex = r"\y(" + "|".join(re.escape(k.lower()) for k in cat.keywords) + r")\y"
            if cat.keyword_cpv:
                cpv_or = [f"{a}.cpv_code IS NULL", f"{a}.cpv_code = ''"]
                for p in cat.keyword_cpv:
                    cpv_or.append(f"{a}.cpv_code LIKE %s")
                    params.append(p + "%")
                parts.append(f"(LOWER({a}.tender_title) ~* %s AND ({' OR '.join(cpv_or)}))")
                params.insert(len(params) - len(cat.keyword_cpv), kw_regex)
            else:
                parts.append(f"LOWER({a}.tender_title) ~* %s")
                params.append(kw_regex)
        return "(" + " OR ".join(parts) + ")", params
    if cat.cpv:
        parts.append(f"{a}.cpv_code LIKE %s")
        params.append(cat.cpv[0] + "%")
    if cat.text_tokens:
        patterns = [f"%{_like_escape(t)}%" for t in cat.text_tokens]
        parts.append(f"(LOWER({a}.tender_title) LIKE ALL(%s) OR LOWER(COALESCE({a}.cpv_description, '')) LIKE ALL(%s))")
        params.extend([patterns, patterns])
    return "(" + " AND ".join(parts) + ")", params


def category_matches(cat: Category, title: str | None, cpv_code: str | None, cpv_description: str | None = None) -> bool:
    """Python mirror of category_where, for tagging rows that were fetched with an OR of categories."""
    title_l = (title or "").lower()
    code = cpv_code or ""
    if cat.preset_id:
        if any(code.startswith(p) for p in cat.cpv):
            return True
        if cat.keywords and any(re.search(r"\b" + re.escape(k) + r"\b", title_l, re.IGNORECASE) for k in cat.keywords):
            if not cat.keyword_cpv or not code or any(code.startswith(p) for p in cat.keyword_cpv):
                return True
        return False
    if cat.cpv and not code.startswith(cat.cpv[0]):
        return False
    if cat.text_tokens:
        desc_l = (cpv_description or "").lower()
        return all(t in title_l for t in cat.text_tokens) or all(t in desc_l for t in cat.text_tokens)
    return True


def match_reason(cat: Category, title: str | None, cpv_code: str | None, cpv_description: str | None = None) -> str | None:
    """Why an award is in the category, in words a buyer can check: its CPV code, or a word in its title.
    The CPV counts first, as it is the notice's own classification; None when the award does not match."""
    title_l = (title or "").lower()
    code = cpv_code or ""
    if cat.preset_id:
        if code and any(code.startswith(p) for p in cat.cpv):
            return f"CPV {code}"
        for k in cat.keywords:
            if re.search(r"\b" + re.escape(k) + r"\b", title_l, re.IGNORECASE):
                if not cat.keyword_cpv or not code or any(code.startswith(p) for p in cat.keyword_cpv):
                    return f"title word “{k}”"
        return None
    if cat.cpv and code.startswith(cat.cpv[0]):
        return f"CPV {code}"
    if category_matches(cat, title, cpv_code, cpv_description):
        return "title or CPV description text"
    return None


# ──────────────────────────────────── authority classification ─────────────────────────────────

AUTHORITY_TYPES: tuple[tuple[str, str], ...] = (
    ("all", "All public bodies"),
    ("local-government", "All local government"),
    ("london-borough", "London boroughs"),
    ("county", "County councils"),
    ("metropolitan", "Metropolitan boroughs"),
    ("city", "City councils"),
    ("district", "District & borough councils"),
    ("parish", "Parish & town councils"),
    ("nhs", "NHS & healthcare"),
    ("education", "Education & academies"),
    ("housing", "Housing associations"),
    ("central-government", "Central government & agencies"),
    ("police-fire", "Police & emergency services"),
    ("other", "Other public bodies"),
)
AUTHORITY_LABELS = dict(AUTHORITY_TYPES)
AUTHORITY_LABELS["local-other"] = "Other local government"

_BUYER_TYPE_IDS = {
    "NHS & Healthcare": "nhs",
    "Education & Academies": "education",
    "Housing Associations": "housing",
    "Central Government & Agencies": "central-government",
    "Police & Emergency Services": "police-fire",
    "Other Public Bodies": "other",
}
_BUYER_TYPE_BY_ID = {v: k for k, v in _BUYER_TYPE_IDS.items()}

# Local government sub-types by name, first match wins. Patterns are kept to syntax that means the
# same in Python's `re` and PostgreSQL's `~*`, because the SQL filter and the Python label must agree.
_LOCAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("london-borough", r"london borough|royal borough of (kensington|greenwich|kingston)|city of westminster|westminster city council"),
    ("parish", r"parish council|town council|community council|parish meeting"),
    ("county", r"county council"),
    ("metropolitan", r"metropolitan (borough|district|city)"),
    ("city", r"city council|city of "),
    ("district", r"(borough|district) council|borough of |district of "),
)


def classify_authority(name: str | None, buyer_type: str | None = None) -> str:
    """Authority type id for a buyer: a local-government sub-type, or its buyer type id."""
    bt = (buyer_type or "").strip() or classify_buyer_type(name or "")
    if bt == LOCAL_GOVERNMENT:
        for type_id, pattern in _LOCAL_PATTERNS:
            if re.search(pattern, name or "", re.IGNORECASE):
                return type_id
        return "local-other"
    return _BUYER_TYPE_IDS.get(bt, "other")


def authority_where(type_id: str, alias: str = "a") -> tuple[str, list[Any]]:
    """Cheap SQL narrowing for an authority type. Local-government sub-types (county, district ...) are
    only narrowed to local government here: telling them apart by name is a regex over every award,
    which cost several seconds in SQL, so authority_matches() finishes the job in Python on the
    already-filtered rows."""
    a = alias
    if type_id in ("", "all"):
        return "TRUE", []
    if type_id == "local-government" or type_id in {t for t, _ in _LOCAL_PATTERNS}:
        return f"{a}.buyer_type = %s", [LOCAL_GOVERNMENT]
    if type_id in _BUYER_TYPE_BY_ID:
        if type_id == "other":
            return f"({a}.buyer_type = %s OR {a}.buyer_type IS NULL OR {a}.buyer_type = '')", [_BUYER_TYPE_BY_ID[type_id]]
        return f"{a}.buyer_type = %s", [_BUYER_TYPE_BY_ID[type_id]]
    raise CategoryError(f"unknown authority type: {type_id}")


def authority_matches(type_id: str, name: str | None, buyer_type: str | None) -> bool:
    """Exact authority-type test, consistent with classify_authority (see authority_where)."""
    if type_id in ("", "all"):
        return True
    actual = classify_authority(name, buyer_type)
    if type_id == "local-government":
        return actual in {t for t, _ in _LOCAL_PATTERNS} or actual == "local-other"
    return actual == type_id


# Words that differ between spellings of the same council ("London Borough of Camden",
# "London Borough Camden", "Camden Council"). Removed, longest first, to find one key per place.
_COUNCIL_NOISE = (
    "LONDON BOROUGH", "ROYAL BOROUGH", "METROPOLITAN", "BOROUGH", "DISTRICT", "COUNTY", "CITY",
    "COUNCIL", "THE", "OF",
)
_COUNCIL_NOISE_RE = re.compile(r"\b(" + "|".join(_COUNCIL_NOISE) + r")\b")


def normalise_name(name: str | None) -> str:
    """Upper-case, '&' as AND, punctuation collapsed -- as buyers_bp._norm_auth_sql does in SQL."""
    text = (name or "").upper().replace("&", " AND ")
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


def canonical_buyer_key(name: str | None, buyer_type: str | None = None) -> str:
    """One key per real buyer. Councils publish under several spellings ("London Borough Of Camden",
    "London Borough of Camden Council") that would otherwise count as separate peers."""
    norm = normalise_name(name)
    bt = (buyer_type or "").strip() or classify_buyer_type(name or "")
    if bt != LOCAL_GOVERNMENT:
        return norm
    place = re.sub(r"\s+", " ", _COUNCIL_NOISE_RE.sub(" ", norm)).strip()
    return place or norm


_LEGAL_SUFFIX_RE = re.compile(r"\b(LIMITED|LTD|PLC|LLP|LLC|INC|CIC|COMPANY|THE)\b")


def canonical_supplier_key(name: str | None) -> str:
    """One key per supplier name: case, punctuation and legal suffix (Ltd / Limited / plc) ignored.
    Suppliers whose canonical records were never merged still appear under several spellings."""
    norm = normalise_name(name)
    stripped = re.sub(r"\s+", " ", _LEGAL_SUFFIX_RE.sub(" ", norm)).strip()
    return stripped or norm


def place_token(name: str | None) -> str:
    """Longest word of a council's canonical key: a cheap SQL ILIKE prefilter before exact matching."""
    words = canonical_buyer_key(name, LOCAL_GOVERNMENT).split()
    return max(words, key=len) if words else normalise_name(name)


# ───────────────────────────────────────── small helpers ───────────────────────────────────────

WINDOWS: dict[str, int | None] = {"1y": 365, "3y": 3 * 365, "5y": 5 * 365, "all": None}
DEFAULT_WINDOW = "3y"
WINDOW_LABELS = {"1y": "Last 12 months", "3y": "Last 3 years", "5y": "Last 5 years", "all": "All time"}

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")
_PLACEHOLDER_SUPPLIER = re.compile(
    r"^(na|n/a|tbc|tbd|unknown|various|confidential|not disclosed|not available|not named|withheld|redacted|"
    r"to be confirmed|pending|none|supplier|contract|contract value|live scraped supplier|not applicable|not awarded|no award|award not made|please refer to weblink|refer to weblink|see website|please see website|withheld for security reasons)$|"
    r"(please see|please refer)|"
    r"^(as per|information withheld|awarded supplier details|confidential information|confidential / sensitive)|"
    r"commercially confidential|"
    r"(see|refer to)[a-z ,']{0,45}attach",
    re.IGNORECASE,
)


def parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value)
    if not _ISO.match(text):
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def window_start(window: str, today: date | None = None) -> str:
    days = WINDOWS.get(window)
    if window not in WINDOWS:
        raise CategoryError(f"unknown window: {window}")
    if days is None:
        return "0000-01-01"
    return ((today or date.today()) - timedelta(days=days)).isoformat()


def term_days(start: Any, end: Any, duration_text: Any) -> float | None:
    """Contract term in days from published dates, else from a plain "N months/years" statement.

    The "12-36 months" some portals fill in when they publish no term is a placeholder, not a
    duration, and is ignored. Terms under a month or over twenty years are treated as data errors."""
    s, e = parse_iso_date(start), parse_iso_date(end)
    days: float | None = None
    if s and e:
        days = float((e - s).days)
    else:
        m = re.match(r"^\s*(\d+)\s*(month|months|year|years)\s*$", str(duration_text or ""), re.IGNORECASE)
        if m:
            n = int(m.group(1))
            days = n * (30.4375 if m.group(2).lower().startswith("month") else 365.25)
    if days is None or not 30 <= days <= 20 * 365.25:
        return None
    return days


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated percentile (q between 0 and 1) of an ascending list."""
    if not sorted_values:
        raise ValueError("percentile of an empty list")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    frac = pos - lo
    return float(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)


def route_label(procurement_type: str | None, is_framework: bool) -> str:
    text = (procurement_type or "").strip()
    if text:
        return text
    return "Framework / Call-off" if is_framework else "Not stated"


def _round_money(value: float | None) -> float | None:
    return None if value is None else round(float(value), 0)


# ─────────────────────────────────────────── analysis ──────────────────────────────────────────

def prepare_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise one deduplicated award row into the fields the analysis uses."""
    framework = bool(raw.get("is_framework"))
    try:
        value = float(raw["contract_value"]) if raw.get("contract_value") is not None else None
    except (TypeError, ValueError):
        value = None
    if value is not None and value <= 0:
        value = None  # the awards table stores 0 where a notice gave no value; never show it as £0
    value_ok = value if (not framework and value is not None and 0 < value < SINGLE_AWARD_STATS_CEILING_GBP) else None
    name = (raw.get("authority_name") or "").strip()
    signed = parse_iso_date(raw.get("date_signed"))
    started = parse_iso_date(raw.get("contract_start_date")) or signed
    days = term_days(raw.get("contract_start_date"), raw.get("contract_end_date"), raw.get("contract_duration"))
    supplier = (raw.get("supplier_display") or raw.get("supplier_name") or "").strip()
    return {
        "id": raw.get("id"),
        "buyer_key": canonical_buyer_key(name, raw.get("buyer_type")),
        "buyer": name,
        "type_id": classify_authority(name, raw.get("buyer_type")),
        "supplier_key": canonical_supplier_key(supplier) or str(raw.get("sup_key") or ""),
        "supplier": supplier,
        "supplier_id": raw.get("supplier_id"),
        "company_number": raw.get("supplier_cnum") or raw.get("company_number"),
        "listable": bool(raw.get("listable", True)) and bool(supplier) and not _PLACEHOLDER_SUPPLIER.search(supplier) and not looks_like_description(supplier),
        "title": (raw.get("tender_title") or "").strip(),
        "cpv": raw.get("cpv_code") or "",
        "value": value,
        "value_ok": value_ok,
        "framework": framework,
        "signed": signed,
        "started": started,
        "ended": parse_iso_date(raw.get("contract_end_date")),
        "term_days": days,
        "route": route_label(raw.get("procurement_type"), framework),
        "url": raw.get("notice_url") or None,
        "shared_n": int(raw.get("shared_n") or 1),
        "notice_value": raw.get("contract_value_notice"),
    }


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _award_public(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": r["title"],
        "supplier": r["supplier"],
        "value": _round_money(r["value"]),
        "value_is_ceiling": r["framework"],
        "signed": _iso(r["signed"]),
        "started": _iso(r["started"]),
        "ends": _iso(r["ended"]),
        "route": r["route"],
        "url": r["url"],
        "cpv": r["cpv"] or None,
        "matched_by": r.get("matched_by"),
        "shared_with": r.get("shared_n", 1),
        "notice_value": _round_money(float(r["notice_value"])) if r.get("shared_n", 1) > 1 and r.get("notice_value") else None,
    }


def _newest_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (r["signed"] or date.min, r["id"] or 0), reverse=True)


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    valued = [r for r in rows if r["value_ok"] is not None]
    very_large = [r for r in rows if not r["framework"] and r["value"] is not None and r["value"] >= SINGLE_AWARD_STATS_CEILING_GBP]
    return {
        "awards": n,
        "buyers": len({r["buyer_key"] for r in rows}),
        "suppliers": len({r["supplier_key"] for r in rows if r["listable"]}),
        "frameworks": sum(1 for r in rows if r["framework"]),
        "total_value": _round_money(sum(r["value_ok"] for r in valued)) if valued else None,
        "very_large_awards": {
            "count": len(very_large),
            "total_value": _round_money(sum(r["value"] for r in very_large)) if very_large else 0.0,
        },
        "coverage": {
            "with_value": round(len(valued) / n, 3) if n else None,
            "with_term": round(sum(1 for r in rows if r["term_days"]) / n, 3) if n else None,
            "with_route": round(sum(1 for r in rows if r["route"] != "Not stated") / n, 3) if n else None,
        },
    }


def _display_name(spellings: dict[str, int]) -> str:
    """The most used spelling of a name, preferring proper case over ALL CAPS when equally common."""
    return sorted(spellings, key=lambda s: (-spellings[s], s == s.upper(), s))[0]


def peers_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per buyer (newest activity first), each with the evidence behind it."""
    by_buyer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_buyer[r["buyer_key"]].append(r)
    peers = []
    for key, group in by_buyer.items():
        group = _newest_first(group)
        latest = group[0]
        # keyed by the canonical supplier key so "Acme Ltd" and "ACME LIMITED" count as one supplier
        spend_by_supplier: dict[str, float] = defaultdict(float)
        count_by_supplier: dict[str, int] = defaultdict(int)
        spellings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in group:
            if r["listable"]:
                count_by_supplier[r["supplier_key"]] += 1
                spend_by_supplier[r["supplier_key"]] += r["value_ok"] or 0.0
                spellings[r["supplier_key"]][r["supplier"]] += 1
        main = main_key = main_id = None
        if count_by_supplier:
            top = max(count_by_supplier, key=lambda s: (spend_by_supplier[s], count_by_supplier[s]))
            main = _display_name(spellings[top])
            main_key = top
            main_id = next((r["supplier_id"] for r in group if r["listable"] and r["supplier_key"] == top and r["supplier_id"]), None)
        valued = [r["value_ok"] for r in group if r["value_ok"] is not None]
        names: dict[str, int] = defaultdict(int)
        for r in group:  # prefer the proper-case, longest spelling of the name
            names[r["buyer"]] += 1
        display = sorted(names, key=lambda s: (s == s.upper(), -len(s), s))[0]
        type_id = latest["type_id"]
        peers.append({
            "key": key,
            "buyer": display,
            "type": type_id,
            "type_label": AUTHORITY_LABELS.get(type_id, type_id),
            "awards": len(group),
            "frameworks": sum(1 for r in group if r["framework"]),
            "suppliers": len({r["supplier_key"] for r in group if r["listable"]}),
            "total_value": _round_money(sum(valued)) if valued else None,
            "main_supplier": main,
            "main_supplier_key": main_key,
            "main_supplier_id": main_id,
            "latest": {
                "supplier": latest["supplier"] if latest["listable"] else None,
                "value": _round_money(latest["value"]),
                "value_is_ceiling": latest["framework"],
                "started": _iso(latest["started"]),
                "route": latest["route"],
                "title": latest["title"],
            },
            "latest_signed": _iso(latest["signed"]),
            "_awards": [_award_public(r) for r in group[:40]],
        })
    peers.sort(key=lambda p: (p["latest_signed"] or "", p["awards"]), reverse=True)
    return peers


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


SIMILAR_CPV_DIGITS = 5  # CPV "category" level: 50531100 and 50531000 are the same kind of work


def cpv_class(code: Any) -> str:
    """The CPV class an award is filed under for supplier similarity ("" when it has no usable code)."""
    digits = re.sub(r"\D", "", str(code or ""))
    return digits[:SIMILAR_CPV_DIGITS] if len(digits) >= 2 else ""


def supplier_cpv_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{supplier key: {name, id, buyers, cpv: {cpv class: [awards, valued spend]}}} for the suppliers a notice names.

    Kept beside the cached analysis (a few numbers per supplier and code, not the 60,000 rows) so "similar
    suppliers" never needs another scan. Framework appointments count as awards but never as spend."""
    index: dict[str, dict[str, Any]] = {}
    spellings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if not r["listable"]:
            continue
        entry = index.setdefault(r["supplier_key"], {"name": "", "id": None, "buyers": set(), "cpv": {}})
        slot = entry["cpv"].setdefault(cpv_class(r["cpv"]), [0, 0.0])
        slot[0] += 1
        slot[1] += r["value_ok"] or 0.0
        entry["buyers"].add(r["buyer_key"])
        entry["id"] = entry["id"] or r["supplier_id"]
        spellings[r["supplier_key"]][r["supplier"]] += 1
    for key, entry in index.items():
        entry["name"] = _display_name(spellings[key])
        entry["buyers"] = len(entry["buyers"])
    return index


def similar_suppliers(index: dict[str, dict[str, Any]], supplier_key: str, limit: int = 15) -> dict[str, Any] | None:
    """Other suppliers in the same category doing the same kind of work as `supplier_key`.

    "Same kind of work" is CPV overlap: a supplier is similar when it has awards under a CPV class (first
    five digits) that the reference supplier also holds. Ranked by awards in the shared classes, then by
    their value. When the reference supplier's awards carry no CPV code there is nothing to overlap on, so
    every other supplier in the category is listed and `basis` says so. None when the supplier is unknown."""
    mine = index.get(supplier_key)
    if mine is None:
        return None
    my_classes = {c for c in mine["cpv"] if c}
    ranked = []
    for key, entry in index.items():
        if key == supplier_key:
            continue
        shared = {c: v for c, v in entry["cpv"].items() if c in my_classes} if my_classes else entry["cpv"]
        if not shared:
            continue
        ranked.append({
            "key": key,
            "supplier": entry["name"],
            "supplier_id": entry["id"],
            "shared_awards": int(sum(v[0] for v in shared.values())),
            "shared_value": _round_money(sum(v[1] for v in shared.values())) or None,
            "buyers": entry["buyers"],
            "awards": int(sum(v[0] for v in entry["cpv"].values())),
            "shared_cpv": sorted((c for c in shared if c), key=lambda c: -shared[c][0])[:3],
        })
    ranked.sort(key=lambda x: (x["shared_awards"], x["shared_value"] or 0, x["awards"], x["supplier"].lower()), reverse=True)
    return {
        "supplier": mine["name"],
        "basis": "cpv" if my_classes else "category",
        "cpv": sorted(my_classes, key=lambda c: -mine["cpv"][c][0]),
        "total": len(ranked),
        "rows": ranked[:limit],
    }


def suppliers_table(rows: list[dict[str, Any]], limit: int = 200) -> list[dict[str, Any]]:
    """Per-supplier comparison, widest adoption first."""
    by_supplier: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["listable"]:
            by_supplier[r["supplier_key"]].append(r)
    out = []
    for key, group in by_supplier.items():
        group = _newest_first(group)
        buyers: dict[str, int] = defaultdict(int)
        for r in group:
            buyers[r["buyer_key"]] += 1
        valued = sorted(r["value_ok"] for r in group if r["value_ok"] is not None)
        terms = [r["term_days"] / 30.4375 for r in group if r["term_days"]]
        repeat = sum(1 for n in buyers.values() if n >= 2)
        names: dict[str, int] = defaultdict(int)
        for r in group:
            names[r["supplier"]] += 1
        out.append({
            "key": key,
            "supplier": _display_name(names),
            "supplier_id": next((r["supplier_id"] for r in group if r["supplier_id"]), None),
            "contracts": sum(1 for r in group if not r["framework"]),
            "framework_appointments": sum(1 for r in group if r["framework"]),
            "buyers": len(buyers),
            "total_value": _round_money(sum(valued)) if valued else None,
            "valued_contracts": len(valued),
            "avg_contract": _round_money(_mean(valued)),
            "median_contract": _round_money(percentile(valued, 0.5)) if valued else None,
            "avg_term_months": round(_mean(terms), 1) if terms else None,
            "terms_known": len(terms),
            "repeat_rate": round(repeat / len(buyers), 2) if len(buyers) >= 3 else None,
            "latest_signed": _iso(group[0]["signed"]),
            "price_band": None,
        })
    # Price bands are relative to this category's own suppliers (tertiles of average contract value),
    # so they mean "cheaper / typical / dearer than the others here", not an absolute price tier.
    avgs = sorted(s["avg_contract"] for s in out if s["valued_contracts"] >= 2 and s["avg_contract"])
    if len(avgs) >= 6:
        low, high = percentile(avgs, 1 / 3), percentile(avgs, 2 / 3)
        for s in out:
            if s["valued_contracts"] >= 2 and s["avg_contract"]:
                s["price_band"] = "£ Lower" if s["avg_contract"] <= low else ("£££ Higher" if s["avg_contract"] > high else "££ Mid")
    out.sort(key=lambda s: (s["buyers"], s["contracts"] + s["framework_appointments"], s["total_value"] or 0), reverse=True)
    return out[:limit]


def _range(values: list[float]) -> dict[str, float] | None:
    if len(values) < MIN_RANGE_SAMPLE:
        return None
    v = sorted(values)
    return {
        "p10": _round_money(percentile(v, 0.10)),
        "median": _round_money(percentile(v, 0.50)),
        "p90": _round_money(percentile(v, 0.90)),
        "min": _round_money(v[0]),
        "max": _round_money(v[-1]),
    }


def cost_table(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Typical contract and annualised values, overall and by authority type.

    Annual value needs a published term (start and end dates, or "N months/years"); awards without
    one still feed the total-contract-value range, and the response says how many of each there were."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["value_ok"] is not None:
            groups[r["type_id"]].append(r)

    def block(label: str, type_id: str | None, items: list[dict[str, Any]]) -> dict[str, Any]:
        annual = [r["value_ok"] / (r["term_days"] / 365.25) for r in items if r["term_days"]]
        return {
            "type": type_id,
            "label": label,
            "contracts": len(items),
            "annualised": len(annual),
            "annual": _range(annual),
            "total": _range([r["value_ok"] for r in items]),
        }

    everything = [r for items in groups.values() for r in items]
    by_type = [block(AUTHORITY_LABELS.get(t, t), t, items) for t, items in groups.items()]
    by_type.sort(key=lambda b: b["contracts"], reverse=True)
    return {
        "overall": block("All buyers in this view", None, everything),
        "by_type": by_type,
        "excluded_frameworks": sum(1 for r in rows if r["framework"]),
        "excluded_outliers": sum(1 for r in rows if not r["framework"] and r["value"] is not None and r["value"] >= SINGLE_AWARD_STATS_CEILING_GBP),
        "excluded_no_value": sum(1 for r in rows if not r["framework"] and r["value_ok"] is None and (r["value"] is None or r["value"] < SINGLE_AWARD_STATS_CEILING_GBP)),
    }


def build_analysis(rows: Iterable[dict[str, Any]], truncated: bool = False, cat: Category | None = None) -> dict[str, Any]:
    """Everything the Market Radar tabs show, computed from deduplicated award rows."""
    prepared = [prepare_row(r) for r in rows]
    if cat is not None:
        for p, r in zip(prepared, rows if isinstance(rows, list) else []):
            p["matched_by"] = match_reason(cat, r.get("tender_title"), r.get("cpv_code"), r.get("cpv_description"))
    peers = peers_table(prepared)
    suppliers = suppliers_table(prepared)
    return {
        "summary": summarise(prepared),
        "peers": peers,
        "suppliers": suppliers,
        "_supplier_cpv": supplier_cpv_index(prepared),
        "cost": cost_table(prepared),
        "truncated": bool(truncated),
    }


# ───────────────────────────────────────── database access ─────────────────────────────────────

_PORTALS = ", ".join("'" + p.replace("'", "''") + "'" for p in UK_IE_SOURCE_PORTALS)

_AWARD_COLUMNS = """
    a.id, a.supplier_id, a.company_number, a.supplier_name, a.authority_name, a.tender_title,
    a.cpv_code, a.contract_value, a.date_signed, a.procurement_type, a.is_framework, a.buyer_type,
    a.notice_url, a.contract_start_date, a.contract_end_date, a.contract_duration,
    COALESCE(NULLIF(a.supplier_id, 0)::text, NULLIF(a.company_number, ''), a.supplier_name) AS sup_key,
    UPPER(TRIM(REGEXP_REPLACE(a.authority_name, '\\s+', ' ', 'g'))) AS auth_key
"""


SCOPES = ("all", "direct")  # direct = leave out framework / call-off appointments


# The columns `cat` selects (_AWARD_COLUMNS), without contract_value, which fetch_award_rows restates.
_RADAR_COLUMNS_NO_VALUE = ", ".join(f"x.{c}" for c in (
    "id", "supplier_id", "company_number", "supplier_name", "authority_name", "tender_title", "cpv_code",
    "date_signed", "procurement_type", "is_framework", "buyer_type", "notice_url", "contract_start_date",
    "contract_end_date", "contract_duration", "sup_key", "auth_key"))


def fetch_award_rows(
    cursor: Any,
    cat: Category,
    authority: str = "all",
    window: str = DEFAULT_WINDOW,
    today: date | None = None,
    scope: str = "all",
) -> tuple[list[dict[str, Any]], bool]:
    """Deduplicated awards in a category: (rows, truncated). One sequential scan of the awards."""
    if scope not in SCOPES:
        raise CategoryError(f"unknown scope: {scope}")
    cat_sql, cat_params = category_where(cat)
    auth_sql, auth_params = authority_where(authority)
    scope_sql = "AND COALESCE(a.is_framework, 0) = 0" if scope == "direct" else ""
    sql = f"""
        WITH cat AS (
            SELECT {_AWARD_COLUMNS}
            FROM contract_awards a
            WHERE a.source_portal IN ({_PORTALS})
              AND a.date_signed >= %s
              AND a.authority_name IS NOT NULL AND TRIM(a.authority_name) <> ''
              AND (a.currency IS NULL OR a.currency = 'GBP')
              AND {cat_sql}
              AND {auth_sql}
              {scope_sql}
        ),
        d0 AS (
            SELECT DISTINCT ON (c.sup_key, c.auth_key, c.date_signed) c.*
            FROM cat c
            ORDER BY c.sup_key, c.auth_key, c.date_signed, c.contract_value DESC NULLS LAST, c.id
        ),
        d AS (
            -- a notice that names several suppliers carries one value on every row: count it once between them
            -- (the same rule as DEDUPED_AWARDS_CTE_SQL, so Market Radar agrees with Buyer and Supplier Intelligence)
            SELECT {_RADAR_COLUMNS_NO_VALUE},
                   CASE WHEN x.shared_n > 1 THEN x.contract_value / x.shared_n ELSE x.contract_value END AS contract_value,
                   x.contract_value AS contract_value_notice, x.shared_n
            FROM (SELECT d.*, {SHARED_VALUE_N_SQL} AS shared_n FROM d0 d) x
        )
        SELECT d.*, s.name AS supplier_display, s.company_number AS supplier_cnum,
               COALESCE(ss.listable, TRUE) AS listable
        FROM d
        LEFT JOIN suppliers s ON s.id = d.supplier_id
        LEFT JOIN supplier_stats ss ON ss.supplier_id = d.supplier_id
        ORDER BY d.date_signed DESC NULLS LAST, d.id DESC
        LIMIT %s
    """
    # placeholders appear in this order in the statement: window start, category, authority type, limit
    params = [window_start(window, today)] + cat_params + auth_params + [MAX_ROWS + 1]
    cursor.execute(sql, params)
    columns = [c[0] for c in cursor.description]
    rows = [dict(zip(columns, r)) for r in cursor.fetchall()]
    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    if authority in {t for t, _ in _LOCAL_PATTERNS}:
        rows = [r for r in rows if authority_matches(authority, r["authority_name"], r["buyer_type"])]
    return rows, truncated


# ──────────────────────────────────────────── cache ────────────────────────────────────────────

class AnalysisCache:
    """Small thread-safe TTL + LRU cache with one lock per key, so the tabs a page opens at once
    share a single scan instead of each running their own."""

    def __init__(self, ttl: float = CACHE_TTL_SECONDS, max_entries: int = CACHE_MAX_ENTRIES, clock: Callable[[], float] = time.time):
        self._ttl = ttl
        self._max = max_entries
        self._clock = clock
        self._data: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _fresh(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item and self._clock() - item[0] < self._ttl:
            self._data.move_to_end(key)
            return item[1]
        return None

    def get_or_compute(self, key: str, compute: Callable[[], Any]) -> Any:
        with self._guard:
            hit = self._fresh(key)
            if hit is not None:
                return hit
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            with self._guard:
                hit = self._fresh(key)
                if hit is not None:
                    return hit
            value = compute()
            with self._guard:
                self._data[key] = (self._clock(), value)
                self._data.move_to_end(key)
                while len(self._data) > self._max:
                    evicted, _ = self._data.popitem(last=False)
                    self._locks.pop(evicted, None)
            return value

    def clear(self) -> None:
        with self._guard:
            self._data.clear()
            self._locks.clear()


ANALYSIS_CACHE = AnalysisCache()


def get_analysis(
    connect: Callable[[], Any],
    cat: Category,
    authority: str = "all",
    window: str = DEFAULT_WINDOW,
    scope: str = "all",
) -> dict[str, Any]:
    """Cached analysis for (category, authority type, window, scope); `connect` opens a DB connection."""
    if authority not in AUTHORITY_LABELS or authority == "local-other":
        raise CategoryError(f"unknown authority type: {authority}")
    key = f"{cat.key}#{authority}#{window}#{scope}"

    def compute() -> dict[str, Any]:
        conn = connect()
        try:
            with conn.cursor() as cur:
                rows, truncated = fetch_award_rows(cur, cat, authority, window, scope=scope)
        finally:
            conn.close()
        result = build_analysis(rows, truncated, cat)
        result["computed_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        return result

    return ANALYSIS_CACHE.get_or_compute(key, compute)


# ─────────────────────────────────────────── CPV labels ────────────────────────────────────────

_CPV_JUNK_LABELS = {"", "not available", "public sector contract", "n/a", "none"}
_cpv_cache: dict[str, Any] = {"at": 0.0, "labels": {}, "counts": {}}
_cpv_lock = threading.Lock()
CPV_TTL_SECONDS = 6 * 3600


def _cpv_prefix(code: str) -> str:
    """The shortest prefix that selects exactly this code's branch ("50720000" -> "5072")."""
    stripped = code.rstrip("0")
    return stripped if len(stripped) >= 2 else code[:2]


def load_cpv_labels(connect: Callable[[], Any], force: bool = False) -> tuple[dict[str, str], dict[str, int]]:
    """({cpv prefix: label}, {cpv prefix: awards}) for every code in the awards data.

    Labels come from the data itself (the most common real description per code). A prefix that
    no award uses on its own ("507") takes the label of its busiest child."""
    with _cpv_lock:
        if not force and _cpv_cache["labels"] and time.time() - _cpv_cache["at"] < CPV_TTL_SECONDS:
            return _cpv_cache["labels"], _cpv_cache["counts"]
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT cpv_code, cpv_description, COUNT(*)
                FROM contract_awards
                WHERE cpv_code ~ '^[0-9]{8}$' AND cpv_description IS NOT NULL
                GROUP BY cpv_code, cpv_description
            """)
            described = cur.fetchall()
            cur.execute("SELECT cpv_code, COUNT(*) FROM contract_awards WHERE cpv_code ~ '^[0-9]{8}$' GROUP BY cpv_code")
            totals = {code: n for code, n in cur.fetchall()}
    finally:
        conn.close()

    best: dict[str, tuple[int, str]] = {}  # code -> (uses, most common real description)
    for code, desc, n in described:
        text = (desc or "").strip()
        if text.lower() not in _CPV_JUNK_LABELS and (code not in best or n > best[code][0]):
            best[code] = (n, text)

    labels: dict[str, str] = {}
    label_uses: dict[str, int] = {}
    for code, (uses, text) in best.items():
        prefix = _cpv_prefix(code)
        if uses > label_uses.get(prefix, -1):
            labels[prefix], label_uses[prefix] = text, uses

    counts: dict[str, int] = {}
    for code, n in totals.items():
        for length in range(2, 9):
            counts[code[:length]] = counts.get(code[:length], 0) + n

    child: dict[str, tuple[int, str]] = {}
    for code, (_, text) in best.items():
        for length in range(2, 8):
            prefix = code[:length]
            if prefix not in labels and totals.get(code, 0) > child.get(prefix, (-1, ""))[0]:
                child[prefix] = (totals.get(code, 0), text)
    for prefix, (_, text) in child.items():
        labels[prefix] = text

    with _cpv_lock:
        _cpv_cache.update(at=time.time(), labels=labels, counts=counts)
    return labels, counts


def suggest_categories(connect: Callable[[], Any], text: str, limit: int = 8) -> dict[str, Any]:
    """Picker suggestions for what the user typed: presets, CPV codes/labels, or an award-title search."""
    text = (text or "").strip()
    low = text.lower()
    presets = [
        {"preset": p["id"], "label": p["label"], "hint": p["hint"]}
        for p in CATEGORY_PRESETS
        if not text or low in p["label"].lower() or low in p["hint"].lower() or any(low in k for k in p["keywords"])
    ]
    cpv: list[dict[str, Any]] = []
    if text:
        labels, counts = load_cpv_labels(connect)
        digits = re.sub(r"\D", "", text)
        if digits and digits == text.replace(" ", ""):
            candidates = [p for p in labels if p.startswith(digits)]
        else:
            low = text.lower()
            candidates = [p for p, label in labels.items() if low in label.lower()]
        # busiest first, shallower (broader) codes first among equally busy ones; a branch that only
        # repeats its parent's label ("Boiler installations" at 4216, 42160, 421600 ...) is dropped
        candidates.sort(key=lambda p: (-counts.get(p, 0), len(p), p))
        seen_labels: set[str] = set()
        for prefix in candidates:
            label = labels[prefix]
            if label.lower() in seen_labels:
                continue
            seen_labels.add(label.lower())
            cpv.append({"cpv": prefix, "label": label, "awards": counts.get(prefix, 0)})
            if len(cpv) >= limit:
                break
    return {"query": text, "presets": presets, "cpv": cpv}


# ───────────────────────────────── organisation and dashboard ──────────────────────────────────

def organisation_profile(name: str, buyer_type: str | None = None, contracts: int | None = None) -> dict[str, Any]:
    """The buyer a user has chosen as "their organisation", as the page needs it."""
    type_id = classify_authority(name, buyer_type)
    return {
        "name": name,
        "key": canonical_buyer_key(name, buyer_type),
        "type": type_id,
        "type_label": AUTHORITY_LABELS.get(type_id, type_id),
        "buyer_type": buyer_type,
        "contracts": contracts,
    }


def search_organisations(cursor: Any, text: str, limit: int = 12) -> list[dict[str, Any]]:
    """Buyers whose name contains the text, busiest first, with spelling variants merged."""
    text = (text or "").strip()
    if len(text) < 2:
        return []
    cursor.execute(
        """
        SELECT authority_name, buyer_type, total_contracts
        FROM buyer_stats
        WHERE authority_name ILIKE %s AND total_contracts > 0
        ORDER BY total_contracts DESC
        LIMIT 80
        """,
        [f"%{_like_escape(text)}%"],
    )
    merged: dict[str, dict[str, Any]] = {}
    for name, buyer_type, contracts in cursor.fetchall():
        key = canonical_buyer_key(name, buyer_type)
        entry = merged.setdefault(key, {"best": (name, buyer_type, contracts or 0), "contracts": 0})
        entry["contracts"] += contracts or 0
        if (contracts or 0) > entry["best"][2]:
            entry["best"] = (name, buyer_type, contracts or 0)
    out = [organisation_profile(e["best"][0], e["best"][1], e["contracts"]) for e in merged.values()]
    out.sort(key=lambda o: o["contracts"] or 0, reverse=True)
    return out[:limit]


def lookup_organisation(cursor: Any, name: str) -> dict[str, Any] | None:
    """The stored buyer with exactly this name (as search_organisations returned it), or None."""
    cursor.execute(
        "SELECT authority_name, buyer_type, total_contracts FROM buyer_stats WHERE authority_name = %s LIMIT 1",
        [name],
    )
    row = cursor.fetchone()
    if not row:
        return None
    return organisation_profile(row[0], row[1], row[2])


def _dashboard_rows(cursor: Any, where_sql: str, params: list[Any], limit: int) -> list[dict[str, Any]]:
    cursor.execute(
        f"""
        SELECT {_AWARD_COLUMNS}, s.name AS supplier_display, s.company_number AS supplier_cnum
        FROM contract_awards a
        LEFT JOIN suppliers s ON s.id = a.supplier_id
        WHERE a.source_portal IN ({_PORTALS})
          AND a.authority_name IS NOT NULL AND TRIM(a.authority_name) <> ''
          AND (a.currency IS NULL OR a.currency = 'GBP')
          AND {where_sql}
        ORDER BY a.date_signed DESC NULLS LAST, a.id DESC
        LIMIT %s
        """,
        params + [limit],
    )
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, r)) for r in cursor.fetchall()]


def _drop_repeats(prepared: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same supplier + buyer + signing date is one contract republished (see the module note)."""
    seen: set[tuple[Any, ...]] = set()
    out = []
    for r in prepared:
        k = (r["supplier_key"], r["buyer_key"], r["signed"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def fetch_org_renewals(cursor: Any, org: dict[str, Any], today: date | None = None, days: int = 183) -> list[dict[str, Any]]:
    """This organisation's own contracts that end within the next `days` days, soonest first."""
    today = today or date.today()
    token = place_token(org["name"]).lower()
    rows = _dashboard_rows(
        cursor,
        "a.authority_name ILIKE %s AND a.contract_end_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' "
        "AND LEFT(a.contract_end_date, 10) BETWEEN %s AND %s",
        [f"%{_like_escape(token)}%", today.isoformat(), (today + timedelta(days=days)).isoformat()],
        2000,
    )
    prepared = [prepare_row(r) for r in rows]
    mine = _drop_repeats([r for r in prepared if r["buyer_key"] == org["key"]])
    mine.sort(key=lambda r: r["ended"] or date.max)
    out = []
    for r in mine:
        item = _award_public(r)
        item["days_left"] = (r["ended"] - today).days if r["ended"] else None
        item["cpv"] = r["cpv"]
        out.append(item)
    return out


def peer_scope(org_type: str) -> str:
    """Authority type used to pick an organisation's peers (an unclassified council's peers are all councils)."""
    return "local-government" if org_type == "local-other" else org_type


def _type_in_scope(scope: str, type_id: str) -> bool:
    if scope in ("", "all"):
        return True
    if scope == "local-government":
        return type_id in {t for t, _ in _LOCAL_PATTERNS} or type_id == "local-other"
    return type_id == scope


def fetch_peer_activity(
    cursor: Any,
    categories: list[Category],
    org: dict[str, Any],
    today: date | None = None,
    days: int = 183,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Recent awards by buyers like this one, in the categories the user is watching."""
    if not categories:
        return []
    today = today or date.today()
    categories = categories[:6]
    scope = peer_scope(org["type"])
    cat_clauses: list[str] = []
    params: list[Any] = []
    for cat in categories:
        sql, p = category_where(cat)
        cat_clauses.append(sql)
        params.extend(p)
    auth_sql, auth_params = authority_where(scope)
    rows = _dashboard_rows(
        cursor,
        f"a.date_signed >= %s AND ({' OR '.join(cat_clauses)}) AND {auth_sql}",
        [(today - timedelta(days=days)).isoformat()] + params + auth_params,
        600,
    )
    out: list[dict[str, Any]] = []
    for r in _drop_repeats([prepare_row(x) for x in rows]):
        if r["buyer_key"] == org["key"] or not _type_in_scope(scope, r["type_id"]):
            continue
        tagged = next((c for c in categories if category_matches(c, r["title"], r["cpv"])), None)
        item = _award_public(r)
        item["buyer"] = r["buyer"]
        item["category"] = tagged.label if tagged else None
        item["category_key"] = tagged.key if tagged else None
        out.append(item)
        if len(out) >= limit:
            break
    return out
