"""Fix plan 5, "Re-test 19 Nov - release 9 and the new layout": every finding as a regression check.
Run: python tests/release9_retest_test.py"""
import datetime as dt
import io
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tender_app import bid_release5 as r5, bid_release7 as r7  # noqa: E402
from tender_app.bid_pdf import text_to_html  # noqa: E402
from tender_app.bid_writer import extract_questions, qa_gate, ends_cleanly  # noqa: E402
from tender_app.bid_style_learn import learn_style, split_answers, find_reusable, reuse_block, section_plan_questions  # noqa: E402
from tender_app.doc_date_extractor import docx_structured_text  # noqa: E402

n = 0


def ok(c, m):
    global n
    n += 1
    assert c, "FAILED: " + m


def read(p):
    return open(os.path.join(ROOT, p), encoding="utf-8").read()


# ---- P0 1: the NHS Wales template on English bids ------------------------------------------------------------------------
ok(not r5.buyer_in_wales("Epsom WC refurbishment", "Registered in England and Wales"), "England and Wales is not a Welsh buyer")
ok(r5.buyer_in_wales("Coaching platform", "NWSSP on behalf of NHS Wales"), "NWSSP is a Welsh buyer")
ok(not r5.buyer_in_wales("New South Wales health"), "New South Wales is not Wales")
_k, note = r5.contract_type_note("Oldham Council - Live Well Evaluation", "Evaluation of the Live Well programme",
                                 "Oldham Council. Suppliers registered in England and Wales may apply.")
ok("Welsh" not in note, "Oldham (England) gets no Welsh language section")
S1 = {"required_submission_documents": [{"name": "Technical submission", "kind": "technical"}, {"name": "Form of tender", "kind": "form"}]}
EPSOM = ("Epsom and St Helier WC refurbishment", "Refurbishment of toilets and washrooms at St Helier Hospital",
         "Epsom and St Helier University Hospitals NHS Trust, Surrey. Registered in England and Wales.")
qs = r7.submission_doc_questions(S1, True, *EPSOM)
tech = qs[0]["question_text"]
for bad in ("Welsh language provision", "Accessibility (WCAG", "Ethics, safeguarding"):
    ok(bad not in tech, "Epsom's technical offer does not ask for %s" % bad)
for want in ("Method of working", "Water safety", "HTM 04-01", "Health and safety", "Quality control"):
    ok(want in tech, "Epsom's technical offer has %s" % want)
ok("Do NOT add sections for" in tech and "Welsh language" in tech.split("Do NOT add sections for")[1], "the writer is told not to add Welsh")
ok(any(q["question_text"].startswith("Price and commercial basis") for q in qs), "a bid with no price document gets a price section")
ok("Welsh" not in r7._KIND_GUIDE["technical"] and "WCAG" not in r7._KIND_GUIDE["technical"], "the shared guide names no Welsh or WCAG")
built = extract_questions(r7.with_submission_questions(S1, *EPSOM))
ok(built[0]["budget_words"] >= 2500, "the works technical offer has room for its sections: %s words" % built[0]["budget_words"])
_wk, wales = r7.technical_sections("NHS Wales coaching platform RFQ", "Digital coaching platform for NWSSP", "")
ok("Proposed solution" in wales and "Welsh language" in wales.split("Do NOT")[0], "NHS Wales keeps its solution and Welsh sections")
sep = dict(S1, separate_technical_commercial=True)
ok(not any("Price and commercial" in q["question_text"] for q in r7.submission_doc_questions(sep, True, *EPSOM)),
   "no extra price section when the price goes in its own file")
ok(len(r7.submission_doc_questions(S1, True)) == 2, "without a title the old behaviour is unchanged")

# ---- P0 2: the misplaced public-sector [CONFIRM] -------------------------------------------------------------------------
TAG = "[CONFIRM: no public-sector work"
b = {"executive_summary": {"content": "Contact caitlyn.murphy@wales.nhs.uk with queries. The Council works in partnership "
                                      "with the NHS. We have worked with NHS trusts and councils. Our council clients value us."}}
ok(r5.flag_unevidenced_claims(b, {"summary": "no evidence of public sector work"}) == 1, "one tag per section")
t = b["executive_summary"]["content"]
ok("nhs.uk" in t and "nhs.[CONFIRM" not in t, "never inside an e-mail address")
ok(t.index(TAG) > t.index("We have worked with NHS trusts"), "the tag follows the bidder's claim")
ok(t.index(TAG) > t.index("partnership with the NHS."), "not after the sentence about the buyer")
b2 = {"executive_summary": {"content": "The Council works with NHS partners across the borough."}}
ok(r5.flag_unevidenced_claims(b2, {"summary": "no evidence of public sector work"}) == 0, "a sentence about the buyer is not a claim")

# ---- "12 years' experience" on every mention ------------------------------------------------------------------------------
s = ("Priority Plumbing has over 12 years of experience. We bring 12 years of experience to the Trust. Our engineers are "
     "Gas Safe. Our 12 years of experience means quality.")
out = r5.reconcile_experience(s, dt.date(2024, 7, 10), dt.date(2026, 11, 20))
ok(out.count("[CONFIRM") == 3, "every mention carries the [CONFIRM]: %s" % out)
ok(r5.reconcile_experience(out, dt.date(2024, 7, 10), dt.date(2026, 11, 20)) == out, "running it twice adds nothing")
out = r5.reconcile_experience(s, None)
ok(out.count("[CONFIRM") == 3, "no incorporation date: every mention is still flagged")

# ---- P0 3: raw formatting in the PDF -------------------------------------------------------------------------------------
md = ("### How we will deliver\n\nIntro with **bold** and [CONFIRM: x].\n\n| Stage | When |\n|---|---|\n| Survey | Week 1 |\n\n"
      "1. First\n2. Second\n- a bullet\n#### Sub point\n---")
h = text_to_html(md)
ok("###" not in h and "---" not in h and "| Stage" not in h, "no Markdown symbols in the PDF html: %s" % h)
ok("<h3>How we will deliver</h3>" in h and "<h4>Sub point</h4>" in h, "headings")
ok("<th>Stage</th>" in h and "<td>Survey</td>" in h, "pipe tables become tables")
ok("<ol>" in h and "<li>First</li>" in h and '<span class="ph">[CONFIRM: x]</span>' in h, "numbered lists and highlighted tags")
ok("h3, h4" in read("tender_app/bid_pdf.py"), "brand colour reaches the PDF sub-headings")

# ---- P0 4: the download clicked by mistake ---------------------------------------------------------------------------------
app = read("web/app.js")
fn = app[app.index("async function downloadFullProposalPdf"):]
fn = fn[:fn.index("\n}\n") if "\n}\n" in fn else 4000]
ok("showConfirm" in fn[:1200] and fn.index("showConfirm") < fn.index("/api/download-proposal-pdf"), "the PDF download asks before charging")
css = read("web/workspace.css")
m = re.search(r"\.bid-modal-footer \{[^}]*z-index:\s*(\d+)", css, re.S)
ok(m and int(m.group(1)) > 18, "the bottom bar sits above the sticky stage bar (z-index 18)")

# ---- P1: past bids have a visible effect ------------------------------------------------------------------------------------
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def p(text, style=None):
    ps = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ps}<w:r><w:t>{text}</w:t></w:r></w:p>"


def tbl(rows):
    return "<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc>{p(c)}</w:tc>" for c in r) + "</w:tr>" for r in rows) + "</w:tbl>"


filler = "We will run the centre with a local team and report bookings every six months in open-book format. " * 5
body = ""
heads = ["Organisation information", "Experience", "Vision 2035", "Financial sustainability", "Month in the life",
         "EDI and safeguarding", "Social value", "Mobilisation", "Risks", "Partners"]
for i, head in enumerate(heads, 1):
    extra = " Safeguarding training is annual and named staff lead it. In 2024 we reached 94% satisfaction." if i == 6 else ""
    body += p(f"Question {i}: {head}", "Heading2") + p(filler) + p("How we deliver", "Heading3") + p(filler + extra)
    if i in (4, 5, 9):
        body += tbl([["Item", "Year 1"], ["Income", "GBP 12,000"]])
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>')
txt = docx_structured_text(buf.getvalue())
ok(txt.startswith("## Question 1: Organisation information") and "| Item | Year 1 |" in txt, "Word headings and tables are kept")
prof = learn_style([{"title": "Matthewsgreen EOI", "text": txt}])
ok(prof["answers"] == 10 and prof["tables"] == 3 and prof["subheadings"] >= 1, "split by question before measuring: %s" % prof)
ok("How we deliver" in prof["common_headings"], "the learned sub-headings come from the Word headings: %s" % prof["common_headings"])
lib = [{"title": "Matthewsgreen EOI", "buyer": "Wokingham Borough Council", "text": txt, "outcome": "unknown"}]
r = find_reusable("Ethics, safeguarding and data protection", lib)
ok(r and "EDI and safeguarding" in r["source_question"], "Oldham's ethics/safeguarding question reuses the EDI and safeguarding answer: %s" % r)
ok(r and "[CONFIRM: 94% - from a past bid]" in r["paragraph"], "the old fact in it is marked")
blk = reuse_block("Ethics, safeguarding and data protection", lib)
ok("REUSABLE PARAGRAPH FOR THIS QUESTION" in blk and "Matthewsgreen EOI" in blk, "the writer for that question gets the paragraph")
ok(reuse_block("Ethics", []) == "", "no past bid: nothing added")
flat = " ".join(txt.replace("#", "").replace("|", " ").split())
ok(len(split_answers(flat)) >= 8, "a past bid already stored as one line is still split by question: %d" % len(split_answers(flat)))
ok(find_reusable("Ethics, safeguarding and data protection", [dict(lib[0], text=flat)]) is not None, "and still gives reusable text")
srv = read("server.py")
ok("_reuse_block(q.get(\"text\", \"\"), winning_bids" in srv, "the per-question writer is handed the reusable paragraph")
ok("docx_structured_text(temp_path" in read("tender_app/blueprints/profiles_bp.py"), "winning-bid uploads keep their structure")

# ---- P1: the answer plan with no questions -----------------------------------------------------------------------------------
sec = section_plan_questions("Oldham Council - Live Well Evaluation", "Evaluation of the Live Well programme")
ok(len(sec) >= 5 and sec[0]["number"] == "S1" and all(q["budget_words"] for q in sec), "a plan of the sections TenderFlow will write")
ok(section_plan_questions("Something else", "")[0]["text"] == "Executive summary", "the house sections when the type is unknown")
ok('plan_source = "sections"' in srv and 'd.plan_source === "sections"' in app, "the plan says it is built from sections")
ok("_lastStyleData = null;   // the Answer plan tab reads the new questions" in app, "building the Bid plan refreshes the answer plan")

# ---- P1: documents and ticks on reopen ----------------------------------------------------------------------------------------
ok("d.files_json && Array.isArray(d.files_json.files)" in app, "History reopen adds back the files the bid was written from")
ok("restoreBidStepsForBid();" in app and "rememberBidStepsForBid();" in app, "the Answer bank tick is remembered per bid")
ok("state.bidUploadedFiles.length > stored.length) syncBidUploadsToServer()" in app, "the fuller set is kept on the server")

# ---- P1: the false "stops mid-sentence" ------------------------------------------------------------------------------------------
g = qa_gate({"itt_question_responses": [
    {"question_number": "4", "response": "The ten-year plan breaks even in year two.", "status": "truncated"},
    {"question_number": "5", "response": "| Day | Use |\n|---|---|\n| Monday | Optalis small hall and"}]})
tr = [i for i in g["issues"] if i["check"] == "truncation"]
ok([i["section"] for i in tr] == ["Question 5"], "a stale 'truncated' status with a clean ending is no alarm: %s" % tr)
ok("last table row" in tr[0]["detail"] and "Optalis small hall and" in tr[0]["detail"], "the check names the place: %s" % tr[0]["detail"])
ok(ends_cleanly("Our plan.\n\n10-year surplus: GBP 67,160"), "a label: value total ends cleanly")
ok(not ends_cleanly("We will invest in the"), "a dangling sentence is still cut off")

# ---- P1: NHS Wales certifications ---------------------------------------------------------------------------------------------
ev = "ISO 9001:2015 certificate number 12345 issued by BSI. ISMS policy: we are working towards ISO 27001 certification."
for text in ("Our ISO 27001-aligned ISMS protects data.", "The Trust requires Cyber Essentials Plus, which we hold.",
             "We hold ISO 27001 certification."):
    out, c = r7.confirm_certifications(text, ev)
    ok(c == 1 and "[CONFIRM:" in out, "claim marked: %s" % out)
out, c = r7.confirm_certifications("| ISO 27001 | Yes |", ev)
ok(c == 1 and out.endswith(" |") and out.count("|") == 3, "a table cell keeps its table: %s" % out)
ok(r7.confirm_certifications("We hold ISO 9001 certification.", ev)[1] == 0, "a certificate on file is not marked")
ok(r7.confirm_certifications("Suppliers must hold ISO 27001 or equivalent.", ev)[1] == 0, "the buyer's requirement is not a claim")

# ---- UI item 9: the pre-generate box counts the reuse --------------------------------------------------------------------------
ok("Reused: ${_reused.length} paragraph" in app and "fact${_facts === 1" in app, '"n paragraphs reused, m facts to confirm"')

# ---- remaining items, fixed after the first pass -------------------------------------------------------------------------------
from tender_app.bid_style import sector_of  # noqa: E402
from tender_app import bid_prices as bp  # noqa: E402
ok(sector_of({"title": "Matthewsgreen Community Centre EOI"}) == "Community venue", "sector: community venue")
ok(sector_of({"title": "NHS data dashboard pilot"}) == "Digital and IT", "sector: digital")
ok(sector_of({"title": "Herts Police plumbing PPM"}) == "Building works and maintenance", "sector: works")
ok(sector_of({"title": "Proposal", "text": "We evaluated the programme through research."}) == "Research and evaluation", "sector from the text")
ok(sector_of({"title": "x"}) == "", "no sector when nothing fits")
ok("[b.buyer, b.sector, b.year]" in app, "the Will use list shows the sector")
ok(bp.to_number("£1,250.50") == 1250.5 and bp.to_number("abc") is None, "amounts read")
ok(bp.parse_price_text("Laptop 14in: 650\nDock | £120.50\nTraining day - 300\nno price here") ==
   {"laptop 14in": 650.0, "dock": 120.5, "training day": 300.0}, "pasted price lines")
SCH = [{"sheet": "Prices", "header": ["Item", "Description", "Qty", "Unit price"],
        "rows": [["D1", "Laptop 14in", "10", ""], ["D2", "Docking station", "4", ""]]}]
u = bp.unit_prices(SCH, {"0:1": "99"}, "D1: 650")
ok(u == [[650.0, 99.0]], "prices by box and by item code: %s" % u)
ok(bp.unit_prices(SCH, {}, "laptop: 600") == [[600.0, None]], "a pasted name matches when its words are in one line item")
ok(r7.commercial_pack_text(SCH).count("[NEEDS INPUT: unit price]") == 2, "without prices nothing is invented")
txt = r7.commercial_pack_text(SCH, units=u)
ok("GBP 6,500.00" in txt and "GBP 396.00" in txt and "**GBP 6,896.00**" in txt and "NEEDS INPUT" not in txt, "priced table: %s" % txt)
bid = {"pricing_notes": {"content": "Intro.\n\n## Commercial offer: price schedule\nold"}}
ok(r7.add_commercial_pack(bid, SCH, "", u) and "old" not in bid["pricing_notes"]["content"], "prices given later rebuild the table")
ok(not r7.add_commercial_pack(bid, SCH, ""), "without prices an existing table is left alone")
try:
    import openpyxl
    from tender_app.submission_pack import build_commercial_xlsx
    wb = openpyxl.load_workbook(io.BytesIO(build_commercial_xlsx("T", "Co", SCH, units=u)))
    vals = [c.value for row in wb.worksheets[0].iter_rows() for c in row]
    ok(650.0 in vals and 99.0 in vals, "the commercial workbook carries the prices")
except ImportError:
    print("  (openpyxl not installed: workbook check skipped)")
ok("...bidPricesPayload(task.row)" in app and "renderBidPricesCard()" in app, "prices go with a re-run and the card shows on the draft")

# Found in the signed-in preview (20 Nov): headings and table rows from a Word bid counted as one long sentence,
# the style brief began "then about 1 sub-heading", and a new tender kept the last tender's Answer plan.
from tender_app.bid_style_learn import style_brief  # noqa: E402
_md = ("# Question 1. Method\n\n" + "We run the site every day with a trained team. " * 8 + "\n\n## How we schedule\n\n"
       "| Task | Frequency | Standard |\n|---|---|---|\n| Water temperature checks | Monthly | HTM 04-01 |\n\n"
       "# Question 2. Safety\n\n" + "Every job has a risk assessment and a method statement. " * 8)
_p = learn_style([{"title": "t", "text": _md, "outcome": "won"}])
ok(_p["answers"] == 2 and _p["tables"] == 1 and _p["words_per_sentence"] <= 12, "sentence length from prose only: %s" % _p)
ok(style_brief(dict(_p, opens_direct=False, subheadings=1.0)).startswith("Use about 1 sub-heading"), "the brief is a whole sentence without an opening summary")
_ob = app[app.index("function openBidModal(row)"):app.index("function openBidModal(row)") + 900]
ok("_lastStyleData = null" in _ob and "_lastBidPlanQuestions = []" in _ob, "opening a tender clears the last tender's plan and questions")
_rf = app[app.index("function renderFormattedMarkdownHtml("):app.index("function _inlineMarkdown(")]
ok("return _previewMarkdown(text)" in _rf and "const inline = t => _inlineMarkdown(" in app, "draft boxes render tables and #### headings, not raw Markdown")
ok('data-section-key="pricing_notes"' in app and "bidPricingDraft" in app, "the priced table is shown on the draft page, not only in the download")

print("release 9 re-test: %d checks passed" % n)
