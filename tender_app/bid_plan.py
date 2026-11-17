"""Internal bid plan: how to bid, which files to prepare and what each one must meet.

Built only from the stage 1 tender dissection (no extra model call), so every line traces to something the buyer's
documents said. Nothing is invented: a field the documents did not state is left out, not guessed.
"""
from __future__ import annotations

import re
from datetime import date, datetime

_NONE = re.compile(r"^\s*(?:none|n/?a|null|not stated|not specified|unknown|-+)?\s*$", re.I)


def _s(v) -> str:
    t = str(v).strip() if v is not None else ""
    return "" if _NONE.match(t) else re.sub(r"\s+", " ", t)


def _list(v) -> list:
    return [x for x in v if x] if isinstance(v, list) else []


def _dicts(v) -> list[dict]:
    return [x for x in _list(v) if isinstance(x, dict)]


def _parse_date(text: str):
    """First DD/MM/YYYY in the text, or None."""
    m = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", str(text or ""))
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _file(name, kind, rules=None, who="You prepare it", mandatory=True, source="") -> dict:
    return {"name": _s(name), "kind": kind, "rules": [r for r in (rules or []) if r], "who": who,
            "mandatory": bool(mandatory), "source": _s(source)}


def _format_rules(stage1: dict) -> list[str]:
    fr = stage1.get("format_requirements") if isinstance(stage1.get("format_requirements"), dict) else {}
    out = []
    for label, key in (("Structure", "structure"), ("Limits", "page_or_word_limits"), ("Header and footer", "header_footer"),
                       ("Case studies and appendices", "case_studies_and_appendices")):
        if _s(fr.get(key)):
            out.append(f"{label}: {_s(fr[key])}")
    out.extend(_s(x) for x in _list(fr.get("other")) if _s(x))
    return out


def build_files(stage1: dict) -> list[dict]:
    """Every file or piece of paperwork the bidder has to hand in, with the buyer's rules for it."""
    s = stage1 or {}
    files: list[dict] = []
    fmt = _format_rules(s)
    separate = bool(s.get("separate_technical_commercial"))
    named = _dicts(s.get("required_submission_documents"))
    questions = _dicts(s.get("itt_questions"))

    for d in named:
        kind = _s(d.get("kind")).lower() or "other"
        rules: list[str] = []
        if d.get("separate_file") or (separate and kind in ("technical", "commercial")):
            rules.append("Submit as its own file.")
        if kind == "technical":
            if separate:
                rules.append("No prices anywhere in it.")
            rules.extend(fmt)
        if kind == "commercial":
            if _s(s.get("payment_terms")):
                rules.append(f"Accept the buyer's payment terms: {_s(s['payment_terms'])}.")
            vi = (s.get("contract_value") or {}).get("vat_inclusive") if isinstance(s.get("contract_value"), dict) else None
            if vi is True:
                rules.append("Prices include VAT.")
            elif vi is False:
                rules.append("Prices exclude VAT.")
        who = "You fill in the buyer's template" if kind in ("form", "commercial") else "You prepare it"
        files.append(_file(d.get("name"), kind, rules, who))

    if questions and not any(f["kind"] == "technical" for f in files):
        limits = [f"Q{_s(q.get('number'))}: {int(q['word_limit'])} words" for q in questions
                  if isinstance(q.get("word_limit"), (int, float)) and q.get("word_limit")]
        rules = (["Answer every question in the buyer's numbering."] +
                 ([("Word limits: " + "; ".join(limits[:10]))] if limits else []) + fmt)
        files.append(_file("Answers to the buyer's questions", "technical", rules, "You write it",
                           source=f"{len(questions)} questions"))

    for f in _dicts(s.get("forms_to_sign")):
        sign = _s(f.get("who_must_sign"))
        files.append(_file(f.get("form_name"), "form",
                           [f"Signed by: {sign}." if sign else "Must be signed.", _s(f.get("notes"))],
                           "A signatory signs it", source=_s(f.get("reference"))))

    for c in _dicts(s.get("certificates_required")):
        mand = _s(c.get("mandatory_or_scored")).upper()
        rules = []
        if _s(c.get("minimum_level")):
            rules.append(f"Minimum: {_s(c['minimum_level'])}.")
        if c.get("expiry_required"):
            rules.append("Must be in date on the submission date.")
        if c.get("equivalent_accepted"):
            rules.append("An equivalent is accepted.")
        files.append(_file(c.get("certificate"), "certificate", rules, "Copy from your records",
                           mandatory=(mand != "DESIRABLE" and mand != "SCORED")))

    for i in _dicts(s.get("insurance_requirements")):
        rules = [f"Minimum cover: {_s(i['minimum_value'])}."] if _s(i.get("minimum_value")) else []
        files.append(_file((_s(i.get("type")) or "Insurance") + " insurance certificate", "insurance", rules,
                           "Ask your broker", mandatory=i.get("mandatory") is not False))

    for fr in _dicts(s.get("financial_requirements")):
        rules = [f"Evidence: {_s(fr['evidence'])}."] if _s(fr.get("evidence")) else []
        if _s(fr.get("threshold")):
            rules.append(f"Threshold: {_s(fr['threshold'])}.")
        files.append(_file(fr.get("requirement"), "financial", rules, "Your accountant or accounts"))

    refs = s.get("references_required") if isinstance(s.get("references_required"), dict) else {}
    if isinstance(refs.get("number_required"), (int, float)) and refs.get("number_required"):
        rules = [f"{int(refs['number_required'])} references."]
        for label, key in (("Type", "type"), ("Format", "format"), ("Minimum contract value", "minimum_contract_value"),
                           ("How recent", "recency_requirement")):
            if _s(refs.get(key)):
                rules.append(f"{label}: {_s(refs[key])}.")
        files.append(_file("Client references", "references", rules, "Your past clients agree to it"))

    sv = s.get("social_value_requirements") if isinstance(s.get("social_value_requirements"), dict) else {}
    if sv.get("required"):
        rules = ([f"Weighting: {_s(sv['weighting'])}."] if _s(sv.get("weighting")) else []) + \
                ([f"Themes: {', '.join(_s(t) for t in _list(sv.get('themes')))}."] if _list(sv.get("themes")) else []) + \
                (["Reporting is required."] if sv.get("reporting_required") else [])
        files.append(_file("Social value response", "social_value", rules, "You write it"))

    seen, out = set(), []
    for f in files:
        key = re.sub(r"\W+", " ", f["name"].lower()).strip()
        if f["name"] and key not in seen:
            seen.add(key)
            out.append(f)
    return out


def build_dates(stage1: dict, today: date | None = None) -> list[dict]:
    """Dated steps in order, with whether each has passed."""
    s = stage1 or {}
    today = today or datetime.now().date()
    rows: list[dict] = []

    def add(event, when, action, note=""):
        w = _s(when)
        if not w:
            return
        d = _parse_date(w)
        rows.append({"event": event, "date": w, "action": action, "note": _s(note),
                     "passed": bool(d and d < today), "_d": d})

    sd = s.get("submission_deadline") if isinstance(s.get("submission_deadline"), dict) else {}
    cd = s.get("clarification_deadline") if isinstance(s.get("clarification_deadline"), dict) else {}
    add("Last day to ask the buyer a question", cd.get("date"), True, cd.get("method"))
    for k in _dicts(s.get("key_dates")):
        add(_s(k.get("event")) or "Step", k.get("date"), bool(k.get("bidder_action")), k.get("contact"))
    t = " ".join(x for x in (_s(sd.get("time")), _s(sd.get("timezone"))) if x)
    add("Submission deadline", sd.get("date"), True, (t + (" via " + _s(sd.get("portal")) if _s(sd.get("portal")) else "")).strip())
    rows.sort(key=lambda r: (r["_d"] is None, r["_d"] or date.max))
    for r in rows:
        r.pop("_d", None)
    return rows


def build_requirements(stage1: dict) -> dict:
    """What decides whether the bid is valid and how it is scored."""
    s = stage1 or {}
    must = [{"requirement": _s(m.get("requirement")), "if_missed": _s(m.get("consequence_of_failure")),
             "evidence": _s(m.get("evidence_required"))}
            for m in _dicts(s.get("mandatory_requirements")) if _s(m.get("requirement"))]
    crit = [{"criterion": _s(c.get("criterion")),
             "weight": c.get("weighting_percent") if isinstance(c.get("weighting_percent"), (int, float)) else None,
             "type": _s(c.get("type")), "method": _s(c.get("scoring_method"))}
            for c in _dicts(s.get("evaluation_criteria")) if _s(c.get("criterion"))]
    rulings = [{"topic": _s(r.get("topic")), "ruling": _s(r.get("ruling"))}
               for r in _dicts(s.get("clarification_rulings")) if _s(r.get("ruling"))]
    excluded = [_s(x) for x in _list(s.get("excluded_suppliers_or_products")) if _s(x)]
    return {"mandatory": must, "scoring": crit, "buyer_rulings": rulings, "excluded": excluded,
            "tupe": s.get("tupe_applies") is True}


def build_steps(stage1: dict, files: list[dict], dates: list[dict], today: date | None = None) -> list[str]:
    """A short ordered how-to, in plain words."""
    s = stage1 or {}
    sd = s.get("submission_deadline") if isinstance(s.get("submission_deadline"), dict) else {}
    portal = _s(sd.get("portal"))
    steps: list[str] = []
    steps.append(f"Register or sign in on {portal} before you start, so the portal is ready on the day." if portal
                 else "Find out which portal or address the buyer wants the bid sent to, and register there first.")
    if any(r["action"] and not r["passed"] and r["event"] != "Submission deadline" for r in dates):
        steps.append("Do the dated actions in the timetable below (questions, site visits, samples) before their dates.")
    if _list(s.get("published_clarifications")) or _list(s.get("clarification_rulings")):
        steps.append("Read the buyer's published answers first: they override the original tender text.")
    n_q = len(_dicts(s.get("itt_questions")))
    if n_q:
        steps.append(f"Write answers to the {n_q} buyer questions within their word limits.")
    if any(f["kind"] == "commercial" for f in files):
        steps.append("Complete the price schedule in the buyer's own template and check the VAT basis.")
    if any(f["kind"] in ("form", "certificate", "insurance", "financial", "references") for f in files):
        steps.append("Gather the forms, certificates, insurance and references listed and check each is in date.")
    if any(f["kind"] == "form" and "sign" in " ".join(f["rules"]).lower() for f in files):
        steps.append("Get the forms signed by the named signatory.")
    if s.get("separate_technical_commercial"):
        steps.append("Keep the technical and commercial offers in separate files: no prices in the technical one.")
    steps.append("Check the pack against the file list, upload it and keep the portal's receipt.")
    return steps


def build_bid_plan(stage1: dict, today: date | None = None) -> dict:
    """The plan shown to the bidder. Safe on an empty or partial dissection."""
    s = stage1 if isinstance(stage1, dict) else {}
    files = build_files(s)
    dates = build_dates(s, today)
    deadline = next((r for r in dates if r["event"] == "Submission deadline"), None)
    warnings: list[str] = []
    if deadline and deadline["passed"]:
        warnings.append(f"The submission deadline ({deadline['date']}) has passed.")
    if s.get("separate_technical_commercial"):
        warnings.append("Technical and commercial offers must be separate files.")
    for amb in _dicts(s.get("ambiguities_detected"))[:4]:
        if _s(amb.get("issue")):
            warnings.append(f"Unclear in the documents ({_s(amb.get('section')) or 'general'}): {_s(amb['issue'])}")
    return {
        "title": _s(s.get("tender_title")),
        "buyer": _s(s.get("contracting_authority")),
        "reference": _s(s.get("tender_reference")),
        "steps": build_steps(s, files, dates, today),
        "files": files,
        "dates": dates,
        "requirements": build_requirements(s),
        "questions": [{"number": _s(q.get("number") or q.get("question_number")), "question_text": _s(q.get("question_text") or q.get("question")),
                       "weighting": q.get("weighting"), "word_limit": q.get("word_limit")} for q in _dicts(s.get("itt_questions"))[:60]],
        "warnings": warnings,
        "empty": not files and not dates,
    }
