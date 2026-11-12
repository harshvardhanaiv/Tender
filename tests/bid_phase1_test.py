"""Phase 1 of the Matthewsgreen fix plan: bid writer, claim checks, alert dates, lots.

Plain asserts, no DB, no network, no model: run with `python tests/bid_phase1_test.py`.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_app import bid_writer as bw  # noqa: E402
from tender_app.llm_json import parse_llm_json_checked  # noqa: E402

PASSED = 0


def ok(cond, msg):
    global PASSED
    assert cond, msg
    PASSED += 1


# --- Matthewsgreen-like Stage 1 dissection -----------------------------------------------------------
STAGE1 = {
    "tender_title": "Matthewsgreen Community Centre operator",
    "scope_of_work": {"summary": "Operate the Matthewsgreen community centre under a 10-year lease: hall hire, "
                                 "bookings for community groups, opening hours, car park, sinking fund.",
                      "key_deliverables": ["community bookings", "hall hire", "lease"]},
    "itt_questions": [
        {"number": "1", "question_text": "Organisation and experience operating community venues", "weighting": "10%"},
        {"number": "4.I", "question_text": "Financial sustainability: provide a business plan with income and costs", "weighting": "40%"},
        {"number": "5.I", "question_text": "Vision 2035: how the centre serves residents", "weighting": "20%"},
        {"number": "5.II", "question_text": "A month in the life of the centre", "weighting": "20%"},
        {"number": "7", "question_text": "Any other information", "weighting": "0%"},
        {"number": "8", "question_text": "Describe your safeguarding approach", "word_limit": 500, "weighting": None},
    ],
    "published_clarifications": [
        {"question": "What rent does the Council expect?", "answer": "No expectations - please state within your bid", "source": "Clarification log 6 Nov"},
    ],
}

# 1. Questions keep the buyer's numbering and get budgets from weightings
qs = bw.extract_questions(STAGE1)
ok([q["number"] for q in qs] == ["1", "4.I", "5.I", "5.II", "7", "8"], "buyer numbering kept, in order")
by = {q["number"]: q for q in qs}
ok(by["4.I"]["budget_words"] == 2000, "40% question gets the most words (capped at 2000)")
ok(by["5.I"]["budget_words"] == by["5.II"]["budget_words"] == 600, "sub-parts share the parent's 20%")
ok(by["1"]["budget_words"] == 600, "10% -> 600 words")
ok(by["7"]["budget_words"] == 250 and by["7"]["scored"] is False, "0% question is unscored and gets the floor")
ok(by["8"]["budget_words"] == 500, "an explicit word limit is the budget")
ok(bw.questionnaire_mode(qs) and not bw.questionnaire_mode(qs[:1]), "two or more questions -> questionnaire mode")
ok(bw.max_tokens_for(2000) <= 7000, "token cap stays under the model limit")

# 2. Truncation detection
ok(parse_llm_json_checked('{"response": "Done."}') == ({"response": "Done."}, False), "complete JSON is not truncated")
v, t = parse_llm_json_checked('{"response": "We will open the centre from 8am and')
ok(t and v["response"].startswith("We will open"), "cut-off JSON is repaired but flagged")
ok(bw.ends_cleanly("We will report monthly."), "sentence ends cleanly")
ok(not bw.ends_cleanly("We will report monthly to the"), "dangling word = cut off")
ok(not bw.ends_cleanly("Our approach covers bookings and"), "dangling 'and' = cut off")
ok(bw.ends_cleanly("Key points:\n- Monthly reports\n- Open-book accounts"), "a bullet may end without a full stop")
ok(not bw.ends_cleanly("| Room | Rate |\n| Hall | £25"), "half a table row = cut off")
ok(bw.ends_cleanly("See the plan [NEEDS INPUT: figure]"), "ends on a placeholder")
ok(bw.ends_cleanly("We look forward to working with you.\n\nYours faithfully,\n\nHemant\nAIVHUB LTD\n+44 7976735746"),
   "a letter may end on its signature block (live run 1 false alarm)")

# 3. write_question: retries a cut-off answer, then marks it; never returns a silent partial answer
calls = []


def fake_call_factory(replies):
    def call(system, user, max_tokens):
        calls.append(max_tokens)
        return replies.pop(0)
    return call


q4 = by["4.I"]
calls.clear()
res = bw.write_question(fake_call_factory(['{"response": "Income comes from hall hire and', '{"response": "Income comes from hall hire. Costs are capped.", "placeholders_used": []}']), q4, "PROMPT")
ok(res["status"] == "ok" and res["response"].endswith("capped."), "retry fixes a cut-off answer")
ok(len(calls) == 2 and calls[1] > calls[0], "the retry gets more room")
res = bw.write_question(fake_call_factory(['{"response": "Income comes from', '{"response": "Income comes from hall hire and']), q4, "PROMPT")
ok(res["status"] == "truncated" and "this answer was cut off" in res["response"], "still cut off -> visibly marked")


def boom(system, user, max_tokens):
    raise RuntimeError("API down")


res = bw.write_question(boom, q4, "PROMPT")
ok(res["status"] == "failed" and "could not be generated" in res["response"], "a failed call is marked, not dropped")

# 3b. Word budgets: an over-long answer is condensed once; a condensed version that is cut off or adds a
# figure is rejected; [NEEDS INPUT] tags count as one word each
q7 = by["7"]  # budget 250, cap 275
long_ans = "We will hold monthly user meetings. " * 60 + "[NEEDS INPUT: frequency]."
ok(bw.answer_words("Rent is [NEEDS INPUT: annual rent figure agreed with the council] a year.") == 5, "a tag counts as one word")
calls.clear()
res = bw.write_question(fake_call_factory([json.dumps({"response": long_ans}),
                                           json.dumps({"response": "We will hold user meetings [NEEDS INPUT: frequency]."})]), q7, "PROMPT")
ok(len(calls) == 2 and res["status"] == "ok" and res["word_count"] == 6 and res.get("condensed_from_words") == 361,
   "over the cap -> condensed once")
calls.clear()
res = bw.write_question(fake_call_factory([json.dumps({"response": "Short and complete answer."})]), q7, "PROMPT")
ok(len(calls) == 1 and "condensed_from_words" not in res, "within the cap -> no extra call")
res = bw.write_question(fake_call_factory([json.dumps({"response": long_ans}),
                                           json.dumps({"response": "We will hold meetings costing £4,000 a year."})]), q7, "PROMPT")
ok(res["response"] == long_ans.strip() and "condensed_from_words" not in res, "a condensed answer with a new £ figure is rejected")
res = bw.write_question(fake_call_factory([json.dumps({"response": long_ans}), '{"response": "We will hold meetings and']), q7, "PROMPT")
ok(res["response"] == long_ans.strip() and res["status"] == "ok", "a cut-off condensed answer is rejected")
res = bw.write_question(fake_call_factory([json.dumps({"response": long_ans}), "not json at all"]), q7, "PROMPT")
ok(res["response"] == long_ans.strip(), "a failed condensing call keeps the long answer")
ok("at most 250 words" in bw.question_prompt(q7, title="T", authority="A", company="C", tender_dissection="",
                                             clarifications_block="", company_data="", company_context="",
                                             answer_bank="", winning_bids="", other_questions=[]),
   "the prompt states the budget as a maximum")

# 4. Prompt carries grounding rules, the clarification answers and the other questions
prompt = bw.question_prompt(q4, title="Matthewsgreen", authority="Wokingham Borough Council", company="AIVHUB LTD",
                            tender_dissection=json.dumps(STAGE1), clarifications_block=bw.clarifications_block(STAGE1),
                            company_data="Org Name: AIVHUB LTD", company_context="", answer_bank="", winning_bids="",
                            other_questions=qs)
ok("GROUNDING RULES" in prompt and "[CONFIRM:" in prompt, "grounding rules in every question prompt")
ok("No expectations - please state within your bid" in prompt, "published clarification answers reach the writer")
ok("- 5.II: A month in the life" in prompt and "- 4.I:" not in prompt, "other questions listed, not this one")

# 5. Identity
clean, issues = bw.sanitize_identity({"contact_name": "Hemant [Surname]", "email": "mahendra.aivhub.com",
                                      "email_2": "ops@aivhub.com", "org_name": "AIVHUB LTD", "phone": "0123"})
ok(clean["contact_name"].startswith("[NEEDS INPUT") and clean["email"].startswith("[NEEDS INPUT"), "bad name and email replaced")
ok(clean["email_2"] == "ops@aivhub.com" and clean["org_name"] == "AIVHUB LTD", "valid fields untouched")
ok(len(issues) == 2, "two identity issues reported")
ok(bw.company_label("", {}) == bw.NEEDS_COMPANY and bw.company_label("Bidding Company") == bw.NEEDS_COMPANY, "no generated company name")
ok(bw.company_label("AIVHUB", {"org_name": "AIVHUB LTD"}) == "AIVHUB LTD", "legal name preferred")

# 6. Certifications
gt_policy = "aivhub ltd isms policy (iso 27001) and qms policy (iso 9001)"
out = bw.flag_unverified_certifications("We are ISO 27001 certified and certified to ISO 9001.", gt_policy)
ok("ISO 27001 certified" not in out and "certified to ISO 9001" not in out and out.count("[NEEDS INPUT") == 2, "policy is not a certificate")
gt_cert = "iso 9001:2015 certificate no. 12345 issued by bsi"
ok("ISO 9001 certified" in bw.flag_unverified_certifications("We are ISO 9001 certified.", gt_cert), "a real certificate is kept")
ok(bw.flag_unverified_certifications("We follow ISO 27001 principles.", gt_policy) == "We follow ISO 27001 principles.", "mentioning a standard is not a claim")

# 7. Answer Bank relevance: fintech case study dropped for a community-centre bid
rows = [
    ("Experience", "Describe relevant experience", "We delivered a payments platform for a leading European bank, cutting fraud by 40%, and a claims portal for a global insurer."),
    ("Experience", "Community venue experience", "We ran bookings for a community hall and residents' groups, managing hall hire and a lease with the council."),
    ("Health & Safety", "H&S policy", "Our H&S policy is reviewed annually."),
]
kept, dropped = bw.filter_answer_bank(rows, STAGE1, "Matthewsgreen community centre")
ok(dropped == 1 and all("bank" not in r[2] for r in kept), "off-sector entry dropped")
ok(kept[0][1] == "Community venue experience" and any(r[0] == "Health & Safety" for r in kept), "relevant first, policies kept")

# 8. Sector: a community-centre lease is not an IT tender
s = bw.tender_sector("Matthewsgreen Community Centre operator", "Lease of a community centre with hall hire", "70130000")
ok(s == {"digital": False, "property": True}, f"community centre -> property, not digital ({s})")
s = bw.tender_sector("Data platform and Power BI support", "Azure data warehouse", "72000000")
ok(s["digital"], "IT tender -> digital")

# 9. QA gate
bid = {
    "document_title": "Tender Response: Matthewsgreen | Bidding Company",
    "cover_letter": {"content": "Dear Wokingham Borough Council Procurement Team,\n\nPlease find our response."},
    "itt_question_responses": [
        {"question_number": "1", "response": "We run community venues. " * 70, "status": "ok", "budget_words": 600},
        {"question_number": "4.I", "response": "Income comes from hall hire and", "status": "ok", "budget_words": 2000},
        {"question_number": "5.I", "response": "", "status": "failed"},
        {"question_number": "8", "response": "Safeguarding lead. " * 300, "status": "ok", "budget_words": 500, "word_limit": 500},
    ],
    "declarations": {"content": "[CONFIRM: no exclusion grounds apply]"},
}
bid["cover_letter"]["content"] += "\n\nContact: Hemant [Surname], email: mahendra.aivhub.com."
qa = bw.qa_gate(bid, closed=True)
checks = {(i["check"], i["severity"], i["section"]) for i in qa["issues"]}
ok(("truncation", "P0", "Question 4.I") in checks, "cut-off answer is P0")
ok(("coverage", "P0", "Question 5.I") in checks, "missing answer is P0")
ok(("word_limit", "P1", "Question 8") in checks, "over the word limit is P1")
ok(any(i["check"] == "identity" and "Bidding Company" in i["detail"] for i in qa["issues"]), "generated company name is P0")
ok(any(i["check"] == "identity" and "Surname" in i["detail"] for i in qa["issues"]), "[Surname] is P0")
ok(any(i["check"] == "identity" and "mahendra.aivhub.com" in i["detail"] for i in qa["issues"]), "email without @ is P0")
ok(("status", "P1", "Whole bid") in checks and not qa["passed"], "closed tender flagged; gate fails")
ok(qa["placeholders"] == len(bw.find_placeholders(bid)) == 2, "placeholder count = [Surname] + [CONFIRM]")
over = bw.qa_gate({"itt_question_responses": [
    {"question_number": "2", "response": "We plan the year. " * 200, "status": "ok", "budget_words": 600},
    {"question_number": "3", "response": "We plan the year. " * 160, "status": "ok", "budget_words": 600},
    {"question_number": "4", "response": "Cost [NEEDS INPUT: annual cost of drains maintenance contract]. " * 300, "status": "ok", "budget_words": 600}]})
ob = {(i["check"], i["severity"], i["section"]) for i in over["issues"]}
ok(("word_budget", "P2", "Question 2") in ob and not any(s == "Question 3" for _, _, s in ob), "over budget +10% is P2; within it is not")
ok(not any(s == "Question 4" and c == "word_budget" for c, _, s in ob), "tag hints don't count towards the budget")
ok(over["passed"], "a budget overrun alone does not fail the gate")
short = bw.qa_gate({"itt_question_responses": [
    {"question_number": "1", "response": "We run venues. " * 50, "status": "ok", "budget_words": 600, "weighting": "10%"},
    {"question_number": "Organisation information", "response": "We run venues. " * 50, "status": "ok", "budget_words": 500, "weighting": None}]})
sh = {(i["check"], i["section"]) for i in short["issues"]}
ok(("coverage", "Question 1") in sh and ("coverage", "Question Organisation information") not in sh,
   "short is flagged for a weighted question, not an unweighted one")
ok("not weighted" in bw.question_prompt(dict(q7, weight_pct=None), title="T", authority="A", company="C", tender_dissection="",
                                        clarifications_block="", company_data="", company_context="", answer_bank="",
                                        winning_bids="", other_questions=[]), "unweighted prompt says so")
dup = {"itt_question_responses": [{"question_number": "1", "response": ("The centre will open seven days a week for residents and groups of all ages with fair prices. " * 3).strip(), "status": "ok"},
                                  {"question_number": "2", "response": ("The centre will open seven days a week for residents and groups of all ages with fair prices. " * 3).strip(), "status": "ok"}]}
ok(any(i["check"] == "duplication" for i in bw.qa_gate(dup)["issues"]), "duplicated paragraph found")
marked = {"itt_question_responses": [{"question_number": "3", "response": "Text.\n\n[NEEDS INPUT: this answer was cut off - regenerate question 3]"}]}
ok(any(i["check"] == "truncation" and i["severity"] == "P0" for i in bw.qa_gate(marked)["issues"]), "the cut-off marker alone is P0 (export has no status)")
ok(bw.qa_gate({"cover_letter": {"content": "All good."}, "itt_question_responses": [{"question_number": "1", "response": "Yes. " * 300, "status": "ok", "budget_words": 300}]})["passed"], "clean bid passes")

# 10. Placeholder pattern
txt = "See [NEEDS INPUT: x], [Add 1–2 case studies here], Date: [to be inserted], [link](http://a), [1], [ ], [x]"
ok([m.group(0) for m in bw.PLACEHOLDER_RE.finditer(txt)] == ["[NEEDS INPUT: x]", "[Add 1–2 case studies here]", "[to be inserted]"], "placeholder pattern")

# 11. Claim checker in server.py (figures from the buyer allowed, invented ones not)
import server  # noqa: E402

br = {
    "cover_letter": {"content": "We will invest £20,000 and reach 60% utilisation. The sinking fund is £2,500 a year and Q4 is worth 40%. "
                                "We worked for a London borough and a leading European bank. We are ISO 27001 certified."},
    "itt_question_responses": [],
}
full = server.verify_and_clean_bid_claims(br, "org name: aivhub ltd. isms policy iso 27001", [],
                                          tender_facts="sinking fund £2,500 pa. weighting 40%")["cover_letter"]["content"]
ok("[PLACEHOLDER: insert verified figure — '£20,000' not found in company data] and" in full, "tag keeps the following space")
end = server.verify_and_clean_bid_claims({"cover_letter": {"content": "Over ten years it totals £25,000."}, "itt_question_responses": []},
                                         "org", [], tender_facts="sinking fund £2,500")["cover_letter"]["content"]
ok(end.endswith("company data]."), f"the sentence keeps its full stop (live run 2): {end}")
out = bw.PLACEHOLDER_RE.sub("", full)  # what is left as asserted prose
ok("£20,000" not in out and "60%" not in out, "invented money and percentage replaced")
ok("£2,500" in out and "40%" in out, "the buyer's own figures kept")
ok("London borough" not in out and "European bank" not in out, "invented client types replaced")
ok("ISO 27001 certified" not in out, "policy-only certification claim replaced")

# 12. Alert dates: a "last updated" date or today's date is not a submission deadline
from tender_app.deep_scrape import _deadlines  # noqa: E402

today = datetime.now(timezone.utc).date()
page = (f"Tender documents. Last updated {today.day} {today.strftime('%B %Y')} by the council.\n"
        "Tenders must be returned by 8 November 2026 at 23:00.\n"
        "Published 1 October 2026 - submit your response through the portal.\n"
        "Clarification questions by 2 November 2026.")
found = {d["date"]: d["kind"] for d in _deadlines(page)}
ok(found.get("8 November 2026 at 23:00") == "submission", f"labelled deadline kept ({found})")
ok(found.get(f"{today.day} {today.strftime('%B %Y')}") == "other", "today's 'last updated' date is not a deadline")
ok(found.get("1 October 2026") == "other", "'Published' date is not a deadline")
ok(found.get("2 November 2026") == "clarification", "clarification deadline classified")

# 13. Lots: one OCDS lot is not "lotted"
from etenders_scraper.sources import find_tender as ft  # noqa: E402


class _Resp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class _Sess:
    def __init__(self, data):
        self.headers = {}
        self._d = data

    def get(self, url, timeout=20):
        return _Resp(self._d)


orig = ft._make_session
try:
    for lots, want in (([{"id": "1"}], "No"), ([{"id": "1"}, {"id": "2"}], "Yes"), ([], "No")):
        ft._make_session = lambda lots=lots: _Sess({"releases": [{"tender": {"title": "T", "lots": lots}, "parties": []}]})
        ok(ft.fetch_find_tender_details("093543-2026")["contract_awarded_in_lots"] == want, f"lots {len(lots)} -> {want}")
finally:
    ft._make_session = orig

print(f"bid_phase1_test: {PASSED} checks passed")
