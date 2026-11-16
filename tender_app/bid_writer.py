"""Bid writing helpers for /api/proposal (Matthewsgreen fix plan, phase 1).

What lives here, and why it is separate from server.py:
- the buyer's questions become the bid skeleton, each with a word budget from its weighting (M5);
- one AI call per question, and a cut-off answer is never accepted silently (M6);
- grounding rules shared by every writer, plus extra claim checks (M7);
- identity fields from the company profile are validated before they reach a prompt (M7);
- Answer Bank entries are kept only when they relate to this tender (M11, light version);
- the QA gate run before export (M10).

Everything is pure Python with the AI call passed in, so it is tested without a server or a model.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from tender_app.llm_json import parse_llm_json_checked
from tender_app.bid_release4 import (limit_from_text as _limit_from_text,
                                     find_ruling_conflicts as _find_ruling_conflicts)

NEEDS_COMPANY = "[NEEDS INPUT: legal company name]"

GROUNDING_RULES = """GROUNDING RULES (these override any other instruction):
1. Every statement about the bidder (experience, clients, case studies, metrics, certifications, staff numbers, turnover, wages, insurances, investment amounts, facilities, sector history) must come from the COMPANY DATA, ANSWER BANK or COMPANY DOCUMENTS given here. If it is not there, write [NEEDS INPUT: <exactly what the bidder must supply>] instead. Never invent a client, project, figure or capability.
2. A policy document is not a certificate. Write "certified" or "accredited" only when a certificate (issuing body or number) is given; otherwise say the bidder has a policy, or write [NEEDS INPUT: certificate details].
3. Use Answer Bank entries and case studies only when they come from the same kind of work as this tender. Ignore entries about a different sector (for example banking or software examples in a building-operator or care tender).
4. Commitments ("we will ...") are allowed, but every number in them (money, percentages, counts, dates, hours) must come from the data given or the buyer's documents; otherwise write [NEEDS INPUT: figure].
5. Never state that declarations, exclusion grounds, conflicts of interest or eligibility are met. Write each one as [CONFIRM: <statement the bidder must tick>].
6. Statements about the buyer, the site or the contract must come from the tender documents or the published clarification answers. Use the buyer's names and spellings exactly as they appear in the latest documents.
7. Where the buyer has published a clarification answer, use it in the bid; never ask the buyer that question again.
8. Past winning bids show structure and tone only. Never copy their figures, partner names, dates or commitments.
9. Plain UK English, active voice, short paragraphs. No empty superlatives (world-class, cutting-edge, unique, robust, best-in-class).
10. Never say the bidder has already done something - raised or submitted clarification questions, visited the site, attended a briefing, registered on the portal, met or spoken to the buyer, submitted a document - unless the COMPANY DATA says so. Write it as a next step ("we will raise this as a clarification question") or as [CONFIRM: <the action, for the bidder to confirm>].
11. If the bidder lacks something the tender needs (equipment, staff, a licence, sector experience), say so plainly and write [NEEDS INPUT] or [CONFIRM: partner or subcontractor]. Never present an unrelated product or service of the bidder (for example a software or data platform) as meeting that need.
12. Never state a financial outcome (break-even, surplus, profit, "self-sustaining by Year N", "covers its costs") unless the figures given here show it. Describe how the plan works, give the buyer's own figures, and leave the bidder's numbers as [NEEDS INPUT: ...]; the bidder's financial model decides the outcome.
13. The buyer reads every word: never write about these rules or about what the bid does not claim ("we do not assert a break-even point", "we will not present our software"). Just leave the claim out.
14. The bid must agree with itself. If the capability note or any part of the bid says the bidder does not currently hold something (24/7 cover for this contract, insurance at the required level, an accreditation, a helpdesk), no other sentence may say the bidder has it. State the narrower true fact precisely instead ("we offer 24/7 emergency call-outs to our domestic customers") and mark the gap [NEEDS INPUT] or [CONFIRM].
15. Percentages, values and dates the buyer published (evaluation weightings, contract value, deadlines) are the buyer's facts: write them plainly, never inside a placeholder.
16. A date that has already passed is never written as something the bidder will still do ("we will send samples by 30 October" after that date). Say it has passed and write [CONFIRM: new date agreed with the buyer].
17. Years of experience: the COMPANY DATA may quote the experience of its people. If the company itself is younger than the years claimed, write it as the team's experience ("our team has over 12 years' experience"), not the company's.
18. Never mention how this document was produced: no "draft", "AI", "generated" or tool names in any text the buyer will read."""

ANSWER_STANDARD = """ANSWER STANDARD:
- The first sentence answers the question directly, in the buyer's words. No preamble ("We are delighted...").
- Turn the question, its sub-parts and any scoring guidance into a checklist and cover every item, in the buyer's order, under short sub-headings that mirror the question.
- Be specific to this buyer and site: use facts from the tender documents and clarification answers. If a sentence could be pasted into any other bid unchanged, cut it.
- Commitments say what, by when, who owns it and how it is measured.
- State assumptions, risks with mitigations and conditions explicitly. Use simple Markdown tables (| a | b |) where a timetable, price list or plan reads better as a table.
- If the buyer states a budget, price ceiling or a split by phase or lot (contract_value in the dissection), the costs stay inside it and the answer says so with the buyer's own figures; never quote a total or phase price above a stated envelope.
- Do not repeat content that belongs to another question; refer to that question instead.
- Follow the buyer's format_requirements in the dissection: its headings, page and word limits, and where case studies go. If case studies belong in an appendix, give a one-line summary here and write "See Appendix: Case Study <n>" instead of the full case study.
- If the buyer supplies a response template, answer inside its structure and wording; do not reformat or rename its sections.
- Finish every sentence. If you are running short of space, end the answer cleanly rather than stopping mid-sentence."""

# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    m = re.search(r"\d[\d,]*", str(value))
    if not m:
        return None
    n = int(m.group(0).replace(",", ""))
    return n if n > 0 else None


def _as_pct(value: Any) -> float | None:
    """'40%' / '40' / 40 / '40 per cent' -> 40.0; 0.4 -> 40.0; None when no number is given."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
    else:
        m = re.search(r"\d+(?:\.\d+)?", str(value))
        if not m:
            return None
        v = float(m.group(0))
    if 0 < v < 1 and not (isinstance(value, str) and "%" in value):
        v *= 100
    return v if 0 <= v <= 100 else None


def _parent_of(number: str) -> str:
    """'5.II' -> '5', 'Q4.I' -> 'Q4', '3a' -> '3', 'Q7' -> 'Q7'; a titled sub-question names its group before the
    dash: 'Proposed team - makeup' -> 'Proposed team' (EEF's RFP, where each of 5 sub-questions repeated the
    group's 40% and so claimed 2,000 words of its own)."""
    n = number.strip()
    m = re.match(r"^([A-Za-z]{0,3}\s*\d+)", n)
    if m:
        return m.group(1).replace(" ", "")
    t = re.match(r"^(.{3,60}?)\s+[-–—:]\s+\S", n)
    return t.group(1).strip().lower() if t else n


# ---------------------------------------------------------------------------
# M5: questions are the skeleton
# ---------------------------------------------------------------------------

TOTAL_WORD_BUDGET = 6000
MIN_BUDGET, MAX_BUDGET = 250, 2000


def extract_questions(stage1: dict | None) -> list[dict]:
    """The buyer's questions from the Stage 1 dissection, in order, each with a word budget.

    Budget: an explicit word limit is a hard cap; otherwise the share of TOTAL_WORD_BUDGET that the
    weighting gives (sub-parts sharing a parent's weighting split it), clamped to 250-2000 words; with
    no weightings at all, an even split.
    """
    raw = (stage1 or {}).get("itt_questions") or []
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for i, q in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(q, dict):
            continue
        text = str(q.get("question_text") or q.get("question") or "").strip()
        if not text:
            continue
        number = str(q.get("number") or q.get("question_number") or f"Q{i + 1}").strip() or f"Q{i + 1}"
        key = (number.lower(), text[:120].lower())
        if key in seen:
            continue
        seen.add(key)
        weight_raw = q.get("weighting")
        out.append({
            "number": number,
            "text": text,
            "weighting": "" if weight_raw is None else str(weight_raw),
            "weight_pct": _as_pct(weight_raw),
            "word_limit": _as_int(q.get("word_limit")) or _limit_from_text(text),
            "page_limit": _as_int(q.get("page_limit")),
            "mandatory": bool(q.get("mandatory", True)),
        })

    # Sub-parts that repeat their parent's weighting share it instead of each claiming all of it.
    groups: dict[tuple[str, float], list[dict]] = {}
    for q in out:
        if q["weight_pct"] is not None:
            groups.setdefault((_parent_of(q["number"]), q["weight_pct"]), []).append(q)
    for (_, pct), members in groups.items():
        if len(members) > 1 and pct > 0:
            for q in members:
                q["weight_share"] = pct / len(members)

    any_weight = any(q["weight_pct"] for q in out)
    even = TOTAL_WORD_BUDGET / max(1, len(out))
    for q in out:
        if q["word_limit"]:
            q["budget_words"] = q["word_limit"]
        elif any_weight and q["weight_pct"] is not None:
            share = q.get("weight_share", q["weight_pct"])
            q["budget_words"] = int(min(MAX_BUDGET, max(MIN_BUDGET, round(TOTAL_WORD_BUDGET * share / 100))))
        else:
            q["budget_words"] = int(min(900, max(300, round(even))))
        q["scored"] = not (q["weight_pct"] == 0)
    return out


def questionnaire_mode(questions: list[dict]) -> bool:
    """Two or more buyer questions: the bid is the answers, not the house template's generic sections."""
    return len(questions) >= 2


def max_tokens_for(budget_words: int) -> int:
    return int(min(7000, max(900, budget_words * 1.7 + 500)))


# ---------------------------------------------------------------------------
# M6: one question per call, never truncated
# ---------------------------------------------------------------------------

_DANGLING = {
    "the", "a", "an", "and", "or", "of", "to", "with", "for", "in", "on", "by", "at", "from", "our", "we",
    "will", "is", "are", "be", "that", "which", "as", "their", "its", "this", "these", "your", "including",
    "such", "via", "into", "per", "than", "but", "if", "so", "also", "each", "all", "any", "has", "have",
}
_LIST_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)]|\|)")


def ends_cleanly(text: str) -> bool:
    """False when an answer looks cut off: it stops mid-sentence or on a dangling word."""
    t = (text or "").rstrip()
    if not t:
        return False
    if t.count("|") and t.splitlines()[-1].strip().startswith("|") and not t.endswith("|"):
        return False  # a table row that stops half way
    last_word = re.findall(r"[A-Za-z']+", t[-40:])
    if last_word and last_word[-1].lower() in _DANGLING and t[-1].isalpha():
        return False
    if t[-1] in ".!?)]\"'”’:|*`":
        return True
    # A letter ends with a signature block (name, company, email, phone), not a full stop.
    if re.search(r"\b(yours (faithfully|sincerely|truly)|kind regards|best regards|with regards)\b", t[-500:], re.I):
        return True
    last_line = t.splitlines()[-1]
    # A contact block (name, company, address, phone) ends on a postcode or a phone number, not a full stop.
    if re.search(r"\b[A-Za-z]{1,2}\d[A-Za-z\d]?\s*\d[A-Za-z]{2}\s*$", last_line) or re.search(r"\+?\d[\d\s()-]{8,}\s*$", last_line):
        return True
    # A bullet or heading may end without a full stop; a prose paragraph may not.
    return bool(_LIST_LINE.match(last_line) or last_line.lstrip().startswith("#"))


def question_prompt(q: dict, *, title: str, authority: str, company: str, tender_dissection: str,
                    clarifications_block: str, company_data: str, company_context: str, answer_bank: str,
                    winning_bids: str, other_questions: list[dict], closed_note: str = "",
                    lang_instruction: str = "") -> str:
    others = "\n".join(f"- {o['number']}: {o['text'][:140]}" for o in other_questions if o is not q)[:3000]
    budget = int(q.get("budget_words") or 500)
    limit_line = (f"HARD WORD LIMIT: {q['word_limit']} words (the buyer stops reading after it)."
                  if q.get("word_limit") else
                  f"LENGTH: at most {budget} words - this question's share of the bid by its weighting. "
                  f"Aim for {int(budget * 0.85)}-{budget} words; do not go over. Tables count, and each "
                  f"[NEEDS INPUT]/[CONFIRM] tag counts as one word."
                  if q.get("weight_pct") is not None else
                  f"LENGTH: at most {budget} words. This question is not weighted: answer it completely, and "
                  f"keep factual answers short. Each [NEEDS INPUT]/[CONFIRM] tag counts as one word.")
    weight_line = f"Weighting: {q['weighting']}" if q.get("weighting") else "Weighting: not stated"
    return f"""Write the answer to ONE question in a tender response for {company}.

TENDER: {title}
BUYER: {authority}
{closed_note}
QUESTION {q['number']}:
{q['text']}
{weight_line}
{limit_line}

OTHER QUESTIONS IN THIS QUESTIONNAIRE (answered separately; do not answer them here, cross-refer instead):
{others or '- none'}

PUBLISHED CLARIFICATION ANSWERS FROM THE BUYER:
{clarifications_block or 'None found in the documents provided.'}

TENDER DISSECTION (from the buyer's documents):
{tender_dissection}

COMPANY DATA (structured profile):
{company_data}

COMPANY DOCUMENTS (extracts):
{company_context or 'None'}

ANSWER BANK (relevant entries only):
{answer_bank or 'None'}
{winning_bids}

{GROUNDING_RULES}

{ANSWER_STANDARD}

Return ONLY valid JSON with no markdown fences:
{{"response": "the full answer, Markdown allowed", "placeholders_used": ["every [NEEDS INPUT: ...] or [CONFIRM: ...] tag you used"]}}
{lang_instruction}"""


OVER_BUDGET_TOLERANCE = 1.10  # an answer may run 10% over its weighting budget before it is condensed
_FIGURE_RE = re.compile(r"£\s?\d[\d,]*(?:\.\d+)?\s*(?:k|m|bn|million|billion)?|\d+(?:\.\d+)?\s?%", re.I)


def answer_words(text: str) -> int:
    """Words in an answer as the buyer will count them once it is filled in: each [NEEDS INPUT: ...] or
    [CONFIRM: ...] tag stands for one figure or name, so it counts as one word, not the words of its hint."""
    return len(PLACEHOLDER_RE.sub("X", text or "").split())


def word_cap(q: dict) -> int:
    """The most words an answer may have: the buyer's limit, else the weighting budget plus the tolerance."""
    limit = _as_int(q.get("word_limit"))
    if limit:
        return limit
    return int(int(q.get("budget_words") or 500) * OVER_BUDGET_TOLERANCE)


def _figures(text: str) -> set[str]:
    return {re.sub(r"[\s,]", "", m.group(0)).lower() for m in _FIGURE_RE.finditer(PLACEHOLDER_RE.sub(" ", text or ""))}


def trim_to_words(text: str, cap: int) -> str:
    """Last resort for a buyer's HARD word limit: drop whole trailing blocks, then trailing sentences, until the
    answer fits. Each [NEEDS INPUT]/[CONFIRM] tag counts as one word, as the buyer will count it. Release 5 left
    Cornwall College D1.1 at 645 words and D1.2 at 600 against a 500-word limit."""
    text = (text or "").strip()
    if answer_words(text) <= cap:
        return text
    nl = chr(10)
    blocks = re.split(nl + r"\s*" + nl, text)
    while len(blocks) > 1 and answer_words((nl + nl).join(blocks)) > cap:
        blocks.pop()
    out = (nl + nl).join(blocks).strip()
    if answer_words(out) > cap:
        sentences = re.split(r"(?<=[.!?])\s+", out)
        while len(sentences) > 1 and answer_words(" ".join(sentences)) > cap:
            sentences.pop()
        out = " ".join(sentences).strip()
    if answer_words(out) > cap:  # one enormous sentence: cut at the cap and close it
        out = " ".join(out.split()[:cap]).rstrip(",;:- ") + "."
    return out


def condense_prompt(q: dict, answer: str, words: int, factor: float = 0.85) -> str:
    limit = _as_int(q.get("word_limit"))
    # Aim well under the cap: asked to trim a small overrun, models hand back nearly the same text.
    target = int((limit or int(q.get("budget_words") or 500)) * factor)
    pct = max(40, min(95, round(100 * target / max(1, words))))
    return f"""This tender answer is {words} words; the limit for it is {target} words. Cut it to about {pct}% of its
current length by deleting whole sentences, bullets and table rows - rewording alone will not get it short enough.

QUESTION {q['number']}:
{q['text']}

Rules:
- Delete in this order: restated question text, introductions and summaries that repeat the body, generic claims
  ("we are committed to..."), duplicated points, then the least important detail.
- Keep every fact, commitment and figure that answers the question.
- Keep every [NEEDS INPUT: ...] and [CONFIRM: ...] tag exactly as written. Never replace a tag with a figure, name or date.
- Add nothing new: no new facts, figures, clients, certifications or examples.
- Keep the headings and the order of the points; tables may be merged or trimmed. Finish every sentence.

ANSWER:
{answer}

Return ONLY valid JSON with no markdown fences:
{{"response": "the shortened answer, Markdown allowed"}}"""


def write_question(call: Callable[[str, str, int], str], q: dict, prompt: str,
                   system: str = "You are a professional UK public-sector bid writer. Return ONLY valid JSON.") -> dict:
    """Write one answer. `call(system, user, max_tokens)` returns the model's raw text.

    A reply that was cut off (JSON needed repairing, or the text stops mid-sentence) is retried once with
    more room and a shorter target; if it is still cut off the answer is kept but marked, with a visible
    [NEEDS INPUT] tag, so the QA gate blocks a clean export instead of the gap going unnoticed.

    A complete answer over its cap (the buyer's word limit, else the weighting budget + 10%) gets one
    condensing call. The shorter version is used only if it is complete, shorter, and adds no new £ or %
    figure; otherwise the long answer stays and the QA gate reports the overrun.
    """
    budget = int(q.get("budget_words") or 500)
    attempts = [
        (prompt, max_tokens_for(budget)),
        (prompt + f"\n\nIMPORTANT: your previous answer was cut off. Write a complete answer of at most "
                  f"{max(150, int(budget * 0.75))} words and finish every sentence.",
         min(8000, int(max_tokens_for(budget) * 1.5))),
    ]
    response, placeholders, status, error = "", [], "failed", ""
    for user, max_tokens in attempts:
        try:
            raw = call(system, user, max_tokens)
            value, truncated = parse_llm_json_checked(raw)
        except Exception as exc:  # noqa: BLE001 - a failed call is retried, then reported
            error = str(exc)[:200]
            continue
        if isinstance(value, dict):
            text = str(value.get("response") or value.get("answer") or "").strip()
            phs = value.get("placeholders_used") or []
        else:
            text, phs = "", []
        if text and len(text) >= len(response):
            response, placeholders = text, [str(p) for p in phs if p] if isinstance(phs, list) else []
        if text and not truncated and ends_cleanly(text):
            status = "ok"
            break
        status = "truncated" if text else status
    if status == "truncated":
        response = (response + f"\n\n[NEEDS INPUT: this answer was cut off - regenerate question {q['number']}]").strip()
    elif status == "failed" and not response:
        response = f"[NEEDS INPUT: the answer to question {q['number']} could not be generated - regenerate it]"
    condensed_from = None
    if status == "ok" and answer_words(response) > word_cap(q):
        before = answer_words(response)
        # Up to two condensing calls, the second aiming lower; then, for a buyer's hard limit only, a plain trim.
        for factor in (0.85, 0.7):
            if answer_words(response) <= word_cap(q):
                break
            current = answer_words(response)
            try:
                value, truncated = parse_llm_json_checked(
                    call(system, condense_prompt(q, response, current, factor), max_tokens_for(budget)))
                short = str(value.get("response") or "").strip() if isinstance(value, dict) else ""
            except Exception:  # noqa: BLE001 - keep the long answer; the QA gate reports the overrun
                short, truncated = "", True
            if (short and not truncated and ends_cleanly(short) and answer_words(short) < current
                    and _figures(short) <= _figures(response)):
                response, condensed_from = short, before
                placeholders = [p for p in placeholders if p in short]
        if answer_words(response) > word_cap(q) and _as_int(q.get("word_limit")):
            trimmed = trim_to_words(response, word_cap(q))
            if trimmed and ends_cleanly(trimmed):
                response, condensed_from = trimmed, before
                placeholders = [p for p in placeholders if p in trimmed]
    words = answer_words(response)
    return {
        "question_number": q["number"],
        "question_text": q["text"],
        "word_limit": q.get("word_limit"),
        "page_limit": q.get("page_limit"),
        "weighting": q.get("weighting") or None,
        "budget_words": budget,
        "response": response,
        "word_count": words,
        "placeholders_used": placeholders,
        "status": status,
        **({"condensed_from_words": condensed_from} if condensed_from else {}),
        **({"error": error} if status == "failed" and error else {}),
    }


# ---------------------------------------------------------------------------
# M7: identity fields and extra claim checks
# ---------------------------------------------------------------------------

EMAIL_OK = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
_NAME_KEYS = ("name", "contact", "signatory", "director", "person")


def sanitize_identity(meta: dict) -> tuple[dict, list[str]]:
    """Profile fields safe to put in a bid: invalid emails and bracketed / partial names become [NEEDS INPUT]."""
    clean: dict = {}
    issues: list[str] = []
    for key, value in (meta or {}).items():
        if not isinstance(value, str) or not value.strip():
            clean[key] = value
            continue
        k = key.lower()
        label = key.replace("_", " ")
        v = value.strip()
        if "email" in k:
            parts = [p for p in re.split(r"[,;\s/]+", v) if p]
            if not parts or not all(EMAIL_OK.match(p) for p in parts):
                issues.append(f"{label} is not a valid email address ({v[:60]})")
                v = f"[NEEDS INPUT: valid {label}]"
        elif any(t in k for t in _NAME_KEYS) and k not in ("org_name", "company_name", "business_name") \
                and re.search(r"[\[\]{}<>]|\b(?:surname|first name|tbc|tbd)\b", v, re.I):
            issues.append(f"{label} is incomplete ({v[:60]})")
            v = f"[NEEDS INPUT: full {label}]"
        clean[key] = v
    return clean, issues


_CONTACT_KEY = re.compile(r"^(contact_name|contact|name|email|e_mail|phone|telephone|tel|mobile|job_title|role|position|contact_role)"
                          r"(?:_(\d+))?$", re.I)
_CONTACT_FIELD = {"contact_name": "name", "contact": "name", "name": "name", "email": "email", "e_mail": "email",
                  "phone": "phone", "telephone": "phone", "tel": "phone", "mobile": "phone", "job_title": "role",
                  "role": "role", "position": "role", "contact_role": "role"}


def bid_contact(meta: dict | None) -> tuple[dict, str]:
    """One signatory for the whole bid (fix plan R4: the letter was signed by one person with another person's
    email and phone). Profiles hold numbered contact sets (contact_name/email/phone, contact_name_2/...); the first
    set with a real name is used, with only that set's email and phone; anything missing becomes [NEEDS INPUT].
    Returns (contact, block for the prompts)."""
    groups: dict[int, dict] = {}
    for key, value in (meta or {}).items():
        m = _CONTACT_KEY.match(str(key))
        if not m or not isinstance(value, str) or not value.strip():
            continue
        groups.setdefault(int(m.group(2) or 1), {})[_CONTACT_FIELD[m.group(1).lower()]] = value.strip()
    real = lambda v: bool(v) and not v.startswith("[NEEDS INPUT")  # noqa: E731
    chosen = next((g for _, g in sorted(groups.items()) if real(g.get("name"))), None)
    if chosen is None:
        chosen = next((g for _, g in sorted(groups.items()) if real(g.get("email"))), {})
    name = chosen.get("name") if real(chosen.get("name")) else "[NEEDS INPUT: signatory full name]"
    who = name if real(chosen.get("name")) else "the signatory"
    contact = {
        "name": name,
        "role": chosen.get("role") if real(chosen.get("role")) else f"[NEEDS INPUT: job title of {who}]",
        "email": chosen.get("email") if real(chosen.get("email")) else f"[NEEDS INPUT: email address of {who}]",
        "phone": chosen.get("phone") if real(chosen.get("phone")) else f"[NEEDS INPUT: phone number of {who}]",
    }
    block = ("BID CONTACT AND SIGNATORY (use exactly these details wherever the bid gives a contact or signs off; "
             "never combine them with other people's details):\n"
             f"Name: {contact['name']}\nJob title: {contact['role']}\nEmail: {contact['email']}\nPhone: {contact['phone']}")
    return contact, block


def without_contacts(meta: dict | None) -> dict:
    """The profile without its contact sets, so writers see one signatory only (the BID CONTACT block)."""
    return {k: v for k, v in (meta or {}).items() if not _CONTACT_KEY.match(str(k))}


def company_label(company: str, meta: dict | None = None) -> str:
    """Name used in the bid: the profile's legal/organisation name, else the profile name, else a tag."""
    for key in ("org_name", "legal_name", "company_name"):
        v = str((meta or {}).get(key) or "").strip()
        if v and not re.search(r"[\[\]]", v):
            return v
    c = (company or "").strip()
    return c if c and c.lower() != "bidding company" else NEEDS_COMPANY


_ISO_CLAIM = re.compile(
    r"\b(?:(?:certified|accredited|registered)\s+(?:to|under|against|with)\s+)?"
    r"(ISO\s*/?\s*(?:IEC\s*)?(\d{4,5})(?::\d{4})?)"
    r"(?:\s*[-–]?\s*(certified|certification|accredited|accreditation|registered))?",
    re.I,
)


def flag_unverified_certifications(text: str, ground_truth_lower: str) -> str:
    """'ISO 27001 certified' / 'certified to ISO 9001' -> a [NEEDS INPUT] tag unless the company data
    holds a certificate for that standard (the word certificate/certified/UKAS near its number)."""
    if not text or "iso" not in text.lower():
        return text

    def has_certificate(num: str) -> bool:
        for m in re.finditer(rf"iso\s*/?\s*(?:iec\s*)?{num}", ground_truth_lower):
            window = ground_truth_lower[max(0, m.start() - 150): m.end() + 150]
            if re.search(r"certificate|certified|certification (?:no|number)|ukas|registration no", window):
                return True
        return False

    def repl(m: re.Match) -> str:
        whole = m.group(0)
        claims = bool(m.group(3)) or whole.lower().startswith(("certified", "accredited", "registered"))
        if not claims or has_certificate(m.group(2)):
            return whole
        return f"[NEEDS INPUT: {m.group(1)} certificate details - no certificate found in company data]"

    return _ISO_CLAIM.sub(repl, text)


# R8: the bid claims the bidder already did something ("We have raised clarification queries through the
# In-Tend Correspondence Facility"). One sentence, stopping at its full stop or the end of the line; tags
# inside it become (...) in the [CONFIRM].
_S = r"[^.!?\n]*"
# Tested one sentence at a time: a lazy prefix over a whole long line was quadratic (8 s on a 380k-character bid).
_ACTION_CORE = re.compile(
    rf"\b(?:"
    rf"we(?:'ve| have| had)\s+(?:already\s+|now\s+|also\s+)?(?:raised|submitted|sent|asked|lodged|posted|put|logged|tabled)\b{_S}"
    rf"\b(?:clarification|clarifications|quer(?:y|ies)|questions?)\b"
    rf"|(?:this|these|it|the (?:point|issue|question|query|matter)s?)\s+(?:has|have)\s+(?:already\s+)?been\s+(?:raised|submitted|asked|logged)\s+(?:with|through|via|in|as)\b"
    rf"|we(?:'ve| have)\s+(?:already\s+)?(?:visited|inspected|toured)\s+the\s+(?:site|centre|center|building|premises|venue)"
    rf"|we(?:'ve| have)\s+(?:already\s+)?attended\s+(?:the|a|your)\s+{_S}\b(?:briefing|site visit|engagement|event|meeting|webinar)"
    rf"|we(?:'ve| have)\s+(?:already\s+)?(?:met|spoken)\s+(?:with\s+)?(?:the|your)\s+(?:council|buyer|authority|officers?|team|client)"
    rf")",
    re.I,
)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


def find_invented_action(text: str) -> str:
    """The first sentence that says the bidder already acted, or ""."""
    for m in _SENTENCE.finditer(text or ""):
        if _ACTION_CORE.search(m.group(0)):
            return m.group(0)
    return ""


def flag_invented_actions(text: str, ground_truth_lower: str = "") -> str:
    """Turn a sentence that says the bidder already acted into a [CONFIRM] tag the bidder must check, unless the
    company data says the same thing. The sentence is kept inside the tag so nothing the user wrote is lost."""
    if not text or not find_invented_action(text):
        return text

    def repl(m: re.Match) -> str:
        whole = m.group(0)
        if not _ACTION_CORE.search(whole):
            return whole
        sentence = whole.strip()
        lead = whole[: len(whole) - len(whole.lstrip())]
        core = re.sub(r"\s+", " ", sentence.rstrip(".!? ")).lower()
        if ground_truth_lower and core[:60] in ground_truth_lower:
            return whole
        inner = sentence.rstrip(".!? ").replace("[", "(").replace("]", ")").replace('"', "'")[:200]
        return f"{lead}[CONFIRM: you have done this - otherwise delete: \"{inner}\"]"

    return _SENTENCE.sub(repl, text)


# ---------------------------------------------------------------------------
# M11 (light): Answer Bank relevance
# ---------------------------------------------------------------------------

_GENERIC = {
    "service", "services", "management", "manage", "managed", "delivery", "deliver", "delivering", "provide",
    "provided", "providing", "support", "supporting", "experience", "experienced", "project", "projects", "client",
    "clients", "customer", "customers", "team", "teams", "quality", "approach", "ensure", "ensuring", "work",
    "working", "works", "company", "organisation", "organization", "business", "solution", "solutions", "year",
    "years", "including", "through", "across", "within", "contract", "contracts", "tender", "tenders", "with",
    "will", "have", "that", "this", "from", "their", "they", "which", "were", "been", "also", "such", "more",
    "other", "into", "over", "each", "what", "when", "your", "about", "would", "should", "could", "must",
    "please", "describe", "detail", "details", "provide", "evidence", "example", "examples", "response",
    "question", "questions", "requirement", "requirements", "including", "based", "using", "used", "made",
    "high", "best", "level", "levels", "process", "processes", "plan", "plans", "policy", "policies", "local",
    "public", "sector", "value", "values", "key", "well", "very", "both", "only", "time", "make", "help",
}
# Categories that hold company-wide policy answers, not sector evidence: always offered.
_ALWAYS_RELEVANT = ("company overview", "health & safety", "health and safety", "equality", "edi", "safeguarding",
                    "insurance", "gdpr", "data protection", "environment", "sustainability", "modern slavery")
MIN_OVERLAP = 3


def _vocab(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z'-]{3,}", (text or "").lower()) if w not in _GENERIC}


def tender_vocabulary(stage1: dict | None, extra: str = "") -> set[str]:
    s = stage1 or {}
    scope = s.get("scope_of_work") if isinstance(s.get("scope_of_work"), dict) else {}
    bits = [str(s.get("tender_title") or ""), str(scope.get("summary") or ""), str(scope.get("full_scope") or "")[:6000],
            " ".join(str(d) for d in (scope.get("key_deliverables") or []) if d), extra]
    for q in s.get("itt_questions") or []:
        if isinstance(q, dict):
            bits.append(str(q.get("question_text") or ""))
    for ec in s.get("evaluation_criteria") or []:
        bits.append(str(ec.get("criterion") if isinstance(ec, dict) else ec))
    return _vocab(" ".join(bits))


def filter_answer_bank(rows: list, stage1: dict | None, extra_text: str = "") -> tuple[list, int]:
    """(kept rows most relevant first, number dropped). Rows are (category, question, answer, ...).

    Policy-type categories are always kept. Other entries (experience, case studies, methodology) are kept
    only when they share at least MIN_OVERLAP distinctive words with the tender, so a fintech case study
    is not offered to a community-centre bid.
    """
    vocab = tender_vocabulary(stage1, extra_text)
    if not vocab:
        return list(rows), 0
    scored = []
    dropped = 0
    for row in rows:
        cat = str(row[0] or "").lower()
        overlap = len(_vocab(f"{row[1] or ''} {str(row[2] or '')[:3000]}") & vocab)
        if any(a in cat for a in _ALWAYS_RELEVANT) or overlap >= MIN_OVERLAP:
            scored.append((overlap, row))
        else:
            dropped += 1
    scored.sort(key=lambda t: -t[0])
    return [r for _, r in scored], dropped


# ---------------------------------------------------------------------------
# M10: QA gate
# ---------------------------------------------------------------------------

SECTION_LABELS = [
    ("cover_letter", "Cover letter"),
    ("executive_summary", "Executive summary"),
    ("understanding_of_requirements", "Understanding of requirements"),
    ("technical_methodology", "Technical methodology"),
    ("relevant_experience", "Relevant experience"),
    ("team_and_personnel", "Team and personnel"),
    ("quality_and_compliance", "Quality and compliance"),
    ("social_value", "Social value"),
    ("pricing_notes", "Pricing notes"),
    ("declarations", "Declarations"),
]

# Any [bracketed note] in prose: [NEEDS INPUT: ...], [CONFIRM: ...], [Add ...], [Surname], [to be inserted].
# Not Markdown links ([text](url)), checkboxes ([ ], [x]) or numeric citations ([1]).
PLACEHOLDER_RE = re.compile(r"\[(?!\d+\])(?![xX ]\])[^\[\]\n]{2,240}\](?!\()")


def bid_texts(bid_response: dict) -> list[tuple[str, str]]:
    """(label, text) for every written part of a bid, in document order."""
    r = bid_response or {}
    out: list[tuple[str, str]] = []
    for key, label in SECTION_LABELS[:1]:
        obj = r.get(key)
        if isinstance(obj, dict) and isinstance(obj.get("content"), str) and obj["content"].strip():
            out.append((label, obj["content"]))
    for i, q in enumerate(r.get("itt_question_responses") or []):
        if isinstance(q, dict):
            out.append((f"Question {q.get('question_number') or i + 1}", str(q.get("response") or "")))
    for key, label in SECTION_LABELS[1:]:
        obj = r.get(key)
        if isinstance(obj, dict) and isinstance(obj.get("content"), str) and obj["content"].strip():
            out.append((label, obj["content"]))
    return out


def find_placeholders(bid_response: dict) -> list[dict]:
    return [{"section": label, "tag": m.group(0)}
            for label, text in bid_texts(bid_response) for m in PLACEHOLDER_RE.finditer(text)]


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if len(p.split()) >= 25]


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(re.findall(r"[a-z0-9']+", a.lower())), set(re.findall(r"[a-z0-9']+", b.lower()))
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


# The generated stand-in name is title case and used as a name ("AIVHUB | Bidding Company", "Bidding Company is
# pleased..."). Ordinary prose ("separate from the bidding company", "the Bidding Company shall") is not it (R3).
_GENERATED_NAME = re.compile(r"(?<![Tt]he )(?<![Aa] )(?<![Aa]ny )(?<![Ee]ach )\bBidding Company\b")
_EMAIL_FIELD = re.compile(r"\be-?mail\s*(?:address)?\s*[:\-]\s*([^\s,;()<>\[\]]+)", re.I)


def qa_gate(bid_response: dict, closed: bool = False, rulings: list | None = None) -> dict:
    """Checks a draft before export. Returns {"issues": [...], "p0": n, "placeholders": n, "passed": bool}.

    P0 (blocks a clean export): a scored question missing, failed or cut off; a section that stops
    mid-sentence; a generated or invalid identity ("Bidding Company", "[Surname]", an email with no @); a claim
    that the bidder already acted ("we have raised clarification queries") outside a [CONFIRM] tag.
    P1: short answers (< 60% of the budget), over a word limit, duplicated paragraphs, tender closed.
    Deadline consistency needs the single effective deadline from phase 2 (TenderSpec) and is not checked yet.
    """
    r = bid_response or {}
    issues: list[dict] = []

    def add(check: str, severity: str, section: str, detail: str) -> None:
        issues.append({"check": check, "severity": severity, "section": section, "detail": detail})

    for i, q in enumerate(r.get("itt_question_responses") or []):
        if not isinstance(q, dict):
            continue
        sec = f"Question {q.get('question_number') or i + 1}"
        text = str(q.get("response") or "").strip()
        status = q.get("status")
        words = answer_words(text)
        budget = _as_int(q.get("budget_words"))
        limit = _as_int(q.get("word_limit"))
        if not text or status == "failed" or "could not be generated - regenerate" in text:
            add("coverage", "P0", sec, "No answer was written.")
        elif status == "truncated" or "this answer was cut off" in text or not ends_cleanly(text):
            add("truncation", "P0", sec, "The answer stops mid-sentence.")
        elif budget and words < 0.6 * budget and (limit or q.get("weighting")):
            # An unweighted question's budget is an even share, a ceiling not a target: no "short" warning.
            add("coverage", "P1", sec, f"Short: {words} words against a target of about {budget}.")
        if limit and words > limit:
            add("word_limit", "P1", sec, f"{words} words; the buyer's limit is {limit}.")
        elif not limit and budget and words > budget * OVER_BUDGET_TOLERANCE:
            add("word_budget", "P2", sec, f"{words} words against a budget of {budget} from its weighting; "
                                          f"trim it so the higher-weighted answers get the space.")

    for label, text in bid_texts(r):
        if label.startswith("Question "):
            continue
        if text.strip() and not ends_cleanly(text):
            add("truncation", "P0", label, "The section stops mid-sentence.")

    all_text = "\n\n".join(t for _, t in bid_texts(r)) + "\n" + str(r.get("document_title") or "")
    for label, text in bid_texts(r):
        hit = find_invented_action(PLACEHOLDER_RE.sub(" ", text))
        if hit:
            add("invented_action", "P0", label, f'Says the bidder already acted: "{hit.strip()[:140]}". '
                                                "Delete it unless you did this.")
    if _GENERATED_NAME.search(all_text):
        add("identity", "P0", "Whole bid", '"Bidding Company" is a generated name; set the company name in your profile.')
    if re.search(r"\[\s*surname\s*\]", all_text, re.I):
        add("identity", "P0", "Whole bid", "A contact name is incomplete ([Surname]); complete it in your profile.")
    for m in _EMAIL_FIELD.finditer(all_text):
        val = m.group(1).strip(".")
        if "@" not in val and "." in val:
            add("identity", "P0", "Whole bid", f'"{val}" is not a valid email address; correct it in your profile.')

    # A summary or covering letter restates the answers by design (EEF: the executive summary repeated paragraphs
    # from three answers), so only answers are compared with each other. Token sets are built once, not per pair.
    skip = re.compile(r"summary|letter", re.I)
    paras = [(label, p, set(re.findall(r"[a-z0-9']+", p.lower())))
             for label, text in bid_texts(r) if not skip.search(label) for p in _paragraphs(text)]
    dup_pairs = 0
    for a in range(len(paras)):
        sa = paras[a][2]
        for b in range(a + 1, len(paras)):
            sb = paras[b][2]
            if sa and sb and len(sa & sb) / len(sa | sb) >= 0.8:
                dup_pairs += 1
                if dup_pairs <= 3:
                    add("duplication", "P1", f"{paras[a][0]} / {paras[b][0]}",
                        "A paragraph is repeated almost word for word.")
    for _sec, _detail in _find_ruling_conflicts(rulings or [], bid_texts(r)):
        add("ruling_conflict", "P0", _sec, _detail)
    if closed:
        add("status", "P1", "Whole bid", "The tender's stored deadline has passed.")

    phs = find_placeholders(r)
    if phs:
        add("placeholders", "P2", "Whole bid", f"{len(phs)} item(s) still need your input ([NEEDS INPUT], [CONFIRM] and similar).")
    p0 = sum(1 for x in issues if x["severity"] == "P0")
    return {"issues": issues, "p0": p0, "placeholders": len(phs), "passed": p0 == 0}


# ---------------------------------------------------------------------------
# Tender sector (which clarification specialists make sense)
# ---------------------------------------------------------------------------

_DIGITAL_CPV = ("48", "72", "302", "642", "3223", "3224")
_DIGITAL_STRONG = re.compile(
    r"\b(software|digital|cloud|cyber|saas|ict|it services?|it systems?|data platform|data warehouse|website|"
    r"web application|app development|application development|api|database|hosting|power bi|azure|aws|"
    r"crm system|erp|network infrastructure|telephony)\b", re.I)
_PROPERTY_CPV = ("70", "45", "507", "797", "909")
_PROPERTY_WORDS = re.compile(
    r"\b(lease|leasehold|tenancy|premises|building|community centre|centre operator|facilit(?:y|ies)|hall|venue|"
    r"heads of terms|repairs|maintenance|landlord|rent|car park|site)\b", re.I)


def _cpv_codes(cpv: str) -> list[str]:
    return re.findall(r"\b(\d{8})(?:-\d)?\b", cpv or "")


def tender_sector(title: str, description: str, cpv: str = "", doc_text: str = "") -> dict:
    """{"digital": bool, "property": bool}: decides which specialist agents review a tender.

    Digital when a CPV code is software/IT/telecoms or the title/description names IT work; the documents
    alone count only with several distinct IT terms (a single "website" in a footer is not enough).
    """
    codes = _cpv_codes(cpv)
    head = f"{title} {description}"
    doc_hits = {m.group(0).lower() for m in _DIGITAL_STRONG.finditer((doc_text or "")[:60000])}
    digital = (any(c.startswith(_DIGITAL_CPV) for c in codes) or bool(_DIGITAL_STRONG.search(head))
               or len(doc_hits) >= 5)
    prop_hits = {m.group(0).lower() for m in _PROPERTY_WORDS.finditer(f"{head} {(doc_text or '')[:60000]}")}
    prop = any(c.startswith(_PROPERTY_CPV) for c in codes) or len(prop_hits) >= 3
    return {"digital": digital, "property": prop}


# ---------------------------------------------------------------------------
# Published clarifications (from the Stage 1 dissection)
# ---------------------------------------------------------------------------


def clarifications_block(stage1: dict | None, limit: int = 6000) -> str:
    items = (stage1 or {}).get("published_clarifications") or []
    lines = []
    for c in items if isinstance(items, list) else []:
        if not isinstance(c, dict):
            continue
        q = str(c.get("question") or "").strip()
        a = str(c.get("answer") or "").strip()
        if q or a:
            src = str(c.get("source") or "").strip()
            lines.append(f"Q: {q}\nA: {a}" + (f"\n(Source: {src})" if src else ""))
    return "\n\n".join(lines)[:limit]
