"""Bid plan builder (tender_app/bid_plan.py): plain-assert script, run with `python tests/bid_plan_test.py`."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tender_app.bid_plan import build_bid_plan  # noqa: E402

fails = []


def ok(cond, label):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        fails.append(label)


TODAY = date(2026, 11, 16)
S1 = {
    "tender_title": "Hall refurbishment", "contracting_authority": "Example Council", "tender_reference": "DN123",
    "submission_deadline": {"date": "30/11/2026", "time": "12:00", "timezone": "GMT", "portal": "ProContract"},
    "clarification_deadline": {"date": "20/11/2026", "method": "portal Q&A"},
    "key_dates": [{"event": "Site visit", "date": "10/11/2026", "bidder_action": True, "contact": "book with Sam"},
                  {"event": "Award", "date": "15/01/2027", "bidder_action": False}],
    "required_submission_documents": [
        {"name": "Technical offer", "kind": "technical", "separate_file": True},
        {"name": "Price schedule", "kind": "commercial", "separate_file": True}],
    "separate_technical_commercial": True,
    "payment_terms": "NET 30 days",
    "contract_value": {"vat_inclusive": False},
    "itt_questions": [{"number": "1", "word_limit": 500}, {"number": "2", "word_limit": None}],
    "forms_to_sign": [{"form_name": "Form of Tender", "who_must_sign": "a Director", "reference": "Appendix 3"}],
    "certificates_required": [{"certificate": "ISO 9001", "mandatory_or_scored": "MANDATORY", "expiry_required": True},
                              {"certificate": "ISO 14001", "mandatory_or_scored": "DESIRABLE"}],
    "insurance_requirements": [{"type": "Public Liability", "minimum_value": "£5,000,000", "mandatory": True}],
    "references_required": {"number_required": 2, "recency_requirement": "last 3 years"},
    "mandatory_requirements": [{"requirement": "Be registered in the UK", "consequence_of_failure": "disqualified"}],
    "evaluation_criteria": [{"criterion": "Quality", "weighting_percent": 60, "type": "Quality"}],
    "clarification_rulings": [{"topic": "VAT", "ruling": "Prices exclude VAT"}],
    "excluded_suppliers_or_products": ["Huawei"],
    "ambiguities_detected": [{"section": "3.1", "issue": "Two different lot sizes"}],
}
p = build_bid_plan(S1, TODAY)
names = [f["name"] for f in p["files"]]
ok("Technical offer" in names and "Price schedule" in names, "named submission documents are files")
tech = next(f for f in p["files"] if f["name"] == "Technical offer")
ok(any("own file" in r for r in tech["rules"]) and any("No prices" in r for r in tech["rules"]), "technical offer: separate and no prices")
price = next(f for f in p["files"] if f["name"] == "Price schedule")
ok(any("NET 30" in r for r in price["rules"]) and any("exclude VAT" in r for r in price["rules"]), "price schedule: payment terms and VAT basis")
ok("Form of Tender" in names and any("Director" in r for r in next(f for f in p["files"] if f["name"] == "Form of Tender")["rules"]), "form to sign names the signatory")
ok(next(f for f in p["files"] if f["name"] == "ISO 9001")["mandatory"] and not next(f for f in p["files"] if f["name"] == "ISO 14001")["mandatory"], "mandatory vs desirable certificate")
ok(any("5,000,000" in r for f in p["files"] for r in f["rules"]), "insurance minimum shown")
ok("Client references" in names, "references become a file")
ok("Answers to the buyer's questions" not in names, "no extra answers file when a technical offer is named")
ok([d["event"] for d in p["dates"]][-1] == "Award" and p["dates"][0]["event"] == "Site visit", "dates are in order")
ok(next(d for d in p["dates"] if d["event"] == "Site visit")["passed"], "past step is marked passed")
sub = next(d for d in p["dates"] if d["event"] == "Submission deadline")
ok(not sub["passed"] and "12:00 GMT via ProContract" in sub["note"], "submission deadline carries time and portal")
ok(p["requirements"]["mandatory"][0]["if_missed"] == "disqualified" and p["requirements"]["excluded"] == ["Huawei"], "requirements and excluded makers")
ok(any("ProContract" in s for s in p["steps"]) and any("separate files" in s for s in p["steps"]), "steps name the portal and the separate-file rule")
ok(any("Unclear" in w for w in p["warnings"]), "ambiguity is a warning")

# No named documents: the questions become the answer file with word limits
q = build_bid_plan({"itt_questions": [{"number": "Q1", "word_limit": 300}, {"number": "Q2", "word_limit": 200}]}, TODAY)
ok(q["files"][0]["name"] == "Answers to the buyer's questions" and "Q1: 300 words" in " ".join(q["files"][0]["rules"]), "questions become one answers file with limits")

# Closed tender warns
c = build_bid_plan({"submission_deadline": {"date": "01/11/2026"}}, TODAY)
ok(any("passed" in w for w in c["warnings"]), "closed tender warns")

# Empty and junk input never raise and say so
for bad in (None, {}, {"required_submission_documents": "x", "key_dates": [None, 3], "forms_to_sign": [{"form_name": None}]}):
    e = build_bid_plan(bad, TODAY)
    ok(e["empty"] and e["files"] == [] and isinstance(e["steps"], list), "empty or junk input is safe: " + str(bad)[:30])

# 'Not stated' is not shown as a value
n = build_bid_plan({"required_submission_documents": [{"name": "Not stated"}], "payment_terms": "None"}, TODAY)
ok(n["files"] == [], "'Not stated' names are dropped")

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
