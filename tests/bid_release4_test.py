"""Release 4 checks (QA scorecard 13 Nov): Excel buyer files, buyer rulings, time zones, writer's note, placeholders,
word limits, PDF page counts. Plain asserts; run: python tests/bid_release4_test.py"""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etenders_scraper.xlsx_text import xlsx_to_text, sheet_summary, classify_sheet
from etenders_scraper.cft_documents import _extract_bytes
from tender_app import bid_release4 as r4
from tender_app.bid_writer import extract_questions, qa_gate

n = 0


def ok(cond, label):
    global n
    n += 1
    assert cond, "FAILED: " + label


def make_xlsx(sheets):
    """sheets: {name: [[cell, ...], ...]} as a minimal valid .xlsx (shared strings)."""
    strings = []

    def sid(s):
        if s not in strings:
            strings.append(s)
        return strings.index(s)

    sheet_xml = []
    for rows in sheets.values():
        body = ""
        for ri, row in enumerate(rows, 1):
            cells = ""
            for ci, v in enumerate(row):
                if v == "":
                    continue
                ref = "ABCDEFGH"[ci] + str(ri)
                if isinstance(v, (int, float)):
                    cells += f'<c r="{ref}"><v>{v}</v></c>'
                else:
                    cells += f'<c r="{ref}" t="s"><v>{sid(v)}</v></c>'
            body += f'<row r="{ri}">{cells}</row>'
        sheet_xml.append(f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{body}</sheetData></worksheet>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        wbs = "".join(f'<sheet name="{nm.replace(chr(38), "&amp;")}" sheetId="{i}" r:id="rId{i}"/>' for i, nm in enumerate(sheets, 1))
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{wbs}</sheets></workbook>')
        rels = "".join(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
        z.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        for i, x in enumerate(sheet_xml, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", x)
        z.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
    return buf.getvalue()


# ---- N1: Excel files are read -------------------------------------------------------------------------------------
wb = make_xlsx({
    "ToR": [["Item", "Description", "Qty", "Unit price", "Total"], ["1", "Laptop 15 inch", 20, "", ""], ["2", "Docking station", 20, "", ""]],
    "Q&A": [["Question", "Answer"], ["Are prices inclusive of VAT?", "No. Prices must exclude VAT; the earlier wording was a clerical error."]],
    "Kherson chipboard": [["Insulation", "m2"], ["Chipboard", 400]],
})
txt = xlsx_to_text(wb)
ok("[Sheet: ToR | price_schedule]" in txt, "price schedule sheet classified")
ok("[Sheet: Q&A | clarifications]" in txt, "Q&A sheet classified")
ok("Laptop 15 inch | 20" in txt and "Docking station" in txt, "line items and quantities are in the text")
ok("must exclude VAT" in txt, "answer text is in the text")
ok(_extract_bytes(wb, "Volume 3 ToR.xlsx") == txt, "_extract_bytes reads .xlsx")
ok([s["name"] for s in sheet_summary(wb)] == ["ToR", "Q&A", "Kherson chipboard"], "sheet names listed")
ok(xlsx_to_text(b"not a zip") == "", "a broken workbook gives empty text, not an error")
ok(classify_sheet([["Question", "Answer"]]) == "clarifications", "classify clarifications")

# ---- R4: a word limit written in the question ----------------------------------------------------------------------
ok(r4.limit_from_text("Describe your approach (maximum 500 words).") == 500, "maximum 500 words")
ok(r4.limit_from_text("Answers must not exceed 750 words") == 750, "not exceed 750 words")
ok(r4.limit_from_text("500 words max") == 500, "500 words max")
ok(r4.limit_from_text("Please describe your sample process.") is None, "no limit in text")
qs = extract_questions({"itt_questions": [
    {"number": "D1.3", "question_text": "Describe your quality control. Maximum 500 words.", "word_limit": None, "weighting": "10%"},
    {"number": "D1.4", "question_text": "Describe delivery.", "word_limit": 300, "weighting": "10%"}]})
ok(qs[0]["word_limit"] == 500 and qs[0]["budget_words"] == 500, "limit from text becomes the hard cap")
ok(qs[1]["word_limit"] == 300, "explicit limit kept")

# ---- R5: grouped placeholders --------------------------------------------------------------------------------------
found = [{"section": f"Q{i % 3}", "tag": "[CONFIRM: partner name]", "action": "partner name"} for i in range(31)]
found.append({"section": "Q1", "tag": "[NEEDS INPUT: turnover]", "action": "turnover"})
g = r4.group_placeholders(found)
ok(len(g) == 2, "32 placeholders collapse to 2 rows")
ok(g[0]["tag"] == "[CONFIRM: partner name] x 31", "count shown")
ok(g[1]["tag"] == "[NEEDS INPUT: turnover]", "single stays plain")

# ---- N2: buyer rulings override the ITT ---------------------------------------------------------------------------
dissection = {"published_clarifications": [{"question": "Are prices inclusive of VAT?",
                                             "answer": "No. Prices must exclude VAT; the ITT clause was a clerical error.", "source": "Q&A"}]}
rl = r4.rulings_list(dissection)
ok(len(rl) == 1, "ruling read from published clarifications")
ok("OVERRIDE" in r4.rulings_block(dissection), "block tells writers the rulings override")
bad = [("Cover letter", "Our prices are inclusive of all applicable local taxes, VAT and duties.")]
ok(len(r4.find_ruling_conflicts(rl, bad)) == 1, "VAT-included draft conflicts with the Q&A")
good = [("Cover letter", "Prices are quoted exclusive of VAT, as confirmed in the buyer's Q&A.")]
ok(r4.find_ruling_conflicts(rl, good) == [], "VAT-excluded draft is fine")
gate = qa_gate({"cover_letter": {"content": "Our prices include all applicable VAT and duties. We look forward to working with you."}}, rulings=rl)
ok(any(i["check"] == "ruling_conflict" and i["severity"] == "P0" for i in gate["issues"]), "QA gate: ruling conflict is P0")
ok(not any(i["check"] == "ruling_conflict" for i in qa_gate({"cover_letter": {"content": "We look forward to working with you."}}, rulings=rl)["issues"]),
   "QA gate: no conflict, no issue")

# ---- N4: buyer entity and addressee -------------------------------------------------------------------------------
st = {"contracting_authority": "Chemonics Group UK Limited (Chemonics International, Inc. as listed on Contracts Finder)",
      "bid_addressee": "Olha Brusniak, Project Officer"}
ok(r4.buyer_name(st, "Chemonics International, Inc.") == "Chemonics Group UK Limited", "buyer documents beat the notice")
ok(r4.buyer_name({"contracting_authority": "NOT STATED"}, "Notice Buyer") == "Notice Buyer", "falls back to the notice")
ok(r4.addressee(st) == "Olha Brusniak, Project Officer", "addressee from the documents")
ok(r4.addressee({"bid_addressee": None}) == "", "no addressee")
ok(r4.addressee({"bid_addressee": "Olha Brusniak, Project Officer, M.: +380 (67) 563-97-74"}) == "Olha Brusniak, Project Officer",
   "phone and label are not part of the greeting")
ok("Submission deadline: 23/11" not in r4.writer_note({"verdict": "No-bid", "fit_score": 0}, [], {
    "key_dates": [{"event": "Submission deadline", "date": "23/11/2026", "bidder_action": True}]}),
   "the submission deadline is not repeated as a dated action")

# ---- N5: deadline with its time zone ------------------------------------------------------------------------------
dd = r4.deadline_display({"submission_deadline": {"date": "23/11/2026", "time": "18:00", "timezone": "GMT+2"}})
ok(dd == "23/11/2026 18:00 GMT+2 (16:00 UK time)", "GMT+2 18:00 is 16:00 UK: " + dd)
ok("UK time" not in r4.deadline_display({"submission_deadline": {"date": "20/11/2026", "time": "12:00 noon", "timezone": "GMT"}}),
   "UK deadline needs no conversion")
ok(r4.deadline_display({"submission_deadline": {"date": "NOT STATED"}}) == "", "no date, no display")

# ---- R2 / R3 / N8: the bid writer's note --------------------------------------------------------------------------
note = r4.writer_note(
    {"verdict": "No-bid", "fit_score": 0, "summary": "No print capability.", "capability_gaps": ["print production equipment"]},
    [{"kind": "sample", "detail": "The sample deadline (30/10/2026) has passed."}],
    {"clarification_deadline": {"date": "09/11/2026", "method": "email to the procurement mailbox"},
     "submission_deadline": {"date": "20/11/2026", "time": "12:00", "timezone": "GMT"},
     "key_dates": [{"event": "Site visit", "date": "16/11/2026 and 18/11/2026", "bidder_action": True, "contact": "book with Susan Moore"}]})
ok("No-bid (fit 0/100)" in note and "print production equipment" in note, "note carries verdict and gap")
ok("30/10/2026" in note, "note names the passed date")
ok("email to the procurement mailbox" in note, "note names the buyer's clarification route")
ok("Site visit: 16/11/2026 and 18/11/2026 - book with Susan Moore" in note, "note carries the site visit dates and contact")
ok(r4.writer_note({"verdict": "Bid", "fit_score": 80}, [], {}) == "", "a good fit with nothing passed has no note")

# ---- N3: structure by the documents the buyer asks for -------------------------------------------------------------
sn = r4.submission_structure_note({"required_submission_documents": [
    {"name": "Technical offer", "kind": "technical", "separate_file": True},
    {"name": "Commercial offer (Volume 3 Excel)", "kind": "commercial", "separate_file": True}],
    "separate_technical_commercial": True})
ok("Technical offer" in sn and "SEPARATE" in sn and "no prices" in sn, "structure note for separate offers")
ok(r4.submission_structure_note({}) == "", "no documents, no note")

# ---- N6: PDF with page counts (PyMuPDF is in the app container) --------------------------------------------------
try:
    import pymupdf  # noqa: F401
except Exception:
    pymupdf = None
if pymupdf:
    from tender_app.bid_pdf import build_pdf, text_to_html
    long_text = "\n\n".join(["Paragraph %d. " % i + "word " * 120 for i in range(30)])
    pdf, counts = build_pdf("Test bid", [
        {"label": "Question 1", "html": "<h2>Q1</h2>" + text_to_html("Short answer with [CONFIRM: x]."), "page_limit": 1},
        {"label": "Question 2", "html": "<h2>Q2</h2>" + text_to_html(long_text), "page_limit": 1},
    ], "<h1>Title</h1>")
    ok(pdf[:4] == b"%PDF", "a real PDF comes back")
    by = {c["label"]: c for c in counts}
    ok(by["Question 1"]["pages"] == 1 and not by["Question 1"]["over"], "short section: 1 page, within limit")
    ok(by["Question 2"]["pages"] > 1 and by["Question 2"]["over"], "long section: over its 1-page limit")
    ok(pymupdf.open("pdf", pdf).page_count == sum(c["pages"] for c in counts), "merged page total matches the section counts")
else:
    print("  (PyMuPDF not installed here: PDF checks skipped; they run in the app container)")

print(f"bid_release4_test: {n} checks passed")
