"""Release 8 (F1): readiness score. Run: python tests/readiness_test.py"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tender_app.bid_writer import qa_gate
from tender_app.readiness import readiness, signoff_items

n = 0


def ok(c, m):
    global n
    n += 1
    assert c, "FAILED: " + m


TODAY = date(2026, 11, 17)
GOOD = "We will run the centre with a trained team, clear opening hours and a monthly review with the council. " * 4
GOOD2 = "Our finances rest on steady hire income, a reserve held for repairs, and a cautious plan that we review every quarter with the trustees. " * 3
S1 = {"itt_questions": [{"number": "1", "question_text": "About you", "weighting": "10%"},
                        {"number": "4", "question_text": "Financial sustainability", "weighting": "40%"}],
      "submission_deadline": {"date": "08/12/2026", "time": "12:00"},
      "required_submission_documents": [{"name": "Price schedule", "kind": "commercial"}, {"name": "DDQ", "kind": "form"}]}


def bid(**over):
    b = {"itt_question_responses": [
        {"question_number": "1", "question_text": "About you", "response": GOOD, "weighting": "10%", "budget_words": 80},
        {"question_number": "4", "question_text": "Financial sustainability", "response": GOOD2, "weighting": "40%", "budget_words": 80}]}
    b.update(over)
    return b


b = bid()
r = readiness(b, S1, qa_gate(b), today=TODAY)
ok(not r["limited"] and r["score"] == 100 and r["p0_open"] == 0, "a clean, complete draft scores 100: %s" % r)
ok(r["signoff_open"] == len(r["signoff"]) and not r["ready"], "sign-off items start unticked, so not ready")
keys = [x["key"] for x in r["signoff"]]
ok("insurance" in keys and "doc-price-schedule" in keys and "doc-ddq" in keys, "sign-off list built from the buyer's documents: %s" % keys)

# a missing answer
b2 = bid()
b2["itt_question_responses"] = b2["itt_question_responses"][:1]
r = readiness(b2, S1, qa_gate(b2), today=TODAY)
ok(r["items"][0]["severity"] == "P0" and "4" in r["items"][0]["text"] and r["score"] <= 88, "a missing buyer question is a P0: %s" % r["items"][0])
# wrong order
b3 = bid()
b3["itt_question_responses"].reverse()
r = readiness(b3, S1, qa_gate(b3), today=TODAY)
ok(any(i["check"] == "order" for i in r["items"]), "answers out of order are flagged")
# the 40% question's problem comes before the 10% question's
b4 = bid()
b4["itt_question_responses"][0]["response"] = "We run it and"
b4["itt_question_responses"][1]["response"] = "We run it and"
r = readiness(b4, S1, qa_gate(b4), today=TODAY)
p0 = [i for i in r["items"] if i["severity"] == "P0"]
ok(len(p0) == 2 and "40%" in p0[0]["text"] and "10%" in p0[1]["text"], "ordered by the marks at stake: %s" % [i["text"] for i in p0])
# placeholders: information after drafting, P0 at the gate
b5 = bid()
b5["itt_question_responses"][0]["response"] = GOOD + " [NEEDS INPUT: your turnover] and [CONFIRM: a date]."
r = readiness(b5, S1, qa_gate(b5), gate=False, today=TODAY)
ok(any(i["check"] == "placeholders" and i["severity"] == "P2" for i in r["items"]) and r["p0_open"] == 0, "placeholders are info after drafting")
r = readiness(b5, S1, qa_gate(b5), gate=True, today=TODAY)
ok(any(i["check"] == "placeholders" and i["severity"] == "P0" for i in r["items"]) and r["p0_open"] == 1, "placeholders are P0 at the gate")
ok("2 item(s) still open: 1 need input, 1 need confirming" in [i["text"] for i in r["items"] if i["check"] == "placeholders"][0], "the same count as the export refusal")
# reused facts
b6 = bid()
b6["itt_question_responses"][0]["response"] = GOOD + " A fund of [CONFIRM: GBP 2,500 - from a past bid]."
r = readiness(b6, S1, qa_gate(b6), today=TODAY)
ok(any(i["check"] == "reused_facts" for i in r["items"]), "facts reused from a past bid are listed")
# deadline
S2 = dict(S1, submission_deadline={"date": "01/11/2026", "time": "12:00"})
r = readiness(b, S2, qa_gate(b), today=TODAY)
ok(any(i["check"] == "deadline" and i["severity"] == "P0" for i in r["items"]), "a passed deadline is a P0")
S3 = dict(S1, submission_deadline={"date": "08/12/2026"})
ok(any("no time" in i["text"] for i in readiness(b, S3, qa_gate(b), today=TODAY)["items"]), "a deadline without a time is flagged")
# price figures
b7 = bid(pricing_notes={"content": "Our fee is GBP 45,000 for the term."})
ok(any(i["check"] == "price_figures" for i in readiness(b7, S1, qa_gate(b7), today=TODAY)["items"]), "price figures are listed for checking")
# no buyer documents: no number
r = readiness(b, {}, qa_gate(b), today=TODAY)
ok(r["limited"] and r["score"] is None and "Limited check" in r["label"], "no buyer documents: a limited check, no number")
# signoffs ticked
done = {k: {"by": "a@b.c", "at": "2026-11-17"} for k in keys}
r = readiness(b, S1, qa_gate(b), signoffs=done, today=TODAY)
ok(r["ready"] and r["signoff_open"] == 0 and r["signoff"][0]["by"] == "a@b.c", "everything ticked and no P0: ready")
ok(len(signoff_items({})) == 2, "the standard sign-off items exist without buyer documents")
_r = {"itt_question_responses": [{"question_number": "1", "weighting": "20%", "response": "word " * 50},
                                 {"question_number": "2", "weighting": "40%", "response": "word " * 400},
                                 {"question_number": "3", "weighting": "40%", "response": "word " * 400}]}
_res = readiness(_r, {"mandatory_requirements": ["Provide your EDRPOU registration number"]}, {})
ok(any(i["check"] == "word_share" for i in _res["items"]), "a 20% question with 6% of the words is flagged")
ok(any(i["check"] == "registration_id" for i in _res["items"]), "the overseas registration number the buyer asks for is flagged")
_r["declarations"] = {"content": "Our EDRPOU number is 12345678."}
ok(not any(i["check"] == "registration_id" for i in readiness(_r, {"mandatory_requirements": ["EDRPOU number"]}, {})["items"]), "supplied number clears it")

print("readiness: %d checks passed" % n)
