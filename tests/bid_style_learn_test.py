"""Release 8: style profile, style brief, reusable text and answer plan. Run: python tests/bid_style_learn_test.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tender_app import bid_style_learn as sl
from tender_app.bid_writer import extract_questions

n = 0


def ok(c, m):
    global n
    n += 1
    assert c, "FAILED: " + m


FILLER = " ".join(["We run the centre every day with a trained team and clear processes in place."] * 6)
PAST = ("Q1. Tell us about your organisation\n"
        "We are a community operator with 12 years of experience and 94% satisfaction in 2024. " + FILLER + "\n\n"
        "How we deliver:\n" + FILLER + "\n\nRisks:\n" + FILLER + "\n\n"
        "Q6. Equality, diversity, inclusion and safeguarding\n"
        "Wokingham Borough Council requires safeguarding. We adopt an EDI policy and a safeguarding policy covering children "
        "and adults at risk, reviewed every 12 months, with a code of conduct for staff and volunteers and a complaints procedure "
        "that is shared with the council and reported to trustees each quarter.\n\n"
        "How we deliver:\n" + FILLER + "\n\nRisks:\n" + FILLER + "\n\n"
        "| Check | When |\n| Policy review | Annual |\n")
BIDS = [{"id": 1, "title": "Matthewsgreen EOI", "buyer": "Wokingham Borough Council", "outcome": "won", "text": PAST},
        {"id": 2, "title": "Lost library bid", "buyer": "Kent", "outcome": "lost", "text": PAST}]

a = sl.split_answers(PAST)
ok(len(a) == 2 and a[0]["label"].startswith("Q1") and a[1]["label"].startswith("Q6"), "answers split on question headings: %s" % [x["label"] for x in a])
ok(sl.split_answers("Short text.") == [], "too little text learns nothing")
ok(len(sl.split_answers(FILLER * 2)) == 1, "no headings: the whole document is one answer")

p = sl.learn_style(BIDS)
ok(p["learned"] and p["answers"] == 4 and p["tables"] >= 1, "profile counts the answers: %s" % p)
ok(10 <= p["words_per_sentence"] <= 30 and p["figures_per_100"] > 0, "sentence length and figure density are measured")
ok("How we deliver" in p["common_headings"] and "Risks" in p["common_headings"], "common sub-headings found: %s" % p["common_headings"])
ok(not sl.learn_style([])["learned"] and not sl.learn_style([{"text": "tiny"}])["learned"], "nothing to learn from")
brief = sl.style_brief(p)
ok("sentences near" in brief and "direct answer" in brief, "style brief: %s" % brief)
ok(sl.style_brief({"learned": False}) == "", "no brief without a profile")

r = sl.find_reusable("Describe your approach to ethics, safeguarding and equality for the evaluation", BIDS, "our own data")
ok(r and r["source_question"].startswith("Q6") and r["source_bid"] == "Matthewsgreen EOI", "matching topic found with its source: %s" % r)
ok("[the buyer]" in r["paragraph"] and "Wokingham" not in r["paragraph"], "old buyer's name replaced")
ok("[CONFIRM: 12 months" not in r["paragraph"] or r["facts_to_confirm"] >= 1, "facts counted")
ok(sl.find_reusable("Describe your pricing model and rates", BIDS) is None, "no match on a different topic")
ok(sl.find_reusable("safeguarding and equality", [BIDS[1]]) is None, "a lost bid gives no reusable text")

qs = extract_questions({"itt_questions": [
    {"number": "1", "question_text": "Describe your approach to ethics, safeguarding and equality", "weighting": "15%"},
    {"number": "2", "question_text": "Provide your price schedule", "weighting": "5%"}]})
rows = sl.answer_plan(qs, p, BIDS, "")
ok(len(rows) == 2 and rows[0]["number"] == "1" and rows[0]["reusable"] and rows[1]["reusable"] is None, "plan keeps the buyer's order, reuse only where it fits")
ok(rows[0]["words"] >= 250 and "How we deliver" in rows[0]["subheadings"], "words and learned sub-headings")
rows2 = sl.answer_plan(qs, {"learned": False}, [], "")
ok(rows2[0]["subheadings"] == "Approach · Delivery · Summary" and rows2[0]["reusable"] is None, "no past bid: standard sub-headings")
print("bid style learn: %d checks passed" % n)
