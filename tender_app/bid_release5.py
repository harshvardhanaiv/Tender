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

_NUM_WORDS = {"five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
              "fifteen": 15, "twenty": 20, "twenty-five": 25, "thirty": 30}
_YEARS = re.compile(r"((?:over|more than|nearly|almost|around|some)\s+)?(\d{1,2}|" + "|".join(_NUM_WORDS) + r")\+?\s+years(?:['’])?\s+"
                    r"(?:of\s+)?(?:combined\s+|trading\s+|industry\s+|relevant\s+|hands-on\s+)?"
                    r"(?:experience|(?:in|within)\s+(?:the\s+)?(?:trade|industry|sector|business|field|plumbing|drainage|construction)|"
                    r"(?:of\s+)?(?:service|trading))", re.I)


def _years_value(raw: str) -> int:
    raw = raw.lower()
    return _NUM_WORDS[raw] if raw in _NUM_WORDS else int(raw)


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
    if not text or not isinstance(text, str):
        return text
    today = today or _dt.date.today()
    if incorporated is None:
        # No incorporation date on file or at Companies House: a long-experience claim is still never left unchecked
        # (Herts plumbing said "over 12 years' experience" three times, unflagged, for a company formed in 2024).
        def _fallback(m: re.Match) -> str:
            if _years_value(m.group(2)) < 8:
                return m.group(0)
            before = text[max(0, m.start() - 40):m.start()]
            if re.search(r"\b(?:team|staff|engineers?|founders?|directors?|people|personnel)\b[^.]{0,30}$", before, re.I):
                return m.group(0)
            return m.group(0) + "\x00"
        marked = _YEARS.sub(_fallback, text)
        at = marked.find("\x00")
        if at < 0:
            return text
        yrs = _years_value(_YEARS.search(text).group(2)) if _YEARS.search(text) else 0
        marked = marked.replace("\x00", "")
        end = _sentence_end(marked, at)
        return marked[:end] + (f" [CONFIRM: the company's records do not show how long it has traded; check that {yrs}+ years "
                               f"is the company's own history or say it is the team's experience]") + marked[end:]
    age = max(0, (today - incorporated).days // 365)
    flagged = False

    def fix(m: re.Match) -> str:
        nonlocal flagged
        years = _years_value(m.group(2))
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


_BANNED_BRANDS = ("Huawei", "ZTE", "Hytera", "Hikvision", "Dahua", "Kaspersky", "Nuctech", "DJI")
_BAN_CUE = re.compile(r"\b(?:prohibit\w*|ban(?:ned)?|exclu\w+|must not|shall not|will not be accepted|not (?:be )?(?:permitted|accepted|eligible)"
                      r"|restrict\w*|disqualif\w+|no equipment|do(?:es)? not accept|not offer\w*)\b", re.I)
_NET_TERMS = re.compile(r"\bnet\s*\d{1,3}(?:\s*days)?\b[^.\n]{0,80}|\b\d{1,3}\s*(?:calendar\s+|working\s+)?days\s+(?:from|of|after)\s+"
                        r"(?:the\s+)?(?:receipt|date)\s+of\s+(?:a\s+|an\s+|the\s+)?(?:correct\s+|valid\s+|undisputed\s+)?invoice[^.\n]{0,40}", re.I)


def fill_buyer_basics(stage1: dict | None, text: str) -> dict:
    """Fill what the model's extraction left empty, by plain text rules: excluded manufacturers named in a sentence
    that bans them, and the payment terms (QA release 5: the banned-manufacturer statement and NET 30 went missing).
    Never overwrites a value the extraction already has."""
    s = stage1 if isinstance(stage1, dict) else {}
    t = str(text or "")
    if not _clean_list(s.get("excluded_suppliers_or_products")) and t:
        found = []
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", t):
            if not _BAN_CUE.search(sentence):
                continue
            for b in _BANNED_BRANDS:
                if re.search(r"\b" + re.escape(b) + r"\b", sentence) and b not in found:
                    found.append(b)
        if found:
            s["excluded_suppliers_or_products"] = found
    cur = str(s.get("payment_terms") or "").strip()
    if (not cur or re.search(r"^(?:none|n/?a|null|not stated)$", cur, re.I)) and t:
        m = _NET_TERMS.search(t)
        if m:
            s["payment_terms"] = re.sub(r"\s+", " ", m.group(0)).strip(" ,;:")
    return s


def submission_rules_block(stage1: dict | None) -> str:
    """The buyer's submission rules as a short list for the writers: the files it wants and how they go in."""
    s = stage1 or {}
    lines = []
    docs = [d for d in (s.get("required_submission_documents") or []) if isinstance(d, dict) and d.get("name")]
    if docs:
        lines.append("- Documents to submit: " + "; ".join(
            f"{d['name']}{' (separate file)' if d.get('separate_file') else ''}" for d in docs[:12]))
    if s.get("separate_technical_commercial"):
        lines.append("- Technical and commercial offers go in SEPARATE files; the technical answer carries no prices.")
    si = s.get("submission_instructions") if isinstance(s.get("submission_instructions"), dict) else {}
    how = str(si.get("how_to_submit") or "").strip()
    if how and not re.search(r"not specified", how, re.I):
        lines.append("- How to submit: " + how[:400])
    return ("SUBMISSION RULES TO REPEAT IN THE BID (a short 'Submission and compliance' note):\n" + "\n".join(lines) + "\n") if lines else ""


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

# The buyer's portal can be read from the notice reference when the notice does not name it (Oldham: OLDH001-DN835252 is The Chest).
def portal_from_reference(*refs: str) -> str:
    """The portal a notice reference points to, or "" when it matches no known pattern. A Due North reference of the form
    <BUYER>-DN<digits> is the buyer's own Chest portal; a bare DN<digits> is ProContract."""
    for ref in refs:
        s = str(ref or "")
        if re.search(r"-DN\d+", s, re.I):
            return "The Chest (Due North)"
        if re.search(r"\bDN\d{5,}\b", s, re.I):
            return "ProContract (Due North)"
        if re.search(r"\bitt_\d+|\bPQQ_\d+", s, re.I):
            return "In-tend"
    return ""


def provisional_structure_note(questionnaire_mode: bool, stage1: dict | None) -> str:
    if questionnaire_mode:
        return ""
    sd = (stage1 or {}).get("submission_deadline")
    portal = str(sd.get("portal") or "").strip() if isinstance(sd, dict) else ""
    if re.search(r"not stated|unknown|^null$|^n/?a$", portal, re.I):
        portal = ""
    if not portal and isinstance(stage1, dict):
        portal = portal_from_reference(*(stage1.get(k) for k in ("reference", "notice_reference", "notice_id", "tender_reference", "ocid")))
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
    from tender_app.bid_writer import placeholder_counts, failed_sections
    open_items = [m.group(0) for _, t in bid_texts(bid_response or {}) for m in PLACEHOLDER_RE.finditer(t)
                  if not any(k in m.group(0).lower() for k in ("could not be generated", "cut off"))]
    counts = placeholder_counts(bid_response or {})
    if counts["total"]:
        distinct = []
        for t in open_items:
            if t not in distinct:
                distinct.append(t)
        parts = [f"{counts['input']} need input", f"{counts['confirm']} need confirming"]
        if counts["other"]:
            parts.append(f"{counts['other']} other gaps")
        if counts["failed"]:
            parts.append(f"{counts['failed']} failed section(s) to regenerate: " + ", ".join(failed_sections(bid_response)[:4]))
        out.append(f"{counts['total']} item(s) still need your input ({'; '.join(parts)})"
                   + (", for example: " + "; ".join(x[:90] for x in distinct[:3]) if distinct else ""))
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


# ---- release 6: a structure per contract type when no ITT was uploaded -------------------------------------------------
_WORKS_RE = re.compile(r"\b(plumb\w*|drain\w*|repair\w*|maintenance|refurbish\w*|building|construction|electrical|heating|boiler|"
                       r"roof\w*|cleaning|grounds|decorat\w*|joinery|mechanical|fabric|reactive|ppm|wc\b|toilet\w*)\b", re.I)
_DIGITAL_RE = re.compile(r"\b(platform|software|digital|saas|app(?:lication)?|portal|website|it\s+system|e-?learning|coaching platform|"
                         r"data\s+system|cloud|licen[cs]e|managed service)\b", re.I)
_WELSH_RE = re.compile(r"\b(wales|welsh|cymru|nwssp|sell2wales)\b", re.I)


_CONSULT_RE = re.compile(r"\b(evaluat\w*|consultan\w*|research|review|study|strategy|advisory|appraisal|assessment|"
                         r"audit|analysis|feasibility|engagement|facilitat\w*|interpretation plan|training)\b", re.I)
_PPM_RE = re.compile(r"\b(ppm|planned (?:preventive )?maintenance|servicing|inspection)\b", re.I)
_DRAIN_RE = re.compile(r"\b(drain\w*|sewer\w*|cctv|jetting|tanker|gully|gullies)\b", re.I)
_WATER_RE = re.compile(r"\b(plumb\w*|water|wc\b|toilet\w*|sanitary|legionella|boiler|heating|tmv|washroom)\b", re.I)


def contract_type_note(title: str, description: str = "", tender_text: str = "") -> tuple:
    """(kind, note): the sections a contract of this kind normally asks for. Used only when the buyer's questions were
    not found, so the draft does not fall back to the same ten house sections for every tender (release 5). Release 7
    adds consultancy / evaluation, a PPM, drainage and water-safety section where the scope has them, and a share of
    the words for each section."""
    head = f"{title or ''} {description or ''}"
    blob = head + " " + str(tender_text or "")[:6000]
    digital = len(_DIGITAL_RE.findall(head)) >= 1
    works = len(_WORKS_RE.findall(head)) >= 1
    consult = len(_CONSULT_RE.findall(head)) >= 1
    if digital and not works:
        sections = [("Proposed solution and how it meets the requirement", 25),
                    ("Implementation, onboarding and migration plan", 20),
                    ("Data protection and information security (UK GDPR; only the certifications evidenced, each [CONFIRM])", 15),
                    ("Accessibility (WCAG 2.2 AA) and usability", 10),
                    ("Support, service levels and account management", 15),
                    ("Pricing and licensing model", 15)]
        if _WELSH_RE.search(blob):
            sections.insert(3, ("Welsh language provision (the Welsh Language Standards apply to Welsh public bodies)", 8))
        kind = "digital"
    elif works:
        sections = [("Method of working and programme", 15)]
        if _PPM_RE.search(blob):
            sections.append(("Planned preventive maintenance (PPM): schedule, inspection regime, records and reporting", 20))
        sections.append(("Reactive and emergency response (response and fix times, out-of-hours cover, area covered from the bidder's base)", 15))
        if _DRAIN_RE.search(blob):
            sections.append(("Drainage service delivery: CCTV surveys, jetting, tankering, waste carriers licence and disposal records", 15))
        if _WATER_RE.search(blob):
            sections.append(("Water safety and plumbing compliance (Legionella and L8 risk management, TMVs, water hygiene, WRAS-approved materials; only qualifications the company data shows)", 10))
        sections += [("Health and safety (risk assessments, method statements, CDM, RIDDOR)", 10),
                     ("Staff, supervision, vetting and DBS/site access", 8),
                     ("Quality control, compliance and the qualifications actually held", 5),
                     ("Mobilisation and contract management", 5),
                     ("Pricing basis (rates, provisional sums, VAT treatment, what is excluded)", 5)]
        kind = "works"
    elif consult:
        sections = [("Understanding of the requirement and the outcomes sought", 12),
                    ("Approach, methodology and theory of change (what will change, evidence, how it is measured)", 25),
                    ("Work plan, milestones and timeline", 15),
                    ("Team, roles and any partner (name who does what; a missing specialism is a [NEEDS INPUT] or partner route)", 15),
                    ("Ethics, safeguarding, data protection and data-sharing agreement (UK GDPR; policies only if evidenced)", 15),
                    ("Risks and how they are managed", 8),
                    ("Social value and added value (only commitments the user gives)", 5),
                    ("Price: fee breakdown by stage or day rate, assumptions, expenses, and VAT treatment stated plainly", 5)]
        kind = "consultancy"
    else:
        return ("", "")
    total = sum(w for _s, w in sections) or 1
    listing = "; ".join(f"{i + 1}. {name} (about {round(100 * w / total)}% of the words)" for i, (name, w) in enumerate(sections))
    note = ("STRUCTURE FOR THIS KIND OF CONTRACT (the buyer's ITT is not available, so the draft is provisional): write the "
            "technical_methodology as these sections, each as a '### ' sub-heading, in this order, giving each the share of the words "
            "shown: " + listing
            + ". Leave social_value.content EMPTY unless the notice asks for social value, and keep relevant_experience to "
              "facts the company data evidences (otherwise one [NEEDS INPUT] line). Do not describe the company as 'fully insured', "
              "'experienced for N years' or 'public sector' unless the company data states it.\n")
    return (kind, note)


# ---- release 6: pricing wording, and Answer Bank claims the fit check says are not evidenced --------------------------
_FIXED_FEE = re.compile(r"\b(lump[- ]sum|fixed (?:fee|price|sum)|fixed[- ]price)\b", re.I)


def pricing_wording_block(text: str) -> str:
    """When the buyer's documents speak of a lump sum or fixed fee, the price answer must use the buyer's wording
    (Thetford: the fee structure was right but 'lump sum' never appeared)."""
    m = _FIXED_FEE.search(str(text or ""))
    if not m:
        return ""
    word = m.group(1).lower().replace("-", " ")
    return (f"PRICING WORDING: the buyer describes the price as a '{word}'. In the pricing answer state the fee as a "
            f"{word} in those words, say what it covers and what is excluded.\n")


_PUBLIC_CLAIM = re.compile(r"[^.\n]*\b(?:public[- ]sector|public and private sector|local (?:authorit|government)|NHS|council clients?)\b[^.\n]*\.?|"
                           r"[^.\n]*\bthousands of (?:projects|jobs|contracts)\b[^.\n]*\.?", re.I)
_NO_PUBLIC = re.compile(r"no (?:evidence|record|history|experience)[^.]{0,60}(?:public|government|council|NHS)|"
                        r"(?:public|government|council|NHS)[^.]{0,60}(?:not (?:evidenced|shown|stated)|no evidence|none)", re.I)


def flag_unevidenced_claims(bid_response: dict, decision: dict | None) -> int:
    """If the fit check says there is no public-sector history, mark each Answer Bank sentence that claims it with a
    [CONFIRM] so the bidder checks it before submitting. Returns how many sentences were marked."""
    d = decision or {}
    evidence = " ".join([str(d.get("summary") or "")] + [str(g) for g in (d.get("capability_gaps") or [])])
    if not _NO_PUBLIC.search(evidence):
        return 0
    n = 0
    for _label, obj, key in _each_text(bid_response):
        text = obj.get(key)
        if not isinstance(text, str) or "[CONFIRM: no public-sector" in text:
            continue

        def mark(m):
            nonlocal n
            s = m.group(0)
            if "[CONFIRM" in s:
                return s
            n += 1
            return s.rstrip() + " [CONFIRM: no public-sector work is evidenced in your profile - check this claim]"
        obj[key] = _PUBLIC_CLAIM.sub(mark, text)
    return n


def export_block_payload(bid_response: dict, blockers: list) -> dict:
    """The body of a refused final export: the blockers, one count split by type, and every open item grouped for
    a to-do list. The same numbers the readiness check and the download warning use (release 7)."""
    from tender_app.bid_writer import placeholder_counts
    items = []
    for label, text in bid_texts(bid_response or {}):
        for m in PLACEHOLDER_RE.finditer(text or ""):
            tag = m.group(0)
            low = tag.lower()
            kind = ("failed" if ("could not be generated" in low or "cut off" in low)
                    else "confirm" if low.startswith("[confirm") else "input" if low.startswith("[needs input") else "other")
            items.append({"section": label, "kind": kind, "tag": tag[:240]})
    return {"error": "This bid is not ready for a final export.", "code": "FINAL_EXPORT_BLOCKED",
            "blockers": (blockers or [])[:25], "counts": placeholder_counts(bid_response or {}), "items": items[:300]}
