"""Services a user adds to Growth Studio beyond the built-in list.

A service is a name plus the CPV branches and title words that describe it, the same shape as a built-in category preset
(tender_app/market_radar.CATEGORY_PRESETS). Where the catalogue comes from:

  * built-in presets (about fifty, grouped), and
  * every CPV code that appears in the awards data, searchable by its own description (nothing is hard-coded), and
  * services users create. A new service is private. The user can offer it to everyone; it becomes visible to all users only
    when an administrator approves it, so one person's typo or spam never lands in anybody else's list.

Pure validation and shaping here (tested without a database); the SQL is in blueprints/growth_bp.py.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from tender_app import market_radar as mr
from tender_app.growth_studio import ValidationError

MAX_PER_USER = 50
MAX_KEYWORDS = 10
MAX_CPV = 6
VISIBILITY = ("private", "suggested", "shared")
_WORD_OK = re.compile(r"^[\w][\w &/'’+-]{0,38}[\w+]$|^[\w]{2}$", re.UNICODE)


def service_id(row_id: int) -> str:
    return f"service:{int(row_id)}"


def parse_service_id(value: str) -> int | None:
    m = re.fullmatch(r"service:(\d{1,9})", str(value or "").strip())
    return int(m.group(1)) if m else None


def ids_in(raw_category: Any) -> list[int]:
    """The custom-service ids named in a category argument ("a,service:5" or a list)."""
    items = raw_category if isinstance(raw_category, (list, tuple)) else str(raw_category or "").split(",")
    return [i for i in (parse_service_id(x) for x in items) if i is not None][:mr.MAX_SERVICES]


def clean_service(body: Mapping[str, Any]) -> dict[str, Any]:
    """Validated {name, keywords, cpv, share} from a create request. Raises ValidationError."""
    name = " ".join(str(body.get("name") or "").split())
    if not 3 <= len(name) <= 60:
        raise ValidationError("Give the service a name of 3 to 60 characters")
    raw_kw = body.get("keywords") or []
    if isinstance(raw_kw, str):
        raw_kw = re.split(r"[,\n;]", raw_kw)
    if not isinstance(raw_kw, (list, tuple)):
        raise ValidationError("keywords must be a list of words or phrases")
    keywords: list[str] = []
    for k in raw_kw:
        k = " ".join(str(k).lower().split())
        if not k:
            continue
        if not _WORD_OK.match(k):
            raise ValidationError(f"“{k[:40]}” is not a usable keyword (2 to 40 letters, numbers or spaces)")
        if k not in keywords:
            keywords.append(k)
    if len(keywords) > MAX_KEYWORDS:
        raise ValidationError(f"Use at most {MAX_KEYWORDS} keywords")
    raw_cpv = body.get("cpv") or []
    if isinstance(raw_cpv, str):
        raw_cpv = re.split(r"[,\s;]+", raw_cpv)
    cpv: list[str] = []
    for c in raw_cpv:
        if not str(c).strip():
            continue
        try:
            prefix = mr._clean_cpv(str(c))
        except mr.CategoryError:
            raise ValidationError(f"“{str(c)[:20]}” is not a CPV code (2 to 8 digits)") from None
        if prefix not in cpv:
            cpv.append(prefix)
    if len(cpv) > MAX_CPV:
        raise ValidationError(f"Use at most {MAX_CPV} CPV codes")
    if not keywords and not cpv:
        raise ValidationError("Add at least one CPV code or keyword so the service can find contracts")
    return {"name": name, "keywords": keywords, "cpv": cpv, "share": bool(body.get("share"))}


def spec_from_row(row_id: int, name: str, cpv_csv: str, keywords_csv: str) -> dict[str, Any]:
    """A category spec (the shape resolve_category reads) from a stored service."""
    return {
        "id": service_id(row_id), "label": name, "hint": "Your own service",
        "cpv": tuple(c for c in (cpv_csv or "").split(",") if c),
        "keywords": tuple(k for k in (keywords_csv or "").split("|") if k),
        "keyword_cpv": (),
    }


def public(row: tuple, me: str) -> dict[str, Any]:
    rid, owner, name, cpv_csv, kw_csv, visibility = row
    return {"id": service_id(rid), "label": name, "cpv": [c for c in (cpv_csv or "").split(",") if c],
            "keywords": [k for k in (kw_csv or "").split("|") if k], "visibility": visibility, "mine": owner == me,
            "group": "Your services" if owner == me else "Shared by other users"}


def search_cpv(labels: Mapping[str, str], counts: Mapping[str, int], query: str, limit: int = 20) -> list[dict[str, Any]]:
    """CPV branches whose description (or code) matches the typed words, busiest first. Only branches of 3 to 8 digits:
    a two-digit division is too broad to be a service on its own (the built-in presets cover those)."""
    q = " ".join(str(query or "").lower().split())
    if len(q) < 2:
        return []
    digits = re.sub(r"\D", "", q)
    words = [w for w in re.split(r"\s+", q) if w]
    out = []
    for code, label in labels.items():
        if len(code) < 3:
            continue
        text = label.lower()
        if (digits and code.startswith(digits)) or (not digits and all(w in text for w in words)):
            out.append({"cpv": code, "label": label, "awards": int(counts.get(code, 0))})
    out.sort(key=lambda r: (-r["awards"], len(r["cpv"]), r["label"]))
    return out[:limit]
