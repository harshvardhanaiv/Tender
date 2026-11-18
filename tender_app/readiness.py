"""Release 8 (F1): the bid readiness score.

A score out of 100 for "is this draft complete and safe to send", with a to-do list ordered by the marks at stake, and a short
sign-off list for the things only the user can confirm. It is a readiness score, not a prediction of an evaluator's mark.
Built from what the pipeline already holds: the QA gate, the buyer's dissection (the TenderSpec) and the draft itself.
Pure functions; the server adds who ticked the sign-off list and when.
"""
import json
import re
from datetime import date, datetime

from tender_app.bid_writer import bid_texts, placeholder_counts, _as_pct, SECTION_LABELS

_FOREIGN_ID = re.compile(r"\b(EDRPOU|DUNS|NCAGE|CAGE code|UEI|GSTIN)\b")
PENALTY = {"P0": 12, "P1": 5, "P2": 1.5}
CAP = {"P0": 60, "P1": 25, "P2": 8}
_PAST_BID_CONFIRM = re.compile(r"\[CONFIRM:[^\]]*from a past bid\]", re.I)
_MONEY = re.compile(r"(?:GBP|£)\s?\d[\d,]*(?:\.\d+)?")
_OPEN_TAG = re.compile(r"\[(?:NEEDS INPUT|CONFIRM)[^\]]*\]", re.I)
_S = lambda v: str(v or "").strip()  # noqa: E731


def _qnum(s):
    return re.sub(r"[^a-z0-9.]", "", _S(s).lower())


def _expected_questions(stage1):
    out = []
    for q in (stage1 or {}).get("itt_questions") or []:
        if isinstance(q, dict) and _S(q.get("question_text") or q.get("question")):
            out.append({"number": _S(q.get("number") or q.get("question_number")), "pct": _as_pct(q.get("weighting"))})
    return out


def signoff_items(stage1):
    """What only the user can confirm before a final export. Keys are stable so a tick survives a re-run."""
    items = [{"key": "insurance", "text": "Insurance certificates are attached (public liability, employers' liability, professional indemnity where asked)."},
             {"key": "declarations", "text": "Every declaration and form is completed and signed by an authorised person."}]
    for d in ((stage1 or {}).get("required_submission_documents") or [])[:20]:
        if not isinstance(d, dict) or not _S(d.get("name")):
            continue
        kind = _S(d.get("kind")).lower()
        key = "doc-" + re.sub(r"[^a-z0-9]+", "-", _S(d.get("name")).lower()).strip("-")[:40]
        if kind == "commercial":
            items.append({"key": key, "text": "Price schedule completed in the buyer's format: %s." % _S(d.get("name"))})
        elif kind in ("form", "registration"):
            items.append({"key": key, "text": "Attached and completed: %s." % _S(d.get("name"))})
    seen, out = set(), []
    for it in items:
        if it["key"] not in seen:
            seen.add(it["key"])
            out.append(it)
    return out


def _deadline_issue(stage1, closed, today):
    dl = (stage1 or {}).get("submission_deadline")
    if closed:
        return ("P0", "The tender's stored deadline has passed.", "Deadline")
    if not isinstance(dl, dict) or not _S(dl.get("date")):
        return ("P1", "No submission deadline was found in the documents. Check the portal and confirm the date, time and time zone.", "Deadline")
    if not _S(dl.get("time")):
        return ("P1", "The deadline has a date (%s) but no time. Confirm the time and time zone on the portal." % _S(dl.get("date")), "Deadline")
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", _S(dl.get("date")))
    if m:
        try:
            if date(int(m.group(3)), int(m.group(2)), int(m.group(1))) < (today or date.today()):
                return ("P0", "The submission deadline (%s) has passed." % _S(dl.get("date")), "Deadline")
        except ValueError:
            pass
    return None


def readiness(bid_response, stage1, qa, closed=False, gate=False, signoffs=None, today=None):
    """Returns {limited, score, items, p0_open, counts, signoff, signoff_open, ready}. `signoffs` maps key -> {by, at}."""
    r = bid_response or {}
    s = stage1 if isinstance(stage1, dict) else {}
    expected = _expected_questions(s)
    have_buyer_docs = bool(expected or s.get("required_submission_documents"))
    items = []
    weights = {}
    for i, q in enumerate(r.get("itt_question_responses") or []):
        if isinstance(q, dict):
            weights["question " + _S(q.get("question_number") or i + 1).lower()] = _as_pct(q.get("weighting"))

    def add(sev, text, section, check, marks=None):
        items.append({"severity": sev, "text": text, "section": section, "check": check, "marks_pct": marks})

    for it in (qa or {}).get("issues", []):
        sec = it.get("section", "")
        if it.get("check") == "placeholders":
            continue                       # counted once below, from the same function the export gate uses
        add(it.get("severity", "P1"), it.get("detail", ""), sec, it.get("check", ""), weights.get(sec.lower()))

    if expected:
        got = [_qnum(q.get("question_number")) for q in (r.get("itt_question_responses") or []) if isinstance(q, dict)]
        want = [_qnum(q["number"]) for q in expected]
        missing = [q["number"] for q in expected if _qnum(q["number"]) not in got]
        if missing:
            add("P0", "Buyer question(s) with no answer: %s." % ", ".join(missing[:8]), "Questions", "questions_answered",
                max([q["pct"] or 0 for q in expected if q["number"] in missing] or [None]) or None)
        elif [g for g in got if g in want] != want:
            add("P1", "The answers are not in the buyer's order or numbering.", "Questions", "order")
    # A heavily weighted question that got far fewer words than its marks share (Herts: PPM worth 20% got 7% of the words).
    try:
        qwords = {}
        for lbl, txt in bid_texts(r):
            if lbl.startswith("Question"):
                qwords[lbl.lower()] = len(re.findall(r"\w+", _OPEN_TAG.sub(" ", txt or "")))
        total_w, total_m = sum(qwords.values()), sum(w for w in weights.values() if w)
        if total_w >= 400 and total_m > 0:
            for lbl, wds in qwords.items():
                m = weights.get(lbl)
                if m and m >= 15 and (wds / total_w) * 100 < 0.5 * (m / total_m) * 100:
                    add("P1", "It has only %d%% of the words for %g%% of the marks. Expand it." % (
                        round(wds * 100 / total_w), m), lbl.capitalize(), "word_share", m)
    except Exception:  # noqa: BLE001
        pass
    # A registration or tax number the buyer asks for from an overseas bidder (Chemonics: the Ukrainian EDRPOU) that the draft never supplies.
    try:
        ask = _FOREIGN_ID.findall(json.dumps(s, default=str))
        if ask:
            body = " ".join(t for _, t in bid_texts(r))
            for name in sorted({a.upper() for a in ask})[:3]:
                if not re.search(re.escape(name) + r"[^.\n]{0,60}(?:\d{5,}|\[(?:CONFIRM|NEEDS INPUT))", body, re.I):
                    add("P1", "The buyer asks for %s. Add your number to the declarations, or mark it [NEEDS INPUT: %s]." % (name, name),
                        "Declarations", "registration_id")
    except Exception:  # noqa: BLE001
        pass
    dl = _deadline_issue(s, closed, today)
    if dl and not (closed and any(i["check"] == "status" for i in items)):   # the QA gate already says it once
        add(dl[0], dl[1], dl[2], "deadline")

    counts = placeholder_counts(r)
    reused = sum(len(_PAST_BID_CONFIRM.findall(t)) for _, t in bid_texts(r))
    if reused:
        add("P1", "%d fact(s) reused from a past bid are still marked [CONFIRM]. Check each against this bid." % reused, "Reused text", "reused_facts")
    other_open = counts["total"] - reused
    if other_open > 0:
        add("P0" if gate else "P2",
            "%d item(s) still open: %d need input, %d need confirming%s." % (
                counts["total"], counts["input"], counts["confirm"], ", %d failed section(s)" % counts["failed"] if counts["failed"] else ""),
            "Whole bid", "placeholders")
    try:
        from tender_app.bid_release7 import repeated_headings
        rep = repeated_headings(bid_texts(r))
        if rep:
            add("P2", "The same sub-heading appears in more than one section (%s). Keep it in one section and point to it from the other." % "; ".join(
                "%s: %s" % (h, ", ".join(ls[:3])) for h, ls in rep[:3]), "Whole bid", "repeated_headings")
    except Exception:  # noqa: BLE001 - a check must never stop the score
        pass
    price_txt = " ".join(t for lbl, t in bid_texts(r) if re.search(r"pric|cost|commercial|fee", lbl, re.I))
    if price_txt:
        figures = _MONEY.findall(_OPEN_TAG.sub(" ", price_txt))
        if figures:
            add("P1", "The price section states %d money figure(s). Check each against your completed price schedule." % len(figures), "Price", "price_figures")

    # Where each item lives, and which can be written again on their own (release 10: "Go to" and "Regenerate this section").
    by_label = {label.lower(): key for key, label in SECTION_LABELS}
    for it in items:
        sec = _S(it["section"])
        m = re.match(r"question\s+(.+)$", sec, re.I)
        it["goto"] = sec
        if it["check"] in ("truncation", "coverage", "questions_answered", "placeholders") and m and it["check"] != "questions_answered":
            it["fix"] = {"question_number": m.group(1).strip()}
        elif it["check"] in ("truncation", "coverage") and sec.lower() in by_label and by_label[sec.lower()] not in ("declarations",):
            it["fix"] = {"section": by_label[sec.lower()]}

    order = {"P0": 0, "P1": 1, "P2": 2}
    items.sort(key=lambda x: (order.get(x["severity"], 3), -(x["marks_pct"] or 0)))
    for it in items:
        if it["marks_pct"]:
            it["text"] = "%s (%g%% of marks): %s" % (it["section"], it["marks_pct"], it["text"]) if it["section"].startswith("Question") else it["text"]
    p0_open = sum(1 for x in items if x["severity"] == "P0")

    sign = []
    for it in signoff_items(s):
        t = (signoffs or {}).get(it["key"]) or {}
        sign.append({**it, "ticked": bool(t), "by": t.get("by", ""), "at": t.get("at", "")})
    signoff_open = sum(1 for x in sign if not x["ticked"])

    if not have_buyer_docs:
        return {"limited": True, "score": None, "label": "Limited check: upload the ITT for a full score", "items": items,
                "p0_open": p0_open, "counts": counts, "signoff": sign, "signoff_open": signoff_open, "ready": False}
    penalty = 0.0
    for sev in PENALTY:
        penalty += min(CAP[sev], PENALTY[sev] * sum(1 for x in items if x["severity"] == sev))
    score = max(0, int(round(100 - penalty)))
    if p0_open:
        score = min(score, 69)   # a draft that cannot be submitted must not look nearly ready (Herts: 86 with a failed section)
    return {"limited": False, "score": score, "label": "Readiness score", "items": items, "p0_open": p0_open, "counts": counts,
            "signoff": sign, "signoff_open": signoff_open, "ready": p0_open == 0 and signoff_open == 0}
