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
                  "the commercial offer, so give no price, rate or total here."),
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
