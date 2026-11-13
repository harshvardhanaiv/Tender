"""Bid / no-bid advice and passed-stage warnings (Civenta fix plan M16, R9, G6, R10).

Before drafting, a professional bid writer tells the client whether to bid at all. This module:
- finds procurement stages whose date has passed (clarification deadline, expression-of-interest window, sample
  deadline, site visit) so the bid neither asks late questions nor ignores a closed gate (G6, R10);
- asks the model for a candid verdict (Bid / Bid with partner / No-bid) judged only from the company's own data,
  scores fit from the per-criterion ratings, and keeps the verdict consistent with the evidence (M16);
- turns the capability gaps into writer instructions, so a gap is stated plainly instead of being filled with an
  unrelated product (R9).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any, Callable

from tender_app.bid_writer import _as_pct
from tender_app.llm_json import parse_llm_json_checked

# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_DATE_NUM = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b")
_DATE_TXT = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def last_date(text: Any) -> _dt.date | None:
    """The latest date written in a string ("01/11/26 - 04/11/26" -> 4 Nov 2026), or None. UK day-first."""
    s = str(text or "")
    found = []
    for d, m, y in _DATE_NUM.findall(s):
        found.append((int(y) + (2000 if len(y) == 2 else 0), int(m), int(d)))
    for d, mon, y in _DATE_TXT.findall(s):
        if mon[:3].lower() in _MONTHS:
            found.append((int(y), _MONTHS[mon[:3].lower()], int(d)))
    for y, m, d in _DATE_ISO.findall(s):
        found.append((int(y), int(m), int(d)))
    dates = []
    for y, m, d in found:
        try:
            dates.append(_dt.date(y, m, d))
        except ValueError:
            continue
    return max(dates) if dates else None


# ---------------------------------------------------------------------------
# G6 / R10: stages that have already passed
# ---------------------------------------------------------------------------

_STAGE_KINDS = (
    ("clarification", r"clarif|question(?:s)? (?:deadline|cut)|q\s*&\s*a"),
    ("eoi", r"expression(?:s)? of interest|\beoi\b|register(?:ing|ed)? interest|opt[- ]in"),
    ("sample", r"\bsamples?\b"),
    ("site_visit", r"site visit|viewing|walk[- ]?round|open day|inspection"),
    ("briefing", r"briefing|bidder(?:s)? (?:day|event)|engagement event|webinar|supplier day"),
    ("selection", r"selection questionnaire|\bsq\b|\bpqq\b|pre-qualification"),
)
_SUBMISSION = re.compile(r"submission|tender deadline|return date|closing date|tender return", re.I)
# Dates that no longer apply, or that record something the buyer did.
_NOT_A_STAGE = re.compile(r"supersed|original|previous|replaced|extended|amended|\bissued\b|\bpublished\b|released|"
                          r"\bupdated?\b|\blog\b|notice", re.I)


def _detail(kind: str, event: str, day: str) -> str:
    if kind == "clarification":
        return f"The clarification deadline ({day}) has passed: new questions to the buyer will not be answered."
    if kind == "eoi":
        return (f"The expression-of-interest window closed on {day}. If you did not express interest by then, "
                f"check with the buyer that you can still bid.")
    if kind == "sample":
        return f"The sample deadline ({day}) has passed. Check whether samples were required from you."
    if kind in ("site_visit", "briefing"):
        return f"{event} took place on {day}. If attendance was mandatory and you did not attend, check you can still bid."
    if kind == "selection":
        return f"{event} closed on {day}. Check you passed this stage before writing the tender response."
    return f"{event} ({day}) has passed."


def stage_warnings(stage1: dict | None, today: _dt.date | None = None) -> list[dict]:
    """Bidder actions whose date has passed, each {"kind", "event", "date" (DD/MM/YYYY), "detail"}.
    The submission deadline itself is handled separately (tender_closed); award and contract dates are ignored
    unless the dissection marks them as something the bidder had to do."""
    today = today or _dt.date.today()
    s1 = stage1 or {}
    out: list[dict] = []
    seen: set = set()

    def add(kind: str, event: str, when: _dt.date | None) -> None:
        if when is None or when >= today or (kind, when) in seen:
            return
        seen.add((kind, when))
        day = when.strftime("%d/%m/%Y")
        out.append({"kind": kind, "event": event, "date": day, "detail": _detail(kind, event, day)})

    cd = s1.get("clarification_deadline")
    add("clarification", "Clarification deadline", last_date(cd.get("date") if isinstance(cd, dict) else cd))

    # When the notice is itself the expression-of-interest stage, its EOI date is the submission deadline.
    eoi_notice = bool(re.search(r"expression of interest|market engagement", str(s1.get("notice_type") or ""), re.I))
    for item in s1.get("key_dates") or []:
        if not isinstance(item, dict):
            continue
        event = str(item.get("event") or "").strip()
        if not event or _SUBMISSION.search(event) or _NOT_A_STAGE.search(event):
            continue
        action = item.get("bidder_action")
        if action is False or str(action).strip().lower() == "false":
            continue  # "Clarification log issued", "Notice published": the buyer's step, not the bidder's
        kind = next((k for k, pat in _STAGE_KINDS if re.search(pat, event, re.I)), "other")
        if (kind == "other" and not action) or (kind == "eoi" and eoi_notice):
            continue
        add(kind, event, last_date(item.get("date")))
    return out


def clarification_window_closed(warnings: list[dict]) -> bool:
    return any(w["kind"] == "clarification" for w in warnings)


def stage_note(warnings: list[dict]) -> str:
    """For the writers: do not ask the buyer late questions, do not claim a passed stage was done."""
    if not warnings:
        return ""
    lines = ["STAGES ALREADY PASSED (do not ask the buyer questions, and do not say the bidder took part in any of "
             "these unless the company data says so):"]
    lines += [f"- {w['event']}: {w['date']}" for w in warnings]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# M16: bid / no-bid
# ---------------------------------------------------------------------------

VERDICTS = ("Bid", "Bid with partner", "No-bid")
_RATING = {"strong": 1.0, "partial": 0.5, "none": 0.0}
_BRIEF_KEYS = ("tender_title", "contracting_authority", "notice_type", "contract_value", "scope_of_work",
               "evaluation_criteria", "mandatory_requirements", "certificates_required", "insurance_requirements",
               "financial_requirements", "references_required", "special_conditions")


def decision_prompt(stage1: dict | None, *, company: str, company_data: str, company_context: str,
                    answer_bank: str, warnings: list[dict], closed: bool) -> str:
    s1 = stage1 or {}
    brief = {k: s1.get(k) for k in _BRIEF_KEYS if s1.get(k)}
    passed = "\n".join(f"- {w['detail']}" for w in warnings) or "- none"
    return f"""Decide whether {company} should bid for this tender, as an experienced bid manager would advise a
client before any writing starts. Be candid: telling a company not to bid for work it cannot deliver is the most
valuable advice you can give.

TENDER (from the buyer's documents):
{json.dumps(brief, indent=1, default=str)[:14000]}

STAGES ALREADY PASSED:
{passed}
{"THE SUBMISSION DEADLINE HAS PASSED." if closed else ""}

COMPANY DATA (structured profile):
{(company_data or 'None')[:6000]}

COMPANY DOCUMENTS (extracts):
{(company_context or 'None')[:6000]}

ANSWER BANK (relevant entries):
{(answer_bank or 'None')[:5000]}

Rules:
- Judge capability only from the company data, documents and answer bank above. Products or services from a
  different line of work are not evidence (a data or software platform is not print equipment, care staff,
  building maintenance or catering).
- criteria: each scored criterion or question once, at the most detailed level the buyer gives, with its weight.
  Do not also list totals or parent groups (if Q1-Q6 carry the quality marks, do not add "Quality 70%").
- mandatory: pass/fail requirements about the bidder (eligibility, registrations, accreditations, insurance,
  financial thresholds). Documents the bidder has to complete are not requirements here.
- rating: "strong" = direct evidence of this kind of work; "partial" = related evidence or a policy only;
  "none" = nothing in the data.
- mandatory status: "met" only with evidence in the data; "not evidenced" when the data is silent; "not met" when
  the data shows the company cannot meet it (wrong line of work, below a threshold).
- capability_gaps: the specific things the tender needs that the company does not evidence, in plain words
  (e.g. "owns no print production equipment"). They will be written into the bid as gaps, never invented.

Return ONLY valid JSON with no markdown fences:
{{"verdict": "Bid | Bid with partner | No-bid",
  "summary": "2-3 plain sentences: the recommendation and the main reasons",
  "criteria": [{{"criterion": "the buyer's criterion name", "weight": "number or null", "rating": "strong | partial | none", "evidence": "what in the company data supports it, or 'none found'"}}],
  "mandatory": [{{"requirement": "short wording", "status": "met | not evidenced | not met", "note": "short"}}],
  "capability_gaps": ["each gap in plain words"],
  "partner_for": ["work a partner or subcontractor would have to cover, if bidding with a partner is the way in"]}}"""


def _weight(v: Any) -> float | None:
    p = _as_pct(v)
    return p if p is not None and p > 0 else None


def normalise_decision(raw: Any, warnings: list[dict], closed: bool) -> dict | None:
    """Clean the model's answer, score fit from the criteria ratings (weighted when weights are given) and keep the
    verdict consistent with the evidence: an unmet mandatory requirement or a very low fit is never a plain "Bid"."""
    if not isinstance(raw, dict):
        return None
    crit = []
    for c in raw.get("criteria") or []:
        if not isinstance(c, dict) or not str(c.get("criterion") or "").strip():
            continue
        rating = str(c.get("rating") or "").strip().lower()
        crit.append({"criterion": str(c["criterion"]).strip()[:160], "weight": _weight(c.get("weight")),
                     "rating": rating if rating in _RATING else "none",
                     "evidence": str(c.get("evidence") or "").strip()[:300]})
    mand = []
    for m in raw.get("mandatory") or []:
        if not isinstance(m, dict) or not str(m.get("requirement") or "").strip():
            continue
        st = str(m.get("status") or "").strip().lower()
        mand.append({"requirement": str(m["requirement"]).strip()[:200],
                     "status": st if st in ("met", "not evidenced", "not met") else "not evidenced",
                     "note": str(m.get("note") or "").strip()[:200]})
    gaps = [str(g).strip()[:200] for g in (raw.get("capability_gaps") or []) if str(g).strip()][:12]
    partner = [str(g).strip()[:200] for g in (raw.get("partner_for") or []) if str(g).strip()][:8]

    fit = None
    if crit:
        total = sum(c["weight"] or 0 for c in crit)
        if total > 0:
            fit = round(100 * sum(_RATING[c["rating"]] * (c["weight"] or 0) for c in crit) / total)
        else:
            fit = round(100 * sum(_RATING[c["rating"]] for c in crit) / len(crit))

    said = str(raw.get("verdict") or "").strip().lower()
    verdict = next((v for v in VERDICTS if v.lower() == said), None)
    if verdict is None:
        verdict = "Bid" if (fit or 0) >= 65 else "Bid with partner" if (fit or 0) >= 35 else "No-bid"
    reasons = []
    unmet = [m for m in mand if m["status"] == "not met"]
    if unmet and verdict == "Bid":
        verdict = "Bid with partner" if partner else "No-bid"
        reasons.append(f"{len(unmet)} mandatory requirement(s) not met")
    if fit is not None and fit < 35 and verdict == "Bid":
        verdict = "Bid with partner" if partner else "No-bid"
        reasons.append(f"fit is {fit}/100 on the evidence")
    return {
        "verdict": verdict,
        "fit_score": fit,
        "summary": str(raw.get("summary") or "").strip()[:700],
        "adjusted_because": reasons,
        "criteria": crit[:20],
        "mandatory": mand[:20],
        "capability_gaps": gaps,
        "partner_for": partner,
        "stage_blockers": [w["detail"] for w in warnings if w["kind"] in ("eoi", "selection", "site_visit", "briefing")],
        "tender_closed": bool(closed),
    }


def assess_bid(call: Callable[[str, str, int], str], prompt: str, warnings: list[dict], closed: bool) -> dict | None:
    """One AI call -> the normalised decision, or None if the call fails (drafting carries on without it)."""
    try:
        value, _ = parse_llm_json_checked(
            call("You are a candid UK public-sector bid manager. Return ONLY valid JSON.", prompt, 2500))
    except Exception:  # noqa: BLE001 - advice is optional; never block the draft on it
        return None
    return normalise_decision(value, warnings, closed)


def capability_note(decision: dict | None) -> str:
    """Writer instructions from the decision: what the company cannot evidence is said plainly (R9)."""
    if not decision:
        return ""
    lines = []
    if decision.get("capability_gaps"):
        lines.append("CAPABILITY GAPS (the company data does not evidence these):")
        lines += [f"- {g}" for g in decision["capability_gaps"]]
        lines.append("Where a question asks about one of these, say plainly that the bidder does not currently hold or "
                     "provide it, then either describe how it would be covered (a partner or subcontractor, written as "
                     "[CONFIRM: partner or subcontractor name and role]) or write [NEEDS INPUT: ...]. Never offer an "
                     "unrelated product or service of the bidder as if it met the requirement, and do not mention those "
                     "products at all: the buyer reads this, so no sentences like 'we will not present our software'. "
                     "State each gap once, in the answer it belongs to, not in every answer.")
    if decision.get("partner_for"):
        lines.append("WORK A PARTNER WOULD COVER: " + "; ".join(decision["partner_for"]))
    return "\n".join(lines) + ("\n" if lines else "")


def decision_issues(decision: dict | None, warnings: list[dict]) -> list[dict]:
    """Export-check items: a No-bid verdict, and each stage that has passed (R10)."""
    issues = []
    if decision and decision.get("verdict") == "No-bid":
        issues.append({"check": "bid_decision", "severity": "P1", "section": "Whole bid",
                       "detail": "The advice is not to bid. " + (decision.get("summary") or
                                 "The company data does not evidence the capability this tender needs.")})
    for w in warnings:
        issues.append({"check": "stage_passed", "severity": "P2" if w["kind"] == "clarification" else "P1",
                       "section": "Whole bid", "detail": w["detail"]})
    return issues
