"""Release 8 (F2): what TenderFlow learns from the past bids chosen for a bid, and the answer plan built from it.

learn_style   -> numbers about how the past answers were written (sentence length, figures, tables, sub-headings, length)
style_brief   -> the sentence added to the drafting prompt
find_reusable -> a paragraph from a past answer on the same topic, with its source, every old fact marked [CONFIRM]
answer_plan   -> one row per buyer question: weight, words, sub-headings, reusable text

Pure functions over text; no database, no model call, no credits.
"""
import re
from collections import Counter

from tender_app.bid_style import _words, mark_reused_facts, normalise_outcome

_SEP = r"[.):\-–—]"
_HEAD = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:\*\*)?(?:"
    r"(?:Q(?:uestion)?\s*\d+[A-Za-z]{0,3}(?:\.\d+|\.[IVX]+)*\.?\s*" + _SEP + r"?)"
    r"|(?:\d{1,2}(?:\.\d{1,2})*" + _SEP + r")|(?:[A-Z]\d{1,2}(?:\.\d{1,2})*" + _SEP + r"))"
    r"(?:\*\*)?\s+\S.{2,110}$", re.I)
_MD_HEAD = re.compile(r"^\s*#{1,3}\s+\S.{2,110}$")
_FIGURE = re.compile(r"(?:GBP|£)\s?\d[\d,.]*|\b\d[\d,.]*\s?%|\b(?:19|20)\d{2}\b|\b\d[\d,]*\b")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[])")


def _paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def _is_subheading(line):
    s = line.strip()
    if re.match(r"^#{2,5}\s+\S", s) and len(s) <= 100:
        return True                     # a Word heading read back as Markdown (release 12)
    if re.fullmatch(r"\*\*[^*]{3,80}\*\*:?", s):
        return True
    if not s or len(s) > 80 or len(s.split()) > 10 or s.endswith((".", ",", ";")):
        return False
    if _HEAD.match(s) or "|" in s:
        return False
    return s.endswith(":") or s[0].isupper() and s.count(" ") <= 7 and not re.search(r"[a-z]\s+(?:is|are|was|were|will|has|have)\s", s)


_INLINE_Q = re.compile(r"(?:(?<=[.!?:])\s+|^|\s{2,}|(?<=[a-z0-9)|])\s+)(Q(?:uestion)?\s*(\d{1,2})(?:\.[\dIVXivx]+)*)\s*[.:)\-–—]?\s+(?=[A-Z])")


def _split_flat(text):
    """A past bid stored as one long line (Word text read without paragraphs, before release 12): split it at inline
    "Question 4 - ..." / "Q4." markers, taking each question number the first time it appears and in rising order, so
    "see Question 2" later in the text is not a new answer."""
    t = text or ""
    lines = [ln for ln in t.splitlines() if ln.strip()]
    if not lines or len(t.split()) / len(lines) < 150:
        return []
    marks, last = [], 0
    for m in _INLINE_Q.finditer(t):
        n = int(m.group(2))
        if n <= last or n > last + 3:
            continue
        before = t[max(0, m.start() - 12):m.start(1)].lower()
        if re.search(r"\b(?:see|in|to|under|and|at|our|of)\s*$", before):
            continue
        marks.append((m.start(1), m.end(), m.group(1)))
        last = n
    out = []
    for i, (start, body_start, label) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(t)
        body = t[body_start:end].strip()
        head = " ".join(body.split()[:8])
        if len(body.split()) >= 60:
            out.append({"label": f"{label} {head}"[:120], "text": body})
    return out


def _chunks(text, low=40, high=120):
    """Sentence groups of about low-high words, for an answer with no blank-line paragraphs."""
    out, cur = [], []
    for s in _SENT.split(text or ""):
        cur.append(s.strip())
        if len(" ".join(cur).split()) >= low:
            out.append(" ".join(cur))
            cur = []
    if cur and len(" ".join(cur).split()) >= 25:
        out.append(" ".join(cur))
    return [c for c in out if len(c.split()) <= high + 60]


def split_answers(text):
    """Split a past bid into its answers by numbered question headings. One whole answer when there are none."""
    lines = (text or "").splitlines()
    marks = [i for i, ln in enumerate(lines) if _HEAD.match(ln)]
    answers = []
    for n, start in enumerate(marks):
        end = marks[n + 1] if n + 1 < len(marks) else len(lines)
        body = "\n".join(lines[start + 1:end]).strip()
        if len(body.split()) >= 60:
            answers.append({"label": lines[start].strip()[:120], "text": body})
    if len(answers) < 2:
        # No numbered questions: split on Markdown headings, which is how a bid exported to Word and read back looks.
        mds = [i for i, ln in enumerate(lines) if _MD_HEAD.match(ln)]
        alt = []
        for n, start in enumerate(mds):
            end = mds[n + 1] if n + 1 < len(mds) else len(lines)
            body = "\n".join(lines[start + 1:end]).strip()
            if len(body.split()) >= 60:
                alt.append({"label": lines[start].strip("# ").strip()[:120], "text": body})
        if len(alt) >= 2:
            return alt
        flat = _split_flat(text)
        if len(flat) >= 2:
            return flat
        whole = (text or "").strip()
        return [{"label": "Whole document", "text": whole}] if len(whole.split()) >= 60 else []
    return answers


def _answer_stats(body):
    words = body.split()
    paras = _paragraphs(body)
    lines = [ln for ln in body.splitlines() if ln.strip()]
    # Sentence length from prose only: Word bids are now read with their headings and table rows kept, and those lines
    # have no full stop, so counting them merged them into the next sentence (a 3-line table read as one 40-word sentence).
    prose = " ".join(ln for ln in lines if "|" not in ln and not ln.lstrip().startswith("#") and not _is_subheading(ln))
    sentences = [s for s in _SENT.split(prose) if s.strip()] or [prose or body]
    prose_words = len(prose.split()) or len(words)
    heads = [ln.strip().strip("#* ").rstrip(":") for ln in lines[1:] if _is_subheading(ln)]
    return {
        "words": len(words),
        "words_per_sentence": prose_words / max(1, len(sentences)),
        "figures_per_100": 100.0 * len(_FIGURE.findall(body)) / max(1, len(words)),
        "has_table": any(ln.count("|") >= 2 or "\t" in ln for ln in lines),
        "subheadings": len(heads),
        "heads": heads,
        "opens_direct": bool(paras) and len(paras[0].split()) <= 80 and len(words) > 150,
    }


def learn_style(bids):
    """The style profile. Lost bids still teach layout and tone, so every bid given is read."""
    stats, titles = [], []
    for b in bids or []:
        found = split_answers(b.get("text", ""))
        if found:
            titles.append(b.get("title", ""))
        stats += [_answer_stats(a["text"]) for a in found]
    if not stats:
        return {"learned": False, "bids": [], "answers": 0}
    n = len(stats)
    heads = Counter(h.lower() for s in stats for h in s["heads"])
    return {
        "learned": True,
        "bids": titles,
        "answers": n,
        "words_per_sentence": round(sum(s["words_per_sentence"] for s in stats) / n),
        "figures_per_100": round(sum(s["figures_per_100"] for s in stats) / n, 1),
        "tables": sum(1 for s in stats if s["has_table"]),
        "subheadings": round(sum(s["subheadings"] for s in stats) / n, 1),
        "words_per_answer": round(sum(s["words"] for s in stats) / n),
        "opens_direct": sum(1 for s in stats if s["opens_direct"]) * 2 >= n,
        "common_headings": [h.capitalize() for h, c in heads.most_common(4) if c >= 2],
    }


def style_brief(profile):
    """One short paragraph for the drafting prompt. Empty when nothing was learned."""
    if not profile.get("learned"):
        return ""
    parts = []
    if profile["opens_direct"]:
        parts.append("Open each answer with a one-paragraph direct answer")
    if profile["subheadings"] >= 1:
        n_heads = max(1, round(profile["subheadings"]))
        parts.append("%s about %d sub-heading%s" % ("then" if parts else "Use", n_heads, "" if n_heads <= 1 else "s"))
    out = (", ".join(parts) + ". ") if parts else ""
    out += "Keep sentences near %d words. " % profile["words_per_sentence"]
    if profile["figures_per_100"] >= 1:
        out += "Back claims with figures (about %d per 100 words). " % round(profile["figures_per_100"])
    if profile["tables"] * 3 >= profile["answers"]:
        out += "Use a table for costs, risks and timelines. "
    return out.strip()


def find_reusable(question_text, bids, evidence_text="", min_overlap=2):
    """A paragraph from a past answer on the same topic, or None. Lost bids give no text."""
    want = _words(question_text)
    best = None
    for b in bids or []:
        if normalise_outcome(b.get("outcome")) == "lost":
            continue
        for a in split_answers(b.get("text", "")):
            if a["label"] == "Whole document":
                continue
            head_hit = len(want & _words(a["label"]))
            body_hit = len(want & _words(a["text"][:3000]))
            # A heading word in common plus one more anywhere in the answer (release 12: "Ethics, safeguarding and
            # data protection" did not match a past "EDI and safeguarding" answer that was about exactly that).
            if head_hit < 1 or head_hit + body_hit < min_overlap:
                continue
            score = 3 * head_hit + body_hit
            if best is None or score > best[0]:
                best = (score, b, a)
    if not best:
        return None
    score, b, a = best
    paras = [p for p in _paragraphs(a["text"]) if 25 <= len(p.split()) <= 160 and "|" not in p]
    if not paras:
        paras = _chunks(a["text"])
    if not paras:
        return None
    para = max(paras, key=lambda p: len(want & _words(p)))
    marked, facts = mark_reused_facts(para, para, evidence_text, (b.get("buyer", ""),))
    return {"paragraph": marked, "facts_to_confirm": facts, "source_bid": b.get("title", ""),
            "source_question": a["label"], "match": score}


def reuse_block(question_text, bids, evidence_text=""):
    """The prompt lines handing one question's writer the matching paragraph from a past bid. Empty when none matches."""
    if not bids:
        return ""
    try:
        r = find_reusable(question_text, bids, evidence_text)
    except Exception:  # noqa: BLE001 - reuse is optional
        return ""
    if not r:
        return ""
    return ("\n\nREUSABLE PARAGRAPH FOR THIS QUESTION (from past bid \"%s\", %s):\n%s\n"
            "Use it as the basis of the matching part of this answer and adapt it to this buyer and site. Keep every "
            "[CONFIRM: ... - from a past bid] marker exactly as written, and add the same marker to any other amount, "
            "date, count or name you take from it.\n" % (r["source_bid"], r["source_question"], r["paragraph"]))


_HOUSE_SECTIONS = [("Executive summary", 10), ("Understanding of the requirement", 15), ("Approach and methodology", 30),
                   ("Team and resources", 15), ("Quality, risk and compliance", 15), ("Social value", 5), ("Pricing basis", 10)]


def section_plan_questions(title, description=""):
    """When the buyer gave no questions: the sections TenderFlow will write for this kind of contract, shaped like
    extract_questions() rows (number, text, weight_pct=None, budget_words), so answer_plan can show them."""
    from tender_app.bid_release5 import contract_type_note
    kind, note = contract_type_note(title or "", description or "")
    sections = []
    m = re.search(r"in this order, giving each the share of the words shown: (.*?)\. Leave social_value", note or "", re.S)
    if m:
        for part in m.group(1).split("; "):
            pm = re.match(r"\d+\.\s*(.*?)\s*\(about (\d+)% of the words\)$", part.strip())
            if pm:
                sections.append((pm.group(1), int(pm.group(2))))
    if not sections:
        sections = _HOUSE_SECTIONS
    total = 6000
    return [{"number": "S%d" % i, "text": name, "weight_pct": None,
             "budget_words": max(150, round(total * pct / 100.0 / 10) * 10)} for i, (name, pct) in enumerate(sections, 1)]


def answer_plan(questions, profile, bids, evidence_text=""):
    """Rows for the plan table. `questions` come from bid_writer.extract_questions, in the buyer's order."""
    rows = []
    heads = " · ".join(profile.get("common_headings") or []) if profile.get("learned") else ""
    for q in questions or []:
        reuse = find_reusable(q.get("text", ""), bids, evidence_text) if profile.get("learned") or bids else None
        rows.append({
            "number": q.get("number", ""),
            "question": (q.get("text") or "")[:140],
            "weight_pct": q.get("weight_pct"),
            "words": q.get("budget_words"),
            "subheadings": heads or "Approach · Delivery · Summary",
            "reusable": reuse,
        })
    return rows
