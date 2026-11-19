"""Release 7: structure the bid by the documents the buyer asks for when it has no scored questionnaire.

Release 6 fell back to the generic house template for Chemonics (an RFQ with no questions) even though the ITT, DDQ
and price workbooks were uploaded. A tender whose buyer documents are present never uses the house template: when
there are no questions, the bid is structured by the buyer's required submission documents.
"""
from __future__ import annotations

import re
from typing import Any

from tender_app.bid_release5 import is_notice_only

_NUMBERED = re.compile(r"^\s*((?:\d+\.)*\d+|[A-Z]\d*(?:\.\d+)*|Annex\s*\d+|Appendix\s*[A-Z0-9]+)\b[\s.:)-]*", re.I)
_FILL_ONLY = re.compile(r"price schedule|pricing schedule|price table|bill of quantities|volume\s*3|commercial offer", re.I)
_FORM = re.compile(r"ddq|due diligence|questionnaire|declaration|form of tender|self[- ]cert|registration|certificate|annex|appendix|form", re.I)

_KIND_GUIDE = {
    "technical": ("Write the technical offer for this requirement against the buyer's specification, in the buyer's own item "
                  "order. Answer each requirement the documents state (items, quantities, standards, delivery, warranty, "
                  "support). Where the specification lists line items, address each by name or reference. Prices go only in "
                  "the commercial offer, so give no price, rate or total here. Where the scope involves them, give each its own "
                  "sub-heading and real content: understanding of the requirement, team and roles, ethics and data protection "
                  "(UK GDPR), accessibility (WCAG 2.2 AA) for digital work, and Welsh language provision when the buyer is a Welsh public body."),
    "commercial": ("Write the price statement the buyer asks for: the pricing basis, currency, VAT treatment exactly as the "
                   "buyer's clarification answers rule, what is included and excluded, validity period and payment terms as "
                   "the buyer states them. Give no figure that the bidder has not supplied: use [NEEDS INPUT: unit price] "
                   "or point to the completed price schedule."),
    "form": ("This is a form or questionnaire the buyer requires. List each item it asks for and, for each, the response "
             "or the evidence to attach. Every declaration, certification, registration number or statement about the "
             "bidder is a [CONFIRM: ...] or [NEEDS INPUT: ...] line for the bidder to complete. Assert nothing as met."),
    "registration": ("This is the registration or eligibility pack. List each document the buyer requires (for example "
                     "company registration, tax or national registration number, bank details form, authorised signatory) "
                     "and mark each as [NEEDS INPUT: ...] or [CONFIRM: ...]. Flag any registration the bidder may not hold "
                     "in the buyer's country."),
}
_DEFAULT_GUIDE = ("Provide this submission document as the buyer's documents describe it. Use only facts in the buyer's "
                  "documents, the company data and the answer bank; mark everything else [NEEDS INPUT: ...] or [CONFIRM: ...].")


def _kind(doc: dict) -> str:
    k = str(doc.get("kind") or "").lower().strip()
    name = str(doc.get("name") or "")
    if k in _KIND_GUIDE:
        return k
    if re.search(r"commercial|price|pricing|financial offer", name, re.I):
        return "commercial"
    if re.search(r"technical|method|specification|proposal", name, re.I):
        return "technical"
    if re.search(r"registration|eligibility", name, re.I):
        return "registration"
    if _FORM.search(name):
        return "form"
    return k or "other"


def _number(doc: dict, index: int) -> str:
    m = _NUMBERED.match(str(doc.get("name") or ""))
    if m and re.search(r"\d", m.group(1)):
        return m.group(1).strip()
    return str(index)


def submission_doc_questions(stage1: dict | None, fallback: bool = False) -> list[dict]:
    """One pseudo-question per narrative submission document, in the order the buyer lists them, shaped like
    stage 1's itt_questions so the per-question writer, word budget and QA gate run unchanged. A price workbook the
    bidder fills in (Volume 3) is not written as prose, and a commercial offer submitted as its own file is skipped."""
    s1 = stage1 or {}
    docs = [d for d in (s1.get("required_submission_documents") or []) if isinstance(d, dict) and str(d.get("name") or "").strip()]
    if not docs and fallback:
        # The buyer's documents were read but the dissection listed no submission documents: still not the house
        # template. A technical offer against the specification, plus the forms the buyer asks to be signed.
        docs = [{"name": "Technical offer", "kind": "technical"}]
        if s1.get("forms_to_sign") or s1.get("certificates_required"):
            docs.append({"name": "Forms, declarations and certificates", "kind": "form"})
    separate = bool(s1.get("separate_technical_commercial"))
    out: list[dict] = []
    seen: set[str] = set()
    for i, d in enumerate(docs, 1):
        name = str(d["name"]).strip()
        kind = _kind(d)
        low = name.lower()
        if low in seen:
            continue
        seen.add(low)
        if kind == "commercial" and (separate or d.get("separate_file") or _FILL_ONLY.search(name)):
            continue  # the buyer's own price workbook, filled in the commercial file
        guide = _KIND_GUIDE.get(kind, _DEFAULT_GUIDE)
        out.append({
            "number": _number(d, i),
            "question_text": f"{name}. {guide}",
            "word_limit": None,
            "page_limit": None,
            "weighting": None,
            "mandatory": True,
        })
    return out


def use_submission_structure(questions: list, stage1: dict | None, buyer_text: str) -> bool:
    """True when there are no scored questions, the buyer's own documents were read (not just the notice), and the
    buyer lists what to submit. In that case the draft is structured by those documents, never by the house template."""
    if questions and len(questions) >= 2:
        return False
    if not (buyer_text or "").strip() or is_notice_only(buyer_text):
        return False
    return bool(submission_doc_questions(stage1, fallback=True))


def with_submission_questions(stage1: dict | None) -> dict:
    """A copy of stage 1 whose itt_questions are the submission documents, for extract_questions()."""
    out = dict(stage1 or {})
    out["itt_questions"] = submission_doc_questions(stage1, fallback=True)
    return out


# ---- Step 5: claim and fit accuracy ------------------------------------------------------------------------------------

_GAS_SCOPE = re.compile(r"\b(gas|boilers?|appliances?|combustion|flue|lpg|heating (?:plant|systems?)|gas safe)\b", re.I)
_GAS_GAP = re.compile(r"gas safe", re.I)


def drop_irrelevant_trade_gaps(decision: dict | None, scope_text: str) -> int:
    """Gas Safe registration applies only to gas appliance work. Drop it from the fit check's capability gaps (and the
    sentences of its summary that name it) when the contract's own scope never mentions gas (Herts plumbing, release 6).
    Returns how many gaps or sentences were dropped."""
    if not isinstance(decision, dict) or _GAS_SCOPE.search(scope_text or ""):
        return 0
    n = 0
    gaps = decision.get("capability_gaps")
    if isinstance(gaps, list):
        kept = [g for g in gaps if not _GAS_GAP.search(str(g))]
        n += len(gaps) - len(kept)
        decision["capability_gaps"] = kept
    for key in ("summary", "advice", "route_to_bid"):
        v = decision.get(key)
        if isinstance(v, str) and _GAS_GAP.search(v):
            sentences = re.split(r"(?<=[.!?])\s+", v)
            kept_s = [x for x in sentences if not _GAS_GAP.search(x)]
            n += len(sentences) - len(kept_s)
            decision[key] = " ".join(kept_s)
    return n


_CERTS = r"(ISO\s?27001|ISO\s?9001|ISO\s?14001|ISO\s?45001|Cyber Essentials(?: Plus)?|CHAS|SafeContractor|Constructionline)"
_CERT_CLAIM = re.compile(r"[^.!?\n]*?\b" + _CERTS + r"\b[^.!?\n]*[.!?]?", re.I)
_CERT_HELD = re.compile(r"\b(certified|accredited|holds?|held|achieved|registered|compliant|certification|certificate|accreditation)\b", re.I)
_CERT_NEG = re.compile(r"\b(not held|not hold|do not hold|does not hold|no (?:current )?(?:iso|certif)|not certified|working towards|will obtain|"
                       r"must hold|required|require[sd]?|if you|partner|equivalent)\b|\[CONFIRM|\[NEEDS INPUT", re.I)


def confirm_certifications(text: str, evidence: str) -> tuple[str, int]:
    """A sentence that says the bidder holds a certification is marked [CONFIRM] unless the company's own records
    contain a certificate for it. A policy PDF is not a certificate (NHS Wales and Oldham stated ISO 27001 plainly)."""
    if not text:
        return text, 0
    count = 0

    def mark(m: re.Match) -> str:
        nonlocal count
        s = m.group(0)
        if not _CERT_HELD.search(s) or _CERT_NEG.search(s):
            return s
        cert = re.search(_CERTS, s, re.I).group(1)
        ev = re.escape(cert).replace(r"\ ", r"\s?")
        if re.search(rf"{ev}.{{0,100}}certificat|certificat.{{0,100}}{ev}", evidence or "", re.I | re.S):
            return s
        count += 1
        return s.rstrip() + f" [CONFIRM: {cert} - only a policy is on file, not a certificate; check you hold it]"

    return _CERT_CLAIM.sub(mark, text), count


def confirm_certifications_in_bid(bid_response: dict, evidence: str) -> int:
    from tender_app.bid_release5 import _each_text
    total = 0
    for _label, obj, key in _each_text(bid_response):
        v = obj.get(key)
        if isinstance(v, str):
            new, c = confirm_certifications(v, evidence)
            if c:
                obj[key] = new
                total += c
    return total


# ---- Release 9: the same sub-heading in two sections ---------------------------------------------------------------------
_HEADING_LINE = re.compile(r"^\s*(?:#{2,5}\s+|\*\*)(.{3,70}?)(?:\*\*)?\s*:?\s*$")
_GENERIC_HEADING = re.compile(r"^(summary|overview|introduction|approach|our approach|conclusion|next steps|assumptions?|risks?)$", re.I)


def repeated_headings(texts) -> list[tuple[str, list[str]]]:
    """Sub-headings that appear in two or more sections of a draft, as [(heading, [section labels])]. Herts section 7 repeated the
    sub-headings of section 4 (health and safety, staff, pricing basis, submission): the second one should point back instead."""
    seen: dict[str, tuple[str, list[str]]] = {}
    for label, text in texts or []:
        mine = set()
        for line in str(text or "").splitlines():
            m = _HEADING_LINE.match(line)
            if not m:
                continue
            h = re.sub(r"\s+", " ", m.group(1)).strip(" *#:")
            if len(h) < 3 or _GENERIC_HEADING.match(h):
                continue
            key = h.lower()
            if key in mine:
                continue
            mine.add(key)
            entry = seen.setdefault(key, (h, []))
            if label not in entry[1]:
                entry[1].append(label)
    return [(h, labels) for h, labels in seen.values() if len(labels) >= 2]


# ---- Release 10: the buyer's question is printed, never our drafting prompt -----------------------------------------------------
def strip_drafting_guides(bid_response: dict | None) -> int:
    """The submission-document questions carry a drafting guide after the document name ("Technical offer. Write the technical
    offer for this requirement ..."). Chemonics, Oldham and NHS Wales printed it in the bid. Keep the buyer's wording only."""
    if not isinstance(bid_response, dict):
        return 0
    guides = [g for g in list(_KIND_GUIDE.values()) + [_DEFAULT_GUIDE] if g]
    n = 0
    for q in bid_response.get("itt_question_responses") or []:
        if not isinstance(q, dict):
            continue
        for key in ("question_text", "response", "question"):
            v = q.get(key)
            if not isinstance(v, str):
                continue
            new = v
            for g in guides:
                head = g[:60]
                i = new.find(head)
                if i >= 0:
                    j = i + len(g)
                    new = (new[:i].rstrip() + " " + new[j:].lstrip()).strip() if new[j:j + 1] else new[:i].rstrip()
            new = re.sub(r"\s+([.:])\s*$", r"\1", new)
            if new != v:
                q[key] = new
                n += 1
    return n


# ---- Release 10: the commercial part of a bid whose prices go in a separate file ---------------------------------------------------
_OANDA = re.compile(r"[^.\n]*\b(?:OANDA|exchange rate|currency conver\w+|converted to (?:GBP|USD|EUR))[^.\n]*[.\n]", re.I)


def commercial_pack_text(schedules, buyer_text: str = "", max_rows: int = 30) -> str:
    """Markdown for the commercial part: the buyer's line items with a unit-price cell left as [NEEDS INPUT], and any currency
    conversion rule the buyer states (Chemonics: OANDA rate). Nothing is priced here; the figures come from the bidder."""
    lines = []
    for sch in (schedules or [])[:3]:
        header = [str(h) for h in (sch.get("header") or [])]
        rows = [r for r in (sch.get("rows") or []) if any(str(c).strip() for c in r)]
        if not header or not rows:
            continue
        qty_i = next((i for i, h in enumerate(header) if re.search(r"\b(qty|quantity|units?)\b", h, re.I)), None)
        keep = [i for i in range(len(header)) if not re.search(r"unit\s*price|price|rate|total|amount", header[i], re.I)][:3]
        if qty_i is not None and qty_i not in keep:
            keep.append(qty_i)
        cols = [header[i] or "Item" for i in keep] + ["Unit price (GBP)"]
        lines.append(f"**{sch.get('sheet') or 'Price schedule'}**")
        lines.append("| " + " | ".join(c.replace("|", "/") for c in cols) + " |")
        lines.append("|" + "---|" * len(cols))
        for r in rows[:max_rows]:
            cells = [(str(r[i]) if i < len(r) else "").replace("|", "/")[:80] for i in keep]
            lines.append("| " + " | ".join(cells) + " | [NEEDS INPUT: unit price] |")
        if len(rows) > max_rows:
            lines.append(f"\n{len(rows) - max_rows} more line(s) are in the buyer's workbook: complete them there.")
        lines.append("")
    if not lines:
        lines.append("The buyer's price schedule is completed in its own workbook. [NEEDS INPUT: unit price for each line item]")
    m = _OANDA.search(buyer_text or "")
    if m:
        lines.append("Currency: " + re.sub(r"\s+", " ", m.group(0)).strip()
                     + " [CONFIRM: state the rate source and the date you used when converting to GBP]")
    return "\n".join(lines).strip()


def add_commercial_pack(bid_response: dict, schedules, buyer_text: str) -> bool:
    """Append the commercial part to the pricing section when the buyer wants prices in a separate file."""
    if not isinstance(bid_response, dict):
        return False
    block = commercial_pack_text(schedules, buyer_text)
    obj = bid_response.get("pricing_notes")
    if not isinstance(obj, dict):
        obj = bid_response["pricing_notes"] = {"content": ""}
    cur = str(obj.get("content") or "")
    if "[NEEDS INPUT: unit price" in cur:
        return False
    obj["content"] = (cur + "\n\n## Commercial offer: price schedule\n" + block).strip()
    return True


# ---- Release 10: a hard word limit is enforced after every later edit -------------------------------------------------------------
_PER_ANSWER = re.compile(r"(\d{2,5})\s*words?\s*(?:per|for each|each|maximum per|max(?:imum)? per)\s*(?:answer|question|response|section)|"
                         r"(?:answers?|questions?|responses?)\s*(?:are |is )?(?:limited to|capped at|max(?:imum)?(?: of)?)\s*(\d{2,5})\s*words?", re.I)


def enforce_word_limits(bid_response: dict | None, stage1: dict | None = None) -> int:
    """Cornwall College D1.4 ran 614 and D1.2 561 words against 500: the limit was applied before later steps (tags, notes) grew
    the text, or was never read onto the question. Applies the buyer's limit last, using the question's own, else a per-answer limit
    in the buyer's format requirements. Returns how many answers were cut."""
    from tender_app.bid_writer import answer_words, trim_to_words
    if not isinstance(bid_response, dict):
        return 0
    fmt = (stage1 or {}).get("format_requirements") if isinstance(stage1, dict) else None
    blob = " ".join(str(v) for v in (fmt or {}).values()) if isinstance(fmt, dict) else ""
    m = _PER_ANSWER.search(blob)
    general = int(m.group(1) or m.group(2)) if m else None
    n = 0
    for q in bid_response.get("itt_question_responses") or []:
        if not isinstance(q, dict):
            continue
        try:
            limit = int(q.get("word_limit") or 0) or (general or 0)
        except (TypeError, ValueError):
            limit = general or 0
        text = str(q.get("response") or "")
        if limit and answer_words(text) > limit:
            q["response"] = trim_to_words(text, limit)
            q["word_count"] = answer_words(q["response"])
            q["word_limit"] = limit
            q["trimmed_to_limit"] = True
            n += 1
    return n


# ---- Release 10: a heading that belongs to the next question is not left at the end of this one (Thetford D / E) -------------------
_Q_HEAD = re.compile(r"^\s*(?:#{1,5}\s*|\*\*)?(?:Question\s+)?([A-Z]\d{0,2}(?:\.\d+)*|\d+(?:\.\d+)*)[.):\s]", re.I)


def fix_spilled_question_headings(bid_response: dict | None) -> int:
    """Moves a trailing line like "## E. Mobilisation" that sits at the end of question D's answer to the start of E's answer, and
    drops it when E already opens with its own heading. Returns how many were moved."""
    if not isinstance(bid_response, dict):
        return 0
    qs = [q for q in (bid_response.get("itt_question_responses") or []) if isinstance(q, dict)]
    nums = [str(q.get("question_number") or "").strip().lower().rstrip(".") for q in qs]
    moved = 0
    for i, q in enumerate(qs[:-1]):
        lines = str(q.get("response") or "").rstrip().splitlines()
        j = len(lines) - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        if j < 0:
            continue
        m = _Q_HEAD.match(lines[j])
        if not m or len(lines[j]) > 120:
            continue
        tag = m.group(1).lower().rstrip(".")
        if tag == nums[i] or tag not in nums[i + 1:]:
            continue
        head = lines[j].strip()
        q["response"] = "\n".join(lines[:j]).rstrip()
        nxt = qs[nums.index(tag, i + 1)]
        body = str(nxt.get("response") or "").lstrip()
        if not _Q_HEAD.match(body.splitlines()[0] if body else ""):
            nxt["response"] = head + "\n\n" + body
        moved += 1
    return moved


# ---- Release 11: questions the dissection missed, and documents for another lot -----------------------------------------------------
_LOT = re.compile(r"\blot\s*(\d+)\b", re.I)
_Q_LINE = re.compile(r"^\s*(?:Question\s+)?([A-Z]\d{1,2}(?:\.\d{1,2})+)\s*[.:)–—-]?\s+(.{25,500})$")
_Q_VERB = re.compile(r"\b(describe|explain|provide|detail|demonstrate|outline|state|confirm|how|what|please)\b", re.I)


def recover_questions(buyer_text: str, minimum: int = 3) -> list[dict]:
    """Cornwall College lost its D1.1 to D1.4 quality questions when the dissection returned none. Reads lettered, numbered
    question lines (D1.1, D1.2 ...) straight from the buyer's text. Needs `minimum` of them, in one letter family."""
    found: dict[str, str] = {}
    for line in str(buyer_text or "").splitlines():
        m = _Q_LINE.match(line)
        if m and _Q_VERB.search(m.group(2)) and m.group(1) not in found:
            found[m.group(1)] = m.group(2).strip()
    fam: dict[str, list[str]] = {}
    for num in found:
        fam.setdefault(num[0], []).append(num)
    best = max(fam.values(), key=len) if fam else []
    if len(best) < minimum:
        return []
    return [{"number": n, "question_text": found[n], "weighting": None} for n in best]


def drop_other_lot_documents(stage1: dict | None, title_text: str) -> int:
    """A bid for Lot 2 must not be structured by "Technical offer - Lot 1" documents. Removes submission documents that name a
    different lot from the one in the tender's title. Returns how many were removed."""
    if not isinstance(stage1, dict):
        return 0
    mine = _LOT.search(str(title_text or ""))
    docs = stage1.get("required_submission_documents")
    if not mine or not isinstance(docs, list):
        return 0
    keep = [d for d in docs if not (isinstance(d, dict) and (lm := _LOT.search(str(d.get("name") or ""))) and lm.group(1) != mine.group(1))]
    removed = len(docs) - len(keep)
    if removed:
        stage1["required_submission_documents"] = keep
    return removed
