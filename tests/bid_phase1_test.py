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
end2 = server.verify_and_clean_bid_claims({"cover_letter": {"content": "Over the term it totals £31,000."}, "itt_question_responses": []},
                                          "org", [], tender_facts="sinking fund £2,500")["cover_letter"]["content"]
ok(end == "Over ten years it totals £25,000." and end2.endswith("company data]."), f"derived 10 x £2,500 kept; an unrelated figure replaced and keeps its full stop: {end} / {end2}")
out = bw.PLACEHOLDER_RE.sub("", full)  # what is left as asserted prose
ok("£20,000" not in out and "60%" not in out, "invented money and percentage replaced")
ok("£2,500" in out and "40%" in out, "the buyer's own figures kept")
ok("London borough" not in out and "European bank" not in out, "invented client types replaced")
ok("ISO 27001 certified" not in out, "policy-only certification claim replaced")

# R7: a decimal percentage is replaced whole, never split into "99." + a tag for "9%"
up = server.verify_and_clean_bid_claims({"executive_summary": {"content": "We will meet 99.9% uptime and 12.5 % growth."},
                                         "itt_question_responses": []}, "org", [], tender_facts="")["executive_summary"]["content"]
ok("99.[" not in up and "'99.9%'" in up and "'12.5%'" in up, f"decimal percentage replaced whole: {up}")
kept = server.verify_and_clean_bid_claims({"executive_summary": {"content": "We will meet 99.9% uptime."}, "itt_question_responses": []},
                                          "org. sla uptime 99.9%", [], tender_facts="")["executive_summary"]["content"]
ok(kept == "We will meet 99.9% uptime.", f"a decimal percentage in the company data is kept: {kept}")

# R8: invented actions become [CONFIRM] items; the QA gate fails any left as prose
act = server.verify_and_clean_bid_claims({"cover_letter": {"content": "Thank you. We have raised clarification queries through the In-Tend "
                                                                      "Correspondence Facility. We will deliver on time."},
                                          "itt_question_responses": [{"question_number": "1", "response": "Scope is unclear. We have raised this in our clarification questions."}]},
                                         "org", [], tender_facts="")
ok(act["cover_letter"]["content"].startswith("Thank you. [CONFIRM: you have done this") and act["cover_letter"]["content"].endswith("We will deliver on time."),
   f"invented action in the letter -> [CONFIRM]: {act['cover_letter']['content']}")
ok("[CONFIRM: you have done this" in act["itt_question_responses"][0]["response"], "and in an answer")
ok(not any(i["check"] == "invented_action" for i in bw.qa_gate(act)["issues"]), "once tagged, the gate passes it")
for s in ("We will raise this as a clarification question.", "Our team has raised standards across 12 venues.",
          "This issue has been raised by residents for years."):
    ok(bw.flag_invented_actions(s) == s, f"not an invented action: {s}")
raw = {"cover_letter": {"content": "We have visited the site and met the Council's officers."}, "itt_question_responses": []}
ok(any(i["check"] == "invented_action" and i["severity"] == "P0" for i in bw.qa_gate(raw)["issues"]), "untagged invented action is P0")
ok("Never say the bidder has already done something" in bw.GROUNDING_RULES, "grounding rule 10 present")

# R3: only the generated name counts, not ordinary prose
for prose in ("The trading arm is separate from the bidding company.", "The Bidding Company shall hold insurance."):
    ok(not any(i["check"] == "identity" for i in bw.qa_gate({"cover_letter": {"content": prose}})["issues"]), f"not a generated name: {prose}")
ok(any(i["check"] == "identity" for i in bw.qa_gate({"cover_letter": {"content": "Bidding Company is pleased to respond."}})["issues"]),
   "the generated name is still caught")

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

# 13b. R4: one signatory with that person's own email and phone
m10, _ = bw.sanitize_identity({"org_name": "AIVHUB LTD", "contact_name": "", "email": "hemant@aivhub.com", "phone": "",
                               "contact_name_2": "Mahendra Solankee", "email_2": "mahendra.aivhub.com", "phone_2": "+44 7976735746"})
c, block = bw.bid_contact(m10)
ok(c["name"] == "Mahendra Solankee" and c["phone"] == "+44 7976735746" and "hemant" not in block, f"one contact set, never mixed ({c})")
ok(c["email"].startswith("[NEEDS INPUT: email address of Mahendra"), "his invalid email becomes a tag, not Hemant's")
ok("email" not in bw.without_contacts(m10) and "phone_2" not in bw.without_contacts(m10) and "org_name" in bw.without_contacts(m10),
   "other contact fields left out of the company data")
c, _ = bw.bid_contact({"email": "hemant@aivhub.com"})
ok(c["name"].startswith("[NEEDS INPUT") and c["email"] == "hemant@aivhub.com", "no name anywhere: email kept, name asked for")

# 13c. EEF: titled sub-questions repeat their group's weighting and share it (24 answers came to 31k words)
eef = {"itt_questions": (
    [{"number": "Executive summary", "question_text": "Begin with a one-page summary.", "page_limit": 1}]
    + [{"number": f"Proposed team - part {i}", "question_text": "q", "weighting": "40%"} for i in range(5)]
    + [{"number": f"Costs - part {i}", "question_text": "q", "weighting": "20%"} for i in range(5)]
    + [{"number": f"Approach - part {i}", "question_text": "q", "weighting": "40%"} for i in range(6)]
    + [{"number": f"Collaboration - part {i}", "question_text": "q", "weighting": "20%"} for i in range(4)]
    + [{"number": "Risks and challenges", "question_text": "q"}])}
eq = bw.extract_questions(eef)
team = [q["budget_words"] for q in eq if q["number"].startswith("Proposed team")]
appr = [q["budget_words"] for q in eq if q["number"].startswith("Approach")]
ok(team == [480] * 5 and appr == [400] * 6, f"group weighting shared: team {team}, approach {appr}")
ok(sum(q["budget_words"] for q in eq) < 9000, f"whole bid stays near the 6,000-word budget (weights add to 120%) ({sum(q['budget_words'] for q in eq)})")
mg = bw.extract_questions({"itt_questions": [{"number": "Q1", "question_text": "a", "weighting": "10%"}, {"number": "Q2", "question_text": "b", "weighting": "10%"},
                                              {"number": "Q3.I", "question_text": "c", "weighting": "10%"}, {"number": "Q3.II", "question_text": "d", "weighting": "10%"}]})
ok([q["budget_words"] for q in mg] == [600, 600, 300, 300], "numbered questions behave as before")

# 13d. EEF: an executive summary restates the answers; answers repeating each other are still caught
same = ("The centre will open seven days a week for residents and groups of all ages with fair prices and a clear booking process for all. " * 3).strip()
sq = bw.qa_gate({"itt_question_responses": [{"question_number": "Executive summary", "response": same, "status": "ok"},
                                             {"question_number": "Team", "response": same, "status": "ok"}]})
ok(not any(i["check"] == "duplication" for i in sq["issues"]), "executive summary repeating an answer is not flagged")
ok(any(i["check"] == "duplication" for i in bw.qa_gate({"itt_question_responses": [{"question_number": "Team", "response": same, "status": "ok"},
                                                                                       {"question_number": "Costs", "response": same, "status": "ok"}]})["issues"]),
   "two answers repeating each other still are")
ok("budget, price ceiling" in bw.ANSWER_STANDARD, "answers stay inside a stated budget envelope")

# 13e. Thetford: a contact block ending on an address is a clean ending, a mid-sentence stop still is not
ok(bw.ends_cleanly("Fee: [NEEDS INPUT: fee]\n\nMahendra Solankee\nAIVHUB LTD\n5 Canterbury Road, Reading, RG2 7TB"), "ends on a postcode")
ok(bw.ends_cleanly("Contact us on\n+44 7976 735746"), "ends on a phone number")
ok(not bw.ends_cleanly("We will deliver the plan in 2027 and the"), "still catches a dangling word")

# 13f. figures derived from a published one are kept; unrelated ones are still replaced
dv = server.verify_and_clean_bid_claims({"cover_letter": {"content": "We set aside £208.33 a month and £25,000 over the term, but £9,999 is new."}, "itt_question_responses": []},
                                        "org", [], tender_facts="sinking fund £2,500 per annum")["cover_letter"]["content"]
ok("£208.33 a month" in dv and "£25,000 over" in dv and "£9,999" not in bw.PLACEHOLDER_RE.sub("", dv), f"derived amounts kept, unrelated replaced: {dv}")

# 14. Bid / no-bid and passed stages (M16, R9, G6, R10)
import datetime as dt  # noqa: E402
from tender_app import bid_decision as bd  # noqa: E402

ok(bd.last_date("EOI on In-Tend 01/11/26 - 04/11/26") == dt.date(2026, 11, 4), "a date range gives its end date")
ok(bd.last_date("by 30th October 2026 at noon") == dt.date(2026, 10, 30), "written-out date")
ok(bd.last_date("not stated") is None, "no date")
s1 = {"clarification_deadline": {"date": "11/11/2026"},
      "key_dates": [{"event": "Expressions of interest", "date": "01/11/2026 - 04/11/2026", "bidder_action": True},
                    {"event": "Sample deadline", "date": "30/10/2026"},
                    {"event": "Tender submission deadline", "date": "01/11/2026"},
                    {"event": "Contract award", "date": "01/10/2026", "bidder_action": False},
                    {"event": "Site visit", "date": "01/12/2026"}]}
w = bd.stage_warnings(s1, today=dt.date(2026, 11, 13))
ok([x["kind"] for x in w] == ["clarification", "eoi", "sample"], f"passed bidder stages only ({w})")
ok(bd.clarification_window_closed(w) and "04/11/2026" in w[1]["detail"], "EOI warning names its closing day")
ok(bd.stage_warnings(s1, today=dt.date(2026, 10, 1)) == [], "nothing passed yet")
ok("Sample deadline: 30/10/2026" in bd.stage_note(w), "writers get the passed stages")
# Live run 7 (Matthewsgreen): none of these is a passed bidder stage
mg = {"notice_type": "UK2: Preliminary market engagement notice (Expression of Interest)", "clarification_deadline": {"date": None},
      "key_dates": [{"event": "Original Expression of Interest deadline (superseded)", "date": "01/11/2026", "bidder_action": True},
                    {"event": "Clarification log issued", "date": "06/11/2026", "bidder_action": False},
                    {"event": "Notice published", "date": "02/10/2026"},
                    {"event": "Expression of interest deadline", "date": "08/11/2026", "bidder_action": True}]}
ok(bd.stage_warnings(mg, today=dt.date(2026, 11, 13)) == [], "superseded, buyer-side and EOI-notice dates are not flagged")

raw = {"verdict": "Bid", "summary": "x", "criteria": [{"criterion": "Print", "weight": "60%", "rating": "none"},
                                                     {"criterion": "Service", "weight": 40, "rating": "Strong"}],
       "mandatory": [{"requirement": "Print plant", "status": "not met"}], "capability_gaps": ["no presses"],
       "partner_for": ["print production"]}
d = bd.normalise_decision(raw, w, False)
ok(d["fit_score"] == 40 and d["verdict"] == "Bid with partner" and d["adjusted_because"], f"unmet mandatory + partner -> Bid with partner ({d})")
ok(bd.normalise_decision(dict(raw, partner_for=[]), w, False)["verdict"] == "No-bid", "no partner route -> No-bid")
ok(bd.normalise_decision({"verdict": "maybe", "criteria": [{"criterion": "A", "rating": "strong"}]}, [], False)["verdict"] == "Bid",
   "unknown verdict falls back to the fit score")
ok(bd.normalise_decision("not a dict", [], False) is None, "bad reply -> no decision")
ok(bd.assess_bid(lambda s, u, m: (_ for _ in ()).throw(RuntimeError("down")), "P", [], False) is None, "a failed call never blocks drafting")
note = bd.capability_note(d)
ok("no presses" in note and "Never offer an unrelated product" in note and "print production" in note, "gaps become writer instructions")
ok(bd.capability_note(None) == "", "no decision -> no note")
iss = bd.decision_issues(dict(d, verdict="No-bid"), w)
ok([(i["check"], i["severity"]) for i in iss] == [("bid_decision", "P1"), ("stage_passed", "P2"), ("stage_passed", "P1"), ("stage_passed", "P1")],
   f"export check items ({iss})")
ok("unrelated product or service" in bw.GROUNDING_RULES and "11." in bw.GROUNDING_RULES, "grounding rule 11 (R9)")

# 15. M8: outcome claims need the model; the model carries the buyer's figures and the answer's gaps
from tender_app import financial_model as fm  # noqa: E402

ok(fm.flag_unsupported_outcomes("The plan reaches a small surplus by Year 3.").startswith("[CONFIRM: keep only if your financial model shows it"),
   "surplus by Year 3 -> [CONFIRM]")
ok(fm.flag_unsupported_outcomes("We will review costs every year.") == "We will review costs every year.", "no outcome claim, untouched")
ok(fm.flag_unsupported_outcomes("[CONFIRM: break-even by Year 3]") == "[CONFIRM: break-even by Year 3]", "already a tag, untouched")
fin_s1 = {"special_conditions": ["Sinking fund: operator contributes GBP 2,500 per annum.",
                                 "Shared costs: operator pays 16% of shared drains, plant, gas and water costs."],
          "published_clarifications": [{"question": "Is the sinking fund fixed?", "answer": "Yes, GBP 2,500 per annum."}]}
bi = fm.buyer_inputs(fin_s1)
ok([b["kind"] for b in bi] == ["fixed", "share"] and bi[0]["value"] == 2500 and abs(bi[1]["pct"] - 0.16) < 1e-9 and "Sinking fund" in bi[0]["label"],
   f"buyer figures found once, named from the terms ({bi})")
gi = fm.gap_inputs("Hire [NEEDS INPUT: Year 1 hall hire income] and [NEEDS INPUT: utilisation rate %] and [CONFIRM: x] [NEEDS INPUT: Year 1 hall hire income]")
ok([(g["kind"], g["is_pct"]) for g in gi] == [("income", False), ("cost", True)], f"gaps become inputs ({gi})")
ok(fm.financial_answer({"itt_question_responses": [{"question_number": "1", "question_text": "Experience", "response": "a"},
                                                   {"question_number": "4.I", "question_text": "Financial sustainability business plan", "weighting": "40%", "response": "b"}]}) == ("4.I", "b"),
   "the weighted financial question is picked")
try:
    import openpyxl  # noqa: F401
    import io as _io
    from openpyxl import load_workbook
    wb = load_workbook(_io.BytesIO(fm.build_model_xlsx("T", "C", fin_s1, "x [NEEDS INPUT: Year 1 hall hire income]")))
    vals = [c for row in wb["Model"].iter_rows(values_only=True) for c in row if isinstance(c, str)]
    ok(wb.sheetnames == ["Inputs", "Model"] and any("MATCH(TRUE" in v for v in vals), "workbook with a computed break-even year")
except ImportError:
    print("  (openpyxl not installed here: workbook check runs in the app container)")

# 16. Search (H5 / R11 / A1)
from tender_app.search_terms import term_matches  # noqa: E402
ok(term_matches("eef digital public service", "develop the alpha and beta of the eef&#039;s digital public service"), "H5: EEF found by its own title words")
ok(term_matches("print and signage", "cornwall college group print &amp; signage"), "R11: and matches &")
ok(not term_matches("eef", "roast beef supply") and term_matches("software", "booking software"), "words match from their start only")
from etenders_scraper.sources import progressive_search as ps  # noqa: E402
ok(ps.ampersand_variant("Print and Signage") == "Print & Signage" and ps.ampersand_variant("Highland roads") is None, "R11: & / and retry spelling")
ok(ps.matches_query({"title": "Print & Signage DPS"}, "print and signage") and not ps.matches_query({"title": "Road gritting"}, "Matthewsgreen"), "H5: loose portal rows need a query word")
from etenders_scraper.sources.notice_links import link_same_tender  # noqa: E402
lk = link_same_tender([{"source": "find_tender", "source_label": "Find a Tender", "title": "Mathewsgreen Community Centre", "contracting_authority": "Wokingham Borough Council", "submission_deadline": "01/11/2026"},
                       {"source": "procontract", "source_label": "ProContract", "title": "Matthewsgreen Community Centre - Expression of Interest", "contracting_authority": "Wokingham Borough Council", "submission_deadline": "08/11/2026", "resource_id": "DN831703"},
                       {"source": "pcs", "title": "Road gritting", "contracting_authority": "Highland Council", "submission_deadline": "01/12/2026"}])
ok(lk[0]["newer_deadline_elsewhere"] == {"date": "08/11/2026", "source_label": "ProContract"} and "newer_deadline_elsewhere" not in lk[1] and "linked_notices" not in lk[2],
   "A1: the two portals' rows are linked, the later deadline shown on the stale one")

print(f"bid_phase1_test: {PASSED} checks passed")
