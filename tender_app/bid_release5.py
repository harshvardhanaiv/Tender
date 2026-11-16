"""Release 5 helpers (QA scorecard 13 Nov pm and the Priority Plumbing pilot). Pure functions, no AI calls.

O1  final export: what blocks it, and what is internal and must never reach the buyer
R3  dates that have passed, written as if still achievable
P   experience years longer than the company has existed; a draft that contradicts itself; buyer percentages
C   statements the buyer requires in every bid (excluded manufacturers, payment terms)
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from tender_app.bid_writer import PLACEHOLDER_RE, bid_texts

_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_MONTH_WORD = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MONTH_WORD + r")\.?,?\s+(\d{4})\b"
    r"|\b(\d{1,2})[/.](\d{1,2})[/.](\d{4}|\d{2})\b", re.I)


def _match_date(m: re.Match) -> _dt.date | None:
    try:
        if m.group(1):
            return _dt.date(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
        y = int(m.group(6))
        return _dt.date(y + 2000 if y < 100 else y, int(m.group(5)), int(m.group(4)))
    except (ValueError, KeyError):
        return None


def _each_text(bid_response: dict):
    """(label, container, key) for every written part of a bid, so a pass can rewrite it in place."""
    r = bid_response or {}
    labels = dict((k, lbl) for k, lbl in (
        ("cover_letter", "Cover letter"), ("executive_summary", "Executive summary"),
        ("understanding_of_requirements", "Understanding of requirements"),
        ("technical_methodology", "Technical methodology"), ("relevant_experience", "Relevant experience"),
        ("team_and_personnel", "Team and personnel"), ("quality_and_compliance", "Quality and compliance"),
        ("social_value", "Social value"), ("pricing_notes", "Pricing notes"), ("declarations", "Declarations")))
    for key, label in labels.items():
        obj = r.get(key)
        if isinstance(obj, dict) and isinstance(obj.get("content"), str) and obj["content"].strip():
            yield label, obj, "content"
    for i, q in enumerate(r.get("itt_question_responses") or []):
        if isinstance(q, dict) and isinstance(q.get("response"), str) and q["response"].strip():
            yield f"Question {q.get('question_number') or i + 1}", q, "response"


def _sentence_end(text: str, pos: int) -> int:
    """Index just after the sentence that contains pos, never inside a [bracketed tag]."""
    depth = 0
    i = pos
    n = len(text)
    while i < n:
        c = text[i]
        if c == "[":
            depth += 1
        elif c == "]":
            if depth == 0:
                return i + 1  # pos was inside a tag: the note goes straight after it
            depth -= 1
        elif c == "\n":
            return i
        elif depth == 0 and c in ".!?" and (i + 1 >= n or text[i + 1] in " \n\"')"):
            return i + 1
        i += 1
    return n


def _sentence_start(text: str, pos: int) -> int:
    i = pos
    while i > 0:
        c = text[i - 1]
        if c == "\n" or (c in ".!?" and text[i:i + 1] in (" ", "\n")):
            break
        i -= 1
    return i


# ---- R3: dates that have passed ---------------------------------------------------------------------------------------

_BY_CUE = re.compile(r"\b(?:by|before|no later than|until|on or before|deadline(?: of| on| is)?|due(?: on| by)?|to meet the)\s*(?:the\s+)?$", re.I)
_FUTURE = re.compile(r"\b(?:will|shall|to be|must|should|intend|plan to|post|send|submit|despatch|dispatch|deliver|provide|raise|register)\b", re.I)
_ALREADY_PAST = re.compile(r"\b(?:has|have|had) (?:now |already )?(?:passed|closed|expired)|\bpassed\b|\bexpired\b|\bclosed on\b|\bmissed\b|"
                           r"\btook place\b|\bwas held\b", re.I)


def passed_date_hits(text: str, today: _dt.date | None = None) -> list:
    """[(date, match)] for each past date the text still treats as a date to meet ("we will send samples by 30 October 2026")."""
    today = today or _dt.date.today()
    out = []
    for m in _DATE.finditer(text or ""):
        when = _match_date(m)
        if when is None or when >= today:
            continue
        start, end = _sentence_start(text, m.start()), _sentence_end(text, m.end())
        sentence = text[start:end]
        before = text[max(start, m.start() - 40):m.start()]
        after = text[m.end():min(end, m.end() + 24)]
        cue = bool(_BY_CUE.search(before)) or bool(re.match(r"\s*(?:enquiry |clarification |sample |submission )?deadline", after, re.I))
        if cue and _FUTURE.search(sentence) and not _ALREADY_PAST.search(sentence):
            out.append((when, m))
    return out


def flag_passed_dates(text: str, today: _dt.date | None = None) -> str:
    """A commitment to meet a date that has already gone gets a [CONFIRM] straight after it, so nobody submits
    "we will send samples by 30 October 2026" in mid-November (QA scorecard R3)."""
    if not text or not isinstance(text, str):
        return text
    hits = passed_date_hits(text, today)
    for when, m in reversed(hits):
        end = _sentence_end(text, m.end())
        if "has passed" in text[m.end():end + 140]:
            continue
        note = (f" [CONFIRM: {when.strftime('%d/%m/%Y')} has passed - agree a new date with the buyer, "
                "or take this commitment out]")
        text = text[:end] + note + text[end:]
    return text


def flag_passed_dates_in_checklist(items: Any, today: _dt.date | None = None) -> int:
    """Checklist rows that still tell the bidder to do something by a date already gone ("Post samples by 30 October
    2026") are marked DATE PASSED in their notes. Returns how many rows were marked."""
    today = today or _dt.date.today()
    n = 0
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        hit = None
        for key, val in item.items():
            if not isinstance(val, str) or "DATE PASSED" in val:
                continue
            for m in _DATE.finditer(val):
                when = _match_date(m)
                # Only a date to act by ("by 30 October 2026", "30/10/2026 deadline"), not a document's own date.
                cue = (_BY_CUE.search(val[max(0, m.start() - 40):m.start()])
                       or re.match(r"\s*(?:\w+ )?deadline", val[m.end():m.end() + 24], re.I))
                if when and when < today and cue and not _ALREADY_PAST.search(val):
                    hit = when
                    break
            if hit:
                break
        if hit:
            note_key = "notes" if "notes" in item or "clause_notes" not in item else "clause_notes"
            item[note_key] = (f"DATE PASSED ({hit.strftime('%d/%m/%Y')}): check with the buyer whether this can still be done. "
                              + str(item.get(note_key) or "")).strip()
            n += 1
    return n


# ---- Pilot: experience longer than the company has existed -----------------------------------------------------------

_YEARS = re.compile(r"((?:over|more than|nearly|almost|around|some)\s+)?(\d{1,2})\+?\s+years(?:['’])?\s+(?:of\s+)?"
                    r"(?:combined\s+|trading\s+|industry\s+)?experience", re.I)
_INC = re.compile(r"(?:incorporat\w*|date_of_creation|date of creation|registered on|established on)\D{0,30}"
                  r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/.]\d{1,2}[/.]\d{4}|\d{1,2}\s+" + _MONTH_WORD + r"\s+\d{4})", re.I)


def incorporation_date(*sources: Any) -> _dt.date | None:
    """The incorporation date from a Companies House result ({"incorporated": "2024-07-10"}) or from profile text."""
    for src in sources:
        if isinstance(src, dict):
            src = src.get("incorporated") or src.get("date_of_creation") or src.get("incorporation_date") or ""
            src = f"incorporated {src}" if src else ""
        m = _INC.search(str(src or ""))
        if not m:
            continue
        raw = m.group(1)
        iso = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
        try:
            if iso:
                return _dt.date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
            dm = _DATE.search(raw)
            when = _match_date(dm) if dm else None
            if when:
                return when
        except ValueError:
            continue
    return None


def reconcile_experience(text: str, incorporated: _dt.date | None, today: _dt.date | None = None) -> str:
    """"Priority Plumbing ... with over 12 years of experience" for a company incorporated in 2024 becomes the team's
    experience, with a [CONFIRM] so the bidder checks whose experience it is."""
    if not text or not isinstance(text, str) or incorporated is None:
        return text
    today = today or _dt.date.today()
    age = max(0, (today - incorporated).days // 365)
    flagged = False

    def fix(m: re.Match) -> str:
        nonlocal flagged
        years = int(m.group(2))
        if years <= age:
            return m.group(0)
        before = text[max(0, m.start() - 40):m.start()]
        if re.search(r"\b(?:team|staff|engineers?|founders?|directors?|people|personnel)\b[^.]{0,30}$", before, re.I):
            return m.group(0)  # already the team's
        phrase = m.group(0)
        mark = ""
        if not flagged:
            flagged = True
            notes.append(f" [CONFIRM: {years} years is the team's experience - the company was incorporated on "
                         f"{incorporated.strftime('%d/%m/%Y')}; say whose experience this is]")
            mark = "\x00"                               # where the first rewritten claim sits; the note follows its sentence
        if re.search(r"\b(?:with|has|have|brings?|offers?|holds?|and)\s+$", before, re.I):
            return "a team with " + phrase + mark      # "has a team with over 12 years of experience"
        if re.search(r"\b(?:our|its|their)\s+$", before, re.I):
            return "team's " + phrase + mark           # "our team's 12 years of experience"
        return phrase + mark

    notes: list = []
    # "..., with a team with over 12 years" reads badly: "..., whose team has over 12 years".
    out = re.sub(r"\bwith a team with\b", "whose team has", _YEARS.sub(fix, text))
    at = out.find("\x00")
    if at >= 0:
        out = out.replace("\x00", "")
        end = _sentence_end(out, at)
        out = out[:end] + notes[0] + out[end:]
    return out


# ---- Pilot: the draft contradicts itself ------------------------------------------------------------------------------

_TOPICS = (
    ("24/7 cover", r"24\s*/\s*7|24[- ]hour|round[- ]the[- ]clock"),
    ("insurance", r"\binsur(?:ed|ance)\b"),
    ("health and safety accreditation", r"\bCHAS\b|SafeContractor|ISO\s?45001|health and safety accreditation"),
    ("quality accreditation", r"ISO\s?9001|quality management system|quality accreditation"),
    ("security clearance", r"\bDBS\b|security[- ]clear|\bvetted\b"),
    ("helpdesk", r"help ?desk|call[- ]handling"),
)
_NEG = re.compile(r"\b(?:do(?:es)? not|don't|doesn't)\s+(?:currently\s+|yet\s+)?(?:have|hold|evidence|provide|operate|offer)\b|"
                  r"\bnot\s+(?:currently|yet)\s+(?:hold|have|evidence|held)\b|\bno\s+(?:evidenced?|named|documented)\b|"
                  r"\b(?:does|do) not evidence\b|\bnot evidenced\b|\bnot held\b", re.I)
_POS_SUBJECT = re.compile(r"\b(?:we|the (?:company|team|bidder)|our (?:team|company|engineers))\s+(?:are|is|operate|operates|have|has|hold|holds|"
                          r"provide|provides|offer|offers|run|runs)\b|\bour\s+(?:\w+[- ]){0,2}(?:24\s*/\s*7|insur)|\bfully insured\b", re.I)
_NOT_A_CLAIM = re.compile(r"\b(?:will|would|partner|subcontractor|require[sd]?|needs?|must|should|if)\b", re.I)
_SENT = re.compile(r"[^.!?\n]+[.!?]?")


def find_contradictions(bid_response: dict) -> list:
    """[{"topic", "claim_section", "claim", "gap_section", "gap"}]: one part of the draft says the bidder has
    something ("We operate 24/7") that another part says it lacks ("We do not currently have 24/7 ... infrastructure")."""
    pos: dict = {}
    neg: dict = {}
    for label, text in bid_texts(bid_response):
        for s in _SENT.findall(text):
            plain = PLACEHOLDER_RE.sub(" ", s)
            for topic, pat in _TOPICS:
                if not re.search(pat, plain, re.I):
                    continue
                if _NEG.search(plain):
                    neg.setdefault(topic, (label, s.strip()))
                elif _POS_SUBJECT.search(plain) and not _NOT_A_CLAIM.search(plain):
                    # the sentence itself, without a tag that happens to sit in front of it
                    pos.setdefault(topic, []).append((label, re.sub(r"^(?:\s*\[[^\[\]]*\])+\s*", "", s.strip())))
    out = []
    for topic, claims in pos.items():
        if topic in neg:
            for label, s in claims:
                out.append({"topic": topic, "claim_section": label, "claim": s, "gap_section": neg[topic][0], "gap": neg[topic][1]})
    return out


def flag_contradictions(bid_response: dict) -> list:
    """One [CONFIRM] per topic, after the first claim, naming every section that makes the claim (so the to-do list
    grows by one item per contradiction, not one per sentence), and one QA issue (P1) per topic."""
    found = find_contradictions(bid_response)
    if not found:
        return []
    by_label = {label: (obj, key) for label, obj, key in _each_text(bid_response)}
    by_topic: dict = {}
    for c in found:
        by_topic.setdefault(c["topic"], []).append(c)
    issues = []
    for topic, claims in by_topic.items():
        sections = []
        for c in claims:
            if c["claim_section"] not in sections:
                sections.append(c["claim_section"])
        first = next((c for c in claims if c["claim_section"] in by_label
                      and by_label[c["claim_section"]][0][by_label[c["claim_section"]][1]].find(c["claim"]) >= 0), None)
        gap = re.sub(r"\s+", " ", PLACEHOLDER_RE.sub("", claims[0]["gap"])).strip()[:150]
        if first is not None:
            obj, key = by_label[first["claim_section"]]
            text = obj[key]
            end = text.find(first["claim"]) + len(first["claim"])
            also = [s for s in sections if s != first["claim_section"]]
            obj[key] = (text[:end] + f" [CONFIRM: {topic} - {claims[0]['gap_section']} says \"{gap}\""
                        + (f"; the same claim is in {', '.join(also[:5])}" if also else "") + "; make them consistent]" + text[end:])
        issues.append({"check": "consistency", "severity": "P1", "section": ", ".join(sections[:6]),
                       "detail": f"Says the bidder has {topic} (\"{claims[0]['claim'][:110]}\") but {claims[0]['gap_section']} "
                                 "says it does not. Make them consistent."})
    return issues


# ---- Pilot: the buyer's own percentages need no company evidence ------------------------------------------------------

def pct_in_buyer_facts(pct: str, facts_lower: str) -> bool:
    """"60%" is the buyer's quality weighting when the dissection holds "weighting_percent": 60 or the notice says
    "Quality ... 60"; it is not a claim about the bidder."""
    p = re.escape(pct)
    return bool(re.search(rf"(?:weight\w*|percent\w*|quality|price|cost|technical|commercial|social value)[\"'\s:=(-]{{0,24}}{p}(?![\d.])", facts_lower)
                or re.search(rf"(?<![\d.]){p}(?![\d.])\s*(?:%|per\s*cent|percent)", facts_lower))


# ---- Chemonics: statements the buyer requires in every bid ------------------------------------------------------------

def _clean_list(value: Any) -> list:
    out = []
    for v in value if isinstance(value, list) else []:
        s = str(v.get("name") if isinstance(v, dict) else v or "").strip()
        if s and not re.search(r"^(?:none|n/?a|null|not stated)$", s, re.I):
            out.append(s)
    return out


def required_statements_block(stage1: dict | None) -> str:
    """For the writers: what the buyer excludes and the payment terms it sets."""
    s = stage1 or {}
    excluded = _clean_list(s.get("excluded_suppliers_or_products"))
    terms = str(s.get("payment_terms") or "").strip()
    if re.search(r"^(?:none|n/?a|null|not stated)$", terms, re.I):
        terms = ""
    lines = []
    if excluded:
        lines.append("- The buyer excludes tenders that offer: " + ", ".join(excluded[:20]) + ". State in the technical "
                     "response that none of these is offered, naming them, as [CONFIRM: ...] for the bidder to tick.")
    if terms:
        lines.append(f"- The buyer's payment terms are: {terms}. The pricing answer accepts them in the buyer's words.")
    return ("STATEMENTS THE BUYER REQUIRES IN EVERY BID:\n" + "\n".join(lines) + "\n") if lines else ""


def apply_required_statements(bid_response: dict, stage1: dict | None) -> list:
    """When the writers left one out, add it: the excluded-manufacturer confirmation to the declarations and the
    payment terms to the price answer. Returns what was added."""
    s = stage1 or {}
    r = bid_response or {}
    added = []
    all_text = "\n".join(t for _, t in bid_texts(r)).lower()
    excluded = _clean_list(s.get("excluded_suppliers_or_products"))
    if excluded and sum(1 for n in excluded if n.lower() in all_text) < max(1, len(excluded) // 2 + len(excluded) % 2):
        line = ("[CONFIRM: no product offered is made by, or contains components from, " + ", ".join(excluded[:20])
                + " (the buyer excludes tenders that offer them)]")
        dec = r.get("declarations")
        if not isinstance(dec, dict):
            dec = r["declarations"] = {"content": "", "forms_referenced": []}
        dec["content"] = (str(dec.get("content") or "").rstrip() + "\n" + line).strip()
        added.append("excluded suppliers")
    terms = str(s.get("payment_terms") or "").strip()
    if terms and not re.search(r"^(?:none|n/?a|null|not stated)$", terms, re.I):
        core = re.sub(r"[^a-z0-9]+", " ", terms.lower()).strip()
        key = re.search(r"net\s*\d+|\d+\s*(?:calendar |working )?days", terms, re.I)
        present = (core and core in re.sub(r"[^a-z0-9]+", " ", all_text)) or (
            key and re.search(re.sub(r"\s+", r"\\s*", re.escape(key.group(0).lower())), all_text))
        if not present:
            line = f"Payment terms: {terms}, as stated in the tender documents. [CONFIRM: we accept these payment terms]"
            target = None
            for q in r.get("itt_question_responses") or []:
                if isinstance(q, dict) and re.search(r"\bpric|commercial|\bcost|financial offer",
                                                     f"{q.get('question_number') or ''} {str(q.get('question_text') or '')[:160]}", re.I):
                    target = (q, "response")
                    break
            if target is None and isinstance(r.get("pricing_notes"), dict):
                target = (r["pricing_notes"], "content")
            if target is None:
                dec = r.get("declarations")
                if not isinstance(dec, dict):
                    dec = r["declarations"] = {"content": "", "forms_referenced": []}
                target = (dec, "content")
            obj, k = target
            obj[k] = (str(obj.get(k) or "").rstrip() + "\n\n" + line).strip()
            added.append("payment terms")
    return added


# ---- Pilot: a general outline is provisional --------------------------------------------------------------------------

def provisional_structure_note(questionnaire_mode: bool, stage1: dict | None) -> str:
    if questionnaire_mode:
        return ""
    sd = (stage1 or {}).get("submission_deadline")
    portal = str(sd.get("portal") or "").strip() if isinstance(sd, dict) else ""
    if re.search(r"not stated|unknown|^null$|^n/?a$", portal, re.I):
        portal = ""
    where = f" from {portal}" if portal else " from the buyer's portal"
    return ("Structure is provisional: the documents provided contain no buyer questionnaire, so this draft uses a "
            f"general outline. Upload the ITT pack{where} and re-run to rebuild it around the buyer's questions.")


# ---- O1: final export -------------------------------------------------------------------------------------------------

# Anything that tells the reader how the document was produced. None of it may appear in a downloaded bid.
INTERNAL_MARKERS = re.compile(r"AI[- ]DRAFT|AI[- ]generated|generated by AI|written by AI|\bChatGPT\b|\bDeepSeek\b|language model", re.I)


def scrub_internal(text: str) -> str:
    """Removes a production marker the model or an older draft left in the text ("AI-generated overview")."""
    if not text or not isinstance(text, str):
        return text
    return re.sub(r"[ \t]{2,}", " ", INTERNAL_MARKERS.sub("", text))


def final_export_blockers(bid_response: dict, qa: dict | None = None) -> list:
    """Why a final (submission-ready) export is refused: open [NEEDS INPUT] / [CONFIRM] items and failed P0 checks."""
    out = []
    open_items = [m.group(0) for _, t in bid_texts(bid_response or {}) for m in PLACEHOLDER_RE.finditer(t)]
    if open_items:
        distinct = []
        for t in open_items:
            if t not in distinct:
                distinct.append(t)
        out.append(f"{len(open_items)} item(s) still need your input, for example: " + "; ".join(x[:90] for x in distinct[:3]))
    for i in (qa or {}).get("issues") or []:
        if i.get("severity") == "P0":
            out.append(f"{i.get('section')}: {i.get('detail')}")
    return out


# ---- R1 / pilot: what the uploaded documents show before any credits are spent -----------------------------------------

_FIN_Q = re.compile(r"financial(?:ly)?\s+(?:sustainab|viab|model|plan|proposal|standing|projection|forecast)|business plan|"
                    r"profit and loss|\bP\s?&\s?L\b|income and (?:cost|expenditure)|cash ?flow|financial sustainability", re.I)
_PCT = re.compile(r"(?<![\d.])(\d{1,3})(?:\.\d+)?\s?%")


def financial_question_weight(text: str) -> int:
    """The weighting (25-100) of a question about finances, when the documents state one near it ("Question 4:
    Financial sustainability ... 40%"); 0 when there is none."""
    best = 0
    head = (text or "")[:200000]
    for m in _FIN_Q.finditer(head):
        window = head[max(0, m.start() - 160): m.end() + 260]
        for p in _PCT.finditer(window):
            n = int(p.group(1))
            if 25 <= n <= 100:
                best = max(best, n)
    return best


_PORTALS = (("ProContract", r"procontract|proactis|due-north"), ("In-Tend", r"in-?tend"),
            ("Delta eSourcing", r"delta[- ]?esourcing"), ("Atamis", r"atamis"), ("Jaggaer", r"jaggaer|bravosolution"))


def portal_hint(text: str) -> str:
    """The e-tendering portal a notice or document names ("released on the Blue Light Atamis portal")."""
    for name, pat in _PORTALS:
        if re.search(pat, text or "", re.I):
            return name
    return ""


# A saved notice is not the buyer's specification or price schedule: it is short and says the documents are
# somewhere else ("available on the Atamis portal after registration").
THIN_DOC_CHARS = 4000
_ELSEWHERE = re.compile(r"(?:available|released|accessed|accessible|obtained|downloaded?|found)\s+(?:only\s+)?(?:on|from|via|through|at)\b|"
                        r"after (?:supplier )?registration|register(?:ing)? (?:on|to|with|for)\b|following registration", re.I)


def is_notice_only(text: str) -> bool:
    t = (text or "").strip()
    return len(t) < THIN_DOC_CHARS and "[Sheet: " not in t and bool(_ELSEWHERE.search(t))


# ---- near-duplicate paragraphs ----------------------------------------------------------------------------------------

_DUP_SKIP = re.compile(r"summary|letter", re.I)


def drop_near_duplicates(bid_response: dict, threshold: float = 0.8, min_words: int = 15) -> int:
    """Removes a paragraph that repeats an earlier one in another part of the bid almost word for word (the checker only
    reported these). The summary and the covering letter restate the answers by design and are left alone, as is any
    part that would be left empty. Returns how many paragraphs were removed."""
    seen: list = []   # (label, token set) of paragraphs kept so far
    removed = 0
    for label, obj, key in _each_text(bid_response):
        if _DUP_SKIP.search(label):
            continue
        paras = re.split(r"\n\s*\n", obj[key])
        keep, kept_sets = [], []
        for p in paras:
            toks = set(re.findall(r"[a-z0-9']+", p.lower()))
            dup = len(toks) >= min_words and any(
                other != label and len(toks & ts) / len(toks | ts) >= threshold for other, ts in seen)
            if dup:
                removed += 1
            else:
                keep.append(p)
                kept_sets.append(toks)
        if keep and len(keep) < len(paras):
            obj[key] = "\n\n".join(keep)
        elif not keep:
            removed -= len(paras)
            kept_sets = [set(re.findall(r"[a-z0-9']+", p.lower())) for p in paras]
        seen.extend((label, ts) for ts in kept_sets if len(ts) >= min_words)
    return removed


# ---- site visits and expressions of interest, read straight from the buyer's text ------------------------------------
# (the dissection prompt asks for them, but a model can skip them; this reads the sentence itself and the writers are
# told, so the draft names the visit dates and the EOI window and says which have passed.)

_DAYS_LIST = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?((?:\s*(?:,|and|&|or|to|-)\s*\d{1,2}(?:st|nd|rd|th)?)*)\s+(" + _MONTH_WORD
                        + r")\.?(?:,?\s+(\d{4}))?", re.I)
_BOOKED_WITH = re.compile(r"\b(?:book(?:ed|ing)?|arrange[d]?|contact|request(?:ed)?)\b[^.\n]{0,60}?\b(?:with|through|via|by (?:contacting|emailing))\s+"
                          r"((?:Mr|Mrs|Ms|Miss|Dr)\.?\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})")


def _dates_in(sentence: str, today: _dt.date) -> list:
    out = []
    for m in _DAYS_LIST.finditer(sentence):
        month = _MONTHS.get(m.group(3)[:3].lower())
        days = [int(m.group(1))] + [int(d) for d in re.findall(r"\d{1,2}", m.group(2) or "")]
        year = int(m.group(4)) if m.group(4) else today.year
        span = bool(re.search(r"\bto\b|-", m.group(2) or ""))
        ds = []
        for d in days:
            try:
                ds.append(_dt.date(year, month, d))
            except ValueError:
                pass
        if span and len(ds) == 2:
            out.append(("range", ds[0], ds[1]))
        else:
            out.extend(("day", d, d) for d in ds)
    return out


def buyer_dates(text: str, today: _dt.date | None = None) -> dict:
    """{site_visits: [dates], site_visit_contact, eoi_windows: [(start, end)]} found in the buyer's own sentences."""
    today = today or _dt.date.today()
    visits, windows, contact = [], [], ""
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        if re.search(r"\bsite visits?\b|\bvisits? (?:to|the) (?:the )?(?:site|premises|property)\b", sentence, re.I):
            for kind, a, b in _dates_in(sentence, today):
                if a not in visits:
                    visits.append(a)
            m = _BOOKED_WITH.search(sentence)
            if m and not contact:
                contact = ((m.group(1) or "") + m.group(2)).strip()
        if re.search(r"expressions? of interest|\bEOI\b", sentence, re.I):
            for kind, a, b in _dates_in(sentence, today):
                if kind == "range" and (a, b) not in windows:
                    windows.append((a, b))
    return {"site_visits": sorted(visits), "site_visit_contact": contact, "eoi_windows": windows}


def buyer_dates_block(text: str, today: _dt.date | None = None) -> str:
    """A note for the writers naming optional site visits and the EOI window, and which of them have gone."""
    today = today or _dt.date.today()
    d = buyer_dates(text, today)
    lines = []
    if d["site_visits"]:
        shown = ", ".join(f"{v.day} {v.strftime('%B %Y')}" + (" (passed)" if v < today else "") for v in d["site_visits"])
        who = f"; visits are booked with {d['site_visit_contact']}" if d["site_visit_contact"] else ""
        lines.append(f"- Site visits offered by the buyer: {shown}{who}. State these dates in the answer.")
    for a, b in d["eoi_windows"]:
        gone = " That window has PASSED: say so, do not write as if it is open, and flag that the bidder may need to ask the buyer whether late entry is allowed." if b < today else ""
        lines.append(f"- Expression of interest window: {a.day} {a.strftime('%B')} to {b.day} {b.strftime('%B %Y')}.{gone}")
    return ("\nBUYER DATES TO STATE (read from the buyer's documents):\n" + "\n".join(lines) + "\n") if lines else ""
