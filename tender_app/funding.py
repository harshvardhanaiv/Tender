"""Read side of the funding opportunities imported from Find a Grant (see fag_import.py).

Open grants only: a grant that has closed is not shown. A grant that is open all year is flagged `ongoing`
(the service forces a far-off closing date on those) and is never ranked or flagged by that date.
Relevance is a plain word match between the grant and the user's company profile, not a prediction."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from tender_app import funding_fit as ff
from tender_app import growth_studio as gs
from tender_app import uk_time

APPLICANT_TYPES = ("Private Sector", "Non-profit", "Public Sector", "Personal / Individual")
CLOSING_SOON_DAYS = 30
PAGE_SIZE = 20
SORTS = ("relevance", "closing", "award")
_SELECT = """
    SELECT label, name, funder, summary, min_award, max_award, total_award, locations, applicants,
           open_date, close_date, close_at, ongoing, apply_url, eligibility, url
    FROM funding_opportunities
"""
_KEYS = ("label", "name", "funder", "summary", "min_award", "max_award", "total_award", "locations", "applicants",
         "open_date", "close_date", "close_at", "ongoing", "apply_url", "eligibility", "url")


def _tables_exist(cursor: Any) -> bool:
    cursor.execute("SELECT to_regclass('public.funding_opportunities') IS NOT NULL")
    if not cursor.fetchone()[0]:
        return False
    cursor.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'funding_opportunities' AND column_name = 'close_at'")
    if not cursor.fetchone():  # a table made before closing times were stored gets its column on first read
        cursor.execute("ALTER TABLE funding_opportunities ADD COLUMN IF NOT EXISTS close_at VARCHAR(16)")
    return True


def _public(row: tuple, today: date) -> dict[str, Any]:
    rec = dict(zip(_KEYS, row))
    # A reference left in the name by the source ("itt_4092 - GRANT- Centre of Excellence ...") is not part of it.
    if rec.get("name"):
        rec["name"] = re.sub(r"^\s*itt_\d+\s*-\s*(?:GRANT-?\s*)?", "", rec["name"], flags=re.I).strip() or rec["name"]
    for key in ("min_award", "max_award", "total_award"):
        rec[key] = float(rec[key]) if rec[key] is not None else None
    # Source quirks: a minimum of £1 means "not stated", and a maximum equal to a big pot is the pot, not one award.
    if rec["min_award"] is not None and rec["min_award"] <= 100:
        rec["min_award"] = None
    if rec["max_award"] is not None and rec["max_award"] == rec["total_award"] and rec["total_award"] > 1e6:
        rec["max_award"] = None
    rec["locations"] = list(rec["locations"] or [])
    rec["applicants"] = list(rec["applicants"] or [])
    close = rec["close_date"]
    rec["days_left"] = None if rec["ongoing"] or not close else (date.fromisoformat(close) - today).days
    return rec


# Whole-word search: words are stemmed, hyphens are spaces, and a few terms stand for their long form.
_SYNONYMS = {
    "ai": ("ai", "artificial intelligence"),
    "dsit": ("dsit", "department for science innovation and technology"),
    "desnz": ("desnz", "department for energy security and net zero"),
    "defra": ("defra", "department for environment food and rural affairs"),
    "ukri": ("ukri", "uk research and innovation"),
    "sme": ("sme", "smes", "small and medium"),
    "smes": ("sme", "smes", "small and medium"),
    "r&d": ("r&d", "research and development"),
    "rd": ("r&d", "research and development"),
    "netzero": ("net zero",),
    "analytics": ("analytics", "data", "business intelligence", "insight"),
    "bi": ("bi", "business intelligence", "analytics"),
}
_TERM = re.compile(r"[a-z0-9&]+")


_ENDING = re.compile("(ations?|ative|ating|ated|ates|ate|ions?)$")


def _search_stem(word: str) -> str:
    """gs.stem plus the -ate/-ation family, so "innovate", "innovation" and "innovative" meet."""
    cut = _ENDING.sub("", word)
    return gs.stem(cut if len(cut) >= 4 else word)


def _norm(text: str | None) -> str:
    """Lower case, hyphens and punctuation as spaces, every word stemmed, padded with spaces for whole-word matching."""
    return " " + " ".join(_search_stem(w) for w in _TERM.findall((text or "").lower().replace("-", " "))) + " "


# The long form of a term finds what the short form finds: "artificial intelligence" and "AI" are one search.
_LONG_FORMS = {phrase: key for key, phrases in _SYNONYMS.items() for phrase in phrases if " " in phrase and key not in ("smes", "rd", "bi", "netzero")}


def _query_groups(q: str) -> list[list[str]]:
    """One group per query word (all groups must match); a group is the phrases any of which satisfies it."""
    groups: list[list[str]] = []
    text = q.lower().replace("-", " ")
    for long_form, key in sorted(_LONG_FORMS.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(r"\b" + re.escape(long_form) + r"\b", key, text)
    for word in _TERM.findall(text):
        phrases = _SYNONYMS.get(word, (word,))
        groups.append([_norm(p) for p in phrases])
    return groups


def text_matches(groups: list[list[str]], rec: dict[str, Any]) -> bool:
    if not groups:
        return True
    hay = _norm(" ".join(filter(None, (rec["name"], rec["funder"], rec["summary"], rec["eligibility"]))))
    return all(any(p in hay for p in group) for group in groups)


def overlaps(rec: dict[str, Any], need_min: float | None, need_max: float | None) -> bool:
    """Whether the grant's award range meets the range the user needs; a missing end of the grant's range is open."""
    low = rec["min_award"] if rec["min_award"] is not None else 0.0
    high = rec["max_award"] if rec["max_award"] is not None else float("inf")
    if rec["max_award"] is None and rec["min_award"] is None:
        return True
    if need_min is not None and high < need_min:
        return False
    return not (need_max is not None and low > need_max)


def dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The service sometimes lists one grant under two slugs (-1, -2): keep the newest slug of identical name, funder and dates."""
    best: dict[tuple, dict[str, Any]] = {}
    for rec in items:
        key = (_norm(rec["funder"]), _norm(rec["name"]), rec["open_date"], rec["close_date"])
        if key not in best or rec["label"] > best[key]["label"]:
            best[key] = rec
    keep = {id(r) for r in best.values()}
    return [r for r in items if id(r) in keep]


BEST_LIMIT = 6


def best_of(items: list[dict[str, Any]], limit: int = BEST_LIMIT) -> list[dict[str, Any]]:
    """The grants worth a look first: a real word match with the profile (two shared words or more), the stronger band
    first, and within a band the ones closing within CLOSING_SOON_DAYS before the rest. Each gets `closing_soon`."""
    picks = []
    for rec in items:
        if len(rec.get("matches") or []) < 2:
            continue
        days = rec.get("days_left")
        picks.append({**rec, "closing_soon": not rec["ongoing"] and days is not None and 0 <= days <= CLOSING_SOON_DAYS})
    picks.sort(key=lambda r: (r["band"] != "high", not r["closing_soon"], -len(r["matches"]), r["days_left"] if r["days_left"] is not None else 99999, r["name"]))
    return picks[:limit]


def get_one(cursor: Any, label: str, today: date | None = None) -> dict[str, Any] | None:
    """One grant by its label (closed ones included, so a saved result can still be opened), or None."""
    if not _tables_exist(cursor):
        return None
    cursor.execute(_SELECT + " WHERE label = %s", (label,))
    row = cursor.fetchone()
    return _public(row, today or date.today()) if row else None


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


def _now_iso(today: date, now: datetime | None) -> str:
    """UK local time as "YYYY-MM-DDTHH:MM". With only a date (tests, previews) it is the start of that day."""
    return (now or datetime.combine(today, datetime.min.time())).strftime("%Y-%m-%dT%H:%M")


def _where(q: str, applicant: str, location: str, min_award: float | None, closing_soon: bool, today: date, now: datetime | None = None):
    iso = today.isoformat()
    # A grant stays listed until its closing time, not the end of its closing day; rows without a time fall back to the day.
    clauses = ["(open_date IS NULL OR open_date <= %s)",
               "(ongoing OR close_date IS NULL OR (close_at IS NULL AND close_date >= %s) OR close_at >= %s)"]
    params: list[Any] = [iso, iso, _now_iso(today, now)]
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
           today: date | None = None, now: datetime | None = None, where: str = "", include_uk_wide: bool = True, fit_applicant: dict[str, Any] | None = None,
           hide_unlikely: bool = False, saved: frozenset[str] | None = None, saved_only: bool = False,
           need_min: float | None = None, need_max: float | None = None) -> dict[str, Any]:
    """Open grants matching the filters. `imported` is False when the importer has never run.
    `where` narrows to grants available where the work is done; `fit_applicant` (see funding_fit) adds each grant's
    eligibility guide; `saved` is the user's saved labels and `saved_only` shows just those."""
    if now is None and today is None:
        now = uk_time.london_now()
    today = today or now.date()
    if not _tables_exist(cursor):
        return {"imported": False, "total": 0, "page": 1, "pages": 0, "items": [], "locations": [], "applicant_types": list(APPLICANT_TYPES)}
    sql_where, params = _where(q.strip(), applicant, location, min_award, closing_soon, today, now)
    cursor.execute(_SELECT + sql_where, params)
    items = dedupe([_public(r, today) for r in cursor.fetchall()])
    groups = _query_groups(q)
    items = [r for r in items if text_matches(groups, r) and (need_min is None and need_max is None or overlaps(r, need_min, need_max))]
    open_where, open_params = _where("", "", "", None, False, today, now)
    cursor.execute(_SELECT + open_where, open_params)
    open_total = len(dedupe([_public(r, today) for r in cursor.fetchall()]))
    cursor.execute("SELECT COUNT(*) FROM funding_opportunities WHERE NOT ongoing AND close_at >= %s AND close_at < %s",
                   (today.isoformat() + "T00:00", _now_iso(today, now)))
    closed_today = cursor.fetchone()[0]  # closed earlier today: hidden, and said so
    if where:
        items = [r for r in items if ff.covers(r["locations"], where, include_uk_wide)]
    for rec in items:
        words = relevance(keywords, rec)
        rec["matches"] = words
        rec["band"] = "high" if len(words) >= 4 else "mid" if len(words) >= 2 else "low" if words else None
        rec["saved"] = bool(saved and rec["label"] in saved)
        if fit_applicant is not None:
            rec["fit"] = ff.assess(rec, fit_applicant, kind=applicant, where=where, has_profile=bool(keywords))
    if saved_only:
        items = [r for r in items if r["saved"]]
    if hide_unlikely and fit_applicant is not None:
        items = [r for r in items if r["fit"]["verdict"] != "unlikely"]
    likely = sum(1 for r in items if r.get("fit", {}).get("verdict") == "likely")
    if sort not in SORTS:
        sort = "relevance"
    far = "9999-99-99"
    if sort == "closing":  # open-all-year grants have no real deadline, so they go last
        items.sort(key=lambda r: (r["ongoing"] or not r["close_date"], r["close_date"] or far, r["name"]))
    elif sort == "award":
        items.sort(key=lambda r: (-(r["max_award"] or 0), r["name"]))
    else:
        rank = {"likely": 0, "check": 1, "unlikely": 2}
        items.sort(key=lambda r: (rank.get(r.get("fit", {}).get("verdict"), 1), -len(r["matches"]), r["ongoing"] or not r["close_date"], r["close_date"] or far, r["name"]))
    total = len(items)
    pages = max(1, -(-total // PAGE_SIZE))
    page = max(1, page)  # a page past the end is an empty page (the Funding page steps back to the last one itself)
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
        "open_total": open_total,
        "closed_today": closed_today,
        "likely": likely,
    }
