"""Release 4 helpers (QA scorecard 13 Nov): buyer rulings, buyer entity, time zones, the bid writer's note,
grouped placeholders and word limits written into question text. Pure functions, no AI calls."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

_PLACEHOLDER = re.compile(r"\[(?!\d+\])(?![xX ]\])[^\[\]\n]{2,240}\](?!\()")

# ---- R4: a word limit written into the question text --------------------------------------------------------------

_LIMIT_TEXT = re.compile(
    r"(?:max(?:imum)?(?:\s+of)?|up to|not\s+(?:to\s+)?exceed(?:ing)?|no\s+more\s+than|limit(?:ed)?\s+(?:of|to)|within|under)\s*"
    r"(?:a\s+)?(\d{2,5})\s*words?|(\d{2,5})\s*words?\s*(?:max(?:imum)?|limit)", re.I)


def limit_from_text(text: str):
    """"maximum 500 words" in the question when the dissection left word_limit empty."""
    m = _LIMIT_TEXT.search(text or "")
    if not m:
        return None
    try:
        n = int(m.group(1) or m.group(2))
    except (TypeError, ValueError):
        return None
    return n if 20 <= n <= 20000 else None


# ---- R5: placeholders listed once with a count --------------------------------------------------------------------

def group_placeholders(found: list) -> list:
    """One row per distinct placeholder with a count and where it appears, e.g. "[CONFIRM: partner name] x 31"."""
    order: list = []
    by_tag: dict = {}
    for p in found:
        key = re.sub(r"\s+", " ", str(p.get("tag") or "")).strip().lower()
        if key not in by_tag:
            by_tag[key] = {"tag": p.get("tag"), "action": p.get("action"), "sections": [], "n": 0}
            order.append(key)
        g = by_tag[key]
        g["n"] += 1
        sec = p.get("section")
        if sec and sec not in g["sections"]:
            g["sections"].append(sec)
    out = []
    for key in order:
        g = by_tag[key]
        secs = g["sections"]
        where = ", ".join(secs[:4]) + (f" and {len(secs) - 4} more" if len(secs) > 4 else "")
        out.append({"section": where, "tag": f"{g['tag']} x {g['n']}" if g["n"] > 1 else g["tag"], "action": g["action"]})
    return out


# ---- N2: buyer rulings override the tender documents ----------------------------------------------------------------

def rulings_list(stage1) -> list:
    out: list = []
    s = stage1 or {}
    for c in (s.get("clarification_rulings") or []):
        if isinstance(c, dict) and str(c.get("ruling") or "").strip():
            out.append({"topic": str(c.get("topic") or "").strip(), "ruling": str(c["ruling"]).strip(),
                        "source": str(c.get("source") or "").strip()})
    for c in (s.get("published_clarifications") or []):
        if isinstance(c, dict) and str(c.get("answer") or "").strip():
            out.append({"topic": str(c.get("question") or "").strip(), "ruling": str(c["answer"]).strip(),
                        "source": str(c.get("source") or "").strip()})
    return out


def rulings_block(stage1, limit: int = 4000) -> str:
    items = rulings_list(stage1)
    if not items:
        return ""
    lines = [f"- {r['topic'][:200]}: {r['ruling'][:400]}" + (f" ({r['source']})" if r["source"] else "") for r in items]
    return ("BUYER RULINGS THAT OVERRIDE THE TENDER DOCUMENTS (a later published answer beats the original wording; "
            "apply each one, and where it changes the bid say \"as confirmed in the buyer's Q&A\"):\n"
            + "\n".join(lines))[:limit] + "\n"


_VAT_EXCL = re.compile(
    r"(?:exclusive of|exclud\w*|excl\.?|net of|before)\s+(?:all\s+)?(?:applicable\s+)?(?:local\s+)?(?:taxes?,?\s*)?(?:and\s+)?VAT"
    r"|VAT\s+(?:is\s+|are\s+)?(?:not\s+included|excluded)|(?:must\s+be\s+)?(?:quoted\s+)?(?:ex|exclusive)\s*[- ]?\s*VAT", re.I)
_VAT_INCL = re.compile(
    r"(?<!not )(?<!ex)(?:inclusive of|including|include[sd]?)\s+(?:all\s+)?(?:applicable\s+)?(?:local\s+)?(?:taxes?,?\s*)?"
    r"(?:and\s+)?(?:VAT|duties)|VAT\s+(?:is\s+|are\s+)?included", re.I)


def find_ruling_conflicts(rulings: list, texts: list) -> list:
    """[(section, detail)] where a draft contradicts a buyer ruling. Currently the VAT basis (Chemonics): the Q&A said
    prices exclude VAT while the draft said they include it."""
    out: list = []
    vat_rules = [r for r in rulings if re.search(r"\bVAT\b", f"{r.get('topic', '')} {r.get('ruling', '')}")]
    excl = any(_VAT_EXCL.search(r["ruling"]) and not _VAT_INCL.search(r["ruling"]) for r in vat_rules)
    if excl:
        for label, text in texts:
            clean = _PLACEHOLDER.sub(" ", text or "")
            m = _VAT_INCL.search(clean)
            if m and not _VAT_EXCL.search(clean[max(0, m.start() - 60): m.end() + 60]):
                out.append((label, 'Says prices include VAT ("' + clean[max(0, m.start() - 30): m.end() + 20].strip()
                            + "\") but the buyer's Q&A rules that prices exclude VAT."))
    return out


# ---- N4: buyer entity and addressee from the buyer's documents ------------------------------------------------------

def buyer_name(stage1, notice_name: str = "") -> str:
    doc = str((stage1 or {}).get("contracting_authority") or "").strip()
    if doc and not re.search(r"not stated|unknown|^n/?a$", doc, re.I):
        return re.sub(r"\s*\(.*$", "", doc).strip() or doc
    return (notice_name or "").strip()


def addressee(stage1) -> str:
    a = str((stage1 or {}).get("bid_addressee") or "").strip()
    if not a or re.search(r"not stated|unknown|^n/?a$|^null$", a, re.I):
        return ""
    # "Olha Brusniak, Project Officer, M.: +380 ..." -> name and role only: no phone, e-mail or label.
    a = re.split(r",?\s*(?:M|T|Tel|Mob|Mobile|Phone|Email|E-?mail)\b\.?\s*:", a, maxsplit=1, flags=re.I)[0]
    a = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "", a)
    a = re.sub(r"\+?\d[\d\s()-]{7,}", "", a)
    return a.strip(" ,;()")


# ---- N5: deadline with its time zone --------------------------------------------------------------------------------

_TZ_HOURS = {"GMT": 0, "UTC": 0, "BST": 1, "WET": 0, "CET": 1, "CEST": 2, "EET": 2, "EEST": 3, "MSK": 3,
             "EST": -5, "EDT": -4, "CST": -6, "PST": -8}


def _tz_offset(tz: str):
    t = (tz or "").upper().replace(" ", "")
    m = re.search(r"(?:GMT|UTC)([+-])(\d{1,2})(?::?(\d{2}))?", t)
    if m:
        h = int(m.group(2)) + (int(m.group(3)) / 60 if m.group(3) else 0)
        return h if m.group(1) == "+" else -h
    for k, v in _TZ_HOURS.items():
        if re.search(r"\b" + k + r"\b", t) or t == k:
            return v
    return None


def deadline_display(stage1) -> str:
    """"23/11/2026 18:00 GMT+2 (16:00 UK time)": the deadline with its zone, plus UK time when the zone differs."""
    sd = (stage1 or {}).get("submission_deadline")
    if not isinstance(sd, dict):
        return ""
    date, tm, tz = (str(sd.get(k) or "").strip() for k in ("date", "time", "timezone"))
    if not date or re.search(r"not stated", date, re.I):
        return ""
    tmatch = re.search(r"(\d{1,2})[:.](\d{2})", tm)
    hh = mm = None
    if tmatch:
        hh, mm = int(tmatch.group(1)), int(tmatch.group(2))
    elif re.search(r"noon", tm, re.I):
        hh, mm = 12, 0
    if hh is not None and re.search(r"p\.?m", tm, re.I) and hh < 12:
        hh += 12
    shown = date + (f" {hh:02d}:{mm:02d}" if hh is not None else (f" {tm}" if tm and not re.search(r"not stated", tm, re.I) else ""))
    if tz and not re.search(r"not stated", tz, re.I):
        shown += f" {tz}"
    off = _tz_offset(tz)
    try:
        if off is not None and hh is not None:
            d = datetime.strptime(date[:10], "%d/%m/%Y").replace(hour=hh, minute=mm)
            try:
                from zoneinfo import ZoneInfo
                uk = d.replace(tzinfo=ZoneInfo("Europe/London")).utcoffset().total_seconds() / 3600
            except Exception:
                uk = 0
            if abs(off - uk) > 1e-6:
                local_uk = d - timedelta(hours=off - uk)
                shown += f" ({local_uk.strftime('%H:%M')} UK time" + ("" if local_uk.date() == d.date() else ", " + local_uk.strftime("%d/%m/%Y")) + ")"
    except Exception:
        pass
    return shown


# ---- N3: structure by the documents the buyer asks for ---------------------------------------------------------------

def submission_documents(stage1) -> list:
    docs = (stage1 or {}).get("required_submission_documents") or []
    return [d for d in docs if isinstance(d, dict) and str(d.get("name") or "").strip()]


def submission_structure_note(stage1) -> str:
    names = submission_documents(stage1)
    if not names:
        return ""
    lines = [f"- {d['name']}" + (f" ({d.get('kind')})" if d.get("kind") else "") + (" - separate file" if d.get("separate_file") else "")
             for d in names]
    sep = bool((stage1 or {}).get("separate_technical_commercial"))
    return ("THE BUYER ASKS FOR THESE SUBMISSION DOCUMENTS (structure the response by them, in this order):\n" + "\n".join(lines)
            + ("\nTechnical and commercial offers must be SEPARATE: the technical offer contains no prices, rates or totals; "
               "prices go only in the commercial offer / the buyer's own price schedule.\n" if sep else "\n"))


# ---- R2 / R3 / N8: the bid writer's note ------------------------------------------------------------------------------

def key_actions(stage1) -> list:
    """N8: dated steps a bidder acts on (site visits, briefings, samples) with who to book with, from the dissection."""
    out = []
    for k in (stage1 or {}).get("key_dates") or []:
        if (isinstance(k, dict) and k.get("bidder_action") and str(k.get("event") or "").strip()
                and not re.search(r"submission deadline|deadline for submission", str(k.get("event")), re.I)):
            out.append(f"{k.get('event')}: {k.get('date') or 'date not stated'}"
                       + (f" - {k['contact']}" if str(k.get("contact") or "").strip() else ""))
    return out


def writer_note(decision, passed, stage1, tender_closed: bool = False) -> str:
    """"Bid writer's note (remove before submission)": verdict and gaps when fit is low, dates already passed, the
    buyer's clarification route, the deadline with its time zone and the dated actions."""
    lines: list = []
    d = decision or {}
    fit = d.get("fit_score")
    verdict = str(d.get("verdict") or "")
    if verdict and (verdict.lower() != "bid" or (isinstance(fit, (int, float)) and fit < 40)):
        lines.append(f"Advice: {verdict}" + (f" (fit {fit}/100)" if fit is not None else "") + ". " + str(d.get("summary") or "").strip())
        gaps = [str(g) for g in (d.get("capability_gaps") or []) if str(g).strip()]
        if gaps:
            lines.append("Not shown in your profile: " + "; ".join(gaps[:6]) + ".")
        route = str(d.get("partner_route") or d.get("recommended_route") or "").strip()
        lines.append("Route: " + (route or "bid only with a partner or subcontractor who holds the missing capability, "
                                   "or decline; the draft marks those parts [CONFIRM]."))
    for w in passed or []:
        lines.append("Already passed: " + str(w.get("detail") or w.get("kind") or "").strip())
    if tender_closed:
        lines.append("The submission deadline has passed. Check whether the buyer extended it before spending more time.")
    cd = (stage1 or {}).get("clarification_deadline")
    if isinstance(cd, dict) and cd.get("method") and not re.search(r"not stated|null|none", str(cd.get("method")), re.I):
        lines.append(f"The buyer takes clarifications by: {cd['method']}.")
    dd = deadline_display(stage1)
    if dd:
        lines.append(f"Submission deadline: {dd}.")
    acts = key_actions(stage1)
    if acts:
        lines.append("Dated actions for the bidder: " + "; ".join(acts[:8]) + ".")
    return "\n".join(x for x in lines if x.strip())
