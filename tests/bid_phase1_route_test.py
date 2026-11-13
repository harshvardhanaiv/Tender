"""End-to-end run of POST /api/proposal with a fake database and a fake AI (no network, no Docker).

Checks the phase 1 wiring: questionnaire mode writes one answer per buyer question in the buyer's order,
leaves out the house-template sections, runs the QA gate, keeps uploads first, never says "Bidding Company",
and refunds the credits when the pipeline fails. Run: `python tests/bid_phase1_route_test.py`.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time

os.environ["ENABLE_SCHEDULERS"] = "0"
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402

LEDGER: list[tuple[str, int]] = []


class FakeCursor:
    def __init__(self):
        self._one = None
        self._all = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split()).lower()
        self._one, self._all = None, []
        if "select role from users" in s:
            self._one = ("member",)
        elif "from credit_wallet" in s and "select" in s:
            self._one = (500,) if "balance" in s else None
        elif "select id, name, profile_text, meta_json from company_profiles" in s:
            self._one = (1, "AIVHUB", "AIVHUB builds AI and data platforms.", json.dumps({"org_name": "AIVHUB LTD"}))
        elif "from company_profiles" in s:
            self._one = (json.dumps({"org_name": "AIVHUB LTD", "contact_name": "Hemant [Surname]",
                                     "email": "mahendra.aivhub.com", "phone": "0118 000000"}),)
        elif "from answer_bank" in s:
            self._all = [
                ("Experience", "Relevant experience", "Payments platform for a leading European bank, fraud down 40%; claims portal for a global insurer.", None, None),
                ("Health & Safety", "H&S policy", "Our health and safety policy is reviewed every year.", None, None),
            ]
        elif s.startswith("update credit_wallet") or "insert into credit_ledger" in s or "insert into credit_transactions" in s:
            LEDGER.append((s[:40], 1))

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all

    def close(self):
        pass


class FakeConn:
    def cursor(self):
        return FakeCursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


server.get_db_connection = lambda *a, **k: FakeConn()
server.init_db = lambda: None  # no schema setup against the fake database

# Credits: record calls instead of touching the wallet tables.
import tender_app.credits as credits  # noqa: E402
import tender_app.metering as metering  # noqa: E402

CHARGES: list[tuple[str, int]] = []
metering.consume_credits = lambda conn, user, cost, reason, ref_id="": CHARGES.append(("charge", cost)) or 500
credits.refund_credits = lambda conn, user, amount, reason, ref_id="": CHARGES.append(("refund", amount)) or 500
credits.get_balance = lambda conn, user: 500  # the low-balance check after a charge

STAGE1 = {
    "tender_reference": "DN831703",
    "tender_title": "Matthewsgreen Community Centre operator",
    "contracting_authority": "Wokingham Borough Council",
    "notice_type": "Expression of interest",
    "submission_deadline": {"date": "08/11/2099", "time": "23:00", "source": "Updated Doc 1"},
    "scope_of_work": {"summary": "Operate the Matthewsgreen community centre: hall hire, community bookings, lease, car park.",
                      "key_deliverables": ["bookings", "hall hire"]},
    "itt_questions": [
        {"number": "1", "question_text": "Tell us about your organisation and experience running community venues.", "weighting": "10%"},
        {"number": "4.I", "question_text": "Financial sustainability: provide a business plan with income and costs.", "weighting": "40%"},
        {"number": "5.II", "question_text": "Describe a month in the life of the centre.", "weighting": "20%"},
    ],
    "published_clarifications": [{"question": "Rent?", "answer": "No expectations - please state within your bid", "source": "Log 6 Nov"}],
    "evaluation_criteria": [{"criterion": "Quality", "weighting_percent": 70}],
}

PROMPTS: list[str] = []
STAGE1_NOW: dict = {}
DECISION = {"value": {"verdict": "Bid", "summary": "Runs community venues.", "criteria": [
    {"criterion": "Quality", "weight": 70, "rating": "strong", "evidence": "runs two venues"}],
    "mandatory": [], "capability_gaps": [], "partner_for": []}}
FAIL = {"on": False}


def fake_chat(system="", user="", max_tokens=4000, temperature=0.3, response_format_json=False, provider=None, api_key=None, model=None):
    PROMPTS.append(user)
    if FAIL["on"] and "Analyse the following tender documents" in user:
        raise RuntimeError("model unavailable")
    if "Analyse the following tender documents" in user:
        return json.dumps(STAGE1_NOW.get("value") or STAGE1)
    if "should bid for this tender" in user:
        return json.dumps(DECISION["value"])
    if "submission checklist" in user:
        return json.dumps({"checklist_items": [{"id": 1, "item_name": "EOI form", "status_flag": "MANDATORY"}]})
    if "clarification questions this bidder should submit" in user:
        return json.dumps({"questions": [{"id": 1, "priority": "HIGH", "category": "Commercial", "question": "Utilities split?",
                                          "impact_if_not_clarified": "pricing"}]})
    if "Write the answer to ONE question" in user:
        num = re.search(r"QUESTION (\S+):", user).group(1)
        if num == "4.I":  # cut off the first time, complete on the retry
            if "previous answer was cut off" not in user:
                return '{"response": "Income comes from hall hire and'
            return json.dumps({"response": "Income comes from hall hire at £25 an hour. We will invest £20,000. [NEEDS INPUT: utilisation ramp]", "placeholders_used": []})
        return json.dumps({"response": f"Answer to {num}. We will report monthly.", "placeholders_used": []})
    if "covering letter" in user:
        return json.dumps({"cover_letter": {"content": "Dear Wokingham Borough Council Procurement Team,\n\nPlease find our response.", "word_count": 9}})
    if "bid compliance lead" in user:
        return json.dumps({"declarations": {"content": "[CONFIRM: No exclusion grounds apply]", "forms_referenced": []}})
    if "project planning expert" in system:
        return json.dumps({"unit": "months", "phases": []})
    return "{}"


server.call_chat_api = fake_chat

app = server.create_app()
app.testing = True
client = app.test_client()
with client.session_transaction() as s:
    s["logged_in"] = True
    s["username"] = "zz_bid_phase1_test"
    s["last_activity"] = time.time()
    s["csrf_token"] = "t"

doc4 = base64.b64encode(b"Document No 4 Questionnaire. Q4 Financial sustainability 40%.").decode()
body = {
    "tender": {"title": "Matthewsgreen Community Centre", "source": "custom", "resource_id": "custom-test-1",
               "contracting_authority": "Wokingham Borough Council", "submission_deadline": "01/11/2099"},
    "company": "AIVHUB", "profile_id": 1, "refresh": True,
    "files": [{"filename": "Doc4_Questionnaire.txt", "content": doc4}],
}


def run(b):
    r = client.post("/api/proposal", json=b, headers={"X-CSRF-Token": "t"})
    assert r.status_code == 200, (r.status_code, r.data[:300])
    return [json.loads(line) for line in r.data.decode().splitlines() if line.strip()]


PASSED = 0


def ok(cond, msg):
    global PASSED
    assert cond, msg
    PASSED += 1


events = run(body)
done = [e for e in events if e.get("status") == "complete"]
ok(done, f"stream completes: {[e for e in events if e.get('status') == 'error']}")
p = done[0]["proposal"]
br = p["bid_response"]
ok([q["question_number"] for q in br["itt_question_responses"]] == ["1", "4.I", "5.II"], "one answer per question, buyer order")
ok(all(q["status"] == "ok" for q in br["itt_question_responses"]), "the cut-off 4.I answer was retried and completed")
ok(p["questionnaire_mode"] is True, "questionnaire mode")
for key in ("executive_summary", "technical_methodology", "social_value", "quality_and_compliance", "pricing_notes", "relevant_experience"):
    ok(key not in br, f"no unrequested {key}")
ok(br["declarations"]["content"].startswith("[CONFIRM"), "declarations are items to confirm")
ok("Bidding Company" not in json.dumps(br) and br["document_title"].endswith("AIVHUB LTD"), "legal name, no 'Bidding Company'")
ok("£20,000" not in re.sub(r"\[[^\]]*\]", "", br["itt_question_responses"][1]["response"]), "invented £20,000 replaced")
ok(any("utilisation ramp" in x["placeholder_text"] for x in br["all_placeholders"]), "placeholders listed for the side panel")
ok("qa_report" in p and any(i["check"] == "identity" for i in p["qa_report"]["issues"]), "QA report carries the profile identity issues")
ok(p["effective_deadline"] == "08/11/2099" and p["tender_closed"] is False, "deadline from the documents (extension) wins")
stage1_prompt = next(x for x in PROMPTS if "Analyse the following tender documents" in x)
ok(stage1_prompt.index("Doc4_Questionnaire.txt (uploaded by the user)") < stage1_prompt.index("Tender Details Overview"), "uploads come first")
q_prompts = [x for x in PROMPTS if "Write the answer to ONE question" in x]
ok(q_prompts and all("No expectations - please state within your bid" in x for x in q_prompts), "clarification answers reach every question")
ok(all("European bank" not in x for x in q_prompts), "off-sector Answer Bank entry left out")
ok(all("mahendra.aivhub.com" not in x and "[Surname]" not in x for x in q_prompts), "invalid identity never reaches a prompt")
ok(any("Left out 1 Answer Bank entry" in e.get("message", "") for e in events), "user told about the dropped entry")
ok(CHARGES == [("charge", server.CREDIT_COST_PROPOSAL)], f"charged once ({CHARGES})")
ok(p["bid_decision"]["verdict"] == "Bid" and p["bid_decision"]["fit_score"] == 100 and p["stage_warnings"] == [],
   f"bid decision in the payload ({p.get('bid_decision')})")
ok(any("Advice: Bid (fit 100/100)" in e.get("message", "") for e in events), "user sees the advice while drafting")
ok(any("clarification questions this bidder should submit" in x for x in PROMPTS), "clarifications asked while the window is open")

# Failure: credits come back.
CHARGES.clear()
FAIL["on"] = True
body2 = dict(body, tender=dict(body["tender"], resource_id="custom-test-2"))
events = run(body2)
err = [e for e in events if e.get("status") == "error"]
ok(err and err[0].get("refunded") is True, "error event says refunded")
ok(CHARGES == [("charge", server.CREDIT_COST_PROPOSAL), ("refund", server.CREDIT_COST_PROPOSAL)], f"refunded ({CHARGES})")
FAIL["on"] = False

# M16 / R9 / G6 / R10: a print tender for a data company, with the clarification and EOI windows already closed.
PROMPTS.clear()
STAGE1_NOW["value"] = dict(STAGE1, tender_title="Print & Signage DPS Lot 2", notice_type="Invitation to tender",
                           clarification_deadline={"date": "11/11/2020", "method": "In-Tend"},
                           key_dates=[{"event": "Expression of interest window", "date": "01/11/2020 - 04/11/2020", "bidder_action": True},
                                      {"event": "Contract award", "date": "01/01/2021", "bidder_action": False}])
DECISION["value"] = {"verdict": "Bid", "summary": "Some overlap.", "criteria": [
    {"criterion": "Print capability", "weight": 60, "rating": "none", "evidence": "none found"},
    {"criterion": "Account management", "weight": 40, "rating": "partial", "evidence": "BI dashboards"}],
    "mandatory": [{"requirement": "Own print production plant", "status": "not met", "note": "a BI company"}],
    "capability_gaps": ["owns no print production equipment"], "partner_for": []}
events = run(dict(body, tender=dict(body["tender"], resource_id="custom-test-3", title="Print & Signage")))
p3 = [e for e in events if e.get("status") == "complete"][0]["proposal"]
ok(p3["bid_decision"]["verdict"] == "No-bid" and p3["bid_decision"]["fit_score"] == 20, f"unmet mandatory + low fit -> No-bid ({p3['bid_decision']})")
ok([w["kind"] for w in p3["stage_warnings"]] == ["clarification", "eoi"], f"passed stages found ({p3['stage_warnings']})")
ok(not any("clarification questions this bidder should submit" in x for x in PROMPTS), "no clarification call after the window closed (G6)")
ok(p3["clarifications"]["questions"] == [] and "has passed" in p3["clarifications"]["strategic_notes"], "and the notes say why")
q3 = [x for x in PROMPTS if "Write the answer to ONE question" in x]
ok(q3 and all("owns no print production equipment" in x and "Never offer an unrelated product" in x for x in q3), "capability gaps reach every answer (R9)")
ok(all("Expression of interest window: 04/11/2020" in x for x in q3), "writers told which stages passed")
chk = {(i["check"], i["severity"]) for i in p3["qa_report"]["issues"]}
ok(("bid_decision", "P1") in chk and ("stage_passed", "P1") in chk and ("stage_passed", "P2") in chk, f"export check lists them ({chk})")
STAGE1_NOW.clear()

# Export: QA banner and dynamic TOC in the .docx.
import io  # noqa: E402
import zipfile  # noqa: E402

bad = dict(br)
bad["itt_question_responses"] = [dict(q) for q in br["itt_question_responses"]]
bad["itt_question_responses"][2]["response"] = "We will open the centre from 8am and"
r = client.post("/api/download-proposal", headers={"X-CSRF-Token": "t"}, json={"title": "Matthewsgreen", "authority": "Wokingham", "company_name": "AIVHUB LTD",
                                                 "bid_response": bad, "checklist": [], "clarifications": []})
ok(r.status_code == 200, f"download works ({r.status_code})")
xml = zipfile.ZipFile(io.BytesIO(r.data)).read("word/document.xml").decode("utf-8")
ok("DRAFT - NOT READY TO SUBMIT" in xml, "failed QA -> draft banner")
ok("Section 2: Responses to the Buyer&apos;s Questions" in xml or "Section 2: Responses to the Buyer's Questions" in xml, "questions straight after the letter")
ok("Section 3: Declarations to Confirm" in xml and "Executive Summary" not in xml, "only the sections the bid has")
r = client.post("/api/download-proposal", headers={"X-CSRF-Token": "t"}, json={"title": "Matthewsgreen", "authority": "Wokingham", "company_name": "AIVHUB LTD",
                                                 "bid_response": br, "checklist": [], "clarifications": []})
xml = zipfile.ZipFile(io.BytesIO(r.data)).read("word/document.xml").decode("utf-8")
ok("DRAFT - NOT READY TO SUBMIT" not in xml, "clean draft has no banner")
ok("utilisation ramp" in xml.split("Appendix C")[-1], "Appendix C lists [NEEDS INPUT] items")
# G5: a stored Gantt must not add a "Technical Methodology" section to a questionnaire bid
gantt = {"unit": "weeks", "phases": [{"id": 1, "label": "Mobilise", "start": 1, "duration": 2}]}
r = client.post("/api/download-proposal", headers={"X-CSRF-Token": "t"}, json={"title": "Matthewsgreen", "authority": "Wokingham", "company_name": "AIVHUB LTD",
                                                 "bid_response": br, "gantt": gantt, "checklist": [], "clarifications": []})
xml = zipfile.ZipFile(io.BytesIO(r.data)).read("word/document.xml").decode("utf-8")
ok(r.status_code == 200 and "Technical Methodology" not in xml, "questionnaire bid + Gantt -> no methodology section")
legacy = {"cover_letter": {"content": "Dear Sir/Madam, please find our response."}, "executive_summary": {"content": "We will deliver."}}
r = client.post("/api/download-proposal", headers={"X-CSRF-Token": "t"}, json={"title": "Office cleaning", "authority": "X", "company_name": "AIVHUB LTD",
                                                 "bid_response": legacy, "gantt": gantt, "checklist": [], "clarifications": []})
xml = zipfile.ZipFile(io.BytesIO(r.data)).read("word/document.xml").decode("utf-8")
ok("Technical Methodology" in xml, "a non-questionnaire bid keeps its Gantt section")
# M13: case studies and references go to an appendix when the buyer's format rules ask for appendices
br_cs = dict(br, itt_question_responses=list(br["itt_question_responses"]) + [
    {"question_number": "Case studies", "question_text": "Please share case studies of relevant projects.", "response": "No case studies are held.", "status": "ok"},
    {"question_number": "References", "question_text": "Please share references.", "response": "No references are held.", "status": "ok"}])
rr = client.post("/api/download-proposal", headers={"X-CSRF-Token": "t"}, json={"title": "EEF", "authority": "EEF", "company_name": "AIVHUB LTD",
                 "bid_response": br_cs, "checklist": [], "clarifications": [], "format_requirements": {"case_studies_and_appendices": "Case studies as appendices"}})
xml_cs = zipfile.ZipFile(io.BytesIO(rr.data)).read("word/document.xml").decode("utf-8")
main_part, _, appx = xml_cs.partition("Appendix D: Case Studies and References</w:t>")
ok("Appendix D: Case Studies and References" in xml_cs and "No case studies are held." in xml_cs.split("Appendix D: Case Studies and References</w:t>")[-1]
   and "No case studies are held." not in xml_cs.split("Appendix A: Document Submission Checklist</w:t>")[0], "case studies and references moved to Appendix D")
rr = client.post("/api/download-proposal", headers={"X-CSRF-Token": "t"}, json={"title": "EEF", "authority": "EEF", "company_name": "AIVHUB LTD",
                 "bid_response": br_cs, "checklist": [], "clarifications": []})
xml_plain = zipfile.ZipFile(io.BytesIO(rr.data)).read("word/document.xml").decode("utf-8")
ok("Appendix D" not in xml_plain and "No case studies are held." in xml_plain, "no appendix request: answers stay in Section 2")

# M16 before credits: the free fit check from the notice, cached
PROMPTS.clear()
CHARGES.clear()
DECISION["value"] = {"verdict": "No-bid", "summary": "No print capability.", "criteria": [{"criterion": "Print", "weight": 100, "rating": "none"}],
                     "mandatory": [], "capability_gaps": ["owns no print presses"], "partner_for": []}
bc_body = {"tender": {"source": "find_tender", "resource_id": "BC-1", "title": "Print & Signage DPS", "description": "Litho and digital print of college prospectuses, signage and banners, with storage and distribution to campuses across Cornwall. Suppliers must own print production plant and hold FSC chain of custody."}, "profile_id": 1}
rr = client.post("/api/bid-check", headers={"X-CSRF-Token": "t"}, json=bc_body)
ok(rr.status_code == 200 and rr.get_json()["decision"]["verdict"] == "No-bid" and rr.get_json()["decision"]["fit_score"] == 0, f"bid check works ({rr.status_code} {rr.data[:200]})")
ok(any("Only the public notice is available" in x for x in PROMPTS) and CHARGES == [], "notice-only prompt, no credits charged")
rr = client.post("/api/bid-check", headers={"X-CSRF-Token": "t"}, json=bc_body)
ok(rr.get_json().get("cached") is True and sum("should bid for this tender" in x for x in PROMPTS) == 1, "second check served from cache")

thin = client.post("/api/bid-check", headers={"X-CSRF-Token": "t"}, json={"tender": {"source": "zz_none", "resource_id": "T-1", "title": "X",
                    "description": "UK Contracts Finder public notice #ocds-1."}, "profile_id": 1})
ok(thin.status_code == 200 and thin.get_json()["decision"] is None and "too little detail" in thin.get_json()["note"], "a stand-in notice is not judged")

# M8: the financial model download
try:
    import openpyxl  # noqa: F401
    rr = client.post("/api/download-financial-model", headers={"X-CSRF-Token": "t"}, json={
        "title": "Matthewsgreen", "company_name": "AIVHUB LTD", "bid_response": br,
        "dissection": {"special_conditions": ["Sinking fund: operator contributes GBP 2,500 per annum."]}})
    wb = openpyxl.load_workbook(io.BytesIO(rr.data))
    cells = [c for row in wb["Inputs"].iter_rows(values_only=True) for c in row]
    ok(rr.status_code == 200 and 2500 in cells and any(isinstance(c, str) and "utilisation ramp" in c for c in cells),
       "model has the buyer's sinking fund and the answer's gaps")
except ImportError:
    print("  (openpyxl not installed here: model download check skipped)")

# M13: title and date in the header, "Page X of Y" in the footer, on every page
z = zipfile.ZipFile(io.BytesIO(r.data))
hdr, ftr = z.read("word/header1.xml").decode("utf-8"), z.read("word/footer1.xml").decode("utf-8")
rels, ctypes = z.read("word/_rels/document.xml.rels").decode("utf-8"), z.read("[Content_Types].xml").decode("utf-8")
ok("Office cleaning | AIVHUB LTD |" in hdr and " PAGE " in ftr and " NUMPAGES " in ftr, "header and Page X of Y footer")
ok('r:id="rIdHdr1"' in xml and "header1.xml" in rels and "footer+xml" in ctypes, "header/footer wired into the package")
import xml.dom.minidom as _md  # noqa: E402
for part in ("word/document.xml", "word/header1.xml", "word/footer1.xml", "word/_rels/document.xml.rels", "[Content_Types].xml"):
    _md.parseString(z.read(part))
ok(True, "every package part is well-formed XML")


# Release 4: the writer's note in the .docx, grouped placeholders (R5), and the PDF export with page counts (N6)
note_body = {"title": "Office cleaning", "authority": "Chemonics Group UK Limited", "company_name": "AIVHUB LTD",
             "writer_note": "Advice: No-bid (fit 0/100). No print capability. Submission deadline: 23/11/2026 18:00 GMT+2 (16:00 UK time).",
             "bid_response": {"cover_letter": {"content": "Dear Olha. " + "We write. " * 40},
                              "itt_question_responses": [{"question_number": "1", "question_text": "Q", "status": "ok",
                                                          "response": "Answer [CONFIRM: partner name] and [CONFIRM: partner name] again. " * 3}]},
             "dissection_rulings": {"published_clarifications": [{"question": "VAT?", "answer": "Prices must exclude VAT."}]}}
rn = client.post("/api/download-proposal", headers={"X-CSRF-Token": "t"}, json=note_body)
zn = zipfile.ZipFile(io.BytesIO(rn.data))
xn = zn.read("word/document.xml").decode("utf-8")
ok("Bid writer&apos;s note (remove before submission)" in xn and "16:00 UK time" in xn, "docx opens with the bid writer's note")
ok("[CONFIRM: partner name] x 6" in xn, "Appendix C groups the repeated placeholder with a count")
try:
    import pymupdf  # noqa: F401
    rp = client.post("/api/download-proposal-pdf", headers={"X-CSRF-Token": "t"}, json=note_body)
    import json as _json
    pc = _json.loads(rp.headers.get("X-Page-Counts", "[]"))
    ok(rp.status_code == 200 and rp.data[:4] == b"%PDF" and any(c["label"] == "Question 1" for c in pc), "PDF export with a page count per section")
except ImportError:
    print("  (PyMuPDF not installed here: PDF export check skipped)")

# Release 4 (round 2): content-based document roles (N7) and the technical + commercial pack (N3)
import base64 as _b64
brief_txt = ("Instructions to bidders. Quality questions: please describe your approach (word limit 500). "
             "Specification: the scope of services. Fee proposal with day rates.")
cr = client.post("/api/classify-bid-docs", headers={"X-CSRF-Token": "t"},
                 json={"files": [{"filename": "brief.txt", "content": _b64.b64encode(brief_txt.encode()).decode()}]})
ok(cr.status_code == 200 and cr.get_json()["files"][0]["roles"] == [0, 1, 2, 3], "classify-bid-docs: one brief covers four boxes")
pk = client.post("/api/download-submission-pack", headers={"X-CSRF-Token": "t"}, json={
    "title": "IT equipment", "authority": "Chemonics Group UK Limited", "company_name": "AIVHUB LTD",
    "price_schedules": [{"sheet": "ToR", "header": ["Item", "Description", "Qty", "Unit price", "Total"], "rows": [["1", "Laptop", "20", "", ""]]}],
    "dissection_rulings": {"published_clarifications": [{"question": "VAT?", "answer": "Prices must exclude VAT."}]},
    "bid_response": {"cover_letter": {"content": "Dear Olha. Our price is 5,000 GBP."}, "pricing_notes": {"content": "Total 9,999 GBP"},
                     "itt_question_responses": [{"question_number": "1", "response": "We deliver in 20 days."}]}})
zp = zipfile.ZipFile(io.BytesIO(pk.data))
ok(pk.status_code == 200 and sorted(zp.namelist()) == ["README.txt", "commercial_offer.xlsx", "technical_offer.docx"], "submission pack has both files")
ok(b"9,999" not in zp.read("technical_offer.docx") and b"5,000" not in zp.read("technical_offer.docx"), "technical offer carries no prices")

print(f"bid_phase1_route_test: {PASSED} checks passed")
