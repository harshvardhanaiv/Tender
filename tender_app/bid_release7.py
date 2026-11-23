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
                  "the commercial offer, so give no price, rate or total here. Give understanding of the requirement and the "
                  "team and roles their own sub-headings."),
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


def technical_sections(title: str = "", description: str = "", buyer_text: str = "") -> tuple:
    """(kind, guide text) naming the sub-headings a technical offer of this kind of contract needs. Release 12: one
    guide for every tender put "Ethics and data protection", "Accessibility (WCAG 2.2 AA)" and "Welsh language" into
    Epsom's WC refurbishment (Surrey) and dropped its method statement, water safety, health and safety and quality
    sections. Sections now follow the contract type (works / digital / consultancy) and the buyer's country."""
    from tender_app.bid_release5 import contract_type_note, buyer_in_wales
    kind, note = contract_type_note(title, description, buyer_text)
    if not kind:
        return ("", "")
    m = re.search(r"in this order, giving each the share of the words shown: (.*?)\. Leave social_value", note, re.S)
    listing = m.group(1) if m else ""
    items = [re.sub(r"^\d+\.\s*", "", x).strip() for x in listing.split("; ") if x.strip()]
    # Price goes in the commercial part, not the technical offer.
    items = [x for x in items if not re.match(r"(?:pricing|price)\b", x, re.I)]
    never = []
    if kind != "digital":
        never.append("accessibility (WCAG)")
    if kind != "consultancy":
        never.append("research ethics")
    if not buyer_in_wales(title, description, buyer_text[:6000]):
        never.append("Welsh language (the buyer is not a Welsh public body)")
    guide = ("Use these sub-headings, in this order, where the buyer's documents do not set their own: " + "; ".join(items) + ". "
             + ("Do NOT add sections for " + ", ".join(never) + ". " if never else ""))
    return (kind, guide)


_PRICE_DOC = {"name": "Price and commercial basis", "kind": "commercial"}


def submission_doc_questions(stage1: dict | None, fallback: bool = False, title: str = "", description: str = "",
                             buyer_text: str = "") -> list[dict]:
    """One pseudo-question per narrative submission document, in the order the buyer lists them, shaped like
    stage 1's itt_questions so the per-question writer, word budget and QA gate run unchanged. A price workbook the
    bidder fills in (Volume 3) is not written as prose, and a commercial offer submitted as its own file is skipped.
    With the title and buyer text, the technical offer names the sections this kind of contract needs (release 12), and
    a bid with no price document at all gets a price section, so the price is never silently dropped (Epsom)."""
    s1 = stage1 or {}
    docs = [d for d in (s1.get("required_submission_documents") or []) if isinstance(d, dict) and str(d.get("name") or "").strip()]
    if not docs and fallback:
        # The buyer's documents were read but the dissection listed no submission documents: still not the house
        # template. A technical offer against the specification, plus the forms the buyer asks to be signed.
        docs = [{"name": "Technical offer", "kind": "technical"}]
        if s1.get("forms_to_sign") or s1.get("certificates_required"):
            docs.append({"name": "Forms, declarations and certificates", "kind": "form"})
    separate = bool(s1.get("separate_technical_commercial"))
    ctype, tech_extra = technical_sections(title, description, buyer_text) if (title or description or buyer_text) else ("", "")
    if ctype and docs and not separate and not any(_kind(d) == "commercial" for d in docs):
        docs = list(docs) + [_PRICE_DOC]
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
        budget = None
        # Re-test 23 Nov (B1): the house sub-headings were part of question_text, so they were printed in the bid and replaced
        # the buyer's own structure (Thetford's Method Statement fell to 8% of the words). The guide now travels in its own
        # field, goes to the writer as private guidance and is dropped when the buyer's document sets its own sub-points.
        own_points = bool(d.get("sub_headings") or d.get("must_include") or d.get("sub_points"))
        if kind == "technical" and tech_extra and not own_points:
            guide = guide + " " + tech_extra
            budget = 2800
        elif ctype:
            budget = {"commercial": 700, "form": 600, "registration": 400}.get(kind)
        out.append({
            "budget_words": budget,
            "number": _number(d, i),
            "question_text": name,
            "writer_guide": guide,
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


def with_submission_questions(stage1: dict | None, title: str = "", description: str = "", buyer_text: str = "") -> dict:
    """A copy of stage 1 whose itt_questions are the submission documents, for extract_questions()."""
    out = dict(stage1 or {})
    out["itt_questions"] = submission_doc_questions(stage1, fallback=True, title=title, description=description,
                                                    buyer_text=buyer_text)
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
_CERT_HELD = re.compile(r"\b(certified|accredited|holds?|held|achieved|registered|compliant|certification|certificate|accreditation|"
                        # release 12: NHS Wales said "our ISO 27001-aligned ISMS" and "we meet Cyber Essentials Plus"
                        r"meets?|aligned|maintain\w*|operat\w*|in place|standards?|our|we have|yes)\b|✓", re.I)
# A certificate on file names its number, issuer or dates; a policy saying "we are working towards certification" does not.
_CERT_PROOF = r"(?:certificate (?:number|no\.?|of registration)|certified by|registration (?:number|no\.?)|valid (?:until|to|from)|expir\w+|issued (?:by|on)|UKAS|IASME)"
_REQUIRE = re.compile(r"(?:required|requires?|require[sd]?|requirement)", re.I)
_WE = re.compile(r"\b(?:we|our|us)\b", re.I)
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
        neg = _CERT_NEG.search(s)
        # "The Trust requires ISO 27001, which we hold" is still a claim (the bare word "requires" used to skip it).
        if neg and _REQUIRE.fullmatch(neg.group(0)) and _WE.search(s):
            neg = None
        if not _CERT_HELD.search(s) or neg:
            return s
        cert = re.search(_CERTS, s, re.I).group(1)
        ev = re.escape(cert).replace(r"\ ", r"\s?")
        # The proof must sit in the same sentence as the certificate's name, not anywhere within a few lines.
        if re.search(rf"{ev}[^.\n]{{0,80}}{_CERT_PROOF}|{_CERT_PROOF}[^.\n]{{0,80}}{ev}", evidence or "", re.I | re.S):
            return s
        count += 1
        tag = f" [CONFIRM: {cert} - only a policy or nothing is on file, not a certificate; check you hold it]"
        if s.strip().startswith("|") and s.rstrip().endswith("|"):
            return s.rstrip()[:-1].rstrip() + tag + " |"     # inside the last cell, so the table still renders
        return s.rstrip() + tag

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
            from tender_app.bid_writer import strip_house_brief
            new = strip_house_brief(new)       # the house sub-heading brief and its "(about N% of the words)" notes (B1)
            new = re.sub(r"\s+([.:])\s*$", r"\1", new)
            if new != v:
                q[key] = new
                n += 1
    return n


# ---- Release 10: the commercial part of a bid whose prices go in a separate file ---------------------------------------------------
_OANDA = re.compile(r"[^.\n]*\b(?:OANDA|exchange rate|currency conver\w+|converted to (?:GBP|USD|EUR))[^.\n]*[.\n]", re.I)


def commercial_pack_text(schedules, buyer_text: str = "", max_rows: int = 30, units: list | None = None) -> str:
    """Markdown for the commercial part: the buyer's line items, and any currency conversion rule the buyer states
    (Chemonics: OANDA rate). Nothing is priced by TenderFlow: a unit price comes only from the bidder (`units`, one list of
    prices per schedule from bid_prices.unit_prices); a row without one stays [NEEDS INPUT]. With a quantity, each priced
    row gets a line total and the schedule a total (release 12: Chemonics had 60 empty rows and nowhere to give prices)."""
    from tender_app.bid_prices import fmt_gbp, to_number
    lines = []
    for si, sch in enumerate((schedules or [])[:3]):
        header = [str(h) for h in (sch.get("header") or [])]
        all_rows = sch.get("rows") or []
        rows = [(ri, r) for ri, r in enumerate(all_rows) if any(str(c).strip() for c in r)]
        if not header or not rows:
            continue
        col = (units[si] if units and si < len(units) else None) or []
        qty_i = next((i for i, h in enumerate(header) if re.search(r"\b(qty|quantity|units?)\b", h, re.I)), None)
        keep = [i for i in range(len(header)) if not re.search(r"unit\s*price|price|rate|total|amount", header[i], re.I)][:3]
        if qty_i is not None and qty_i not in keep:
            keep.append(qty_i)
        priced_any = any(v is not None for v in col)
        with_total = priced_any and qty_i is not None
        cols = [header[i] or "Item" for i in keep] + ["Unit price (GBP)"] + (["Line total (GBP)"] if with_total else [])
        lines.append(f"**{sch.get('sheet') or 'Price schedule'}**")
        lines.append("| " + " | ".join(c.replace("|", "/") for c in cols) + " |")
        lines.append("|" + "---|" * len(cols))
        total, missing = 0.0, 0
        for ri, r in rows[:max_rows]:
            cells = [(str(r[i]) if i < len(r) else "").replace("|", "/")[:80] for i in keep]
            price = col[ri] if ri < len(col) else None
            qty = to_number(r[qty_i]) if qty_i is not None and qty_i < len(r) else None
            if price is None:
                missing += 1
                cells.append("[NEEDS INPUT: unit price]")
                if with_total:
                    cells.append("")
            else:
                cells.append(fmt_gbp(price))
                if with_total:
                    line = price * (qty if qty is not None else 1)
                    total += line
                    cells.append(fmt_gbp(line))
            lines.append("| " + " | ".join(cells) + " |")
        if with_total:
            lines.append("| " + " | ".join(["**Total (excluding VAT)**"] + [""] * (len(cols) - 2) + [f"**{fmt_gbp(total)}**"]) + " |")
            if missing:
                lines.append(f"\n{missing} line(s) still have no price, so the total is not complete yet.")
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


_COMM_HEAD = "## Commercial offer: price schedule"


def add_commercial_pack(bid_response: dict, schedules, buyer_text: str, units: list | None = None) -> bool:
    """Append the commercial part to the pricing section when the buyer wants prices in a separate file. With the
    bidder's prices (`units`) an existing commercial block is rebuilt in place, so prices can be given after the draft."""
    if not isinstance(bid_response, dict):
        return False
    block = commercial_pack_text(schedules, buyer_text, units=units)
    obj = bid_response.get("pricing_notes")
    if not isinstance(obj, dict):
        obj = bid_response["pricing_notes"] = {"content": ""}
    cur = str(obj.get("content") or "")
    if _COMM_HEAD in cur:
        if not units:
            return False
        start = cur.index(_COMM_HEAD)
        end = cur.find("\n## ", start + len(_COMM_HEAD))
        old_block = cur[start:end if end >= 0 else len(cur)]
        keep = re.search(r"^Currency: .*$", old_block, re.M)
        if keep and "Currency: " not in block:
            block += "\n" + keep.group(0)          # the buyer's currency rule, read when the draft was written
        obj["content"] = (cur[:start].rstrip() + "\n\n" + _COMM_HEAD + "\n" + block + ("\n\n" + cur[end + 1:] if end >= 0 else "")).strip()
        return True
    elif "[NEEDS INPUT: unit price" in cur and not units:
        return False
    obj["content"] = (cur + "\n\n" + _COMM_HEAD + "\n" + block).strip()
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
    # Re-test 23 Nov (Cornwall D1.4: 652 words against a limit of 560): an answer whose own record lost its limit (the draft's
    # question numbering differed, or the limit sat only in the buyer's list) takes the limit of the buyer's question of the
    # same number, then the limit written in its own question text.
    from tender_app.bid_release4 import limit_from_text as _lim_text
    buyer_limit = {}
    for bq in ((stage1 or {}).get("itt_questions") or []) if isinstance(stage1, dict) else []:
        if isinstance(bq, dict):
            try:
                lim = int(bq.get("word_limit") or 0) or _lim_text(str(bq.get("question_text") or bq.get("question") or ""))
            except (TypeError, ValueError):
                lim = 0
            num = re.sub(r"[^a-z0-9.]", "", str(bq.get("number") or bq.get("question_number") or "").lower())
            if lim and num:
                buyer_limit[num] = int(lim)
    n = 0
    for q in bid_response.get("itt_question_responses") or []:
        if not isinstance(q, dict):
            continue
        try:
            own = int(q.get("word_limit") or 0)
            if not own:
                num = re.sub(r"[^a-z0-9.]", "", str(q.get("question_number") or "").lower())
                own = buyer_limit.get(num) or int(_lim_text(str(q.get("question_text") or "")) or 0)
            limit = own or (general or 0)
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
