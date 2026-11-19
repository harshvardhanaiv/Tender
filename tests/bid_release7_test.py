"""Release 7 checks: structure by submission documents (Chemonics), templates by contract type (consultancy, works),
financial-question brief (Matthewsgreen Q4). Plain asserts; run: python tests/bid_release7_test.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_app import bid_release5 as r5
from tender_app import bid_release7 as r7
from tender_app import financial_model as fm
from tender_app.bid_writer import extract_questions, questionnaire_mode

n = 0


def ok(cond, label):
    global n
    n += 1
    assert cond, "FAILED: " + label


LONG = "Tender documents. " * 400
CHEM = {"itt_questions": [], "separate_technical_commercial": True, "required_submission_documents": [
    {"name": "5.1 Technical offer", "kind": "technical", "separate_file": True},
    {"name": "5.3 Commercial offer (Volume 3 price schedule)", "kind": "commercial", "separate_file": True},
    {"name": "Annex 1 cover letter", "kind": "form"},
    {"name": "DDQ", "kind": "form"},
    {"name": "Registration pack", "kind": "registration"}]}

# ---- Step 1: structure by the buyer's submission documents -------------------------------------------------------------
qs = r7.submission_doc_questions(CHEM)
names = [q["question_text"].split(".")[0] for q in qs]
ok(len(qs) == 4, "the priced Volume 3 workbook is not written as prose: %s" % names)
ok(qs[0]["number"] == "5.1", "the buyer's own numbering is kept: %s" % qs[0]["number"])
ok("no price" in qs[0]["question_text"].lower(), "the technical offer carries no prices")
ok(any("DDQ" in q["question_text"] for q in qs) and any("registration" in q["question_text"].lower() for q in qs), "DDQ and registration sections exist")
ok(r7.use_submission_structure([], CHEM, LONG), "uploaded ITT with no questions uses the submission structure")
ok(not r7.use_submission_structure([], CHEM, ""), "no buyer text: no submission structure")
ok(not r7.use_submission_structure([], CHEM, "Documents are available on the Atamis portal after registration."), "a notice alone does not")
two = [{"number": "1", "question_text": "a"}, {"number": "2", "question_text": "b"}]
ok(not r7.use_submission_structure(two, CHEM, LONG), "real questions win over submission documents")
ok(r7.use_submission_structure([], {"itt_questions": []}, LONG), "buyer documents with nothing listed still do not fall back to the house template")
built = extract_questions(r7.with_submission_questions(CHEM))
ok(len(built) == 4 and all(q["budget_words"] >= 250 for q in built), "extract_questions gives each a word budget")
ok(questionnaire_mode(built), "four sections run in questionnaire mode")

# ---- Step 2: templates by contract type ---------------------------------------------------------------------------------
k, note = r5.contract_type_note("Oldham Council - Live Well Evaluation", "Evaluation of the Live Well programme")
ok(k == "consultancy", "Oldham is a consultancy: %s" % k)
for want in ("theory of change", "Work plan", "Ethics", "data-sharing agreement", "VAT"):
    ok(want.lower() in note.lower(), "consultancy note has %s" % want)
k, note = r5.contract_type_note("Hertfordshire Police plumbing PPM and reactive repairs", "Planned preventive maintenance of plumbing")
ok(k == "works" and "PPM" in note and "Legionella" in note and "Reactive" in note, "plumbing PPM gets PPM, reactive and Legionella sections")
ok("% of the words" in note, "each section has a word share")
k, note = r5.contract_type_note("Herts Police drainage maintenance", "drain clearance and CCTV surveys")
ok(k == "works" and "Drainage service delivery" in note and "PPM" not in note.split("Method")[1][:60], "drainage gets a delivery section")
k, note = r5.contract_type_note("Epsom & St Helier WC refurbishment", "Refurbishment of toilets")
ok(k == "works" and "Water safety" in note, "WC refurbishment gets water safety")
k, _ = r5.contract_type_note("NHS Wales coaching platform RFQ", "Digital coaching platform for NWSSP")
ok(k == "digital", "digital still digital")

# ---- Step 3: the financial question's brief -----------------------------------------------------------------------------
S1 = {"special_conditions": [{"term": "Sinking fund: operator contributes GBP 2,500 per annum"}],
      "published_clarifications": [{"question": "Utilities?", "answer": "The operator pays 16% of drains, plant and gas costs.", "source": "Clarification log 6 Nov"}]}
q4 = {"number": "4", "text": "Financial sustainability: provide a business plan with income and costs for the term", "weight_pct": 40.0}
q7 = {"number": "7", "text": "Describe your safeguarding policy", "weight_pct": 10.0}
ok(fm.is_heavy_financial(q4) and not fm.is_heavy_financial(q7), "only the 40% financial question is heavy")
ok(not fm.is_heavy_financial({"number": "4", "text": q4["text"], "weight_pct": 10.0}), "a light financial question is not heavy")
blk = fm.financial_question_block(q4, S1, False)
ok("2,500" in blk and "16%" in blk, "the buyer's published figures are listed: %s" % blk)
ok("[NEEDS INPUT" in blk and "Do not state a surplus" in blk, "without a model, no outcome may be stated")
blk2 = fm.financial_question_block(q4, S1, True)
ok("OWN FINANCIAL MODEL" in blk2 and "Do not state a surplus" not in blk2, "with a model the answer uses its figures")
ok("sensitivity" in blk2, "the structure includes sensitivity")

# ---- Step 4: failed sections ---------------------------------------------------------------------------------------------
from tender_app.bid_writer import failed_sections, placeholder_counts, qa_gate
bad = {"cover_letter": {"content": "Dear Team,\n\nWe submit.\n\nYours faithfully,\nA Person"},
       "itt_question_responses": [{"question_number": "1", "question_text": "Q", "response": "[NEEDS INPUT: x] and [CONFIRM: y]."}],
       "social_value": {"content": "[NEEDS INPUT: the social value section could not be generated - regenerate the bid]"}}
ok(failed_sections(bad) == ["Social value"], "a section that failed is found: %s" % failed_sections(bad))
c = placeholder_counts(bad)
ok(c == {"input": 1, "confirm": 1, "other": 0, "failed": 1, "total": 3}, "one count split by type: %s" % c)
g = qa_gate(bad)
ok(any(i["section"] == "Social value" and i["severity"] == "P0" for i in g["issues"]), "a failed section is a P0")
ok(g["placeholders"] == 3 and g["counts"]["total"] == 3, "the QA gate uses the same count")
ok(failed_sections({"cover_letter": {"content": "Fine."}}) == [], "a clean bid has no failed sections")
blk = r5.final_export_blockers(bad, g)
ok(blk[0].startswith("3 item(s) still need your input (1 need input; 1 need confirming; 1 failed"), "refusal shows the split: %s" % blk[0])
pay = r5.export_block_payload(bad, blk)
ok(pay["code"] == "FINAL_EXPORT_BLOCKED" and pay["counts"]["total"] == 3 and len(pay["items"]) == 3, "payload carries counts and items")
ok({i["kind"] for i in pay["items"]} == {"input", "confirm", "failed"}, "items are typed")

# ---- Step 5: claim and fit accuracy --------------------------------------------------------------------------------------
import datetime
inc = datetime.date(2024, 7, 10); today = datetime.date(2026, 11, 16)
ok("[CONFIRM" in r5.reconcile_experience("With more than ten years in the trade we respond fast.", inc, today), "spelled-out years in the trade are checked")
ok("[CONFIRM" not in r5.reconcile_experience("Our 2 years of experience.", inc, today), "years within the company's age pass")
d = {"capability_gaps": ["Gas Safe registration", "No police references"], "summary": "No PPM record. Gas Safe is not shown. Insurance unclear."}
ok(r7.drop_irrelevant_trade_gaps(d, "Plumbing PPM and reactive repairs for sinks and taps") == 2 and d["capability_gaps"] == ["No police references"]
   and "Gas Safe" not in d["summary"] and "Insurance" in d["summary"], "Gas Safe dropped when the scope has no gas: %s" % d)
d2 = {"capability_gaps": ["Gas Safe registration"]}
ok(r7.drop_irrelevant_trade_gaps(d2, "Boiler servicing and gas appliance repairs") == 0 and d2["capability_gaps"], "Gas Safe kept for gas work")
t, n_ = r7.confirm_certifications("We are ISO 27001 certified and hold Cyber Essentials.", "ISO 27001 information security policy")
ok(n_ == 1 and "[CONFIRM: ISO 27001" in t, "a held-certification claim with only a policy on file is marked: %s" % t)
t, n_ = r7.confirm_certifications("We are ISO 9001 certified.", "ISO 9001 certificate issued by BSI")
ok(n_ == 0, "a certificate on file leaves the claim alone")
t, n_ = r7.confirm_certifications("We do not hold ISO 27001; a partner will.", "")
ok(n_ == 0, "a denial is not marked")
bid = {"cover_letter": {"content": "We are ISO 27001 certified."}}
ok(r7.confirm_certifications_in_bid(bid, "policy only") == 1 and "[CONFIRM" in bid["cover_letter"]["content"], "applies across a bid")

# ---- release 9: repeated sub-headings and the portal named from the notice reference -------------------------------------------
rep = r7.repeated_headings([("Section 4", "## Health and safety\ntext\n## Pricing basis"),
                            ("Section 7", "**Health and safety**\nmore\n## Summary"), ("Section 8", "## Summary")])
ok(rep == [("Health and safety", ["Section 4", "Section 7"])], "a repeated sub-heading is found, generic ones are not: %s" % rep)
ok(r7.repeated_headings([("A", "## Staffing"), ("B", "## Pricing")]) == [] and r7.repeated_headings(None) == [], "no repeat, no finding")
from tender_app import readiness as _rd
_res = _rd.readiness({"sections": []}, {}, {})
ok(isinstance(_res, dict), "readiness still runs")
ok(r5.portal_from_reference("OLDH001-DN835252") == "The Chest (Due North)", "Oldham reference points to The Chest")
ok(r5.portal_from_reference("DN831703") == "ProContract (Due North)" and r5.portal_from_reference("", None, "x") == "", "bare DN is ProContract; unknown is empty")
ok("The Chest (Due North)" in r5.provisional_structure_note(False, {"reference": "OLDH001-DN835252"}), "the provisional note names the portal")
ok("the buyer's portal" in r5.provisional_structure_note(False, {}), "no reference keeps the old wording")

# ---- release 10: drafting guides never printed, experience claims flagged without an incorporation date, score capped -----------
_qq = r7.submission_doc_questions({"required_submission_documents": [{"name": "5.1 Technical offer", "kind": "technical"}]})
_bd = {"itt_question_responses": [{"question_number": "5.1", "question_text": _qq[0]["question_text"],
                                   "response": "Intro. " + r7._KIND_GUIDE["technical"] + " Our offer."}]}
ok(r7.strip_drafting_guides(_bd) == 2 and _bd["itt_question_responses"][0]["question_text"] == "5.1 Technical offer."
   and "Write the technical offer" not in _bd["itt_question_responses"][0]["response"], "the drafting guide is stripped: %s" % _bd)
_bd2 = {"itt_question_responses": [{"question_text": "Q1 text", "response": "Fine."}]}
ok(r7.strip_drafting_guides(_bd2) == 0 and r7.strip_drafting_guides(None) == 0, "a clean bid is untouched")
_exp = r5.reconcile_experience("We bring over 12 years' experience of plumbing.", None, datetime.date(2026, 11, 18))
ok("[CONFIRM" in _exp, "a long experience claim with no incorporation date is still flagged: %s" % _exp)
ok("[CONFIRM" not in r5.reconcile_experience("Our 3 years of experience.", None, datetime.date(2026, 11, 18)), "a short claim is left alone")
_p0 = _rd.readiness({"cover_letter": {"content": "[NEEDS INPUT: the covering letter could not be generated - regenerate the bid]"},
                     "itt_question_responses": [{"question_number": "1", "question_text": "Q", "response": "Good answer. " * 40}]},
                    {"itt_questions": [{"number": "1", "question_text": "Q", "weighting": "100%"}]},
                    {"issues": [{"check": "coverage", "severity": "P0", "section": "Cover letter", "detail": "x"}]})
ok(_p0["p0_open"] >= 1 and _p0["score"] <= 69, "the score is capped while a P0 is open: %s" % _p0["score"])

# ---- release 10b: commercial part, word limits, spilled headings, past-bid split ------------------------------------------------------
_ct = r7.commercial_pack_text([{"sheet": "Vol 3", "header": ["Item", "Description", "Qty", "Unit price"], "rows": [["1", "Laptop", "10", ""], ["2", "Dock", "10", ""]]}],
                              "Prices are converted to GBP at the OANDA rate on the closing date. Other text.")
ok("[NEEDS INPUT: unit price]" in _ct and "| Laptop" in _ct and "OANDA" in _ct and "[CONFIRM" in _ct, "commercial part has line items, price cells and the currency rule: %s" % _ct)
_bc = {"pricing_notes": {"content": "Our pricing."}}
ok(r7.add_commercial_pack(_bc, [], "") and "unit price" in _bc["pricing_notes"]["content"] and not r7.add_commercial_pack(_bc, [], ""), "added once")
_long = " ".join(["word"] * 614) + "."
_wl = {"itt_question_responses": [{"question_number": "D1.4", "response": _long, "word_limit": 500}, {"question_number": "D1.5", "response": _long}]}
ok(r7.enforce_word_limits(_wl, {"format_requirements": {"page_or_word_limits": "500 words per answer"}}) == 2
   and all(len(q["response"].split()) <= 500 for q in _wl["itt_question_responses"]), "a hard limit is applied last, also from the format requirements")
_sp = {"itt_question_responses": [{"question_number": "D", "response": "Team text.\n\n## E. Mobilisation"}, {"question_number": "E", "response": "Plan text."}]}
ok(r7.fix_spilled_question_headings(_sp) == 1 and _sp["itt_question_responses"][1]["response"].startswith("## E. Mobilisation")
   and "Mobilisation" not in _sp["itt_question_responses"][0]["response"], "a heading spilled onto the previous question moves back: %s" % _sp)

# ---- release 11: Cornwall regression guards ---------------------------------------------------------------------------------------------
_txt = chr(10).join(["Section D1", "D1.1 Describe your method statement for delivering the digital print service", "D1.2 Explain how you will manage quality across the contract", "D1.3 Provide details of your staffing and supervision plans", "D1.4 Demonstrate how you will deliver social value in the contract", "1.1 The Contractor shall supply goods."])
_rq = r7.recover_questions(_txt)
ok([q["number"] for q in _rq] == ["D1.1", "D1.2", "D1.3", "D1.4"], "lettered question lines are recovered: %s" % _rq)
ok(r7.recover_questions(chr(10).join(["1.1 The Contractor shall provide the services.", "1.2 The Contractor shall describe it."])) == [], "numbered clauses are not questions")
_s1 = {"required_submission_documents": [{"name": "Technical offer - Lot 1: FE Prospectus"}, {"name": "Technical offer - Lot 2: Digital and Litho"}, {"name": "SSQ"}]}
ok(r7.drop_other_lot_documents(_s1, "Cornwall College Lot 2 digital print") == 1 and len(_s1["required_submission_documents"]) == 2, "a Lot 1 document is dropped from a Lot 2 bid")
from tender_app import bid_release4 as _r4
ok(not _r4.find_ruling_conflicts([{"topic": "VAT", "ruling": "Prices exclude VAT."}], [("S", "Invoices must include VAT exemption grounds.")]), "VAT exemption grounds is not 'prices include VAT'")

print("release 7: %d checks passed" % n)
