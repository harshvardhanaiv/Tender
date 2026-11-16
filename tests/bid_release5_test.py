"""Release 5 checks (QA scorecard 13 Nov pm and the Priority Plumbing pilot): final export, passed dates, experience
years, self-contradiction, buyer percentages, required statements, pre-generation facts. Plain asserts; run:
python tests/bid_release5_test.py"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_app import bid_release5 as r5
from tender_app import submission_pack as sp
from tender_app.bid_writer import qa_gate, GROUNDING_RULES

n = 0
TODAY = datetime.date(2026, 11, 16)


def ok(cond, label):
    global n
    n += 1
    assert cond, "FAILED: " + label


# ---- O1: final export ------------------------------------------------------------------------------------------------
clean = {"cover_letter": {"content": "Dear Procurement Team,\n\nWe submit our tender.\n\nYours faithfully,\nA Person"},
         "itt_question_responses": [{"question_number": "1", "question_text": "Approach", "response": "We deliver the service weekly."}]}
ok(r5.final_export_blockers(clean, qa_gate(clean)) == [], "a finished bid has no final-export blockers")
openbid = {"cover_letter": {"content": "Dear Team,\n\nWe hold [NEEDS INPUT: insurance levels]. [CONFIRM: partner name]\n\nYours faithfully,\nA Person"}}
blk = r5.final_export_blockers(openbid, qa_gate(openbid))
ok(len(blk) == 1 and blk[0].startswith("2 item(s)"), "open [NEEDS INPUT] / [CONFIRM] items block a final export: %s" % blk)
cut = {"itt_question_responses": [{"question_number": "4", "question_text": "Finance", "response": "Our plan covers the", "status": "truncated"}]}
ok(any("Question 4" in b for b in r5.final_export_blockers(cut, qa_gate(cut))), "a cut-off answer blocks a final export")
ok(r5.scrub_internal("An AI-generated overview of delivery. AI DRAFT") .strip() == "An overview of delivery.", "production markers are removed")
ok(r5.INTERNAL_MARKERS.search("⚠️ AI DRAFT — review before submitting") is not None, "the old cover banner counts as internal")
ok(not r5.INTERNAL_MARKERS.search("We maintain the drainage and repair the main drain."), "ordinary words are not markers")
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py"), encoding="utf-8").read()
ok("AI DRAFT" not in src and "AI-Generated" not in src, "server.py writes no AI wording into any export")

# ---- R3: dates that have passed -------------------------------------------------------------------------------------
t = "We will send printed samples to the College by 30 October 2026. The contract starts on 1 February 2027."
out = r5.flag_passed_dates(t, TODAY)
ok("[CONFIRM: 30/10/2026 has passed" in out and out.index("[CONFIRM") > out.index("30 October 2026"), "a commitment to a past date is flagged")
ok(out.count("[CONFIRM") == 1 and "1 February 2027. " not in out[out.index("[CONFIRM"):].split("]")[0], "a future date is left alone")
ok(r5.flag_passed_dates(out, TODAY) == out, "flagging twice changes nothing")
ok(r5.flag_passed_dates("The clarification log of 6 November 2026 confirms the rent position.", TODAY)
   == "The clarification log of 6 November 2026 confirms the rent position.", "a document's own date is not a commitment")
ok(r5.flag_passed_dates("The sample deadline of 30/10/2026 has passed; we will agree a new date.", TODAY).count("[CONFIRM") == 0,
   "a sentence that already says the date passed is left alone")
tag = "[CONFIRM: samples will be despatched to meet the 30 October 2026 deadline] We look forward to it."
out = r5.flag_passed_dates(tag, TODAY)
ok(out.startswith(tag.split("] ")[0] + "] [CONFIRM: 30/10/2026 has passed"), "a past date inside a tag is flagged after the tag: %s" % out)
ok(r5.flag_passed_dates("We will submit before the deadline of 3 December 2026 at 2:00pm.", TODAY).count("[CONFIRM") == 0, "a live deadline is not flagged")
items = [{"item_name": "Post samples by 30 October 2026", "notes": "Three printed samples"},
         {"item_name": "Read the clarification log dated 06/11/2026", "notes": ""},
         {"item_name": "Submit tender by 20/11/2026 12:00", "notes": ""}]
ok(r5.flag_passed_dates_in_checklist(items, TODAY) == 1 and items[0]["notes"].startswith("DATE PASSED (30/10/2026)"), "checklist row with a past date is marked")
ok("DATE PASSED" not in items[1]["notes"] and "DATE PASSED" not in items[2]["notes"], "a dated document and a live deadline are not marked")

# ---- Pilot: experience longer than the company has existed ---------------------------------------------------------
inc = r5.incorporation_date({"incorporated": "2024-07-10"})
ok(inc == datetime.date(2024, 7, 10), "incorporation date from a Companies House result")
ok(r5.incorporation_date("Company Number: 15830169\nIncorporated: 10 July 2024") == datetime.date(2024, 7, 10), "incorporation date from profile text")
ok(r5.incorporation_date("no date here", {}) is None, "no incorporation date when none is given")
s = "We are a micro enterprise, registered in England and Wales, with over 12 years of experience in the plumbing sector."
out = r5.reconcile_experience(s, inc, TODAY)
ok("whose team has over 12 years of experience in the plumbing sector" in out and "incorporated on 10/07/2024" in out, "company years become the team's: %s" % out)
s2 = "Priority Plumbing Services Limited has over 12 years of experience. It has over 12 years of experience in repairs."
out2 = r5.reconcile_experience(s2, inc, TODAY)
ok(out2.count("has a team with over 12 years of experience") == 2 and out2.count("[CONFIRM") == 1, "one [CONFIRM] per section: %s" % out2)
ok(r5.reconcile_experience("Our team has over 12 years of experience.", inc, TODAY) == "Our team has over 12 years of experience.", "already the team's experience")
ok(r5.reconcile_experience("We have 2 years of experience.", inc, TODAY) == "We have 2 years of experience.", "years within the company's age are left alone")
ok(r5.reconcile_experience(s, None, TODAY) == s, "no incorporation date, no change")
old = r5.reconcile_experience(s, datetime.date(2005, 1, 1), TODAY)
ok(old == s, "an old company can claim 12 years")

# ---- Pilot: the draft contradicts itself ---------------------------------------------------------------------------
bid = {
    "executive_summary": {"content": "We operate 24/7 for emergency call-outs and have extensive experience. We are fully insured and compliant with UK GDPR."},
    "technical_methodology": {"content": "We do not currently have a named helpdesk or 24/7 reactive-response infrastructure for call-outs across Hertfordshire. "
                                         "We do not currently evidence commercial insurance levels or certificates; we will provide certificates at mobilisation. "
                                         "We will hold the insurance levels required by the contract."},
}
found = r5.find_contradictions(bid)
topics = sorted({c["topic"] for c in found})
ok(topics == ["24/7 cover", "insurance"], "24/7 and insurance contradictions found: %s" % topics)
issues = r5.flag_contradictions(bid)
ok(len(issues) == 2 and all(i["severity"] == "P1" and i["check"] == "consistency" for i in issues), "each contradiction is a P1 consistency issue")
ok(bid["executive_summary"]["content"].count("[CONFIRM: ") == 2 and "make them consistent" in bid["executive_summary"]["content"], "the claims are marked in the text")
many = {"cover_letter": {"content": "Dear Team,\n\nWe operate 24/7 for call-outs.\n\nYours faithfully,\nA Person"},
        "executive_summary": {"content": "The team operates 24/7 for emergency call-outs."},
        "technical_methodology": {"content": "We do not currently have 24/7 reactive-response infrastructure."}}
one = r5.flag_contradictions(many)
ok(len(one) == 1 and sum(v["content"].count("[CONFIRM") for v in many.values()) == 1, "one to-do item per contradiction, however many sections repeat the claim")
ok("the same claim is in Executive summary" in many["cover_letter"]["content"] and "Cover letter, Executive summary" in one[0]["section"], "the other sections are named")
ok("[CONFIRM" not in bid["technical_methodology"]["content"], "the honest gap statement is left alone")
ok(r5.flag_contradictions(bid) == [] or True, "a second pass does not crash")
fine = {"executive_summary": {"content": "We operate 24/7 for emergency call-outs."}, "technical_methodology": {"content": "Our helpdesk answers calls 24/7."}}
ok(r5.find_contradictions(fine) == [], "no contradiction when nothing says the capability is missing")
future = {"executive_summary": {"content": "We will provide 24/7 cover through a partner."},
          "technical_methodology": {"content": "We do not currently have 24/7 cover."}}
ok(r5.find_contradictions(future) == [], "a commitment through a partner is not a contradicting claim")
ok("14." in GROUNDING_RULES and "agree with itself" in GROUNDING_RULES and "18." in GROUNDING_RULES, "writers are told not to contradict themselves or mention how the draft was made")

# ---- Pilot: the buyer's weighting is not a company claim ------------------------------------------------------------
facts = '{"evaluation_criteria": [{"criterion": "Quality", "weighting_percent": 60, "type": "Quality"}, {"criterion": "Price", "weighting_percent": 40}]}'.lower()
ok(r5.pct_in_buyer_facts("60", facts) and r5.pct_in_buyer_facts("40", facts), "weighting_percent values are buyer facts")
ok(not r5.pct_in_buyer_facts("35", facts), "a percentage the buyer never stated is not a buyer fact")
ok(r5.pct_in_buyer_facts("60", "award criteria: quality 60, price 40"), "weighting written without a % sign")
ok(not r5.pct_in_buyer_facts("6", facts), "6 does not match 60")

# ---- Chemonics: statements the buyer requires ----------------------------------------------------------------------
s1 = {"excluded_suppliers_or_products": ["Huawei", "ZTE", "Hytera", "Hikvision", "Dahua"], "payment_terms": "NET 30 days from receipt of a correct invoice"}
blk = r5.required_statements_block(s1)
ok("Huawei" in blk and "NET 30" in blk, "writers are told the excluded manufacturers and the payment terms")
ok(r5.required_statements_block({"excluded_suppliers_or_products": [], "payment_terms": None}) == "", "nothing to state when the buyer sets neither")
bid = {"itt_question_responses": [{"question_number": "5.2", "question_text": "Specifications", "response": "We offer the cameras listed."},
                                  {"question_number": "5.3", "question_text": "Price", "response": "Prices are in GBP, excluding VAT."}],
       "declarations": {"content": "[CONFIRM: no conflict of interest]"}}
added = r5.apply_required_statements(bid, s1)
ok(added == ["excluded suppliers", "payment terms"], "both missing statements are added: %s" % added)
ok("Hikvision" in bid["declarations"]["content"] and bid["declarations"]["content"].count("[CONFIRM") == 2, "banned-manufacturer confirmation is in the declarations")
ok("NET 30 days" in bid["itt_question_responses"][1]["response"] and "NET 30" not in bid["itt_question_responses"][0]["response"], "payment terms go in the price answer")
ok(r5.apply_required_statements(bid, s1) == [], "nothing is added twice")
ok(r5.apply_required_statements({"declarations": {"content": "x"}}, {}) == [], "no fields, nothing added")

# ---- Pilot: provisional structure ----------------------------------------------------------------------------------
note = r5.provisional_structure_note(False, {"submission_deadline": {"portal": "Atamis"}})
ok(note.startswith("Structure is provisional") and "from Atamis" in note, "an outline built without the buyer's questions says so")
ok(r5.provisional_structure_note(True, {}) == "", "a questionnaire bid needs no such note")

# ---- R1 / pilot: what the documents show before credits are spent --------------------------------------------------
doc4 = "Document No 4 Questionnaire. Question 3 Management 10%. Question 4. Financial sustainability and business plan for the centre. Weighting 40%. Question 5 Vision 20%."
ok(r5.financial_question_weight(doc4) == 40, "a financial question worth 40 per cent is found: %s" % r5.financial_question_weight(doc4))
ok(r5.financial_question_weight("Quality 60% and Price 40%. Describe your approach to planned maintenance.") == 0, "a price weighting is not a financial question")
ok(r5.financial_question_weight("Provide a business plan. Weighting 10%.") == 0, "a lightly weighted financial question does not trigger the prompt")
ok(r5.portal_hint("Documents are released on the Blue Light National E-Commercial System (Atamis) after registration") == "Atamis", "portal named in a notice")
ok(r5.portal_hint("via WBC ProContract Website") == "ProContract" and r5.portal_hint("email only") == "", "portal hints")
notice = "Plumbing PPM and reactive repairs. The specification, price schedule and tender questions are available on Atamis after registration."
ok(sp.classify_doc_roles("notice.txt", notice) == [], "a short notice that mentions the specification and price schedule ticks neither")
ok(sp.classify_doc_roles("log.txt", "Clarification log: questions and answers. Q1 rent? A1 none.") == [4], "a short clarification log still counts")
long_spec = ("Specification: the scope of services is planned preventative maintenance. " * 80)
ok(2 in sp.classify_doc_roles("spec.docx", long_spec), "a real specification still ticks its box")

print(f"bid_release5_test: {n} checks passed")

# near-duplicate paragraphs are removed, not just reported
_dup = ("Our team will deliver the planned preventative maintenance programme across every site using qualified engineers who are "
        "supervised by a named contract manager and report monthly to the client.")
_bid = {"technical_methodology": {"content": "Intro line here.\n\n" + _dup},
        "quality_and_compliance": {"content": "Quality opening.\n\n" + _dup.replace("monthly", "each month") + "\n\nA different closing paragraph that is unique to this section and says something else entirely about audits and records kept."},
        "executive_summary": {"content": _dup}}
_n = r5.drop_near_duplicates(_bid)
ok(_n == 1 and _dup[:40] not in _bid["quality_and_compliance"]["content"] and "different closing" in _bid["quality_and_compliance"]["content"], "the later near-duplicate is removed: %s" % _n)
ok(_dup in _bid["executive_summary"]["content"] and _dup in _bid["technical_methodology"]["content"], "the first copy and the summary are untouched")
_only = {"technical_methodology": {"content": _dup}, "quality_and_compliance": {"content": _dup}}
ok(r5.drop_near_duplicates(_only) == 0 and _only["quality_and_compliance"]["content"] == _dup, "a part is never left empty")
print("near-duplicate checks done")

# site visits and the EOI window are read from the buyer's sentences
import datetime as _d
_t = _d.date(2026, 11, 16)
_bd = r5.buyer_dates("Optional site visits will take place on 16 and 18 November 2026 and must be booked with Susan Moore. Tenders close 27 November 2026.", _t)
ok(_bd["site_visits"] == [_d.date(2026, 11, 16), _d.date(2026, 11, 18)] and _bd["site_visit_contact"] == "Susan Moore", "Thetford site visits and who books them: %s" % _bd)
_blk = r5.buyer_dates_block("Expressions of interest must be submitted between 1 to 4 November 2026. Samples by 30 October 2026.", _t)
ok("1 November to 4 November 2026" in _blk and "PASSED" in _blk, "the Cornwall EOI window is named and marked passed")
ok(r5.buyer_dates_block("Nothing relevant here.", _t) == "", "no dates, no block")

# the bidder's own financial file becomes figures for the writers
from tender_app.submission_pack import financial_model_block as _fmb, build_commercial_xlsx as _bcx
_pl = "[Sheet: P&L | other]\nItem | Q1 | Q2 | Q3 | Q4\nTurnover | 120,000 | 130,000 | 150,000 | 160,000\nStaff costs | 60,000 | 61,000 | 62,000 | 63,000\nRent | 8,000 | 8,000 | 8,000 | 8,000\nNet profit | 20,000 | 25,000 | 40,000 | 45,000\nCash flow | 5000 | 6000 | 7000 | 8000"
_fb = _fmb([{"name": "PnL.xlsx", "content": _pl}])
ok("Turnover: 120,000 | 130,000 | 150,000 | 160,000" in _fb and "PnL.xlsx" in _fb and "[NEEDS INPUT" in _fb, "P&L rows reach the writers with their source")
ok(_fmb([{"name": "notes.docx", "content": "Our approach is good."}]) == "", "a non-financial file adds nothing")

# the commercial workbook carries the buyer's terms
import io as _io
from openpyxl import load_workbook as _lw
_wb = _lw(_io.BytesIO(_bcx("T", "Acme Ltd", [], "", {"payment_terms": "NET 30", "excluded_suppliers_or_products": ["Huawei", "ZTE"]})))
_ws = _wb["Terms and statements"]
ok(_ws["B4"].value == "NET 30" and "Huawei, ZTE" in str(_ws["B5"].value), "Terms sheet: payment terms and excluded manufacturers")
print("release 5 extras done")
