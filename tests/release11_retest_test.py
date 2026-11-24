"""Re-test 23 Nov pm (release 11): buyer questions lost on Matthewsgreen, a cut word after money, past-bid content leaking into an
unrelated bid, and an unflagged empty price section. Run: python tests/release11_retest_test.py"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ENABLE_SCHEDULERS", "0")
os.environ.setdefault("ENABLE_EMAIL_SCHEDULER", "0")

from tender_app import bid_release7 as r7  # noqa: E402
from tender_app import bid_style as bs  # noqa: E402
from tender_app import bid_writer as bw  # noqa: E402
from tender_app.bid_style_learn import find_reusable  # noqa: E402

n = 0


def ok(cond, msg):
    global n
    n += 1
    assert cond, msg


# 1. Matthewsgreen's Q-numbered questionnaire is recovered when the dissection returns none.
doc4 = open(os.path.join(ROOT, "tests", "fixtures", "matthewsgreen_reconstructed", "Doc4_Questionnaire_Updated_12.10.26.txt"), encoding="utf-8").read()
qs = r7.recover_questions(doc4)
nums = [q["number"] for q in qs]
ok(nums == ["Q1", "Q2", "Q3.I", "Q3.II", "Q4.I", "Q5.I", "Q5.II", "Q5.III", "Q6", "Q7", "Q8"], nums)
ok(all(q["number"] != "Q4" for q in qs), "a parent heading with sub-questions is not a question of its own")
ok(next(q for q in qs if q["number"] == "Q4.I")["weighting"] == "40%", "Q4 keeps its 40% weighting")
ok(next(q for q in qs if q["number"] == "Q3.II")["weighting"] == "10%", "a sub-question inherits its parent's weighting")
ok(next(q for q in qs if q["number"] == "Q4.I")["question_text"].endswith("shared costs."), "a question wrapped onto a second line is whole")
built = bw.extract_questions({"itt_questions": qs})
ok(bw.questionnaire_mode(built) and len(built) == 11, "the recovered questions run in questionnaire mode")
ok(r7.recover_questions("Q1 Describe it fully please.\nQ2 Explain the thing fully please.") == [], "two lines are not a questionnaire")
ok(r7.recover_questions("\n".join(["1.1 The Contractor shall provide the services.", "1.2 The Contractor shall describe it."])) == [], "clauses are not questions")

# 2. A money figure never swallows the first letter of the next word.
import server  # noqa: E402

txt = "We hold financial capacity for a £300,000 multi-year contract and a £2.5m turnover and £40 m reserve."
out = server.verify_and_clean_bid_claims({"cover_letter": {"content": txt}, "itt_question_responses": []}, "org", [],
                                         tender_facts="estimated value £300,000")["cover_letter"]["content"]
ok("]ulti" not in out and "multi-year" in out, out)
ok("a £300,000 multi-year contract" in out, "the buyer's own figure is kept intact: " + out)
ok(out.count("not found in company data") == 2, "invented figures (with or without a space before m) are still replaced: " + out)

# 3. A weak past-bid match gives style only.
lease = {"id": 1, "title": "Matthewsgreen Community Centre lease", "outcome": "won", "buyer": "Wokingham",
         "text": "## Question 1: Equality, diversity and safeguarding\n\n" + (
             "We will run a sensitivity test with income down 20% and costs up 10%, and our trustees safeguard children and adults at risk "
             "through a named lead, annual training for every volunteer and a clear route for raising any concern with the Council. ") * 2
         + "".join("\n\n## Question %d: Topic %s\n\n%s\n" % (i, w, "The centre opens seven days a week for residents and groups. " * 7)
                   for i, w in ((2, "hours"), (3, "staffing"), (4, "finance")))}
blk = bs.past_bids_block([lease])
ok("income down 20%" in blk, "a matching bid is shown in full")
weak = dict(lease, style_only=True)
blk2 = bs.past_bids_block([weak])
ok("income down" not in blk2 and "Weak match" in blk2, "a weak match shows no wording or figures")
ok(find_reusable("Equality and safeguarding", [lease]) is not None, "a matching bid still offers a reusable paragraph")
ok(find_reusable("Equality and safeguarding", [weak]) is None, "a weak match offers no reusable paragraph")
src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
ok('_b["style_only"] = True' in src and "LOW_MATCH_PCT" in src, "the draft route marks weak matches style-only")

# 4. Prices-not-entered also fires when the gaps say rate, fee or cost.
bid = {"itt_question_responses": [{"question_number": "1", "response": "Fees. " + " ".join(
    "[NEEDS INPUT: day rate for role %d]" % i for i in range(5)) + " Done.", "status": "ok"}]}
ok(any(i["check"] == "prices_missing" for i in bw.qa_gate(bid)["issues"]), "empty rate / fee lines raise the prices warning")

# 5. The answer plan says when no numbered questions were found.
app = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
ok("We found no numbered questions - check the documents." in app, "the plan warns before credits are spent")

# 6. More "N years" phrasings are tagged; a reworded repeat is dropped; links and prices are real.
from tender_app import bid_release5 as r5  # noqa: E402
for phrase in ("We have 12 years working in plumbing and heating.", "Our firm brings 12 years delivering drainage services.",
               "Over 12 years of hands-on expertise in water hygiene."):
    out = r5.reconcile_experience(phrase, None)
    ok("[CONFIRM" in out, "years claim tagged: " + phrase)
ok(r5.reconcile_experience("The contract runs for 12 years working in phases.", None).count("[CONFIRM") == 0, "a contract term is not tagged")
base = "We will keep a named lead on site for every visit, record each job in our system, report to the Council monthly and review every completed job with the client within two working days of completion."
br = {"itt_question_responses": [{"question_number": "1", "response": base},
                                 {"question_number": "2", "response": "Opening paragraph about mobilisation, training and the first thirty days of the contract on site." + chr(10) * 2 + base.replace("keep a named lead", "keep a named supervisor") + " Thank you."}]}
ok(r5.drop_near_duplicates(br) == 1, "a lightly reworded repeat in another answer is dropped")
home = open(os.path.join(ROOT, "web", "login.html"), encoding="utf-8").read()
ok('id="toggleLink" href="/login.html#signup"' in home and 'id="forgotLink" href="/login.html#forgot"' in home, "sign-up and forgot links are real")
pj = open(os.path.join(ROOT, "web", "pricing.js"), encoding="utf-8").read()
ok("_priceFallback" in pj and 'anonymousOk: true' in pj.split("catPromise")[1][:200], "logged-out prices fall back instead of staying blank")
ph = open(os.path.join(ROOT, "web", "pricing.html"), encoding="utf-8").read()
ok('id="pilotPlan"' in ph and "Answer Bank" in ph and "certificates" in ph, "the pilot section lists the onboarding steps")

print("release 11 re-test: %d checks passed" % n)
