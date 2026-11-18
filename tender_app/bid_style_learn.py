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
    if not s or len(s) > 80 or len(s.split()) > 10 or s.endswith((".", ",", ";")):
        return False
    if _HEAD.match(s) or "|" in s:
        return False
    return s.endswith(":") or s[0].isupper() and s.count(" ") <= 7 and not re.search(r"[a-z]\s+(?:is|are|was|were|will|has|have)\s", s)


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
        whole = (text or "").strip()
        return [{"label": "Whole document", "text": whole}] if len(whole.split()) >= 60 else []
    return answers


def _answer_stats(body):
    words = body.split()
    sentences = [s for s in _SENT.split(body) if s.strip()] or [body]
    paras = _paragraphs(body)
    lines = [ln for ln in body.splitlines() if ln.strip()]
    heads = [ln.strip().rstrip(":") for ln in lines[1:] if _is_subheading(ln)]
    return {
        "words": len(words),
        "words_per_sentence": len(words) / max(1, len(sentences)),
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
        parts.append("then about %d sub-heading%s" % (max(1, round(profile["subheadings"])), "" if round(profile["subheadings"]) <= 1 else "s"))
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
            if head_hit < 1 or head_hit + body_hit < min_overlap + 1:
                continue
            score = 3 * head_hit + body_hit
            if best is None or score > best[0]:
                best = (score, b, a)
    if not best:
        return None
    score, b, a = best
    paras = [p for p in _paragraphs(a["text"]) if 25 <= len(p.split()) <= 160]
    if not paras:
        return None
    para = max(paras, key=lambda p: len(want & _words(p)))
    marked, facts = mark_reused_facts(para, para, evidence_text, (b.get("buyer", ""),))
    return {"paragraph": marked, "facts_to_confirm": facts, "source_bid": b.get("title", ""),
            "source_question": a["label"], "match": score}


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
