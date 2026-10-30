"""Plain-language reasons behind a buyer's match score, and an estimated chance of engagement.

The match score itself is computed in buyers_bp.opportunity_feed (35% sector, 25% proximity, 25% renewal, 15% openness).
This module turns the same four component scores into something a reader can see without opening anything:

  factors(...)      one entry per component: its weight, its 0-100 score, a band (strong / fair / weak) and one sentence.
  engagement(...)   an ESTIMATED likelihood that this buyer would engage with this supplier, from the same public award data
                    plus how many contracts the buyer lets. It is a heuristic ranking aid, never a prediction of winning,
                    and the wording says so.

Pure functions, no database, tested in tests/test_buyer_fit.py.
"""
from __future__ import annotations

import math

from typing import Any

WEIGHTS = {"sector": 0.35, "proximity": 0.25, "renewal": 0.25, "openness": 0.15}
LABELS = {"sector": "Sector match", "proximity": "Distance", "renewal": "Renewal timing", "openness": "Openness to new suppliers"}


# What a company sells, read from its profile text, as CPV divisions (the first two digits of a CPV code). The words are deliberately
# plain ones a profile uses; a buyer is then scored by how much of what it buys falls in those divisions, never by its name.
SECTOR_WORDS: dict[str, tuple[str, ...]] = {
    "72": ("software", "data", "analytics", "business intelligence", "machine learning", "artificial intelligence", "cyber", "cloud", "it services",
           "digital", "platform", "web", "app development", "systems integration", "managed service", "crm", "database"),
    "48": ("software", "analytics", "business intelligence", "platform", "application", "crm", "erp", "saas"),
    "79": ("consulting", "consultancy", "recruitment", "marketing", "research", "legal", "audit", "translation", "training provider", "print"),
    "45": ("construction", "building works", "building contractor", "builder", "civil engineering", "groundworks", "refurbishment", "roofing", "paving", "driveway", "surfacing", "scaffold", "drainage", "demolition"),
    "71": ("architect", "engineering consult", "surveying", "design services", "planning consult"),
    "50": ("repair", "maintenance", "servicing", "fleet"),
    "77": ("grounds", "landscap", "tree", "forestry", "horticult", "garden"),
    "90": ("waste", "recycling", "cleaning", "sewage", "environmental services"),
    "85": ("care", "social work", "health", "nursing", "domiciliary", "mental health", "residential care"),
    "80": ("education", "training", "school", "tuition", "apprentice"),
    "55": ("catering", "hospitality", "hotel", "food service"),
    "60": ("transport", "taxi", "bus", "logistics", "haulage", "courier"),
    "34": ("vehicle", "automotive"),
    "33": ("medical equipment", "pharmaceutical", "medical device"),
    "31": ("electrical", "lighting", "electrical equipment"),
    "09": ("energy", "electricity", "fuel", "solar"),
    "98": ("funeral", "leisure service"),
}
DIVISION_LABELS = {
    "72": "IT services", "48": "software packages", "79": "business services", "45": "construction", "71": "architecture and engineering",
    "50": "repair and maintenance", "77": "grounds and forestry", "90": "waste and cleaning", "85": "health and social care", "80": "education and training",
    "55": "catering and hospitality", "60": "transport", "34": "vehicles", "33": "medical equipment", "31": "electrical equipment", "09": "energy",
    "98": "other services", "30": "office equipment", "32": "telecoms equipment", "35": "security equipment", "38": "laboratory equipment",
    "39": "furniture", "42": "machinery", "44": "building materials", "51": "installation", "63": "transport support", "64": "postal and telecoms",
    "65": "utilities", "66": "financial services", "70": "property services", "73": "research", "75": "public administration", "76": "oil and gas",
    "92": "leisure and culture", "15": "food", "18": "clothing", "22": "printed matter", "03": "agriculture",
}


def division_label(code: str) -> str:
    return DIVISION_LABELS.get(code, "CPV division " + code)


def infer_divisions(text: str) -> list[str]:
    """CPV divisions a company's profile text points to, most specific first. Empty when the text names nothing recognisable."""
    low = " " + " ".join((text or "").lower().replace("/", " ").replace("-", " ").split()) + " "
    scored = []
    for div, words in SECTOR_WORDS.items():
        hits = sum(1 for w in words if (" " + w + " " in low if len(w) <= 4 else (" " + w) in low))
        if hits:
            scored.append((hits, div))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [d for _h, d in scored][:6]


def sector_share(shares: dict[str, int] | None, divisions: list[str]) -> tuple[float, int, int]:
    """(share of the buyer's awards in these divisions, awards in them, all its awards) from {division: awards}."""
    if not shares:
        return 0.0, 0, 0
    total = sum(int(v) for v in shares.values()) or 0
    mine = sum(int(shares.get(d, 0)) for d in divisions)
    return (mine / total if total else 0.0), mine, total


def sector_score(share: float, awards: int | None = None) -> float:
    """0 when the buyer buys nothing in your divisions; 13% of its awards is a fair fit, 30% or more a strong one.
    With `awards` (how many of its contracts are in your divisions) a buyer with a handful of matching contracts scores below one with
    hundreds, so 25 buyers with a high share no longer all score alike (re-test 2 B1)."""
    base = 0.0 if share <= 0 else min(100.0, 40.0 + share * 200.0)
    if awards is None or base == 0:
        return base
    depth = min(1.0, math.log10(1 + max(0, awards)) / 2.5)   # 1 contract ~ 0.12, 10 ~ 0.42, 100 and over ~ 0.8 to 1
    return base * (0.5 + 0.5 * depth)


def renewal_score(days: int | None) -> float:
    """Pre-market engagement happens months before a contract ends: best at about 4 to 9 months, nothing for a renewal that is days away."""
    if days is None or days < 60 or days > 365:
        return 30.0
    if 120 <= days <= 270:
        return 100.0
    return 70.0


def sector_score_by_spend(spend_share: float, awards: int | None = None) -> float:
    """Sector fit from the share of the buyer's SPEND that is in your divisions (re-test 3 B1: scoring on award counts left the top 50
    within five points). The square root keeps a small share visible: 2% scores 20, 10% scores 45, 25% scores 71, 50% or more 100.
    Fewer than ten matching contracts count for less, as in sector_score."""
    if spend_share <= 0:
        return 0.0
    base = min(100.0, 100.0 * math.sqrt(spend_share / 0.5))
    if awards is None:
        return base
    depth = min(1.0, math.log10(1 + max(0, awards)) / 2.5)
    return base * (0.5 + 0.5 * depth)


def spend_share(spend: dict[str, Any] | None, divisions: list[str]) -> tuple[float, float, float]:
    """(share of the buyer's spend in these divisions, spend in them, all its spend) from {division: GBP}. (0, 0, 0) when unknown or nil."""
    if not spend:
        return 0.0, 0.0, 0.0
    total = sum(max(0.0, float(v or 0)) for v in spend.values())
    mine = sum(max(0.0, float(spend.get(d) or 0)) for d in divisions)
    return (mine / total if total else 0.0), mine, total


def gbp_short(amount: float) -> str:
    amount = float(amount or 0)
    if amount >= 1e9:
        return f"£{amount / 1e9:.1f}bn"
    if amount >= 1e6:
        return f"£{amount / 1e6:.1f}m"
    if amount >= 1e3:
        return f"£{amount / 1e3:.0f}k"
    return f"£{amount:.0f}"


def value_weight(value: float | None) -> float:
    """0.55 to 1.0 from the renewing contract's value: an unstated value counts as a modest one, a £1m contract is worth chasing,
    £10m or more is the top."""
    if not value or value <= 0:
        return 0.6
    return round(min(1.0, 0.55 + 0.45 * max(0.0, math.log10(value) - 4.0) / 3.0), 3)   # £10k 0.55, £100k 0.70, £1m 0.85, £10m 1.0


def renewal_points(days: int | None, value: float | None = None, fit: float = 1.0) -> float:
    """Renewal timing for a contract IN YOUR SERVICES: how well the date suits an approach, times what the contract is worth, times how
    well it fits (1.0 when its CPV division is one you sell in). Nothing (0) when there is no such renewal 60 to 365 days away: a renewal
    of something you do not sell, or one that is too close to act on, earns no points (re-test 3 B2)."""
    if days is None or days < 60 or days > 365:
        return 0.0
    if days < 120:
        timing = 55.0 + (days - 60) * 0.75          # 55 at 60 days to 100 at 120
    elif days <= 270:
        timing = 100.0
    else:
        timing = 100.0 - (days - 270) * 0.5          # 100 at 270 days to about 52 at 365
    return round(timing * value_weight(value) * max(0.0, min(1.0, fit)), 1)


def band(score: float) -> str:
    return "strong" if score >= 75 else "fair" if score >= 50 else "weak"


def day_phrase(days: int) -> str:
    return "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"


def factors(*, cpv_score: float, prox_score: float, renew_score: float, openness_score: float, tier: int, cpv_match: bool,
            dist_km: float | None, days_until_renewal: int | None, next_renewal: str, repeat_display: str, total_contracts: int,
            sector_note: str = "", renewal_contract: str = "", renewal_scoped: bool = False) -> list[dict[str, Any]]:
    # "WFD Systems Improvement Project (IT services) ends 31 Mar 2027": the contract is named so the user can judge it (re-test 2 B2)
    named = f"{renewal_contract} " if renewal_contract else "A contract "
    if sector_note:
        sector = sector_note
    elif tier == 0:
        sector = "Add a company profile to score sector match (neutral until then)."
    elif tier == 1:
        sector = "Buys in the same CPV sectors as your won tenders." if cpv_match else "No overlap yet with the CPV sectors of your won tenders."
    else:
        sector = "Its name matches a sector keyword in your company profile." if cpv_score >= 80 else "No sector keyword from your profile appears in its name."
    if dist_km is None or dist_km >= 9999:
        prox = "Its location is not known, so distance is scored neutrally."
    else:
        prox = f"About {int(round(dist_km * 0.621371))} miles from your chosen location."
    if days_until_renewal is not None and 60 <= days_until_renewal <= 180:
        renew = f"{named}is due to renew {day_phrase(days_until_renewal)} ({next_renewal}): time to engage before it is re-procured."
    elif days_until_renewal is not None and 180 < days_until_renewal <= 365:
        renew = f"{named}is due to renew in about {round(days_until_renewal / 30)} months ({next_renewal})."
    elif days_until_renewal is not None and 0 <= days_until_renewal < 60:
        renew = f"{named}ends {day_phrase(days_until_renewal)} ({next_renewal}): too close to act on, so it adds nothing to the score."
    elif renewal_scoped:
        renew = "No renewals in your services in the next 12 months."
    else:
        renew = "No renewal is scheduled in the next year."
    if total_contracts >= 5:
        opened = f"Re-awards to the same supplier {repeat_display} of the time, across {total_contracts} contracts."
    else:
        opened = "Too few published contracts to judge openness (scored neutrally)."
    scores = {"sector": cpv_score, "proximity": prox_score, "renewal": renew_score, "openness": openness_score}
    notes = {"sector": sector, "proximity": prox, "renewal": renew, "openness": opened}
    return [{"key": k, "label": LABELS[k], "weight": int(WEIGHTS[k] * 100), "score": int(round(scores[k])), "band": band(scores[k]), "note": notes[k]}
            for k in ("sector", "proximity", "renewal", "openness")]


def engagement(*, cpv_score: float, prox_score: float, renew_score: float, openness_score: float, total_contracts: int) -> dict[str, Any]:
    """Estimated likelihood of engagement: sector and openness count most, renewal timing and distance next, and a buyer
    that lets many contracts has more chances to meet you than one that lets two."""
    volume = min(100.0, total_contracts * 4.0)
    pct = round(0.30 * cpv_score + 0.25 * openness_score + 0.20 * renew_score + 0.15 * prox_score + 0.10 * volume)
    pct = max(5, min(95, pct))
    level = "high" if pct >= 70 else "medium" if pct >= 50 else "low"
    return {"percent": pct, "level": level,
            "note": "Estimated from public award data (sector, openness, renewal timing, distance and how many contracts the buyer lets). "
                    "It ranks buyers for you; it does not predict that you would win."}
