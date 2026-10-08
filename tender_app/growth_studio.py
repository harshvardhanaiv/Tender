"""Growth Studio: outreach signals, campaigns and their results for a supplier.

A signal is a reason to approach one buyer now, found in data TenderFlow already holds:

  * renewal     a contract in the supplier's category whose award notice gives an end date in the
                coming weeks (`contract_awards`);
  * development a planning approval for a tender-scale scheme (`planning_applications`). The
                buyer of the subcontract packages is the developer, not the council, so the target
                is the applicant company when the register names one;
  * engagement  a market engagement a public sector buyer has published through the buyer
                workspace and recorded a public notice link for (`market_engagements`).

Data rules are Market Radar's (tender_app/market_radar.py), so a figure here agrees with Buyer
Intelligence: recurring notices collapse to one (same supplier + buyer + date signed), a framework's
value is a ceiling shared by every appointed supplier and is never shown as spend, one award above
SINGLE_AWARD_STATS_CEILING_GBP is ignored, value 0 means "not published", councils that publish
under several spellings are one buyer.

"Fit" is a documented, rules-based 0-100 ranking, not AI and not a win probability. Every point comes
from a field on the record and `parts` says where it came from, so the page can show why a signal
ranks where it does. Weights are in the score_* functions.

What is deliberately NOT here, because the data does not support it:

  * buyer contacts: no table holds one, so the user types a role-based inbox per target;
  * sending and open/click tracking: Phase 1 exports a CSV (or a Mailchimp audience) and the user
    records sent / replied / meeting by hand, so there is no "opened" figure;
  * a contract value for planning approvals (registers publish none) or for a framework;
  * regions: award notices carry no reliable buyer location, so "where" is a text match on the
    buyer's (or the planning authority's) name and address.

Division of labour as in Market Radar: SQL only filters, every number is computed in plain Python
from the rows, so it is deterministic and testable without a database.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Mapping

from etenders_scraper.awards import UK_IE_SOURCE_PORTALS
from etenders_scraper.planning.scoring import lead_score
from tender_app import market_radar as mr

SIGNAL_TYPES = ("renewal", "development", "engagement")
SIGNAL_LABELS = {
    "renewal": "Contract renewal",
    "development": "New development",
    "engagement": "Market engagement open",
}
SIGNAL_PLURALS = {"renewal": "renewals", "development": "new developments", "engagement": "market engagements"}
DEFAULT_DAYS = 180
MIN_DAYS, MAX_DAYS = 30, 730
MAX_WHERE = 60
MAX_PER_TYPE = 25  # signals shown per type: one big type must not push the others off the page
MAX_CONTRACTS_SHOWN = 8
MAX_RENEWAL_ROWS = 4000
MAX_PLANNING_ROWS = 400
MAX_ENGAGEMENT_ROWS = 200
CACHE_TTL_SECONDS = 10 * 60

CACHE = mr.AnalysisCache(ttl=CACHE_TTL_SECONDS, max_entries=24)


class ValidationError(ValueError):
    """The caller's input is malformed (maps to HTTP 400)."""


# ───────────────────────────────────────── small helpers ──────────────────────────────────────────

def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def gbp(value: float | None) -> str | None:
    return None if value is None else f"£{value:,.0f}"


def round_sig(value: float, digits: int = 3) -> int:
    """29,993 -> 30,000: a figure that says "about" should not look more exact than it is."""
    if not value:
        return 0
    return int(round(value, digits - 1 - int(math.floor(math.log10(abs(value))))))


def long_date(d: date | None) -> str | None:
    return None if d is None else f"{d.day} {d.strftime('%B %Y')}"


def ago_text(days: int) -> str:
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    return f"{max(2, round(days / 30.4375))} months ago"


def in_days_text(days: int) -> str:
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


def _clean_where(raw: Any) -> str:
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if text and len(text) < 2:
        raise ValidationError("The place or buyer filter needs at least 2 characters")
    if len(text) > MAX_WHERE:
        raise ValidationError(f"The place or buyer filter can be at most {MAX_WHERE} characters")
    return text


# ───────────────────────────────────────────── filters ────────────────────────────────────────────

@dataclass(frozen=True)
class Filters:
    """What a Signals request asks for. Built by filters_from; `key` is the cache key."""

    category: mr.Category
    types: tuple[str, ...] = SIGNAL_TYPES
    days: int = DEFAULT_DAYS
    authority: str = "all"
    where: str = ""
    frameworks: bool = True

    def to_public(self) -> dict[str, Any]:
        return {
            "category": self.category.to_public(),
            "types": list(self.types),
            "days": self.days,
            "authority": self.authority,
            "authority_label": mr.AUTHORITY_LABELS[self.authority],
            "where": self.where,
            "frameworks": self.frameworks,
        }

    def to_request(self) -> dict[str, Any]:
        """The arguments that rebuild these filters (what the page stores per profile)."""
        out: dict[str, Any] = dict(self.category.to_public())
        out.pop("key", None)
        out.pop("label", None)
        out.update(types=list(self.types), days=self.days, authority=self.authority, where=self.where,
                   frameworks=self.frameworks)
        return out


def _flag(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def filters_from(source: Mapping[str, Any], cpv_labels: dict[str, str] | None = None) -> Filters:
    """Validate request arguments (query string or JSON body) into Filters. Raises mr.CategoryError
    for a bad category and ValidationError for anything else, both of which mean HTTP 400."""
    cat = mr.resolve_category(source.get("category") or source.get("preset"), source.get("cpv"), source.get("q"), cpv_labels)

    raw_types = source.get("types")
    if raw_types in (None, "", []):
        types = SIGNAL_TYPES
    else:
        items = raw_types if isinstance(raw_types, (list, tuple)) else str(raw_types).split(",")
        wanted = {str(t).strip() for t in items if str(t).strip()}
        unknown = sorted(wanted - set(SIGNAL_TYPES))
        if unknown or not wanted:
            raise ValidationError(f"types must be some of: {', '.join(SIGNAL_TYPES)}")
        types = tuple(t for t in SIGNAL_TYPES if t in wanted)

    raw_days = source.get("days")
    try:
        days = DEFAULT_DAYS if raw_days in (None, "") else int(raw_days)
    except (TypeError, ValueError):
        raise ValidationError("days must be a whole number") from None
    if not MIN_DAYS <= days <= MAX_DAYS:
        raise ValidationError(f"days must be between {MIN_DAYS} and {MAX_DAYS}")

    authority = str(source.get("authority") or "all").strip()
    if authority not in mr.AUTHORITY_LABELS or authority == "local-other":
        raise ValidationError(f"unknown authority type: {authority}")

    return Filters(
        category=cat,
        types=types,
        days=days,
        authority=authority,
        where=_clean_where(source.get("where")),
        frameworks=_flag(source.get("frameworks"), True),
    )


# ───────────────────────────────────── profile keywords and matching ──────────────────────────────

# Procurement boilerplate says nothing about what a company does ("services", "contract", "council"),
# so it never counts towards a match between a profile and an award title.
STOPWORDS = frozenset("""
a about above across after all also an and any are as at be been being both but by can could do does each
for from had has have having he her here his how i if in into is it its just may more most no not of on one
only or other our out over own per same she should so some such than that the their them then there these
they this those through to too under up us very was we were what when where which while who why will with
would you your
contract contracts service services provision provide provided providing supply supplies supplier suppliers
framework agreement works work council councils county borough district authority authorities public sector
delivery support management team teams company limited ltd plc group solutions solution client clients
customer customers uk england experience quality standard standards requirement requirements
""".split())

_WORD = re.compile(r"[a-z][a-z0-9]+")
_SUFFIX = re.compile(r"(ings|ing|ers|er|ed|es|s|e)$")


def stem(word: str) -> str:
    """Crude suffix stripping so "boilers", "boiler" and "boiling" meet; deterministic and good enough
    to compare a company's own description with an award title."""
    word = word.lower()
    if len(word) <= 4:
        return word
    base = _SUFFIX.sub("", word)
    return base if len(base) >= 4 else word


def tokens(text: str | None) -> list[str]:
    return [stem(w) for w in _WORD.findall((text or "").lower()) if len(w) >= 3 and w not in STOPWORDS]


def profile_keywords(profile_text: str | None, meta: Mapping[str, Any] | None = None, limit: int = 80) -> frozenset[str]:
    """The distinctive words a company uses about itself, most frequent first."""
    meta = meta or {}
    counts = Counter(tokens(profile_text))
    for field in ("business_type", "org_name"):
        counts.update(tokens(str(meta.get(field) or "")))
    return frozenset(word for word, _ in counts.most_common(limit))


def keyword_overlap(keywords: frozenset[str], *texts: str | None) -> int:
    if not keywords:
        return 0
    present: set[str] = set()
    for text in texts:
        present.update(tokens(text))
    return len(keywords & present)


def suggest_presets(keywords: frozenset[str], limit: int = 3) -> list[dict[str, Any]]:
    """Category presets that share words with the company's own description, best match first."""
    out = []
    for spec in mr.CATEGORY_PRESETS:
        words = set(tokens(" ".join((spec["label"], spec["hint"], *spec["keywords"]))))
        hits = sorted(keywords & words)
        if hits:
            out.append({"preset": spec["id"], "label": spec["label"], "matches": hits})
    out.sort(key=lambda s: (-len(s["matches"]), s["label"]))
    return out[:limit]


# ───────────────────────────────────────────── scoring ────────────────────────────────────────────

def fit_band(score: int) -> str:
    return "high" if score >= 75 else "mid" if score >= 50 else "low"


def _part(key: str, label: str, points: int, maximum: int, note: str) -> dict[str, Any]:
    return {"key": key, "label": label, "points": points, "max": maximum, "note": note}


def _finish(parts: list[dict[str, Any]]) -> dict[str, Any]:
    score = sum(p["points"] for p in parts)
    return {"score": score, "band": fit_band(score), "parts": parts}


def category_strength(cat: mr.Category, title: str | None, cpv_code: str | None, cpv_description: str | None = None) -> str | None:
    """How an award sits in a category: "cpv" (its own CPV code is in the category), "keyword" (only
    the title or CPV description matches) or None (not in the category)."""
    if not mr.category_matches(cat, title, cpv_code, cpv_description):
        return None
    code = cpv_code or ""
    if cat.preset_id:
        return "cpv" if any(code.startswith(p) for p in cat.cpv) else "keyword"
    return "cpv" if cat.cpv else "keyword"


def score_renewal(contract: Mapping[str, Any], cat: mr.Category, keywords: frozenset[str]) -> dict[str, Any]:
    """Timing 35, category 30, profile words 20, evidence 15."""
    days_left = contract.get("days_left")
    if days_left is None or days_left < 0:
        timing, timing_note = 0, "End date unknown or already passed"
    elif days_left < 30:
        timing, timing_note = 10, f"Ends in {plural(days_left, 'day')}: probably too late to influence the next buying decision"
    elif days_left < 60:
        timing, timing_note = 22, f"Ends in {days_left} days: tight, but still worth an approach"
    elif days_left < 180:
        timing, timing_note = 35, f"Ends in {days_left} days: time to approach before the buyer starts to re-procure"
    elif days_left < 270:
        timing, timing_note = 25, f"Ends in {days_left} days: early, so the buyer may not have started planning"
    else:
        timing, timing_note = 15, f"Ends in {days_left} days: a long way off"

    strength = category_strength(cat, contract.get("title"), contract.get("cpv"), contract.get("cpv_description"))
    if strength == "cpv":
        category, category_note = 30, f"The contract's CPV code ({contract.get('cpv')}) is in {cat.label}"
    elif strength == "keyword":
        category, category_note = 20, f"The contract title matches {cat.label}, but its CPV code does not"
    else:
        category, category_note = 0, "Not in the chosen category"

    overlap = keyword_overlap(keywords, contract.get("title"), contract.get("cpv_description"))
    if not keywords:
        profile, profile_note = 0, "The company profile has no description to compare: add one to improve this part"
    elif overlap == 0:
        profile, profile_note = 0, "The contract's title and category description share no words with your company profile"
    else:
        profile = {1: 8, 2: 14}.get(overlap, 20)
        profile_note = f"The contract's title and category description share {plural(overlap, 'word')} with your company profile"

    evidence = [
        (5, contract.get("value") is not None, "a published value", "no published value"),
        (5, bool(contract.get("suppliers")), "a named incumbent", "no named incumbent"),
        (5, contract.get("term_years") is not None, "a known contract term", "no known term"),
    ]
    evidence_points = sum(p for p, ok, _, _ in evidence if ok)
    have = [yes for _, ok, yes, _ in evidence if ok]
    lack = [no for _, ok, _, no in evidence if not ok]
    evidence_note = ("The notice gives " + ", ".join(have) if have else "The notice gives little detail") + (
        f" ({', '.join(lack)})" if lack and have else "")

    return _finish([
        _part("timing", "Timing", timing, 35, timing_note),
        _part("category", "Category", category, 30, category_note),
        _part("profile", "Your profile", profile, 20, profile_note),
        _part("evidence", "Evidence", evidence_points, 15, evidence_note),
    ])


# Planning approvals only matter to firms that build, fit out or service buildings. Presets that do,
# CPV divisions for construction (45) and architectural / engineering services (71), and words that
# show a custom search is about the same thing. Anything else (software, care, waste ...) gets no
# development signals rather than irrelevant ones.
BUILD_PRESETS = frozenset({"housing-repairs-gas", "construction-works", "grounds-tree-works"})
BUILD_CPV_PREFIXES = ("45", "71")
BUILD_WORDS = (
    "build", "construction", "plumb", "heating", "boiler", "electrical", "mechanical", "groundwork", "roofing",
    "joinery", "landscap", "demolition", "brickwork", "civil", "refurbish", "fit-out", "fitout", "scaffold",
    "drainage", "insulation", "glazing", "kitchen", "bathroom", "flooring", "decorat", "carpentry", "surfacing",
)


def build_strength(cat: mr.Category) -> str | None:
    """"preset", "cpv" or "keyword" when planning approvals are relevant to the category, else None."""
    if cat.preset_id:
        return "preset" if cat.preset_id in BUILD_PRESETS else None
    if cat.cpv:
        return "cpv" if cat.cpv[0].startswith(BUILD_CPV_PREFIXES) else None
    if any(word in token for token in cat.text_tokens for word in BUILD_WORDS):
        return "keyword"
    return None


# Not an approval of a scheme: the council's opinion on whether an environmental assessment is needed.
SCREENING_PATTERN = r"(screening|scoping)\s+(opinion|request|direction)|eia\s+(screening|scoping)"
_SCREENING_RE = re.compile(SCREENING_PATTERN, re.IGNORECASE)
_PERSONAL_TITLE_RE = re.compile(r"^\s*(mr|mrs|ms|miss|mx|dr|sir|lord|lady|prof)\b", re.IGNORECASE)
_COMPANY_FORM_RE = re.compile(r"\b(ltd|limited|llp|plc|llc|lp|inc|cic|group|holdings)\b", re.IGNORECASE)


def is_screening_opinion(description: str | None) -> bool:
    return bool(_SCREENING_RE.search(description or ""))


def score_development(row: Mapping[str, Any], strength: str, today: date) -> dict[str, Any]:
    """Scheme 35 (the Planning Leads lead score, scaled), timing 20, category 10, someone to approach 35.

    Growth Studio is for outreach, so a scheme with no developer named can never rank like one with a
    developer: it is research, not a target."""
    planning = row.get("lead_score")
    if planning is None:
        planning = lead_score(dict(row), today)
    potential = round(0.35 * max(0, min(100, int(planning))))

    decided = row.get("decided_date")
    age = (today - decided).days if isinstance(decided, date) else None
    if age is None or age < 0:
        timing, timing_note = 0, "No usable approval date"
    elif age <= 120:
        timing, timing_note = 20, f"Approved {ago_text(age)}: packages are usually let after approval"
    elif age <= 240:
        timing, timing_note = 14, f"Approved {ago_text(age)}"
    elif age <= 365:
        timing, timing_note = 8, f"Approved {ago_text(age)}: some packages may already be let"
    else:
        timing, timing_note = 0, f"Approved {ago_text(age)}: most packages will already be let"

    category = 7 if strength == "keyword" else 10
    developer = bool(_applicant(row))
    dwellings = (row.get("n_dwellings") or 0) >= 1
    address = bool((row.get("address") or "").strip())
    approach = (30 if developer else 0) + (3 if dwellings else 0) + (2 if address else 0)
    named = [what for ok, what in ((developer, "the developer"), (dwellings, "a dwelling count"), (address, "a site address")) if ok]
    approach_note = ("The register names " + ", ".join(named)) if named else "The register names no developer, dwelling count or address"
    if named and not developer:
        approach_note += ", but no developer to approach"
    return _finish([
        _part("scheme", "Scheme", potential, 35, f"Planning Leads rates this scheme {planning} out of 100 for size, decision, stage and recency"),
        _part("timing", "Timing", timing, 20, timing_note),
        _part("category", "Category", category, 10,
              "Approvals are relevant to " + ("your category" if strength != "keyword" else "the words in your search")),
        _part("approach", "Someone to approach", approach, 35, approach_note),
    ])


def category_relation(a: mr.Category, b: mr.Category) -> str | None:
    """"same", "related" (their CPV branches or search words overlap) or None."""
    if a.key == b.key:
        return "same"

    def prefixes_overlap() -> bool:
        return any(p.startswith(q) or q.startswith(p) for p in a.cpv for q in b.cpv)

    if (a.cpv or b.cpv) and prefixes_overlap():
        return "related"
    def words(c: mr.Category) -> set[str]:
        return {stem(w) for w in c.text_tokens} | {stem(w) for k in c.keywords for w in _WORD.findall(k.lower())}

    if (a.text_tokens or b.text_tokens) and words(a) & words(b):
        return "related"
    return None


def score_engagement(row: Mapping[str, Any], relation: str, today: date) -> dict[str, Any]:
    """Timing 40, category 40, evidence 20."""
    deadline = row.get("response_deadline")
    if deadline is None:
        timing, timing_note = 20, "No response deadline is given"
    else:
        left = (deadline - today).days
        if left >= 14:
            timing, timing_note = 40, f"Responses are open for {left} more days"
        elif left >= 7:
            timing, timing_note = 30, f"Responses close in {left} days"
        elif left >= 1:
            timing, timing_note = 20, f"Responses close in {plural(left, 'day')}"
        else:
            timing, timing_note = 10, "Responses close today"
    category = 40 if relation == "same" else 25
    evidence = (10 if deadline is not None else 0) + (10 if row.get("published_url") else 0)
    return _finish([
        _part("timing", "Timing", timing, 40, timing_note),
        _part("category", "Category", category, 40,
              "The buyer is engaging on your chosen category" if relation == "same" else "The buyer's category overlaps yours"),
        _part("evidence", "Evidence", evidence, 20, "The buyer gave a response deadline and a public notice link" if evidence == 20
              else "The notice is missing a deadline" if deadline is None else "No notice link"),
    ])


# ─────────────────────────────────────────── signal builders ──────────────────────────────────────

def _contract_entries(rows: Iterable[Mapping[str, Any]], today: date) -> list[dict[str, Any]]:
    """One entry per contract: rows with the same title and end date are one contract with several
    suppliers (a framework appoints many, a multi-lot award names several)."""
    groups: dict[tuple[str, date, bool], list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        ended = r["ended"]
        if not ended or ended < today:
            continue
        groups[(re.sub(r"\s+", " ", r["title"].lower()).strip(), ended, bool(r["framework"]))].append(r)
    entries = []
    for (_, ended, framework), members in groups.items():
        suppliers: dict[str, str] = {}
        for r in members:
            if r["listable"] and r["supplier_key"]:
                suppliers.setdefault(r["supplier_key"], r["supplier"])
        valued = [r["value_ok"] for r in members if r["value_ok"] is not None]
        value = None if framework or not valued else max(valued)
        terms = [r["term_days"] for r in members if r["term_days"]]
        term_days = max(terms) if terms else None
        first = members[0]
        entries.append({
            "title": first["title"] or "Untitled contract",
            "ends": ended.isoformat(),
            "days_left": (ended - today).days,
            "framework": framework,
            "suppliers": list(suppliers.values()),
            "supplier_count": len(suppliers),
            "value": value,
            "term_years": round(term_days / 365.25, 1) if term_days else None,
            "annual_value": round_sig(value / (term_days / 365.25)) if value and term_days else None,
            "route": first["route"],
            "cpv": first["cpv"],
            "cpv_description": first.get("cpv_description") or "",
            "url": next((r["url"] for r in members if r["url"]), None),
        })
    return entries


def _renewal_text(entry: Mapping[str, Any]) -> tuple[str, str]:
    kind = "framework" if entry["framework"] else "contract"
    headline = f"“{entry['title']}” {kind} ends {in_days_text(entry['days_left'])} ({long_date(date.fromisoformat(entry['ends']))})"
    bits = []
    suppliers = entry["suppliers"]
    if suppliers:
        extra = entry["supplier_count"] - min(len(suppliers), 3)
        names = ", ".join(suppliers[:3]) + (f" and {plural(extra, 'other')}" if extra > 0 else "")
        bits.append(("Appointed suppliers on record: " if entry["framework"] else "Incumbent: ") + names + ".")
    if entry["framework"]:
        bits.append("A framework's value is shared by every appointed supplier, so none is shown.")
    elif entry["value"] is not None:
        value = f"Last award on record: {gbp(entry['value'])}"
        if entry["annual_value"] and entry["term_years"]:
            value += f" (about {gbp(entry['annual_value'])} a year over {entry['term_years']:g} years)"
        bits.append(value + ".")
    return headline, " ".join(bits)


def renewal_signals(rows: Iterable[Mapping[str, Any]], cat: mr.Category, keywords: frozenset[str], today: date,
                    authority: str = "all") -> list[dict[str, Any]]:
    """One signal per buyer from prepared award rows (mr.prepare_row output plus cpv_description)."""
    by_buyer: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["buyer_key"] and mr.authority_matches(authority, r["buyer"], r.get("buyer_type")):
            by_buyer[r["buyer_key"]].append(r)
    out = []
    for buyer_key, members in by_buyer.items():
        entries = _contract_entries(members, today)
        if not entries:
            continue
        for e in entries:
            e["fit"] = score_renewal(e, cat, keywords)
        entries.sort(key=lambda e: (-e["fit"]["score"], e["days_left"], e["title"]))
        top = entries[0]
        name = Counter(r["buyer"] for r in members).most_common(1)[0][0]
        headline, detail = _renewal_text(top)
        type_id = members[0]["type_id"]
        out.append({
            "key": f"renewal:{buyer_key}",
            "type": "renewal",
            "badge": SIGNAL_LABELS["renewal"],
            "buyer": name,
            "authority": mr.AUTHORITY_LABELS.get(type_id, type_id),
            "headline": headline,
            "detail": detail,
            "date": top["ends"],
            "days": top["days_left"],
            "fit": top["fit"]["score"],
            "band": top["fit"]["band"],
            "fit_parts": top["fit"]["parts"],
            "contracts": [{k: v for k, v in e.items() if k != "fit"} | {"fit": e["fit"]["score"]} for e in entries[:MAX_CONTRACTS_SHOWN]],
            "more_contracts": max(0, len(entries) - MAX_CONTRACTS_SHOWN),
            "target": {"name": name, "key": buyer_key, "targetable": True, "reason": None},
            "links": [{"label": "Award notice", "url": top["url"]}] if top["url"] else [],
        })
    return out


def _applicant(row: Mapping[str, Any]) -> str | None:
    """The developer company named on the application, never an individual."""
    company = re.sub(r"\s+", " ", str(row.get("applicant_company") or "")).strip()
    if not company or (_PERSONAL_TITLE_RE.match(company) and not _COMPANY_FORM_RE.search(company)):
        return None
    return company


def development_signals(rows: Iterable[Mapping[str, Any]], strength: str, today: date) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        decided = row.get("decided_date")
        if not isinstance(decided, date) or decided > today or is_screening_opinion(row.get("description")):
            continue
        fit = score_development(row, strength, today)
        developer = _applicant(row)
        dwellings = row.get("n_dwellings") or 0
        scheme = (f"{dwellings:,} dwellings" if dwellings > 1 else "a major scheme" if row.get("app_size") == "Large" else "a scheme")
        scheme_text = f"{scheme} at {row['address'].strip()}" if (row.get("address") or "").strip() else f"{scheme} in {row.get('authority')}"
        description = re.sub(r"\s+", " ", str(row.get("description") or "")).strip()
        bits = [description[:240] + ("…" if len(description) > 240 else "")] if description else []
        if row.get("agent_company"):
            bits.append(f"Planning agent: {row['agent_company'].strip()}.")
        if not developer:
            bits.append("The register names no developer: open the planning record to find the applicant.")
        links = [{"label": label, "url": row[col]} for label, col in (("Planning record", "detail_url"), ("PlanIt", "planit_url"), ("Documents", "docs_url")) if row.get(col)]
        out.append({
            "key": f"development:{row['id']}",
            "type": "development",
            "badge": SIGNAL_LABELS["development"],
            "buyer": developer or "Developer not named",
            "authority": f"Planning authority: {row.get('authority')}" if row.get("authority") else "",
            "headline": f"{scheme_text[0].upper() + scheme_text[1:]} approved {ago_text((today - decided).days)}",
            "detail": " ".join(bits),
            "date": decided.isoformat(),
            "days": (today - decided).days,
            "fit": fit["score"],
            "band": fit["band"],
            "fit_parts": fit["parts"],
            "scheme": {
                "summary": scheme_text,
                "authority": row.get("authority"),
                "address": row.get("address"),
                "dwellings": dwellings or None,
                "approved": decided.isoformat(),
                "agent": row.get("agent_company"),
            },
            "target": (
                {"name": developer, "key": "dev:" + (mr.canonical_supplier_key(developer) or developer.upper()), "targetable": True, "reason": None}
                if developer else
                {"name": None, "key": None, "targetable": False,
                 "reason": "No developer company is named on this application, so there is nobody to add to a campaign."}
            ),
            "links": links,
        })
    return out


def engagement_signals(rows: Iterable[Mapping[str, Any]], cat: mr.Category, today: date, authority: str = "all") -> list[dict[str, Any]]:
    out = []
    for row in rows:
        org = (row.get("organisation") or "").strip()
        deadline = row.get("response_deadline")
        if not org or (deadline is not None and deadline < today):
            continue
        try:
            raw = row.get("category_json")
            spec = json.loads(raw) if raw else {}
            their = mr.resolve_category(spec.get("preset"), spec.get("cpv"), spec.get("q"))
        except (ValueError, TypeError, AttributeError):
            continue
        relation = category_relation(cat, their)
        if not relation or not mr.authority_matches(authority, org, None):
            continue
        fit = score_engagement(row, relation, today)
        bits = []
        if deadline is not None:
            bits.append(f"Responses by {long_date(deadline)}.")
        if row.get("supplier_day_at"):
            where = f" ({row['supplier_day_place']})" if row.get("supplier_day_place") else ""
            bits.append(f"Supplier day: {row['supplier_day_at']}{where}.")
        bits.append("Responding now can shape the specification before it is written.")
        key = mr.canonical_buyer_key(org)
        out.append({
            "key": f"engagement:{row['id']}",
            "type": "engagement",
            "badge": SIGNAL_LABELS["engagement"],
            "buyer": org,
            "authority": mr.AUTHORITY_LABELS.get(mr.classify_authority(org), ""),
            "headline": f"{org} is running a market engagement: “{row['title']}”"
                        + (f", open until {long_date(deadline)}" if deadline is not None else ""),
            "detail": " ".join(bits),
            "date": deadline.isoformat() if deadline is not None else None,
            "days": (deadline - today).days if deadline is not None else None,
            "fit": fit["score"],
            "band": fit["band"],
            "fit_parts": fit["parts"],
            "engagement": {
                "title": row["title"],
                "type": row.get("engagement_type"),
                "deadline": deadline.isoformat() if deadline is not None else None,
                "category": row.get("category_label"),
            },
            "target": {"name": org, "key": key, "targetable": True, "reason": None},
            "links": [{"label": "Published notice", "url": row["published_url"]}],
        })
    return out


# ───────────────────────────────────────── database access ────────────────────────────────────────

_PORTALS = ", ".join("'" + p.replace("'", "''") + "'" for p in UK_IE_SOURCE_PORTALS)


def _like(text: str) -> str:
    return f"%{mr._like_escape(text)}%"


def fetch_renewal_rows(cursor: Any, f: Filters, today: date) -> tuple[list[dict[str, Any]], bool]:
    """Prepared award rows whose end date falls in the next `days` days: (rows, truncated). Deduplicated
    like Market Radar (supplier + buyer + date signed, highest value kept)."""
    cat_sql, cat_params = mr.category_where(f.category)
    auth_sql, auth_params = mr.authority_where(f.authority)
    conds = [
        f"a.source_portal IN ({_PORTALS})",
        "a.contract_end_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'",
        "LEFT(a.contract_end_date, 10) BETWEEN %s AND %s",
        "a.authority_name IS NOT NULL AND TRIM(a.authority_name) <> ''",
        "(a.currency IS NULL OR a.currency = 'GBP')",
        cat_sql,
        auth_sql,
    ]
    params: list[Any] = [today.isoformat(), (today + timedelta(days=f.days)).isoformat()] + cat_params + auth_params
    if not f.frameworks:
        conds.append("COALESCE(a.is_framework, 0) = 0")
    if f.where:
        conds.append("a.authority_name ILIKE %s")
        params.append(_like(f.where))
    sql = f"""
        WITH cat AS (
            SELECT a.id, a.supplier_id, a.company_number, a.supplier_name, a.authority_name, a.tender_title,
                   a.cpv_code, a.cpv_description, a.contract_value, a.date_signed, a.procurement_type,
                   a.is_framework, a.buyer_type, a.notice_url, a.contract_start_date, a.contract_end_date,
                   a.contract_duration,
                   COALESCE(NULLIF(a.supplier_id, 0)::text, NULLIF(a.company_number, ''), a.supplier_name) AS sup_key,
                   UPPER(TRIM(REGEXP_REPLACE(a.authority_name, '\\s+', ' ', 'g'))) AS auth_key
            FROM contract_awards a
            WHERE {" AND ".join(conds)}
        ),
        d AS (
            SELECT DISTINCT ON (c.sup_key, c.auth_key, c.date_signed) c.*
            FROM cat c
            ORDER BY c.sup_key, c.auth_key, c.date_signed, c.contract_value DESC NULLS LAST, c.id
        )
        SELECT d.*, s.name AS supplier_display, s.company_number AS supplier_cnum,
               COALESCE(ss.listable, TRUE) AS listable
        FROM d
        LEFT JOIN suppliers s ON s.id = d.supplier_id
        LEFT JOIN supplier_stats ss ON ss.supplier_id = d.supplier_id
        ORDER BY LEFT(d.contract_end_date, 10), d.id
        LIMIT %s
    """
    cursor.execute(sql, params + [MAX_RENEWAL_ROWS + 1])
    columns = [c[0] for c in cursor.description]
    raw = [dict(zip(columns, r)) for r in cursor.fetchall()]
    truncated = len(raw) > MAX_RENEWAL_ROWS
    prepared = []
    for r in raw[:MAX_RENEWAL_ROWS]:
        row = mr.prepare_row(r)
        row["cpv_description"] = r.get("cpv_description") or ""
        row["buyer_type"] = r.get("buyer_type")
        prepared.append(row)
    return prepared, truncated


def fetch_planning_rows(cursor: Any, f: Filters, today: date) -> list[dict[str, Any]]:
    """Genuine, tender-scale schemes approved in the last `days` days, best Planning Leads score first."""
    conds = [
        "low_value_reason IS NULL",
        "app_state IN ('Permitted', 'Conditions')",
        "decided_date BETWEEN %s AND %s",
        "(app_size = 'Large' OR COALESCE(n_dwellings, 0) >= 10)",
        "COALESCE(description, '') !~* %s",
    ]
    params: list[Any] = [today - timedelta(days=f.days), today, SCREENING_PATTERN]
    if f.where:
        conds.append("(authority ILIKE %s OR region ILIKE %s OR address ILIKE %s)")
        params += [_like(f.where)] * 3
    cursor.execute(
        f"""
        SELECT id, uid, authority, country, region, description, address, postcode, app_size, app_state,
               app_type, n_dwellings, applicant_company, agent_company, decided_date, start_date,
               lead_score, detail_url, planit_url, docs_url
        FROM planning_applications
        WHERE {" AND ".join(conds)}
        ORDER BY lead_score DESC NULLS LAST, decided_date DESC
        LIMIT %s
        """,
        params + [MAX_PLANNING_ROWS],
    )
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, r)) for r in cursor.fetchall()]


def fetch_engagement_rows(cursor: Any, today: date, exclude_username: str) -> list[dict[str, Any]]:
    """Engagements other users have published with a public notice link and not yet closed."""
    cursor.execute(
        """
        SELECT id, organisation, title, category_json, category_label, engagement_type, supplier_day_at,
               supplier_day_place, response_deadline, published_url
        FROM market_engagements
        WHERE status = 'published' AND COALESCE(published_url, '') <> ''
          AND (response_deadline IS NULL OR response_deadline >= %s)
          AND username <> %s
        ORDER BY response_deadline NULLS LAST, published_at DESC NULLS LAST
        LIMIT %s
        """,
        [today, exclude_username, MAX_ENGAGEMENT_ROWS],
    )
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, r)) for r in cursor.fetchall()]


def build_signals(
    connect: Callable[[], Any],
    f: Filters,
    keywords: frozenset[str],
    username: str,
    today: date | None = None,
) -> dict[str, Any]:
    """Every signal for these filters, best fit first, before the user's own state (dismissed, opted
    out, already in a campaign) is applied. The award and planning queries are cached per filter set."""
    today = today or date.today()
    cat = f.category
    signals: list[dict[str, Any]] = []
    notes: dict[str, Any] = {"truncated": False, "development": None}

    if "renewal" in f.types:
        key = f"renewal#{cat.key}#{f.authority}#{f.days}#{f.where.lower()}#{int(f.frameworks)}#{today}"

        def load_renewals() -> tuple[list[dict[str, Any]], bool]:
            conn = connect()
            try:
                with conn.cursor() as cur:
                    return fetch_renewal_rows(cur, f, today)
            finally:
                conn.close()

        rows, truncated = CACHE.get_or_compute(key, load_renewals)
        notes["truncated"] = truncated
        signals += renewal_signals(rows, cat, keywords, today, f.authority)

    if "development" in f.types:
        strength = build_strength(cat)
        if strength is None:
            notes["development"] = (
                "New-development signals are not shown for this category: planning approvals only matter to "
                "construction, building services and similar work."
            )
        else:
            key = f"development#{f.days}#{f.where.lower()}#{today}"

            def load_planning() -> list[dict[str, Any]]:
                conn = connect()
                try:
                    with conn.cursor() as cur:
                        return fetch_planning_rows(cur, f, today)
                finally:
                    conn.close()

            signals += development_signals(CACHE.get_or_compute(key, load_planning), strength, today)

    if "engagement" in f.types:
        conn = connect()
        try:
            with conn.cursor() as cur:
                rows = fetch_engagement_rows(cur, today, username)
        finally:
            conn.close()
        signals += engagement_signals(rows, cat, today, f.authority)

    signals.sort(key=lambda s: (-s["fit"], s["days"] if s["days"] is not None else 10**6, s["key"]))
    return {"signals": signals, "notes": notes}


def apply_user_state(
    signals: list[dict[str, Any]],
    dismissed: set[str],
    opted_out: set[str],
    campaigns_by_buyer: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Attach what this user already did with each signal. A buyer who asked not to be contacted can
    never become a target again, whichever signal they appear under."""
    out = []
    for s in signals:
        s = dict(s)
        target = dict(s["target"])
        key = target.get("key")
        is_opted_out = bool(key) and key in opted_out
        if is_opted_out:
            target["targetable"] = False
            target["reason"] = "This buyer asked not to be contacted: they are on your do-not-contact list."
        s["target"] = target
        s["state"] = {
            "dismissed": s["key"] in dismissed,
            "opted_out": is_opted_out,
            "campaigns": list(campaigns_by_buyer.get(key, [])) if key else [],
        }
        out.append(s)
    return out


# ───────────────────────────────────────────── messages ───────────────────────────────────────────

# name, label, Mailchimp merge tag (<= 10 characters, as Mailchimp allows), whether it varies per
# recipient. Sender fields are the same on every message so the Mailchimp export writes them in.
MERGE_FIELDS: tuple[dict[str, Any], ...] = (
    {"name": "buyer_name", "label": "Buyer name", "tag": "COMPANY", "per_recipient": True, "types": SIGNAL_TYPES},
    {"name": "buyer_contact", "label": "Greeting (the procurement team)", "tag": "GREETING", "per_recipient": True, "types": SIGNAL_TYPES},
    {"name": "contract_title", "label": "Contract title", "tag": "CONTRACT", "per_recipient": True, "types": ("renewal",)},
    {"name": "renewal_date", "label": "Contract end date", "tag": "RENEWDATE", "per_recipient": True, "types": ("renewal",)},
    {"name": "incumbent", "label": "Incumbent supplier", "tag": "INCUMBENT", "per_recipient": True, "types": ("renewal",)},
    {"name": "last_award_value", "label": "Last award value", "tag": "AWARDVALUE", "per_recipient": True, "types": ("renewal",)},
    {"name": "scheme_summary", "label": "Scheme", "tag": "SCHEME", "per_recipient": True, "types": ("development",)},
    {"name": "approval_date", "label": "Approval date", "tag": "APPROVED", "per_recipient": True, "types": ("development",)},
    {"name": "planning_authority", "label": "Planning authority", "tag": "PLANAUTH", "per_recipient": True, "types": ("development",)},
    {"name": "engagement_title", "label": "Engagement title", "tag": "ENGAGEMENT", "per_recipient": True, "types": ("engagement",)},
    {"name": "response_deadline", "label": "Response deadline", "tag": "DEADLINE", "per_recipient": True, "types": ("engagement",)},
    {"name": "category", "label": "Category", "tag": "CATEGORY", "per_recipient": True, "types": SIGNAL_TYPES},
    {"name": "company_name", "label": "Your company", "tag": "MYCOMPANY", "per_recipient": False, "types": SIGNAL_TYPES},
    {"name": "sender_name", "label": "Your name", "tag": "SENDER", "per_recipient": False, "types": SIGNAL_TYPES},
)
MERGE_FIELD_NAMES = frozenset(m["name"] for m in MERGE_FIELDS)
_MERGE_TOKEN = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")

OPT_OUT_LINE = 'If you would rather not hear from us about this, reply "stop" and we will not contact you again.'
_OPT_OUT_RE = re.compile(
    r"opt[ -]?out|unsubscribe|reply\s+[\"“']?stop|(do not|don't|no longer) (wish|want) to (hear|receive)|not contact you again",
    re.IGNORECASE,
)
MAX_SUBJECT = 200
MAX_BODY = 6000
GREETING = "procurement team"


def has_opt_out(body: str | None) -> bool:
    return bool(_OPT_OUT_RE.search(body or ""))


def render_template(text: str, fields: Mapping[str, str | None]) -> tuple[str, list[str], list[str]]:
    """Fill {{merge_fields}}: (text, fields that were known but empty, names that are not merge fields).
    An unknown name is left as typed so the user can see and fix it."""
    missing: list[str] = []
    unknown: list[str] = []

    def fill(match: re.Match) -> str:
        name = match.group(1).lower()
        if name not in MERGE_FIELD_NAMES:
            if match.group(1) not in unknown:
                unknown.append(match.group(1))
            return match.group(0)
        value = (fields.get(name) or "").strip()
        if not value and name not in missing:
            missing.append(name)
        return value

    return _MERGE_TOKEN.sub(fill, text or ""), missing, unknown


def template_names(text: str | None) -> list[str]:
    return list(dict.fromkeys(m.group(1).lower() for m in _MERGE_TOKEN.finditer(text or "")))


def message_warnings(subject: str | None, body: str | None) -> list[dict[str, str]]:
    out = []
    if not (subject or "").strip():
        out.append({"code": "no_subject", "text": "The message has no subject."})
    if not (body or "").strip():
        out.append({"code": "no_body", "text": "The message has no body."})
    elif not has_opt_out(body):
        out.append({"code": "no_opt_out", "text": "The message has no way to opt out. Add a line such as: " + OPT_OUT_LINE})
    unknown = sorted({n for n in template_names(subject) + template_names(body) if n not in MERGE_FIELD_NAMES})
    if unknown:
        out.append({"code": "unknown_fields", "text": "Not a merge field: " + ", ".join("{{" + n + "}}" for n in unknown)})
    return out


def sender_fields(profile_name: str | None, profile_text: str | None, meta: Mapping[str, Any] | None, category_label: str) -> dict[str, str]:
    """What a message says about the sender, taken from the company profile as the user wrote it."""
    meta = meta or {}
    text = re.sub(r"\s+", " ", str(profile_text or "")).strip()
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0] if text else ""
    return {
        "company_name": str(meta.get("org_name") or profile_name or "").strip(),
        "sender_name": str(meta.get("contact_name") or "").strip(),
        "sender_email": str(meta.get("email") or "").strip(),
        "sender_phone": str(meta.get("phone") or "").strip(),
        "summary": first[:260],
        "category": category_label,
    }


def default_message(signal_type: str, sender: Mapping[str, str]) -> dict[str, str]:
    """A plain first draft the user edits. Only per-recipient facts are merge fields; what the sender
    says about itself is written in, from the company profile, so nothing is invented."""
    company = sender.get("company_name") or "our company"
    about = sender.get("summary") or ""
    sign_off = "\n".join(x for x in ("Kind regards,", sender.get("sender_name"), company if sender.get("sender_name") else None,
                                      sender.get("sender_phone"), sender.get("sender_email")) if x)
    if signal_type == "development":
        subject = "{{category}} for {{scheme_summary}}"
        opening = ("We noticed that planning permission was granted on {{approval_date}} for {{scheme_summary}} "
                   "({{planning_authority}}).")
        ask = ("We would welcome the chance to be considered for {{category}} packages as the project moves towards "
               "procurement, and can share references and accreditations on request.")
    elif signal_type == "engagement":
        subject = "Our response to your market engagement: {{engagement_title}}"
        opening = "We saw {{buyer_name}}'s market engagement, “{{engagement_title}}”, which asks for responses by {{response_deadline}}."
        ask = ("We would like to take part and share what we have learned delivering {{category}} for public sector "
               "buyers. Please let us know the best way to respond.")
    else:
        subject = "{{category}} support ahead of your contract renewal"
        opening = "We noticed that {{buyer_name}}'s contract “{{contract_title}}” is due to end on {{renewal_date}}."
        ask = ("We would welcome the chance to be considered when this goes back to market, and are happy to share "
               "references and accreditations or to answer any questions you have before then.")
    paragraphs = ["Hello {{buyer_contact}},", f"{opening} {about}".strip(), ask, sign_off, OPT_OUT_LINE]
    return {"subject": subject, "body": "\n\n".join(p for p in paragraphs if p)}


def fields_for_target(target: Mapping[str, Any], sender: Mapping[str, str]) -> dict[str, str]:
    """Merge-field values for one campaign target, from the signal as it was when it was added."""
    snap = target.get("snapshot") or {}
    kind = target.get("signal_type") or snap.get("type")
    values = {
        "buyer_name": target.get("buyer_name") or "",
        "buyer_contact": GREETING,
        "category": sender.get("category") or "",
        "company_name": sender.get("company_name") or "",
        "sender_name": sender.get("sender_name") or "",
    }
    if kind == "renewal":
        contract = (snap.get("contracts") or [{}])[0]
        ends = mr.parse_iso_date(contract.get("ends") or snap.get("date"))
        values.update(
            contract_title=contract.get("title") or "",
            renewal_date=long_date(ends) or "",
            incumbent=(contract.get("suppliers") or [""])[0] if not contract.get("framework") else "",
            last_award_value=gbp(contract.get("value")) or "",
        )
    elif kind == "development":
        scheme = snap.get("scheme") or {}
        values.update(
            scheme_summary=scheme.get("summary") or "",
            approval_date=long_date(mr.parse_iso_date(scheme.get("approved") or snap.get("date"))) or "",
            planning_authority=scheme.get("authority") or "",
        )
    elif kind == "engagement":
        eng = snap.get("engagement") or {}
        values.update(
            engagement_title=eng.get("title") or "",
            response_deadline=long_date(mr.parse_iso_date(eng.get("deadline"))) or "",
        )
    return values


def clean_ai_message(obj: Any) -> dict[str, Any]:
    """Make a model reply safe to put in the editor: text only, bounded, always with an opt-out line.
    Raises ValueError when there is nothing usable."""
    if not isinstance(obj, dict) or not isinstance(obj.get("subject"), str) or not isinstance(obj.get("body"), str):
        raise ValueError("The AI reply had no subject and body.")
    subject = re.sub(r"\s+", " ", obj["subject"]).strip()[:MAX_SUBJECT]
    body = obj["body"].replace("\r\n", "\n").replace("\r", "\n").strip()[:MAX_BODY - len(OPT_OUT_LINE) - 4]
    if not subject or not body:
        raise ValueError("The AI reply had an empty subject or body.")
    if not has_opt_out(body):
        body = f"{body}\n\n{OPT_OUT_LINE}"
    return {"subject": subject, "body": body, "warnings": message_warnings(subject, body)}


def ai_prompt(sender: Mapping[str, str], signal_types: Iterable[str], category_label: str, answers: list[dict[str, str]],
              profile_text: str, extra: str = "") -> tuple[str, str]:
    types = [t for t in SIGNAL_TYPES if t in set(signal_types)] or ["renewal"]
    allowed = [m for m in MERGE_FIELDS if m["name"] != "incumbent" and any(t in m["types"] for t in types)]
    fields = ", ".join("{{" + m["name"] + "}}" for m in allowed)
    system = (
        "You write short, plain-English outreach emails for a UK supplier approaching public sector buyers.\n"
        "Rules:\n"
        "1. Use only the facts in the SUPPLIER FACTS block. Never invent accreditations, certifications, clients, contract "
        "values, years of experience, locations or dates. If a fact is not given, leave it out.\n"
        f"2. Use these merge fields exactly as written, with double curly braces, and no others: {fields}.\n"
        "3. Address the buyer's procurement team, never a named person. Do not mention the incumbent supplier by name.\n"
        "4. The body is 90 to 150 words: polite and specific, with no hype and no promises of savings.\n"
        f"5. End the body with this sentence, word for word: {OPT_OUT_LINE}\n"
        '6. Reply with only JSON: {"subject": "...", "body": "..."}.'
    )
    facts = [f"Company: {sender.get('company_name') or 'not given'}"]
    if sender.get("sender_name"):
        facts.append(f"Signed by: {sender['sender_name']}")
    if profile_text.strip():
        facts.append("Company profile, as the company wrote it:\n" + profile_text.strip()[:2500])
    for a in answers:
        facts.append(f"{a['category']}: {a['question']} {a['answer']}".strip()[:500])
    kinds = {"renewal": "a contract that is about to end", "development": "a planning approval for a new scheme",
             "engagement": "a market engagement the buyer has opened"}
    user = (
        f"Category the supplier sells into: {category_label}\n"
        f"The email is triggered by: {' and '.join(kinds[t] for t in types)}.\n\n"
        "SUPPLIER FACTS\n" + "\n\n".join(facts)
        + (f"\n\nExtra instructions from the user (do not break the rules above): {extra.strip()[:500]}" if extra.strip() else "")
    )
    return system, user


# ───────────────────────────────────────────── validation ─────────────────────────────────────────

CHANNELS = {"email": "Email", "letter": "Letter", "phone": "Phone", "portal": "Portal or web form", "other": "Other"}
CAMPAIGN_STATUSES = ("draft", "active", "paused", "completed")
TARGET_STATUSES = ("not_sent", "sent", "replied", "meeting", "opted_out")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
ROLE_WORDS = frozenset("""
procurement contracts contract tenders tender commercial purchasing estates housing facilities info enquiries
enquiry admin office customerservices customer accounts supplier suppliers sourcing commissioning contact hello
team buying finance property repairs maintenance support
""".split())


def valid_email(value: str) -> bool:
    return len(value) <= 254 and bool(_EMAIL_RE.match(value))


def looks_personal_email(email: str) -> bool:
    """firstname.lastname@ style addresses: the common sign of a named person rather than a role inbox."""
    local = email.split("@", 1)[0].lower()
    parts = re.split(r"[._-]", local)
    return len(parts) == 2 and all(p.isalpha() and len(p) >= 2 for p in parts) and not any(p in ROLE_WORDS for p in parts)


def _text(value: Any, field: str, limit: int, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValidationError(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text")
    text = value.replace("\r\n", "\n").strip()
    if required and not text:
        raise ValidationError(f"{field} is required")
    if len(text) > limit:
        raise ValidationError(f"{field} can be at most {limit} characters")
    return text


def validate_campaign(data: Mapping[str, Any], partial: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "name" in data or not partial:
        out["name"] = re.sub(r"\s+", " ", _text(data.get("name"), "name", 120, required=True) or "").strip()
    if "channel" in data:
        if data["channel"] not in CHANNELS:
            raise ValidationError(f"channel must be one of: {', '.join(CHANNELS)}")
        out["channel"] = data["channel"]
    if "status" in data:
        if data["status"] not in CAMPAIGN_STATUSES:
            raise ValidationError(f"status must be one of: {', '.join(CAMPAIGN_STATUSES)}")
        out["status"] = data["status"]
    if "subject" in data:
        out["subject"] = _text(data["subject"], "subject", MAX_SUBJECT) or ""
    if "body" in data:
        out["body"] = _text(data["body"], "body", MAX_BODY) or ""
    return out


def validate_target_patch(data: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "included" in data:
        if not isinstance(data["included"], bool):
            raise ValidationError("included must be true or false")
        out["included"] = data["included"]
    if "contact_email" in data:
        raw = data["contact_email"]
        email = _text(raw, "contact_email", 254) if raw is not None else ""
        email = (email or "").lower()
        if email and not valid_email(email):
            raise ValidationError("That does not look like an email address")
        out["contact_email"] = email or None
    if "status" in data:
        if data["status"] not in TARGET_STATUSES:
            raise ValidationError(f"status must be one of: {', '.join(TARGET_STATUSES)}")
        out["status"] = data["status"]
    if "note" in data:
        out["note"] = _text(data["note"], "note", 500) or None
    return out


def status_timestamps(status: str, current: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """The sent / replied / meeting timestamps after a status change. They stay nested (a meeting
    implies a reply, a reply implies it was sent), so the funnel can never show more meetings than
    replies. Opting out keeps whatever was recorded."""
    sent, replied, meeting = current.get("sent_at"), current.get("replied_at"), current.get("meeting_at")
    if status == "not_sent":
        return {"sent_at": None, "replied_at": None, "meeting_at": None}
    if status == "sent":
        return {"sent_at": sent or now, "replied_at": None, "meeting_at": None}
    if status == "replied":
        return {"sent_at": sent or now, "replied_at": replied or now, "meeting_at": None}
    if status == "meeting":
        return {"sent_at": sent or now, "replied_at": replied or now, "meeting_at": meeting or now}
    return {"sent_at": sent, "replied_at": replied, "meeting_at": meeting}


# ───────────────────────────────────────────── exports ────────────────────────────────────────────

def csv_cell(value: Any) -> str:
    """A cell that a spreadsheet will show as text: values that start like a formula (buyer names and
    contract titles come from public notices and could) are prefixed with an apostrophe."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def eligible_targets(targets: Iterable[Mapping[str, Any]], opted_out: set[str]) -> list[Mapping[str, Any]]:
    return [t for t in targets if t.get("included") and t.get("status") != "opted_out" and t.get("buyer_key") not in opted_out]


EXPORT_FORMATS = ("csv", "mailchimp", "mailchimp-message")
CSV_BOM = chr(0xFEFF)  # a byte order mark, so Excel opens the file as UTF-8 and shows £ correctly
_MAILCHIMP_COLUMNS = (
    ("Email Address", None), ("Company", "buyer_name"), ("Greeting", "buyer_contact"), ("Contract", "contract_title"),
    ("Renewal Date", "renewal_date"), ("Incumbent", "incumbent"), ("Last Award Value", "last_award_value"),
    ("Scheme", "scheme_summary"), ("Approval Date", "approval_date"), ("Planning Authority", "planning_authority"),
    ("Engagement", "engagement_title"), ("Response Deadline", "response_deadline"), ("Category", "category"),
)


def mailchimp_message(body: str, sender: Mapping[str, str]) -> str:
    """The body with each per-recipient merge field as a Mailchimp tag (*|COMPANY|*). Fields that are
    the same on every message are written in, because the audience file has no column for them."""
    tags = {m["name"]: m["tag"] for m in MERGE_FIELDS if m["per_recipient"]}
    fixed = {"company_name": sender.get("company_name") or "", "sender_name": sender.get("sender_name") or ""}

    def swap(match: re.Match) -> str:
        name = match.group(1).lower()
        if name in tags:
            return f"*|{tags[name]}|*"
        if name in fixed:
            return fixed[name]
        return match.group(0)

    return _MERGE_TOKEN.sub(swap, body or "")


def build_export(targets: Iterable[Mapping[str, Any]], subject: str, body: str, sender: Mapping[str, str],
                 fmt: str, opted_out: set[str]) -> str:
    """The campaign as a spreadsheet: one row per included target that has not opted out, with the
    message personalised for that buyer (csv) or an audience for Mailchimp (mailchimp)."""
    if fmt not in EXPORT_FORMATS:
        raise ValidationError(f"format must be one of: {', '.join(EXPORT_FORMATS)}")
    if fmt == "mailchimp-message":
        return mailchimp_message(body, sender)
    rows = eligible_targets(targets, opted_out)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    if fmt == "csv":
        writer.writerow(["Buyer", "Signal", "Detail", "Date", "Contact email", "Subject", "Message", "Status", "Source link"])
        for t in rows:
            fields = fields_for_target(t, sender)
            snap = t.get("snapshot") or {}
            link = (snap.get("links") or [{}])[0].get("url") or ""
            writer.writerow([csv_cell(v) for v in (
                t.get("buyer_name"), SIGNAL_LABELS.get(t.get("signal_type"), t.get("signal_type")), snap.get("headline"),
                snap.get("date"), t.get("contact_email"), render_template(subject, fields)[0], render_template(body, fields)[0],
                t.get("status"), link)])
    else:
        writer.writerow([name for name, _ in _MAILCHIMP_COLUMNS] + ["Signal", "Source Link"])
        for t in rows:
            fields = fields_for_target(t, sender)
            snap = t.get("snapshot") or {}
            link = (snap.get("links") or [{}])[0].get("url") or ""
            writer.writerow([csv_cell(t.get("contact_email") if key is None else fields.get(key, "")) for _, key in _MAILCHIMP_COLUMNS]
                            + [csv_cell(SIGNAL_LABELS.get(t.get("signal_type"), "")), csv_cell(link)])
    return CSV_BOM + buffer.getvalue()


# ─────────────────────────────────────────── performance ──────────────────────────────────────────

_MONEY_NUMBER = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(bn|billion|m|million|k|thousand)?(?![a-z0-9])", re.IGNORECASE)
_MULTIPLIER = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}


def parse_money(text: Any) -> float | None:
    """Pounds in a free-text value such as "£1,250,000", "1.2m" or "GBP 450000"; None when there is no
    single GBP figure (a range, another currency, "TBC", zero, or a bare small number)."""
    s = str(text or "").strip()
    if not s or re.search(r"€|\beur\b|\busd\b|\$", s, re.IGNORECASE):
        return None
    found = list(_MONEY_NUMBER.finditer(s))
    if len(found) != 1:
        return None
    m = found[0]
    value = float((m.group(1) + (m.group(2) or "")).replace(",", ""))
    mult = (m.group(3) or "").lower()
    if mult:
        value *= _MULTIPLIER[mult]
    elif not re.search(r"£|gbp|pounds?", s, re.IGNORECASE) and value < 1000:
        return None
    return value if value > 0 else None


def pipeline_matches(targets: Iterable[Mapping[str, Any]], pipeline_rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Pipeline tenders the user began tracking after contacting that buyer. Each tender is credited to
    the first campaign that reached the buyer, so the total never counts one tender twice."""
    touches: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for t in targets:
        if t.get("sent_at") and t.get("buyer_key"):
            touches[t["buyer_key"]].append(t)
    out = []
    for p in pipeline_rows:
        key = mr.canonical_buyer_key(p.get("contracting_authority"))
        created = p.get("created_at")
        candidates = [t for t in touches.get(key, []) if created is None or t["sent_at"] <= created]
        if not candidates:
            continue
        first = min(candidates, key=lambda t: (t["sent_at"], t["campaign_id"]))
        out.append({
            "pipeline_id": p["id"],
            "campaign_id": first["campaign_id"],
            "buyer_key": key,
            "title": p.get("title") or "",
            "stage": p.get("stage") or "",
            "value": parse_money(p.get("estimated_value")),
        })
    return out


def performance_summary(
    campaigns: list[Mapping[str, Any]],
    targets: list[Mapping[str, Any]],
    matches: list[Mapping[str, Any]],
    days: int | None,
    today: date | None = None,
) -> dict[str, Any]:
    """Headline tiles for the window, the response funnel, and one row per campaign (all time)."""
    today = today or date.today()
    start = None if days is None else datetime.combine(today - timedelta(days=days), datetime.min.time())
    in_window = [t for t in targets if t.get("sent_at") and (start is None or t["sent_at"] >= start)]
    sent = len(in_window)
    replied = sum(1 for t in in_window if t.get("replied_at"))
    meetings = sum(1 for t in in_window if t.get("meeting_at"))
    window_ids = {t["campaign_id"] for t in in_window}
    window_matches = [m for m in matches if m["campaign_id"] in window_ids]
    valued = [m["value"] for m in window_matches if m["value"] is not None]

    rows = []
    for c in campaigns:
        mine = [t for t in targets if t["campaign_id"] == c["id"]]
        won = [m for m in matches if m["campaign_id"] == c["id"]]
        rows.append({
            "id": c["id"],
            "name": c["name"],
            "status": c["status"],
            "targets": sum(1 for t in mine if t.get("included")),
            "sent": sum(1 for t in mine if t.get("sent_at")),
            "replied": sum(1 for t in mine if t.get("replied_at")),
            "meetings": sum(1 for t in mine if t.get("meeting_at")),
            "tenders": len(won),
            "last_activity": c["last_activity_at"].isoformat() if c.get("last_activity_at") else None,
        })
    return {
        "days": days,
        "tiles": {
            "campaigns_run": len(window_ids),
            "buyers_reached": len({t["buyer_key"] for t in in_window}),
            "sent": sent,
            "replied": replied,
            "meetings": meetings,
            "reply_rate": round(replied / sent, 3) if sent else None,
            "tenders_tracked": len(window_matches),
            "pipeline_value": round(sum(valued)) if valued else None,
            "tenders_with_value": len(valued),
        },
        "funnel": [
            {"label": "Sent", "value": sent},
            {"label": "Replied", "value": replied},
            {"label": "Meeting", "value": meetings},
        ],
        "campaigns": rows,
    }
