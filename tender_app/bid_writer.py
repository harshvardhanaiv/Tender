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
9. Plain UK English, active voice, short paragraphs. No empty superlatives (world-class, cutting-edge, unique, robust, best-in-class)."""

ANSWER_STANDARD = """ANSWER STANDARD:
- The first sentence answers the question directly, in the buyer's words. No preamble ("We are delighted...").
- Turn the question, its sub-parts and any scoring guidance into a checklist and cover every item, in the buyer's order, under short sub-headings that mirror the question.
- Be specific to this buyer and site: use facts from the tender documents and clarification answers. If a sentence could be pasted into any other bid unchanged, cut it.
- Commitments say what, by when, who owns it and how it is measured.
- State assumptions, risks with mitigations and conditions explicitly. Use simple Markdown tables (| a | b |) where a timetable, price list or plan reads better as a table.
- Do not repeat content that belongs to another question; refer to that question instead.
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
    """'5.II' -> '5', 'Q4.I' -> 'Q4', '3a' -> '3', 'Q7' -> 'Q7'."""
    n = number.strip()
    m = re.match(r"^([A-Za-z]{0,3}\s*\d+)", n)
    return m.group(1).replace(" ", "") if m else n


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
            "word_limit": _as_int(q.get("word_limit")),
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
    last_line = t.splitlines()[-1]
    # A bullet or heading may end without a full stop; a prose paragraph may not.
    return bool(_LIST_LINE.match(last_line) or last_line.lstrip().startswith("#"))


def question_prompt(q: dict, *, title: str, authority: str, company: str, tender_dissection: str,
                    clarifications_block: str, company_data: str, company_context: str, answer_bank: str,
                    winning_bids: str, other_questions: list[dict], closed_note: str = "",
                    lang_instruction: str = "") -> str:
    others = "\n".join(f"- {o['number']}: {o['text'][:140]}" for o in other_questions if o is not q)[:3000]
    limit_line = (f"HARD WORD LIMIT: {q['word_limit']} words (the buyer stops reading after it)."
                  if q.get("word_limit") else f"Target length: about {q['budget_words']} words.")
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


def write_question(call: Callable[[str, str, int], str], q: dict, prompt: str,
                   system: str = "You are a professional UK public-sector bid writer. Return ONLY valid JSON.") -> dict:
    """Write one answer. `call(system, user, max_tokens)` returns the model's raw text.

    A reply that was cut off (JSON needed repairing, or the text stops mid-sentence) is retried once with
    more room and a shorter target; if it is still cut off the answer is kept but marked, with a visible
    [NEEDS INPUT] tag, so the QA gate blocks a clean export instead of the gap going unnoticed.
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
    words = len(response.split())
    return {
        "question_number": q["number"],
        "question_text": q["text"],
        "word_limit": q.get("word_limit"),
        "weighting": q.get("weighting") or None,
        "budget_words": budget,
        "response": response,
        "word_count": words,
        "placeholders_used": placeholders,
        "status": status,
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


_EMAIL_FIELD = re.compile(r"\be-?mail\s*(?:address)?\s*[:\-]\s*([^\s,;()<>\[\]]+)", re.I)


def qa_gate(bid_response: dict, closed: bool = False) -> dict:
    """Checks a draft before export. Returns {"issues": [...], "p0": n, "placeholders": n, "passed": bool}.

    P0 (blocks a clean export): a scored question missing, failed or cut off; a section that stops
    mid-sentence; a generated or invalid identity ("Bidding Company", "[Surname]", an email with no @).
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
        words = len(text.split())
        budget = _as_int(q.get("budget_words"))
        limit = _as_int(q.get("word_limit"))
        if not text or status == "failed" or "could not be generated - regenerate" in text:
            add("coverage", "P0", sec, "No answer was written.")
        elif status == "truncated" or "this answer was cut off" in text or not ends_cleanly(text):
            add("truncation", "P0", sec, "The answer stops mid-sentence.")
        elif budget and words < 0.6 * budget:
            add("coverage", "P1", sec, f"Short: {words} words against a target of about {budget}.")
        if limit and words > limit:
            add("word_limit", "P1", sec, f"{words} words; the buyer's limit is {limit}.")

    for label, text in bid_texts(r):
        if label.startswith("Question "):
            continue
        if text.strip() and not ends_cleanly(text):
            add("truncation", "P0", label, "The section stops mid-sentence.")

    all_text = "\n\n".join(t for _, t in bid_texts(r)) + "\n" + str(r.get("document_title") or "")
    if re.search(r"\bbidding company\b", all_text, re.I):
        add("identity", "P0", "Whole bid", '"Bidding Company" is a generated name; set the company name in your profile.')
    if re.search(r"\[\s*surname\s*\]", all_text, re.I):
        add("identity", "P0", "Whole bid", "A contact name is incomplete ([Surname]); complete it in your profile.")
    for m in _EMAIL_FIELD.finditer(all_text):
        val = m.group(1).strip(".")
        if "@" not in val and "." in val:
            add("identity", "P0", "Whole bid", f'"{val}" is not a valid email address; correct it in your profile.')

    paras = [(label, p) for label, text in bid_texts(r) for p in _paragraphs(text)]
    dup_pairs = 0
    for a in range(len(paras)):
        for b in range(a + 1, len(paras)):
            if _jaccard(paras[a][1], paras[b][1]) >= 0.8:
                dup_pairs += 1
                if dup_pairs <= 3:
                    add("duplication", "P1", f"{paras[a][0]} / {paras[b][0]}",
                        "A paragraph is repeated almost word for word.")
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
