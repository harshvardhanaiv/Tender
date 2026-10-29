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
    "45": ("construction", "building", "civil engineering", "groundworks", "refurbishment", "roofing", "paving", "driveway", "surfacing", "scaffold", "drainage", "demolition"),
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


def sector_score(share: float) -> float:
    """0 when the buyer buys nothing in your divisions; 13% of its awards is a fair fit, 30% or more a strong one."""
    return 0.0 if share <= 0 else min(100.0, 40.0 + share * 200.0)


def renewal_score(days: int | None) -> float:
    """Pre-market engagement happens months before a contract ends: best at about 4 to 9 months, nothing for a renewal that is days away."""
    if days is None or days < 60 or days > 365:
        return 30.0
    if 120 <= days <= 270:
        return 100.0
    return 70.0


def band(score: float) -> str:
    return "strong" if score >= 75 else "fair" if score >= 50 else "weak"


def day_phrase(days: int) -> str:
    return "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"


def factors(*, cpv_score: float, prox_score: float, renew_score: float, openness_score: float, tier: int, cpv_match: bool,
            dist_km: float | None, days_until_renewal: int | None, next_renewal: str, repeat_display: str, total_contracts: int,
            sector_note: str = "") -> list[dict[str, Any]]:
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
        renew = f"A contract is due to renew {day_phrase(days_until_renewal)} ({next_renewal}): time to engage before it is re-procured."
    elif days_until_renewal is not None and 180 < days_until_renewal <= 365:
        renew = f"Next renewal is about {round(days_until_renewal / 30)} months away ({next_renewal})."
    elif days_until_renewal is not None and 0 <= days_until_renewal < 60:
        renew = f"A contract ends {day_phrase(days_until_renewal)} ({next_renewal}): too close to act on, so it adds nothing to the score."
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
