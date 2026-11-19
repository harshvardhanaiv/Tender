"""Regression checks for every finding in the fix plan's re-tests of 17 and 18 November 2026 (release 7 and release 8), each
written from the case the QA report gives, so the same defect fails a test if it comes back. No network, no Docker.
Run: python tests/release8_retest_test.py"""
from __future__ import annotations

import datetime
import io
import json
import os
import sys
import time
import zipfile

os.environ["ENABLE_SCHEDULERS"] = "0"
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_app import bid_release4 as r4  # noqa: E402
from tender_app import bid_release5 as r5  # noqa: E402
from tender_app import bid_release7 as r7  # noqa: E402
from tender_app import readiness as rd  # noqa: E402
from tender_app.bid_decision import decision_prompt  # noqa: E402
from tender_app.bid_style import mark_reused_facts  # noqa: E402
from tender_app.bid_style_learn import split_answers  # noqa: E402
from tender_app.bid_writer import ends_cleanly, answer_words  # noqa: E402

n = 0
TODAY = datetime.date(2026, 11, 19)
NL = chr(10)


def ok(cond, label):
    global n
    n += 1
    assert cond, "FAILED: " + label


# ======== 17 Nov (release 7) P0 items =====================================================================================
# Past-bid fact guard corrupted the buyer's own dates: "14 December [CONFIRM: 2026 - from a past bid]".
past = "Our Matthewsgreen bid: the hall opens on 14 December 2026, with a GBP 2,500 sinking fund and 94% satisfaction."
buyer_docs = "The contract starts on 14 December 2026. Deadline 8 November 2026 23:00."
t, c = mark_reused_facts("Mobilisation completes by 14 December 2026, ahead of the start.", past, buyer_docs)
ok(c == 0 and "[CONFIRM" not in t, "a year the buyer's documents state is never tagged as a past-bid fact: %s" % t)
t, c = mark_reused_facts("We achieved 94% satisfaction.", past, buyer_docs)
ok(c == 1 and "[CONFIRM: 94% - from a past bid]" in t, "a fact only the past bid holds is still tagged: %s" % t)

# Drafting instructions printed in the bid (Chemonics x4, Oldham x2, NHS Wales x2).
qs = r7.submission_doc_questions({"required_submission_documents": [{"name": "Question 3: Technical proposal (no cost information)", "kind": "technical"}]})
bid = {"itt_question_responses": [{"question_number": "3", "question_text": qs[0]["question_text"], "response": "We will deliver it."}]}
r7.strip_drafting_guides(bid)
ok("Write the technical offer" not in json.dumps(bid) and "Answer each requirement" not in json.dumps(bid),
   "the buyer's question is printed, never the drafting prompt: %s" % bid["itt_question_responses"][0]["question_text"])

# Answers cut off mid-sentence (Matthewsgreen Q4 ends "...current financial position are").
ok(not ends_cleanly("We enclose the last three years of accounts and current financial position are"), "Q4's cut-off ending is caught")
ok(not ends_cleanly("What we will provide, and when" + NL + "| Item | Date |" + NL + "| Laptops | 30 days"), "a half table is caught")

# Herts social value failed three runs in a row; it is a P0 with a Regenerate button, and blocks a near-ready score.
failed = {"itt_question_responses": [{"question_number": "1", "question_text": "Q", "response": "A full answer. " * 50}],
          "social_value": {"content": "[NEEDS INPUT: the social value section could not be generated - regenerate the bid]"}}
from tender_app.bid_writer import qa_gate  # noqa: E402
res = rd.readiness(failed, {"itt_questions": [{"number": "1", "question_text": "Q"}]}, qa_gate(failed))
sv = [i for i in res["items"] if i["section"] == "Social value"]
ok(sv and sv[0]["severity"] == "P0" and sv[0].get("fix") == {"section": "social_value"}, "the failed section can be regenerated on its own: %s" % sv)
ok(res["score"] <= 69, "Herts showed 86/100 with a failed section; a P0 now caps the score: %s" % res["score"])
ok(all(i.get("goto") for i in res["items"]), "every to-do item has a Go to target")

# ======== 17 Nov P1 items ================================================================================================
# Notice-only Oldham lost its team, ethics and data-protection sections; the consultancy template has them.
k, note = r5.contract_type_note("Oldham Council - Live Well Evaluation", "Evaluation of the Live Well programme")
for want in ("Team", "Ethics", "data protection", "Price"):
    ok(want.lower() in note.lower(), "Oldham structure has %s" % want)
k, note = r5.contract_type_note("NHS Wales coaching platform RFQ", "Digital coaching platform for NWSSP, NHS Wales")
for want in ("Data protection", "WCAG", "Welsh"):
    ok(want.lower() in note.lower(), "NHS Wales structure has %s" % want)
ok(all(w in r7._KIND_GUIDE["technical"] for w in ("understanding", "data protection", "WCAG", "Welsh")),
   "a technical offer built from the buyer's documents is asked for the same sections")

# Chemonics commercial offer: line items with [NEEDS INPUT: unit price] rows and the OANDA/GBP rule.
sch = [{"sheet": "Volume 3", "header": ["Item", "Description", "Qty", "Unit price", "Total"],
        "rows": [[str(i), "Laptop model %d" % i, "5", "", ""] for i in range(1, 61)]}]
txt = r7.commercial_pack_text(sch, "Prices in USD are converted to GBP at the OANDA rate on the closing date.")
ok(txt.count("[NEEDS INPUT: unit price]") == 30 and "more line(s)" in txt and "OANDA" in txt, "commercial part: rows, overflow note and currency rule")

# Cornwall College over word limits (D1.4 614, D1.2 561 against 500).
long = " ".join(["word"] * 613) + "."
wl = {"itt_question_responses": [{"question_number": "D1.4", "response": long, "word_limit": 500},
                                 {"question_number": "D1.2", "response": " ".join(["word"] * 560) + "."}]}
r7.enforce_word_limits(wl, {"format_requirements": {"page_or_word_limits": "Maximum 500 words per answer"}})
ok(all(answer_words(q["response"]) <= 500 for q in wl["itt_question_responses"]), "both answers fit the 500-word limit")

# Herts plumbing: "over 12 years' experience" for a company formed in 2024, with and without a known incorporation date.
for phrase in ("We bring over 12 years' experience to every job.", "We have over 12 years of plumbing experience.",
               "With 12 years' hands-on plumbing and heating experience we respond fast."):
    ok("[CONFIRM" in r5.reconcile_experience(phrase, datetime.date(2024, 7, 10), TODAY), "flagged with a 2024 incorporation: %s" % phrase)
    ok("[CONFIRM" in r5.reconcile_experience(phrase, None, TODAY), "flagged with no incorporation date: %s" % phrase)
ok("[CONFIRM" not in r5.reconcile_experience("The contract runs for 12 years of service.", None, TODAY), "a contract term is not a claim")
three = {"itt_question_responses": [{"question_number": str(i), "response": "Priority Plumbing has over 12 years' experience in plumbing."}
                                    for i in (1, 2, 3)]}
res = rd.readiness(three, {"itt_questions": [{"number": str(i), "question_text": "q"} for i in (1, 2, 3)]}, {"issues": []})
ok(sum(1 for i in res["items"] if i["check"] == "experience_claim") == 3, "an unflagged claim that slipped through is listed, per section")

# Thetford Question D had no heading: the next question's heading sat at the end of D.
sp = {"itt_question_responses": [{"question_number": "C", "response": "C text."},
                                 {"question_number": "D", "response": "Team text." + NL + NL + "## E. Mobilisation"},
                                 {"question_number": "E", "response": "Plan text."}]}
r7.fix_spilled_question_headings(sp)
ok(sp["itt_question_responses"][2]["response"].startswith("## E. Mobilisation") and "Mobilisation" not in sp["itt_question_responses"][1]["response"],
   "E's heading moves to E")

# "What TenderFlow learned" read the Matthewsgreen EOI as one 5,755-word answer.
body = " ".join(["text"] * 70)
eoi = NL.join(["Expression of interest", "Question 1 - Organisation information", body, "Q2: Experience of running venues", body,
               "Question 3.I Community benefit", body, "Q4 Financial sustainability", body])
ok(len(split_answers(eoi)) == 4, "the past bid is split by its questions: %s" % [a["label"] for a in split_answers(eoi)])

# ======== 18 Nov (release 8) items =======================================================================================
# P0 Cornwall: D1.1 to D1.4 missing, and a "Lot 1" technical offer in a Lot 2 bid.
cornwall = NL.join(["D1 Method statement (80% of the quality marks)",
                    "D1.1 Describe how you will deliver the digital and litho print service for the College",
                    "D1.2 Explain how you will manage quality and proofing across the contract",
                    "D1.3 Provide details of your account management and staffing",
                    "D1.4 Demonstrate how you will reduce environmental impact and add social value"])
ok([q["number"] for q in r7.recover_questions(cornwall)] == ["D1.1", "D1.2", "D1.3", "D1.4"], "Cornwall's four quality questions are recovered")
s1 = {"required_submission_documents": [{"name": "Technical offer - Lot 1: FE Prospectus"}, {"name": "Technical offer - Lot 2: Digital and Litho"}]}
r7.drop_other_lot_documents(s1, "Cornwall College Group - Print Services Lot 2 Digital and Litho")
ok([d["name"] for d in s1["required_submission_documents"]] == ["Technical offer - Lot 2: Digital and Litho"], "only the Lot 2 document stays")

# P1 Chemonics: "include VAT exemption grounds" is not "prices include VAT".
rul = [{"topic": "VAT", "ruling": "Prices must be quoted exclusive of VAT."}]
ok(not r4.find_ruling_conflicts(rul, [("Commercial", "Invoices must include VAT exemption grounds.")]), "no false positive on VAT exemption")
ok(r4.find_ruling_conflicts(rul, [("Commercial", "Our prices include VAT.")]), "a real contradiction is still found")

# P1 Epsom: 60 miles from Aylesbury not raised. The fit check is now told to name distance.
ok("Distance" in decision_prompt({}, company="Priority Plumbing", company_data="Based in Aylesbury", company_context="",
                                 answer_bank="", warnings=[], closed=False), "the fit check asks about base-to-site distance")

# Writer's note: one decision line first, actions listed.
note = r4.writer_note({"verdict": "No-bid", "fit_score": 50, "summary": "No PPM record."}, [],
                      {"submission_deadline": {"date": "03/12/2026", "time": "12:00"}})
ok(note.startswith("Recommendation: NO-BID (fit 50/100). Deadline: 03/12/2026"), "the note leads with the decision: %s" % note[:80])

# ======== Routes: server-side bid uploads, regenerate one section, dashed box for reused text ===============================
import server  # noqa: E402

UP = {}


class Cur:
    def __init__(self):
        self._one, self._all = None, []

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split()).lower()
        self._one, self._all = None, []
        if "select role from users" in s:
            self._one = ("member",)
        elif "select balance" in s or ("from credit_wallet" in s and "select" in s):
            self._one = (500,)
        elif s.startswith("delete from bid_uploads"):
            for k in [k for k in UP if k[0] == params[0] and k[1] == params[1]]:
                UP.pop(k)
        elif s.startswith("insert into bid_uploads"):
            UP[(params[0], params[1], params[2])] = params[3]
        elif s.startswith("select filename, content from bid_uploads"):
            self._all = [(k[2], v) for k, v in UP.items() if k[0] == params[0] and k[1] == params[1]]
        elif "from company_profiles" in s and "select id, name" in s:
            self._one = (1, "Priority Plumbing Ltd", "Plumbing and drainage contractor based in Aylesbury.", "{}")
        elif "from answer_bank" in s:
            self._all = []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all

    def close(self):
        pass


class Conn:
    def cursor(self):
        return Cur()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


server.get_db_connection = lambda *a, **k: Conn()
server.init_db = lambda: None
import tender_app.credits as credits  # noqa: E402
import tender_app.metering as metering  # noqa: E402
metering.consume_credits = lambda conn, user, cost, reason, ref_id="": 500
credits.refund_credits = lambda conn, user, amount, reason, ref_id="": 500
credits.get_balance = lambda conn, user: 500
CALLS = []


def fake_chat(system="", user="", max_tokens=0, **kw):
    CALLS.append(user)
    if "ONE question" in user:
        return json.dumps({"response": "We run a planned maintenance programme with monthly visits and records for every site.", "placeholders_used": []})
    return json.dumps({"content": "We will deliver social value through local apprenticeships and [NEEDS INPUT: number of hours]."})


server.call_chat_api = fake_chat
app = server.create_app()
app.testing = True
client = app.test_client()
with client.session_transaction() as s:
    s["logged_in"] = True
    s["username"] = "zz_r8_test"
    s["last_activity"] = time.time()
    s["csrf_token"] = "t"
H = {"X-CSRF-Token": "t"}

# Cornwall reopened with 2 of its 5 files: all five are kept on the server and come back.
five = [{"filename": "Doc%d.pdf" % i, "content": "UEsDBA" * (i + 1)} for i in range(1, 6)]
r = client.put("/api/bid-uploads", headers=H, json={"tender_key": "cf:cornwall-lot2", "files": five})
ok(r.status_code == 200 and r.get_json()["kept"] == 5, "five files are kept: %s" % r.get_json())
r = client.get("/api/bid-uploads?tender_key=cf:cornwall-lot2")
ok([f["filename"] for f in r.get_json()["files"]] == ["Doc%d.pdf" % i for i in range(1, 6)], "all five come back on reopen")
r = client.put("/api/bid-uploads", headers=H, json={"tender_key": "cf:cornwall-lot2", "files": five[:4]})
ok(len(client.get("/api/bid-uploads?tender_key=cf:cornwall-lot2").get_json()["files"]) == 4, "a removed file stays removed")
ok(client.get("/api/bid-uploads?tender_key=cf:other").get_json()["files"] == [], "another tender's files are not shared")
r = client.put("/api/bid-uploads", headers=H, json={"tender_key": "x", "files": [{"filename": "f%d" % i, "content": "a"} for i in range(16)]})
ok(r.status_code == 400, "the file count is capped")

# Regenerate one section or one answer, free, without a full run.
r = client.post("/api/bid-regenerate-section", headers=H, json={"section": "social_value", "dissection": {"tender_title": "Herts Police plumbing"}, "profile_id": 1})
ok(r.status_code == 200 and r.get_json()["ok"] and "apprenticeships" in r.get_json()["content"], "social value written again: %s" % r.get_json())
r = client.post("/api/bid-regenerate-section", headers=H, json={"question_number": "2", "profile_id": 1,
                "dissection": {"itt_questions": [{"number": "1", "question_text": "Describe reactive repairs"},
                                                 {"number": "2", "question_text": "Describe your PPM programme", "weighting": "20%"}]}})
ok(r.status_code == 200 and r.get_json()["answer"]["question_number"] == "2" and "planned maintenance" in r.get_json()["answer"]["response"],
   "question 2 written again: %s" % r.get_json())
ok(client.post("/api/bid-regenerate-section", headers=H, json={"section": "nonsense", "profile_id": 1}).status_code == 400, "unknown section refused")

# The Word draft puts a reused paragraph in a dashed box with its label; the writer's note is boxed and labelled internal.
body = {"title": "Herts Police plumbing", "authority": "Hertfordshire Constabulary", "company_name": "Priority Plumbing Ltd",
        "writer_note": "Recommendation: NO-BID (fit 50/100).",
        "bid_response": {"cover_letter": {"content": "Dear Team,\n\nWe submit our bid.\n\nYours faithfully,\nA Person"},
                         "itt_question_responses": [{"question_number": "1", "question_text": "PPM", "status": "ok",
                                                     "response": "Our PPM visits reached [CONFIRM: 94% - from a past bid] first-time fix."}]}}
r = client.post("/api/download-proposal", headers=H, json=body)
xml = zipfile.ZipFile(io.BytesIO(r.data)).read("word/document.xml").decode("utf-8")
ok("Reused from a past bid" in xml and 'w:val="dashed"' in xml, "the reused paragraph is boxed in the Word draft")
ok("DRAFT, INTERNAL, REMOVE BEFORE SUBMISSION" in xml and "Recommendation: NO-BID" in xml, "the writer's note is boxed and labelled")

print("release 8 re-test: %d checks passed" % n)
