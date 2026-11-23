"""Release 8 (F2/F4): per-bid control of which past bids shape a new bid, and the rules that keep their facts out.

Pure functions, no database. The server loads the account's library and calls these.
Modes: "auto" (closest bids, won count most), "library" (the bids the user ticked), "off".
"""
import re

OUTCOME_WEIGHT = {"won": 3.0, "unknown": 1.0, "lost": 0.5}
MODES = ("auto", "library", "off")
_WORD = re.compile(r"[a-z]{4,}")
_STOP = {"with", "that", "this", "from", "have", "will", "your", "their", "them", "were", "been", "into", "such", "than",
         "services", "service", "contract", "tender", "provide", "provision", "council", "framework"}
_ANON_RE = re.compile(r"anonymi[sz]ed|anonymous(?:ly)?|(?:must|should|shall)\s+not\s+(?:contain|include|identify|name)[^.]{0,80}"
                      r"(?:company|organisation|bidder|supplier|tenderer)|not\s+identify\s+(?:the\s+)?(?:bidder|tenderer|organisation)", re.I)
_ANON_QUALITY_RE = re.compile(r"(quality|technical|method)", re.I)


def normalise_outcome(value):
    v = str(value or "").strip().lower()
    if v in OUTCOME_WEIGHT:
        return v
    if v in ("win", "awarded", "success", "successful"):
        return "won"
    if v in ("loss", "unsuccessful", "not won"):
        return "lost"
    return "unknown"


def normalise_mode(value, library_size=0):
    v = str(value or "").strip().lower()
    if v in MODES:
        return v
    return "auto" if library_size else "off"


def _words(text):
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def rank_past_bids(library, tender_title, tender_text):
    """Score every library bid by topic overlap with this tender, scaled by outcome. Highest first."""
    want = _words(tender_title) | set(list(_words((tender_text or "")[:6000]))[:80])
    scored = []
    for b in library or []:
        have = _words(b.get("title", "")) | _words((b.get("text") or "")[:6000])
        overlap = len(want & have)
        title_hit = len(_words(tender_title) & _words(b.get("title", "")))
        outcome = normalise_outcome(b.get("outcome"))
        score = (overlap + 3 * title_hit) * OUTCOME_WEIGHT[outcome]
        scored.append((score, b))
    scored.sort(key=lambda p: (-p[0], -int(p[1].get("id") or 0)))
    return [b for s, b in scored]


_SECTORS = (
    ("Community venue", r"community (?:centre|center|hall|hub)|village hall|leisure centre|lease|venue|youth centre"),
    ("Health and care", r"\b(nhs|health|care home|hospital|clinical|gp|wellbeing|social care|patients?)\b"),
    ("Building works and maintenance", r"\b(plumb\w*|drain\w*|refurbish\w*|repairs?|maintenance|construction|roof\w*|electrical|heating|toilets?|wc)\b"),
    ("Digital and IT", r"\b(software|digital|platform|saas|app|website|data|cloud|it services?|cyber|dashboard|ai)\b"),
    ("Research and evaluation", r"\b(evaluat\w*|research|consultan\w*|review|study|appraisal|interpretation plan|feasibility)\b"),
    ("Education and training", r"\b(school|education|training|college|university|learning|pupils?|students?)\b"),
    ("Supplies and equipment", r"\b(supply|supplies|equipment|goods|laptops?|furniture|printing|signage|vehicles?)\b"),
)


def sector_of(bid):
    """A short sector label for a past bid from its title (then the start of its text), for the "Will use" list.
    Empty when nothing fits. Release 12: the re-test asked for the sector beside topic match and year."""
    b = bid or {}
    for source in (str(b.get("title") or ""), str(b.get("text") or "")[:3000]):
        best, hits = "", 0
        for name, pat in _SECTORS:
            n = len(re.findall(pat, source, re.I))
            if n > hits:
                best, hits = name, n
        if best:
            return best
    return ""


def topic_match(bid, tender_title):
    """Share of the tender title's words found in the past bid, as a whole percent (shown as "topic match" on the bid page).
    None when the title has no usable words."""
    want = _words(tender_title)
    if not want:
        return None
    have = _words((bid or {}).get("title", "")) | _words(((bid or {}).get("text") or "")[:20000])
    return round(100 * len(want & have) / len(want))


def choose_past_bids(mode, library, selected_ids, tender_title, tender_text, limit=3):
    """The bids to use for this one bid. 'library' keeps the user's ticks (up to 5); 'auto' picks the closest few."""
    mode = normalise_mode(mode, len(library or []))
    if mode == "off" or not library:
        return []
    if mode == "library":
        keep = {int(i) for i in selected_ids or [] if str(i).lstrip("-").isdigit()}
        return [b for b in library if int(b.get("id") or 0) in keep][:5]
    ranked = rank_past_bids(library, tender_title, tender_text)
    return ranked[:limit]


def past_bids_block(bids, max_chars=6000):
    """Prompt block. A lost bid gives layout and tone only; its text is not shown as reusable."""
    if not bids:
        return ""
    per_bid = max(1500, max_chars // len(bids))
    parts = []
    for i, b in enumerate(bids, 1):
        outcome = normalise_outcome(b.get("outcome"))
        meta = " | ".join(x for x in (b.get("buyer") or "", b.get("year") or "", outcome) if x)
        head = "[Past Bid %d: %s%s]" % (i, b.get("title", ""), " (" + meta + ")" if meta else "")
        if outcome == "lost":
            parts.append(head + "\n(Lost bid: use only its layout, headings and tone. Reuse no wording from it.)")
            continue
        text = b.get("text") or ""
        if len(text) > per_bid:
            text = text[:per_bid] + "\n[...truncated]"
        parts.append(head + "\n" + text)
    return ("\n\nPAST BIDS (style, structure and reusable wording where the topic matches):\n" + "\n\n".join(parts) +
            "\nPAST BID RULES: The buyer's questions, order, numbering and word limits always win; past bids only shape "
            "what goes inside an answer. Reuse wording only where the topic matches, and keep it short. Any amount, "
            "percentage, date, year, accreditation, count or other organisation's name taken from a past bid must be "
            "written as [CONFIRM: <value> - from a past bid]. Replace the old buyer's name with \"[the buyer]\". "
            "Do not reuse a fact that is not also in DATA / ANSWER BANK / CHECKLIST without that marker.")


_FACT = re.compile(r"(?:GBP|£)\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|m|million))?|\b\d{1,3}(?:\.\d+)?\s?%|\b(?:19|20)\d{2}\b|\b\d{1,3}(?:,\d{3})+\b")


_MONTH_BEFORE = re.compile(r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s*$|\d{1,2}[/.-]\d{1,2}[/.-]\s*$", re.I)


def _is_date_year(text, start):
    """True for a year that is part of a date in the bid ("31 January 2027", "15/12/2026"): a date is the buyer's timetable,
    not a fact carried over from a past bid (Re-test 23 Nov: Oldham's own work-plan dates were boxed "from a past bid")."""
    return bool(_MONTH_BEFORE.search(text[max(0, start - 14):start]))


def mark_reused_facts(text, past_text, evidence_text, buyer_names=(), today=None):
    """Mark facts that appear in the past bids but nowhere in this company's own evidence or the buyer's documents.
    Years inside a date, and years later than this year (the contract period), are never marked: a past bid cannot be the
    source of either. Returns (text, count)."""
    if not text or not past_text:
        return text or "", 0
    import datetime
    this_year = (today or datetime.date.today()).year
    past = set(m.group(0).strip() for m in _FACT.finditer(past_text))
    evidence = evidence_text or ""
    count = 0

    def sub(m):
        nonlocal count
        v = m.group(0).strip()
        if re.fullmatch(r"(?:19|20)\d{2}", v) and (int(v) > this_year or _is_date_year(text, m.start())):
            return m.group(0)
        tail = text[m.end():m.end() + 12]
        if v in past and v not in evidence and not tail.lstrip().startswith(("- from a past bid", "]")):
            count += 1
            return "[CONFIRM: %s - from a past bid]" % v
        return m.group(0)

    out = _FACT.sub(sub, text)
    for name in buyer_names or ():
        if name and len(name) > 3 and name in out:
            out = out.replace(name, "[the buyer]")
            count += 1
    return out, count


LOW_MATCH_PCT = 40


def past_bid_advice(bids, matches):
    """One plain sentence when the chosen past bids are a weak guide (Re-test 23 Nov: Oldham's only library bid was a 25% topic
    match with the outcome pending). Empty when there is nothing to say. `matches` maps bid id -> topic match %."""
    if not bids:
        return ""
    best = max([int(matches.get(b.get("id"), 0) or 0) for b in bids] or [0])
    won = any(normalise_outcome(b.get("outcome")) == "won" for b in bids)
    reasons = []
    if best < LOW_MATCH_PCT:
        reasons.append("the closest one matches this tender by only %d%%" % best)
    if not won:
        reasons.append("none is marked Won")
    if not reasons:
        return ""
    return ("Your past bids are a weak guide here: " + " and ".join(reasons) + ". Consider writing in the standard style "
            "(past bids off) so a poorly matched bid does not shape the answers.")


def asks_for_anonymised_answers(buyer_text):
    """True when the buyer's instructions ask for quality answers that do not identify the bidder."""
    t = buyer_text or ""
    for m in _ANON_RE.finditer(t):
        window = t[max(0, m.start() - 300): m.end() + 300]
        if _ANON_QUALITY_RE.search(window):
            return True
    return False


def resolve_branding(enabled, has_brand_kit, anonymise):
    """Per-bid branding switch. Anonymised answers override branding in the answers; cover page and pricing keep it."""
    on = bool(enabled) if enabled is not None else bool(has_brand_kit)
    on = on and bool(has_brand_kit)
    return {"enabled": on, "answers": on and not anonymise, "cover_and_pricing": on}


def per_bid_settings(body, library_size, has_brand_kit, buyer_text=""):
    """Settings stored on the bid, with the profile defaults for anything the request leaves out."""
    anon = asks_for_anonymised_answers(buyer_text) if body.get("anonymise_answers") is None else bool(body.get("anonymise_answers"))
    return {
        "branding_enabled": body.get("branding_enabled") if body.get("branding_enabled") is not None else bool(has_brand_kit),
        "past_bids_mode": normalise_mode(body.get("past_bids_mode"), library_size),
        "past_bid_ids": [i for i in (body.get("past_bid_ids") or body.get("winning_bid_ids") or [])][:5],
        "anonymise_answers": anon,
    }


_TEMPLATE_RE = re.compile(r"(?:must|should|shall)\s+(?:be\s+)?(?:submitted|completed|returned|provided)\s+(?:using|on|in)\s+(?:the\s+)?(?:attached|enclosed|buyer'?s|our|supplied|provided)?\s*"
                          r"(?:response\s+|answer\s+|submission\s+)?(?:template|form|proforma|pro forma)|"
                          r"do\s+not\s+(?:alter|amend|reformat|change)\s+(?:the\s+)?(?:template|format|layout)|use\s+the\s+(?:attached\s+)?response\s+template", re.I)
_FONT_RE = re.compile(r"\b(?:Arial|Calibri|Times New Roman|Verdana|Tahoma)\b[^.\n]{0,40}\b\d{1,2}\s?(?:pt|point)?|"
                      r"\b(?:minimum|min\.?)\s+font\s+size|\bfont\s+size\s+(?:of\s+)?(?:at least\s+)?\d{1,2}|\bfont\b[^.\n]{0,30}\b(?:must|shall)\b", re.I)


def branding_rules(format_requirements, buyer_text=""):
    """The buyer's own format rules decide branding (release 8, F4). Returns {anonymise, buyer_template, set_font, auto_off, reason}.
    Anonymised answers, a buyer template or set fonts switch branding off with a one-line reason; the user can switch it back on."""
    fr = format_requirements if isinstance(format_requirements, dict) else {}
    own = " ".join(str(fr.get(k) or "") for k in ("structure", "page_or_word_limits", "header_footer", "other"))
    other = fr.get("other")
    if isinstance(other, list):
        own = own.replace(str(other), " ") + " " + " ".join(str(x) for x in other)
    text = own + "\n" + (buyer_text or "")
    anon = asks_for_anonymised_answers(text)
    tmpl = bool(_TEMPLATE_RE.search(text))
    font = bool(_FONT_RE.search(own)) or bool(_FONT_RE.search(buyer_text or "")) and bool(re.search(r"\bfont\b", buyer_text or "", re.I))
    reasons = []
    if anon:
        reasons.append("the buyer asks for anonymised answers")
    if tmpl:
        reasons.append("the buyer supplies a template that must not be reformatted")
    if font:
        reasons.append("the buyer sets the font")
    return {"anonymise": anon, "buyer_template": tmpl, "set_font": font, "auto_off": bool(reasons),
            "reason": ("Branding is off because " + " and ".join(reasons) + ". Switch it back on if the buyer allows it.") if reasons else ""}
