"""Run: python tests/release10_retest_test.py
Fixes for the 23 Nov re-test (fix plan 6): past-bid fact guard (item 4), nested placeholders and public facts (item 5)."""
import datetime as dt
import os
import re
import sys

os.environ.setdefault("ENABLE_SCHEDULERS", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tender_app import bid_release5 as r5  # noqa: E402
from tender_app.bid_style import mark_reused_facts, past_bid_advice  # noqa: E402

n = 0


def ok(cond, msg):
    global n
    assert cond, "FAILED: " + msg
    n += 1


TODAY = dt.date(2026, 11, 23)

# ---- item 4: the past-bid fact guard --------------------------------------------------------------------------------
plan = "Mobilisation 15 Dec 2026 - 31 January 2027. Fieldwork to 15/03/2027. We won in 2019 and hold 15% share; delivery by 2027."
out, cnt = mark_reused_facts(plan, "2019 2027 2026 15%", "", today=TODAY)
ok("31 January 2027" in out and "15/03/2027" in out and "15 Dec 2026" in out, "years inside a date are never tagged: %s" % out)
ok("by 2027" in out and "[CONFIRM: 2027" not in out, "a year after this one (the contract period) is never tagged: %s" % out)
ok("[CONFIRM: 2019 - from a past bid]" in out and "[CONFIRM: 15% - from a past bid]" in out and cnt == 2, "a real past-bid fact is still tagged: %s" % out)
out2, cnt2 = mark_reused_facts("We won in 2019.", "2019", "The buyer's notice says 2019.", today=TODAY)
ok(cnt2 == 0 and out2 == "We won in 2019.", "a fact in the buyer's documents is not from a past bid")

bids_weak = [{"id": 1, "title": "Community centre EOI", "outcome": "unknown"}]
adv = past_bid_advice(bids_weak, {1: 25})
ok("only 25%" in adv and "none is marked Won" in adv and "standard style" in adv, "a weak, unwon match gets advice: %s" % adv)
ok(past_bid_advice([{"id": 2, "outcome": "won"}], {2: 70}) == "", "a strong won match gets none")
ok("none is marked Won" in past_bid_advice([{"id": 2, "outcome": "lost"}], {2: 70}) and "only" not in past_bid_advice([{"id": 2, "outcome": "lost"}], {2: 70}),
   "no Won alone is said without a low-match clause")
ok(past_bid_advice([], {}) == "", "no past bids, no advice")

# ---- item 5: tags, public thresholds, sensitivity percentages ---------------------------------------------------------
nested = "Income is [NEEDS INPUT: year 1 income at [PLACEHOLDER: insert verified percentage — '80%' not found in company data]] this year."
flat = r5.flatten_nested_placeholders(nested)
ok(flat == "Income is [NEEDS INPUT: year 1 income at 80%] this year.", "nested placeholders are flattened: %s" % flat)
ok(r5.flatten_nested_placeholders("[A [CONFIRM: x] b] and [C [CONFIRM: y [PLACEHOLDER: z]]]").count("[") <= 3, "deeper nesting is flattened too")
ok(r5.flatten_nested_placeholders("No tags here [1] and [x].") == "No tags here [1] and [x].", "text without nesting is untouched")

pct = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*%")
ref = [""]
fn = lambda m: "<%s>" % m.group(1)  # noqa: E731
res = r5.sub_outside_tags(pct, fn, "Up 5% here [NEEDS INPUT: at 80%] and 7% there.", ref)
ok(res == "Up <5>% here [NEEDS INPUT: at 80%] and <7>% there.".replace("<5>%", "<5>").replace("<7>%", "<7>") or "[NEEDS INPUT: at 80%]" in res and "<80>" not in res,
   "a figure inside a tag is left alone: %s" % res)
ok(r5.is_statutory_figure("£36 million") and r5.is_statutory_figure("£36m") and not r5.is_statutory_figure("£5 million"), "the Modern Slavery £36m threshold is allow-listed")
ok(r5.is_sensitivity_pct("costs up ", 9) and r5.is_sensitivity_pct("income down ", 12) and not r5.is_sensitivity_pct("We hold a ", 10),
   "'costs up 10%' and 'income down 20%' are both sensitivity figures; a plain claim is not")

# the real guard, end to end
import server  # noqa: E402

bid = {"cover_letter": {"content": "Under the Modern Slavery Act the £36 million turnover threshold applies. If income falls by 20% and costs rise by 10% we still break even. "
                                   "We have grown 47% in two years. Year 1 income is [NEEDS INPUT: year 1 income at 80%]."}}
server.verify_and_clean_bid_claims(bid, "We deliver community services.", tender_facts="The tender is for a community centre lease.")
txt = bid["cover_letter"]["content"]
ok("£36 million turnover threshold" in txt and "PLACEHOLDER: insert verified figure" not in txt, "the £36m legal threshold is not flagged: %s" % txt)
ok("[NEEDS INPUT: year 1 income at 80%]" in txt, "a figure inside an existing tag is untouched: %s" % txt)
ok(txt.count("[CONFIRM: assumption]") == 2, "20 and 10 percent are treated the same way, as assumptions to confirm: " + txt)
ok("PLACEHOLDER: insert verified percentage — '47%'" in txt, "a growth claim about the company is still flagged: %s" % txt)
ok(not re.search(r"\[[^\[\]]*\[(?:PLACEHOLDER|NEEDS INPUT|CONFIRM)", txt), "no placeholder sits inside another: %s" % txt)

# ---- the follow-up round: items 8, 9, Cornwall limit, Chemonics clarification, other Word files ----------------------------------
from tender_app.bid_release7 import enforce_word_limits  # noqa: E402
from tender_app.bid_writer import (answer_words, clarification_changes, clarifications_block, question_prompt,  # noqa: E402
                                   uncited_clarification_changes)
from tender_app.docx_styles import apply_heading_styles  # noqa: E402

para = "We will deliver the programme with a dedicated project manager who owns quality, risk and reporting for the whole contract term."
twice = {"itt_question_responses": [{"question_number": "1", "response": para + "\n\nA separate paragraph about mobilisation that is long enough to count as its own distinct block of text.\n\n" + para}]}
ok(r5.drop_near_duplicates(twice) == 1 and twice["itt_question_responses"][0]["response"].count("dedicated project manager") == 1,
   "item 8: a paragraph repeated inside one answer is dropped")

long_answer = " ".join("w%d." % i for i in range(652))
cornwall = {"itt_question_responses": [{"question_number": "D1.4", "question_text": "Describe delivery", "response": long_answer}]}
ok(enforce_word_limits(cornwall, {"itt_questions": [{"number": "D1.4", "question_text": "x", "word_limit": 560}]}) == 1
   and answer_words(cornwall["itt_question_responses"][0]["response"]) <= 560, "Cornwall: an answer that lost its limit takes the buyer's limit by number")
own_text = {"itt_question_responses": [{"question_number": "9", "question_text": "Explain (maximum 100 words)", "response": long_answer}]}
ok(enforce_word_limits(own_text, {}) == 1 and answer_words(own_text["itt_question_responses"][0]["response"]) <= 100, "a limit written in the answer's own question text is used")

heavy = {"number": "1", "text": "Delivery", "weighting": "30%", "weight_pct": 30, "word_limit": None, "budget_words": 1000}
light = dict(heavy, weight_pct=5, weighting="5%")
_kw = dict(title="t", authority="a", company="c", tender_dissection="", clarifications_block="", company_data="", company_context="",
           answer_bank="", winning_bids="")
ok("Aim for 950-1000" in question_prompt(heavy, other_questions=[heavy], **_kw) and "Aim for 850-1000" in question_prompt(light, other_questions=[light], **_kw),
   "item 9: a heavily weighted question is told to use the full range")

s1 = {"published_clarifications": [{"question": "Is VAT in the rates?", "answer": "The VAT reference in section 4 was a clerical error; quote prices exclusive of VAT."},
                                   {"question": "Where do we send it?", "answer": "Via the portal."}]}
ok(len(clarification_changes(s1)) == 1 and clarifications_block(s1).startswith("CHANGES TO THE DOCUMENTS"), "Chemonics: a correcting clarification is put first as a change")
ok(len(uncited_clarification_changes(s1, {"cover_letter": {"content": "We price ex VAT."}})) == 1, "Chemonics: a draft that never cites it is flagged")
ok(uncited_clarification_changes(s1, {"cover_letter": {"content": "Per the buyer's published clarification, prices are exclusive of VAT.\n\nMore."}}) == [],
   "Chemonics: a draft that cites it is not flagged")
ok(clarification_changes({"published_clarifications": [{"question": "q", "answer": "Via the portal."}]}) == [], "an ordinary answer is not a change")

sample = ('<w:document><w:body>'
          '<w:p><w:pPr><w:spacing w:after="1"/></w:pPr><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr><w:t>Bid Title</w:t></w:r></w:p>'
          '<w:p><w:r><w:rPr><w:b/><w:sz w:val="24"/></w:rPr><w:t>Sub title</w:t></w:r></w:p>'
          '<w:p><w:r><w:rPr><w:sz w:val="22"/></w:rPr><w:t>Body text</w:t></w:r></w:p>'
          '<w:tbl><w:tr><w:tc><w:p><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr><w:t>In a table</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
          '<w:p><w:r><w:rPr><w:b/><w:sz w:val="24"/></w:rPr><w:t>After the table</w:t></w:r></w:p>'
          '</w:body></w:document>')
styled = apply_heading_styles(sample)
ok(styled.count('w:pStyle w:val="Heading1"') == 1 and styled.count('w:pStyle w:val="Heading3"') == 2, "titles get Heading 1 and 3 by size: " + styled)
ok("<w:p><w:r><w:rPr><w:sz w:val=\"22\"/></w:rPr><w:t>Body text</w:t></w:r></w:p>" in styled, "body text is untouched")
ok("<w:tc><w:p><w:r>" in styled, "a bold line inside a table is not made a heading")

# the home page's tender count comes from the database through a public route, and a database problem gives null, not a made-up number
from tender_app.blueprints import auth_bp as _ab  # noqa: E402


class _Cur:
    def execute(self, q, a=None): self.q = q
    def fetchone(self): return (2198,)
    def close(self): pass


class _Conn:
    def cursor(self): return _Cur()
    def close(self): pass


_app = server.create_app()
_app.testing = True
_ab._get_db = lambda: _Conn()
_ab._STATS_CACHE.update({"at": 0.0, "n": None})
_r = _app.test_client().get("/api/public/stats")
ok(_r.status_code == 200 and _r.get_json() == {"ok": True, "tenders": 2198}, "the public stats route answers a logged-out visitor with the real count")


def _down():
    raise RuntimeError("db down")


_ab._get_db = _down
_ab._STATS_CACHE.update({"at": 0.0, "n": None})
ok(_app.test_client().get("/api/public/stats").get_json()["tenders"] is None, "a database problem gives null")
_home = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "home.html"), encoding="utf-8").read()
_pricing = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "pricing.html"), encoding="utf-8").read()
ok("2,198" not in _home and "82%" not in _home and 'id="heroTenders"' in _home, "the home page has no typed-in statistics (the tender count is filled in from the database)")
ok("Old billing plan" not in _pricing and '<span id="balanceAmt"></span>' in _pricing, "the pricing page's raw HTML says nothing about a legacy plan or a zero balance")


# a development run never sends real mail: EMAIL_DRY_RUN stops every sender before it reaches an SMTP server
import smtplib as _smtplib  # noqa: E402
from tender_app import email_svc as _email_svc  # noqa: E402
from tender_app import user_mailer as _um  # noqa: E402

_calls = []
_real = (_smtplib.SMTP, _smtplib.SMTP_SSL)
_smtplib.SMTP = _smtplib.SMTP_SSL = lambda *a, **k: _calls.append(a) or (_ for _ in ()).throw(AssertionError("SMTP was contacted"))
os.environ["EMAIL_DRY_RUN"] = "1"
os.environ["SMTP_HOST"], os.environ["SMTP_USER"], os.environ["SMTP_PASS"] = "smtp.example.com", "u@example.com", "x"
try:
    ok(_email_svc.send_email("a@example.com", "Subject", "Body") is True and not _calls, "EMAIL_DRY_RUN: send_email reports sent and contacts no server")
    ok(server._send_smtp_email("a@example.com", "Subject", "<p>Body</p>") is True and not _calls, "EMAIL_DRY_RUN: the alert sender contacts no server")
    try:
        _um._default_factory("smtp.example.com", 587, "starttls")
        raised = False
    except OSError:
        raised = True
    ok(raised and not _calls, "EMAIL_DRY_RUN: a user's own mail account is not contacted either")
finally:
    os.environ.pop("EMAIL_DRY_RUN", None)
    _smtplib.SMTP, _smtplib.SMTP_SSL = _real
compose = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docker-compose.yml"), encoding="utf-8").read()
ok('EMAIL_DRY_RUN: "1"' in compose, "the development compose file turns the dry run on")

for _name, _src in (("home", _home), ("pricing", _pricing)):
    ok('id="footLegal"' in _src and "AIVHUB LTD" in _src and "09941441" in _src and "Canterbury Road" in _src and "hello@aivhub.com" in _src,
       "the %s footer names the operator, number, registered office and contact" % _name)
    ok("UK GDPR" in _src and "Firebase" in _src and "UK, EU or US" in _src and "/legal/privacy.html" in _src,
       "the %s footer states how data is handled, using only what the privacy policy already says" % _name)
_privacy = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "legal", "privacy.html"), encoding="utf-8").read()
ok("Firebase" in _privacy and "UK, EU, or US" in _privacy, "the footer's data statement matches the published privacy policy")

print("release 10 re-test: %d checks passed" % n)
