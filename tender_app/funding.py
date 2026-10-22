"""Read side of the funding opportunities imported from Find a Grant (see fag_import.py).

Open grants only: a grant that has closed is not shown. A grant that is open all year is flagged `ongoing`
(the service forces a far-off closing date on those) and is never ranked or flagged by that date.
Relevance is a plain word match between the grant and the user's company profile, not a prediction."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable

from tender_app import growth_studio as gs

APPLICANT_TYPES = ("Private Sector", "Non-profit", "Public Sector", "Personal / Individual")
CLOSING_SOON_DAYS = 30
PAGE_SIZE = 20
SORTS = ("relevance", "closing", "award")
_SELECT = """
    SELECT label, name, funder, summary, min_award, max_award, total_award, locations, applicants,
           open_date, close_date, ongoing, apply_url, eligibility, url
    FROM funding_opportunities
"""
_KEYS = ("label", "name", "funder", "summary", "min_award", "max_award", "total_award", "locations", "applicants",
         "open_date", "close_date", "ongoing", "apply_url", "eligibility", "url")


def _tables_exist(cursor: Any) -> bool:
    cursor.execute("SELECT to_regclass('public.funding_opportunities') IS NOT NULL")
    return bool(cursor.fetchone()[0])


def _public(row: tuple, today: date) -> dict[str, Any]:
    rec = dict(zip(_KEYS, row))
    for key in ("min_award", "max_award", "total_award"):
        rec[key] = float(rec[key]) if rec[key] is not None else None
    rec["locations"] = list(rec["locations"] or [])
    rec["applicants"] = list(rec["applicants"] or [])
    close = rec["close_date"]
    rec["days_left"] = None if rec["ongoing"] or not close else (date.fromisoformat(close) - today).days
    return rec


def relevance(keywords: frozenset[str], rec: dict[str, Any]) -> list[str]:
    """The profile words that also appear in the grant, as the grant spells them, sorted. Empty without a profile."""
    if not keywords:
        return []
    text = " ".join(filter(None, (rec["name"], rec["funder"], rec["summary"], rec["eligibility"]))).lower()
    found: dict[str, str] = {}
    for word in gs._WORD.findall(text):
        if len(word) >= 3 and word not in gs.STOPWORDS:
            stem = gs.stem(word)
            if stem in keywords:
                found.setdefault(stem, word)
    return sorted(found.values())


def _where(q: str, applicant: str, location: str, min_award: float | None, closing_soon: bool, today: date):
    iso = today.isoformat()
    clauses = ["(open_date IS NULL OR open_date <= %s)", "(ongoing OR close_date IS NULL OR close_date >= %s)"]
    params: list[Any] = [iso, iso]
    if q:
        clauses.append("(name ILIKE %s OR funder ILIKE %s OR summary ILIKE %s)")
        params += [f"%{q}%"] * 3
    if applicant:
        clauses.append("%s = ANY(applicants)")
        params.append(applicant)
    if location:
        clauses.append("%s = ANY(locations)")
        params.append(location)
    if min_award is not None:
        clauses.append("(max_award IS NULL OR max_award >= %s)")
        params.append(min_award)
    if closing_soon:
        clauses.append("(NOT ongoing AND close_date IS NOT NULL AND close_date <= %s)")
        params.append((today + timedelta(days=CLOSING_SOON_DAYS)).isoformat())
    return " WHERE " + " AND ".join(clauses), params


def search(cursor: Any, *, q: str = "", applicant: str = "", location: str = "", min_award: float | None = None,
           closing_soon: bool = False, sort: str = "relevance", page: int = 1, keywords: frozenset[str] = frozenset(),
           today: date | None = None) -> dict[str, Any]:
    """Open grants matching the filters. `imported` is False when the importer has never run."""
    today = today or date.today()
    if not _tables_exist(cursor):
        return {"imported": False, "total": 0, "page": 1, "pages": 0, "items": [], "locations": [], "applicant_types": list(APPLICANT_TYPES)}
    where, params = _where(q.strip(), applicant, location, min_award, closing_soon, today)
    cursor.execute(_SELECT + where, params)
    items = [_public(r, today) for r in cursor.fetchall()]
    for rec in items:
        words = relevance(keywords, rec)
        rec["matches"] = words
        rec["band"] = "high" if len(words) >= 4 else "mid" if len(words) >= 2 else "low" if words else None
    if sort not in SORTS:
        sort = "relevance"
    far = "9999-99-99"
    if sort == "closing":  # open-all-year grants have no real deadline, so they go last
        items.sort(key=lambda r: (r["ongoing"] or not r["close_date"], r["close_date"] or far, r["name"]))
    elif sort == "award":
        items.sort(key=lambda r: (-(r["max_award"] or 0), r["name"]))
    else:
        items.sort(key=lambda r: (-len(r["matches"]), r["ongoing"] or not r["close_date"], r["close_date"] or far, r["name"]))
    total = len(items)
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(1, page), pages)
    cursor.execute("SELECT DISTINCT UNNEST(locations) FROM funding_opportunities ORDER BY 1")
    locations = [r[0] for r in cursor.fetchall()]
    cursor.execute("SELECT MAX(seen_at) FROM funding_opportunities")
    refreshed = cursor.fetchone()[0]
    return {
        "imported": True,
        "total": total,
        "page": page,
        "pages": pages,
        "items": items[(page - 1) * PAGE_SIZE: page * PAGE_SIZE],
        "locations": locations,
        "applicant_types": list(APPLICANT_TYPES),
        "refreshed": refreshed.isoformat() if refreshed else None,
        "has_profile": bool(keywords),
    }
