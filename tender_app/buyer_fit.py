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


def band(score: float) -> str:
    return "strong" if score >= 75 else "fair" if score >= 50 else "weak"


def factors(*, cpv_score: float, prox_score: float, renew_score: float, openness_score: float, tier: int, cpv_match: bool,
            dist_km: float | None, days_until_renewal: int | None, next_renewal: str, repeat_display: str, total_contracts: int) -> list[dict[str, Any]]:
    if tier == 0:
        sector = "Add a company profile to score sector match (neutral until then)."
    elif tier == 1:
        sector = "Buys in the same CPV sectors as your won tenders." if cpv_match else "No overlap yet with the CPV sectors of your won tenders."
    else:
        sector = "Its name matches a sector keyword in your company profile." if cpv_score >= 80 else "No sector keyword from your profile appears in its name."
    if dist_km is None or dist_km >= 9999:
        prox = "Its location is not known, so distance is scored neutrally."
    else:
        prox = f"About {int(round(dist_km))} km from your chosen location."
    if days_until_renewal is not None and 0 <= days_until_renewal <= 180:
        renew = f"A contract is due to renew in {days_until_renewal} days ({next_renewal})."
    elif days_until_renewal is not None and 180 < days_until_renewal <= 365:
        renew = f"Next renewal is about {round(days_until_renewal / 30)} months away ({next_renewal})."
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
