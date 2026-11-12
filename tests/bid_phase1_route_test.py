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
FAIL = {"on": False}


def fake_chat(system="", user="", max_tokens=4000, temperature=0.3, response_format_json=False, provider=None, api_key=None, model=None):
    PROMPTS.append(user)
    if FAIL["on"] and "Analyse the following tender documents" in user:
        raise RuntimeError("model unavailable")
    if "Analyse the following tender documents" in user:
        return json.dumps(STAGE1)
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

# Failure: credits come back.
CHARGES.clear()
FAIL["on"] = True
body2 = dict(body, tender=dict(body["tender"], resource_id="custom-test-2"))
events = run(body2)
err = [e for e in events if e.get("status") == "error"]
ok(err and err[0].get("refunded") is True, "error event says refunded")
ok(CHARGES == [("charge", server.CREDIT_COST_PROPOSAL), ("refund", server.CREDIT_COST_PROPOSAL)], f"refunded ({CHARGES})")
FAIL["on"] = False

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

print(f"bid_phase1_route_test: {PASSED} checks passed")
