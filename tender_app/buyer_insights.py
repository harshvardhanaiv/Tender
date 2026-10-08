"""Plain-language insight on one buyer's procurement pattern, for Market Radar's Insights drawer.

The model is never asked to analyse raw data. Everything numeric is computed here, in plain Python, from
the buyer's own Buyer Intelligence profile (tender_app/blueprints/buyers_bp.py) and its awards in the
Market Radar category. `build_facts` turns those into a short list of already-formatted statements, the
model only turns them into prose, and `numbers_are_grounded` rejects any reply that mentions a figure
that is not in the facts. A buyer with fewer than `min_awards` awards gets no model call at all: the
drawer says plainly that the data is too thin, which is the project's rule for every derived figure.
"""
from __future__ import annotations

import re
import statistics
from datetime import date, datetime
from typing import Any

from tender_app.market_radar import cpv_class


def money(v: float | None) -> str:
    """Same rounding as fmtMoney in web/buyer-workspace.js, so the prose matches the table."""
    if v is None:
        return "not published"
    a = abs(v)

    def trim(x: float, d: int) -> str:
        return f"{x:.{d}f}".rstrip("0").rstrip(".") if d and "." in f"{x:.{d}f}" else f"{x:.{d}f}"

    if a >= 9.995e8:
        return "£" + trim(v / 1e9, 1) + "bn"
    if a >= 9.995e5:
        return "£" + trim(v / 1e6, 0 if a >= 1e7 else 1) + "m"
    if a >= 1e4:
        return f"£{round(v / 1e3)}k"
    return f"£{round(v):,}"


def _date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _retender_gap_months(history: list[dict[str, Any]]) -> tuple[float, int] | None:
    """Median months between consecutive awards that share a CPV class, and how many gaps that rests on."""
    by_class: dict[str, list[date]] = {}
    for h in history:
        c, d = cpv_class(h.get("cpv_code")), _date(h.get("date_signed"))
        if c and d:
            by_class.setdefault(c, []).append(d)
    gaps = []
    for dates in by_class.values():
        dates = sorted(set(dates))
        gaps += [(b - a).days / 30.4375 for a, b in zip(dates, dates[1:]) if (b - a).days >= 30]
    if len(gaps) < 2:
        return None
    return statistics.median(gaps), len(gaps)


def build_facts(buyer: str, profile: dict[str, Any] | None, category_label: str | None, category_awards: list[dict[str, Any]],
                category_total: int) -> list[str]:
    """The statements the model may use, one per line, already rounded and formatted."""
    facts: list[str] = [f"Buyer: {buyer}."]
    if profile and profile.get("stats"):
        st = profile["stats"]
        n = int(st.get("total_contracts") or 0)
        facts.append(f"Authority type: {profile.get('buyer_type') or 'not stated'}.")
        facts.append(f"{n} contract awards on record between {str(st.get('earliest_award') or '')[:10]} and {str(st.get('latest_award') or '')[:10]}.")
        first, last = _date(st.get("earliest_award")), _date(st.get("latest_award"))
        if first and last and (last - first).days >= 365 and n:
            facts.append(f"That is about {n / ((last - first).days / 365.25):.1f} awards a year.")
        if st.get("total_spend"):
            facts.append(f"Total published value of non-framework awards: {money(float(st['total_spend']))}.")
        if st.get("avg_contract_value"):
            facts.append(f"Average non-framework award value: {money(float(st['avg_contract_value']))}.")
        if st.get("framework_appointments"):
            facts.append(f"{st['framework_appointments']} of the awards are framework or call-off appointments (their values are shared ceilings, not spend).")
        comp, direct = int(st.get("competitive_awards") or 0), int(st.get("direct_awards") or 0)
        if comp or direct:
            facts.append(f"{comp} awards were competitive and {direct} were direct awards.")
        if st.get("unique_suppliers"):
            facts.append(f"{st['unique_suppliers']} different suppliers have been awarded contracts.")
        if st.get("repeat_supplier_pct") is not None:
            facts.append(f"{st['repeat_supplier_pct']}% of awards went to a supplier that had already won from this buyer.")
        for s in (profile.get("top_suppliers") or [])[:3]:
            won = f"{s.get('contracts_won')} contracts"
            val = f", {money(float(s['total_value']))} published value" if s.get("total_value") else ""
            facts.append(f"Notable supplier: {s.get('supplier_name')} ({won}{val}).")
        for c in [c for c in (profile.get("cpv_breakdown") or []) if c.get("cpv_code") != "uncategorized"][:3]:
            facts.append(f"Frequent sector: {c.get('cpv_description')} ({c.get('count')} awards).")
        gap = _retender_gap_months(profile.get("contract_history") or [])
        if gap:
            facts.append(f"Where it bought the same kind of work (same CPV class) more than once, the median gap between awards was about {gap[0]:.0f} months, across {gap[1]} gaps.")
    if category_awards and category_label:
        values = [a["value"] for a in category_awards if a.get("value") and not a.get("value_is_ceiling")]
        routes: dict[str, int] = {}
        for a in category_awards:
            routes[a.get("route") or "Not stated"] = routes.get(a.get("route") or "Not stated", 0) + 1
        facts.append(f"In the category \"{category_label}\" it has {category_total} awards in the period looked at.")
        facts.append("Routes in that category: " + ", ".join(f"{k} {v}" for k, v in sorted(routes.items(), key=lambda kv: -kv[1])) + ".")
        if len(values) >= 3:
            facts.append(f"Median published value of its awards in that category: {money(statistics.median(values))}.")
    return facts


SYSTEM_PROMPT = (
    "You write a short factual summary of one UK public buyer's procurement pattern for a market research tool. "
    "Use ONLY the numbered facts you are given. Do not add any figure, date, supplier, claim or comparison that is not "
    "in the facts, do not estimate, and do not speculate about reasons or future behaviour. Quote amounts exactly as "
    "written in the facts. If a topic has no fact (for example how often it re-tenders), leave it out rather than guess. "
    "Write 3 to 5 plain sentences in one paragraph, no headings, bullets or markdown."
)


def build_prompt(facts: list[str]) -> str:
    return "Facts:\n" + "\n".join(f"{i}. {f}" for i, f in enumerate(facts, 1)) + "\n\nSummarise the buyer's procurement pattern."


# A figure with its unit ("£480k", "£1.4m", "50%", "18"), so "£2m" is not excused by a "2" elsewhere in the facts.
_NUM = re.compile(r"£?\d[\d,]*(?:\.\d+)?(?:bn|k|m|%)?(?![A-Za-z])")


def _numbers(text: str) -> set[str]:
    out = set()
    for m in _NUM.finditer(text):
        token = m.group(0).replace(",", "")
        out.add(re.sub(r"(\d)\.0+(?=\D|$)", r"\1", token))  # "2.0" and "2" are the same figure
    return out


def numbers_are_grounded(narrative: str, facts: list[str]) -> bool:
    """True when every figure in the narrative also appears, with its unit, in the facts (nothing invented or recomputed)."""
    return _numbers(narrative) <= _numbers("\n".join(facts))
