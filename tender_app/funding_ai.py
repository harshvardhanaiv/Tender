"""AI tools for one grant on the Funding page: eligibility check, summary, application draft and deep analysis.

This module holds everything that does not need Flask: the full grant text, the prompts and the cleaning of the
AI's answer. funding_bp.py loads the grant and the company profile, charges credits and stores the result.
The AI only sees the grant text and the company profile it is given and is told never to invent either."""
from __future__ import annotations

import re
from typing import Any

from tender_app import fag_import as fag
from tender_app import growth_studio as gs

KINDS = ("eligibility", "summary", "draft", "analysis", "questions", "budget", "evidence")
# Which tools need the user's company profile to say anything useful.
NEEDS_PROFILE = {"eligibility": True, "summary": False, "draft": True, "analysis": True, "questions": False, "budget": False, "evidence": False}
VERDICTS = ("eligible", "probably", "unclear", "unlikely", "not_eligible")
STATUSES = ("met", "unclear", "not_met")
RECOMMENDATIONS = ("go", "maybe", "no_go")
_TEXT_MAX = 14000
_LIST_MAX = 12
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")


def valid_label(label: Any) -> bool:
    return isinstance(label, str) and bool(_LABEL.match(label))


def full_text(label: str, fetch=None) -> tuple[str, bool]:
    """(the grant page text, whether the live page was read). Every rich-text field of the grant is flattened, so
    eligibility, supporting information and how to apply are all included whatever the service calls the tabs."""
    try:
        get = fetch or (lambda lab: fag._get(fag._session(), f"/grants/{lab}"))
        fields = ((get(label) or {}).get("grantDetail") or {}).get("fields") or {}
    except Exception:  # noqa: BLE001 - the stored record is still enough to work from
        return "", False
    parts: list[str] = []
    for key, value in fields.items():
        if isinstance(value, dict):
            text = fag.rich_text(value)
        elif isinstance(value, str) and key not in ("grantWebpageUrl",):
            text = value.strip()
        else:
            continue
        if text:
            parts.append(f"{re.sub(r'(?<!^)(?=[A-Z])', ' ', key).lower()}:\n{text}")
    return "\n\n".join(parts)[:_TEXT_MAX], bool(parts)


def grant_block(g: dict[str, Any], page_text: str = "") -> str:
    """The grant as plain text for a prompt."""
    def money(n):
        return f"£{n:,.0f}" if n is not None else "not stated"

    close = "open all year" if g.get("ongoing") else (g.get("close_date") or "no closing date stated")
    lines = [
        f"Name: {g.get('name') or ''}",
        f"Funder: {g.get('funder') or 'not stated'}",
        f"Award per project: {money(g.get('min_award'))} to {money(g.get('max_award'))}; total pot {money(g.get('total_award'))}",
        f"Opens: {g.get('open_date') or 'not stated'}; closes: {close}",
        f"Who can apply: {', '.join(g.get('applicants') or []) or 'not stated'}",
        f"Where: {', '.join(g.get('locations') or []) or 'not stated'}",
        f"Summary: {g.get('summary') or 'not stated'}",
        f"Eligibility (as stored): {g.get('eligibility') or 'not stated'}",
    ]
    if page_text:
        lines.append(f"FULL GRANT PAGE TEXT:\n{page_text}")
    return "\n".join(lines)


_SYSTEM = (
    "You are a senior UK grant-funding adviser. You work only from the GRANT text and the COMPANY text you are given. "
    "Never invent facts about the grant or the company, never invent dates, amounts, rules or evidence. When the text "
    "does not say, write 'Not stated' or mark it unclear. Plain English, short sentences. Reply with JSON only."
)


def build_prompt(kind: str, grant: str, company_name: str, company: str, answers: str = "") -> tuple[str, str]:
    """(system, user) for one tool."""
    head = f"GRANT:\n{grant}\n\nCOMPANY ({company_name or 'none chosen'}):\n{company or 'No company profile given.'}\n"
    if kind == "eligibility":
        task = (
            "Check whether this company can apply. Compare each eligibility condition of the grant with what the company text shows.\n"
            'Return JSON: {"verdict": "eligible|probably|unclear|unlikely|not_eligible", "headline": "one sentence", '
            '"criteria": [{"criterion": "the grant condition", "status": "met|unclear|not_met", "evidence": "what the company text shows, or what is missing"}], '
            '"missing": ["facts or documents needed to be sure"], "next_steps": ["what to do next"]}\n'
            "Use 'eligible' only when every stated condition is clearly met."
        )
    elif kind == "summary":
        task = (
            "Summarise this grant for a busy manager.\n"
            'Return JSON: {"overview": "2-3 sentences: what it pays for and why", "funds": ["what can be funded"], '
            '"who_can_apply": ["types of applicant and conditions"], "award": "size of award and any match funding", '
            '"key_dates": ["opening, closing and decision dates that are stated"], '
            '"deal_breakers": ["up to three things most likely to rule an applicant out"], "how_to_apply": ["steps"]}'
        )
    elif kind == "draft":
        task = (
            "Write a first draft of an application to this grant for this company, using only the company text and the approved "
            "answers. Where the company text gives no evidence for a claim, do not make one: write a bracketed prompt such as "
            "[add: number of staff] and list it in gaps.\n"
            'Return JSON: {"sections": [{"heading": "", "text": "the draft paragraph(s)", "gaps": ["what the company must add"]}], '
            '"gaps": ["every missing fact, in one list"]}\n'
            "Sections: Organisation background, Project description, Need and benefits, Outcomes and how they will be measured, "
            "Delivery plan and timescale, Budget and value for money, Match funding (only if the grant asks for it)."
        )
        head += f"\nAPPROVED ANSWERS FROM THE COMPANY'S ANSWER BANK:\n{answers or 'None.'}\n"
    elif kind == "analysis":
        task = (
            "Give a deep analysis of whether and how this company should apply.\n"
            'Return JSON: {"recommendation": "go|maybe|no_go", "reason": "2 sentences", '
            '"funder_priorities": ["what the funder clearly wants"], "strengths": ["where the company fits, with evidence"], '
            '"weaknesses": ["where it does not fit or evidence is missing"], '
            '"competition": "how contested this grant is likely to be, based only on the text (pot size, award size, who can apply)", '
            '"assessment_hints": ["what an assessor is likely to look for, based on the text"], '
            '"risks": ["deadline, match funding, state aid, reporting or delivery risks"], "next_steps": ["ordered actions"]}'
        )
    elif kind == "questions":
        task = (
            "List the questions an applicant should put to the funder before applying: things the grant text leaves unclear, "
            "missing or ambiguous (match funding, eligible and ineligible costs, state aid / subsidy control, deadlines and decision dates, "
            "who counts as eligible, reporting, payment in arrears, partnership rules). Do not ask what the text already answers.\n"
            'Return JSON: {"questions": [{"question": "ready to send", "topic": "match funding|eligible costs|state aid|deadlines|eligibility|process|other", '
            '"why": "what in the text is unclear or missing"}]}'
        )
    elif kind == "budget":
        task = (
            "Extract what the applicant needs to build the budget. State only what the grant text says.\n"
            'Return JSON: {"funding_rate_percent": a number 1-100 when the text states the share of project cost the grant pays, else null, '
            '"rate_note": "how the text states the rate or match requirement, or Not stated", "match_required": "cash or in-kind match rules, or Not stated", '
            '"eligible_costs": ["cost types the grant pays for"], "ineligible_costs": ["cost types it does not pay for"], '
            '"state_aid": "subsidy control or state aid points, or Not stated", "other_conditions": ["payment in arrears, cost caps, VAT, reporting"]}'
        )
    elif kind == "evidence":
        task = (
            "List the documents and evidence an applicant is likely to need to apply or to be paid, using only what the text says or "
            "clearly implies (for example accounts, insurance, policies, governance papers, certificates, bank details, quotes).\n"
            'Return JSON: {"items": [{"item": "document name, short", "why": "what the text says that needs it", "stage": "application|award|claim", '
            '"required": true when the text clearly requires it, false when it is only likely}]}'
        )
    else:
        raise ValueError(f"unknown tool: {kind}")
    return _SYSTEM, head + "\n" + task


def _text(value: Any, limit: int = 1200) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _texts(value: Any, limit: int = 400) -> list[str]:
    if not isinstance(value, list):
        return []
    return [t for t in (_text(v, limit) for v in value[:_LIST_MAX]) if t]


def clean(kind: str, raw: Any) -> dict[str, Any]:
    """The AI's answer cut down to the keys the page shows, with the fixed-choice fields forced to known values."""
    d = raw if isinstance(raw, dict) else {}
    if kind == "eligibility":
        verdict = d.get("verdict") if d.get("verdict") in VERDICTS else "unclear"
        criteria = []
        for c in (d.get("criteria") if isinstance(d.get("criteria"), list) else [])[:_LIST_MAX]:
            if isinstance(c, dict) and _text(c.get("criterion")):
                status = c.get("status") if c.get("status") in STATUSES else "unclear"
                criteria.append({"criterion": _text(c["criterion"], 400), "status": status, "evidence": _text(c.get("evidence"), 500)})
        return {"verdict": verdict, "headline": _text(d.get("headline"), 300), "criteria": criteria,
                "missing": _texts(d.get("missing")), "next_steps": _texts(d.get("next_steps"))}
    if kind == "summary":
        return {"overview": _text(d.get("overview"), 800), "funds": _texts(d.get("funds")), "who_can_apply": _texts(d.get("who_can_apply")),
                "award": _text(d.get("award"), 400), "key_dates": _texts(d.get("key_dates")),
                "deal_breakers": _texts(d.get("deal_breakers")), "how_to_apply": _texts(d.get("how_to_apply"))}
    if kind == "draft":
        sections = []
        for s in (d.get("sections") if isinstance(d.get("sections"), list) else [])[:10]:
            if isinstance(s, dict) and _text(s.get("heading")):
                sections.append({"heading": _text(s["heading"], 120), "text": _text(s.get("text"), 4000), "gaps": _texts(s.get("gaps"), 200)})
        return {"sections": sections, "gaps": _texts(d.get("gaps"), 200)}
    if kind == "analysis":
        rec = d.get("recommendation") if d.get("recommendation") in RECOMMENDATIONS else "maybe"
        return {"recommendation": rec, "reason": _text(d.get("reason"), 600), "funder_priorities": _texts(d.get("funder_priorities")),
                "strengths": _texts(d.get("strengths")), "weaknesses": _texts(d.get("weaknesses")), "competition": _text(d.get("competition"), 600),
                "assessment_hints": _texts(d.get("assessment_hints")), "risks": _texts(d.get("risks")), "next_steps": _texts(d.get("next_steps"))}
    if kind == "questions":
        out = []
        for q in (d.get("questions") if isinstance(d.get("questions"), list) else [])[:_LIST_MAX + 3]:
            if isinstance(q, dict) and _text(q.get("question")):
                out.append({"question": _text(q["question"], 400), "topic": _text(q.get("topic"), 40) or "other", "why": _text(q.get("why"), 400)})
        return {"questions": out}
    if kind == "budget":
        rate = d.get("funding_rate_percent")
        rate = float(rate) if isinstance(rate, (int, float)) and not isinstance(rate, bool) and 0 < rate <= 100 else None
        return {"funding_rate_percent": rate, "rate_note": _text(d.get("rate_note"), 500), "match_required": _text(d.get("match_required"), 500),
                "eligible_costs": _texts(d.get("eligible_costs"), 200), "ineligible_costs": _texts(d.get("ineligible_costs"), 200),
                "state_aid": _text(d.get("state_aid"), 600), "other_conditions": _texts(d.get("other_conditions"), 300)}
    if kind == "evidence":
        out = []
        for i in (d.get("items") if isinstance(d.get("items"), list) else [])[:_LIST_MAX + 6]:
            if isinstance(i, dict) and _text(i.get("item")):
                stage = i.get("stage") if i.get("stage") in ("application", "award", "claim") else "application"
                out.append({"item": _text(i["item"], 160), "why": _text(i.get("why"), 300), "stage": stage, "required": i.get("required") is True})
        return {"items": out}
    raise ValueError(f"unknown tool: {kind}")


def is_empty(kind: str, result: dict[str, Any]) -> bool:
    """An answer with nothing usable in it (the AI replied with unrelated JSON). Such an answer is not charged or saved."""
    if kind == "budget":
        return not any(result.get(k) for k in ("funding_rate_percent", "rate_note", "match_required", "eligible_costs", "ineligible_costs", "state_aid", "other_conditions"))
    key = {"eligibility": "criteria", "summary": "overview", "draft": "sections", "analysis": "reason", "questions": "questions", "evidence": "items"}[kind]
    return not result.get(key)


_DOC_WORDS = {"policy": "polici", "policies": "polici"}


def _doc_tokens(text: str) -> set[str]:
    return {gs.stem(_DOC_WORDS.get(w, w)) for w in gs._WORD.findall((text or "").lower()) if len(w) >= 4 and w not in gs.STOPWORDS and w not in ("document", "copy", "latest", "recent", "evidence", "proof", "statement", "certificate")}


def match_documents(items: list[dict[str, Any]], documents: list[dict[str, Any]], today=None) -> list[dict[str, Any]]:
    """Each evidence item with the user's own documents whose file name shares a meaningful word with it (a hint, not a proof)."""
    from datetime import date

    today = today or date.today()
    docs = [(d, _doc_tokens(d.get("filename") or "")) for d in documents]
    out = []
    for it in items:
        want = _doc_tokens(it.get("item") or "")
        found = []
        for d, have in docs:
            if want & have:
                expiry = (d.get("expiry_date") or "")[:10]
                try:
                    expired = bool(expiry) and date.fromisoformat(expiry) < today
                except ValueError:
                    expired = False
                found.append({"filename": d.get("filename") or "", "expiry_date": expiry or None, "expired": expired})
        out.append({**it, "documents": found[:3]})
    return out
