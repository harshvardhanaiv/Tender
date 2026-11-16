"""Separate technical and commercial offers (QA scorecard N3), plus content-based document classification (N7) and
financial-file detection (R1).

- price_schedules_from_text: the buyer's price-schedule sheets, read back from the "[Sheet: name | price_schedule]" text.
- build_commercial_xlsx: the buyer's line items with Qty, a Unit price input column, formula totals and a VAT line that
  follows the buyer's ruling. Prices are left for the bidder: nothing is invented.
- build_technical_docx: the technical offer with every price removed (it must contain none).
- build_pack_zip: both files plus a short README.
"""
from __future__ import annotations

import io
import re
import zipfile

_SHEET_BLOCK = re.compile(r"\[Sheet: ([^\]|]+?) \| price_schedule\]\n(.*?)(?=\n\n\[Sheet: |\Z)", re.S)


def price_schedules_from_text(text: str) -> list:
    """[{sheet, header, rows}] from the extracted workbook text of price_schedule sheets."""
    out = []
    for m in _SHEET_BLOCK.finditer(text or ""):
        rows = [[c.strip() for c in line.split(" | ")] for line in m.group(2).splitlines() if line.strip()]
        # The column header is the first row that has a quantity column and a price/total column; title rows above it
        # (the buyer's ITT number, translations) are not the header.
        hi = next((i for i, r in enumerate(rows[:15])
                   if re.search(r"\b(qty|quantity|units?)\b", " ".join(r), re.I) and re.search(r"\b(unit\s*price|price|total|amount)\b", " ".join(r), re.I)), 0)
        if len(rows) - hi >= 2:
            out.append({"sheet": m.group(1).strip(), "header": rows[hi], "rows": rows[hi + 1:]})
    return out


def _col(header: list, pattern: str):
    for i, h in enumerate(header):
        if re.search(pattern, h, re.I):
            return i
    return None


def build_commercial_xlsx(title: str, company: str, schedules: list, vat_note: str = "", terms: dict | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    wb.remove(wb.active)
    yellow = PatternFill("solid", fgColor="FEF08A")
    bold = Font(bold=True)
    for sch in schedules or [{"sheet": "Price schedule", "header": ["Item", "Description", "Qty", "Unit price", "Total"], "rows": []}]:
        ws = wb.create_sheet(re.sub(r"[\\/*?:\[\]]", " ", sch["sheet"])[:31] or "Prices")
        header = list(sch["header"])
        qty_i = _col(header, r"\b(qty|quantity|units?)\b")
        unit_i = _col(header, r"unit\s*price|price|rate")
        tot_i = _col(header, r"total|amount")
        extra = []
        if unit_i is None:
            header.append("Unit price")
            unit_i = len(header) - 1
            extra.append("unit")
        if tot_i is None:
            header.append("Total")
            tot_i = len(header) - 1
        ws.append([f"Commercial offer - {title}"])
        ws.append([f"Bidder: {company}"])
        ws.append(["Prices in GBP, fixed, delivered as the buyer's terms require. Enter the unit price in the yellow cells; totals calculate."])
        if vat_note:
            ws.append([vat_note])
        ws.append([])
        hr = ws.max_row + 1
        ws.append(header)
        for c in ws[hr]:
            c.font = bold
        first = hr + 1
        for r in sch["rows"]:
            row = list(r) + [""] * (len(header) - len(r))
            n = ws.max_row + 1
            if unit_i is not None:
                row[unit_i] = None
            if qty_i is not None and tot_i is not None and unit_i is not None:
                q = chr(65 + qty_i)
                u = chr(65 + unit_i)
                row[tot_i] = f"=IF(ISNUMBER({u}{n}),{u}{n}*{q}{n},0)"
            if qty_i is not None:
                try:
                    row[qty_i] = float(str(row[qty_i]).replace(",", "")) if str(row[qty_i]).strip() else None
                except ValueError:
                    pass
            ws.append(row)
            ws.cell(row=n, column=unit_i + 1).fill = yellow
        last = ws.max_row
        if tot_i is not None and last >= first:
            t = chr(65 + tot_i)
            ws.append([])
            tr = ws.max_row + 1
            ws.cell(row=tr, column=max(1, tot_i)).value = "Total (excluding VAT)" if "exclu" in vat_note.lower() else "Total"
            ws.cell(row=tr, column=max(1, tot_i)).font = bold
            ws.cell(row=tr, column=tot_i + 1).value = f"=SUM({t}{first}:{t}{last})"
            ws.cell(row=tr, column=tot_i + 1).font = bold
        for i in range(len(header)):
            ws.column_dimensions[chr(65 + i)].width = 18 if i != 1 else 44
    _terms_sheet(wb, company, terms or {}, bold)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _terms_sheet(wb, company: str, terms: dict, bold) -> None:
    """The buyer's commercial terms and the statements it requires, filled in for the bidder to confirm (Chemonics: NET 30
    payment, and a statement that no excluded manufacturer's product is offered)."""
    pay = str(terms.get("payment_terms") or "").strip()
    excluded = [str(x).strip() for x in (terms.get("excluded_suppliers_or_products") or []) if str(x).strip()]
    if not pay and not excluded:
        return
    ws = wb.create_sheet("Terms and statements")
    ws.append([f"Commercial terms and required statements - {company}"])
    ws["A1"].font = bold
    ws.append([])
    ws.append(["Item", "Buyer's requirement", "Bidder's response"])
    for c in ws[3]:
        c.font = bold
    if pay:
        ws.append(["Payment terms", pay, "Accepted (bidder to confirm)"])
    if excluded:
        ws.append(["Excluded manufacturers / products", ", ".join(excluded[:20]),
                   "No product offered is made by, or contains components from, these (bidder to confirm)"])
    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["B"].width = 50
    ws.column_dimensions["C"].width = 60


_PRICE = re.compile(r"(?:£|GBP|EUR|€|USD|\$)\s?\d[\d,]*(?:\.\d+)?\s*(?:k|m|million)?|\d[\d,]*(?:\.\d+)?\s?(?:GBP|pounds)\b", re.I)


def strip_prices(text: str) -> str:
    """Technical offer: no prices. Currency amounts become a pointer to the commercial offer."""
    return _PRICE.sub("[see commercial offer]", text or "")


def build_technical_docx(title: str, company: str, authority: str, sections: list) -> bytes:
    """sections: [(heading, text)]. Written with python-docx; every currency amount is removed."""
    from docx import Document
    from docx.shared import Pt
    d = Document()
    d.styles["Normal"].font.name = "Calibri"
    d.styles["Normal"].font.size = Pt(11)
    d.add_heading(f"Technical offer - {title}", level=0)
    d.add_paragraph(f"Prepared for: {authority}")
    d.add_paragraph(f"Prepared by: {company}")
    d.add_paragraph("This technical offer contains no prices. Prices are in the separate commercial offer.")
    for heading, text in sections:
        body = strip_prices(text)
        if not body.strip():
            continue
        d.add_heading(heading, level=1)
        for line in body.split("\n"):
            t = line.strip()
            if not t:
                continue
            if t.startswith(("* ", "- ", "• ")):
                d.add_paragraph(t[2:], style="List Bullet")
            else:
                d.add_paragraph(t.replace("**", ""))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def build_pack_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


# ---- R1: is this a financial file? (by content, not by name) ---------------------------------------------------------

_FIN_TERMS = re.compile(r"\b(turnover|revenue|income|ebitda|gross profit|net profit|operating profit|profit and loss|p ?& ?l|"
                        r"balance sheet|cash ?flow|total assets|total liabilities|debtors|creditors|forecast|projection|"
                        r"expenditure|surplus|deficit)\b", re.I)


def looks_financial(name: str, text: str) -> bool:
    if re.search(r"financ|p ?& ?l|p and l|profit|accounts|cash ?flow|forecast|projection|budget model", name or "", re.I):
        return True
    head = (text or "")[:20000]
    distinct = {m.group(1).lower() for m in _FIN_TERMS.finditer(head)}
    numbers = len(re.findall(r"\d[\d,]{3,}", head))
    return len(distinct) >= 3 and numbers >= 10


# ---- N7: which of the five expected buyer documents does this file cover? ---------------------------------------------

_ROLE_PATTERNS = [
    # 0 invitation / instructions
    re.compile(r"instructions? to (?:bidders|tenderers)|invitation to tender|how to (?:bid|submit|tender)|submission (?:deadline|requirements)|"
               r"expression of interest|request for (?:quotation|proposals?|information)", re.I),
    # 1 questionnaire / response template
    re.compile(r"(?:quality|technical|method statement)\s+question|question(?:naire)?\s+\d|\bQ\d+\b.*\b(?:describe|explain|provide)\b|"
               r"response template|selection questionnaire|please describe|word limit", re.I),
    # 2 specification / scope
    re.compile(r"\b(?:specification|scope of (?:work|services|requirements)|terms of reference|requirements? (?:specification|schedule)|"
               r"statement of requirements|the requirement)\b", re.I),
    # 3 pricing / commercial
    re.compile(r"\b(?:price schedule|pricing (?:schedule|template|table)|bill of quantities|fee (?:proposal|requirements?|structure)|"
               r"unit price|rate card|commercial (?:offer|proposal)|quotation)\b", re.I),
    # 4 updates / clarifications
    re.compile(r"\b(?:clarification(?:s)? (?:log|response|answers?)|questions? and answers|q ?& ?a|addendum|addenda|"
               r"amendment|corrigendum|update(?:d)? to (?:the )?tender|responses? to (?:bidders'? )?questions)\b", re.I),
]


def classify_doc_roles(name: str, text: str) -> list:
    """Indexes (0-4) of the expected-document boxes this file covers, from its content and sheet kinds. One file can
    cover several (a brief with instructions, questions and fee requirements)."""
    head = (text or "")[:40000]
    roles = {i for i, p in enumerate(_ROLE_PATTERNS) if p.search(head)}
    from tender_app.bid_release5 import is_notice_only
    if is_notice_only(text):
        # A saved notice that says the specification and price schedule are on a portal is neither of them
        # (pilot: a 2 KB notice text ticked both boxes).
        roles &= {4}
    if "| price_schedule]" in head:
        roles.add(3)
    if "| clarifications]" in head:
        roles.add(4)
    if "| specification]" in head:
        roles.add(2)
    return sorted(roles)


# ---- R1: the bidder's own financial file, as figures the writers may use -----------------------------------------------

_FIN_LABEL = re.compile(r"turnover|revenue|income|sales|grant|donation|cost|expens|wage|salar|staff|rent|rates|utilit|insurance|"
                        r"maintenance|depreciation|profit|surplus|deficit|ebitda|margin|cash|balance|reserve|total|net\b|gross|q[1-4]|"
                        r"year\s*\d|fy\d*|forecast|projection", re.I)
_NUM = re.compile(r"^\(?-?[£$€]?\s?\d[\d,]*(?:\.\d+)?\)?%?$")


def financial_model_block(documents: list, max_rows: int = 120) -> str:
    """Rows of a financial file (a P&L, forecast or cash flow the bidder uploaded) as 'label: figures', for the writers
    to build a financial answer from. Only numbers that are in the file appear; nothing is calculated or invented.
    documents: [{name, content}]. Empty when no financial file is attached."""
    blocks = []
    for d in documents or []:
        name, text = str(d.get("name") or d.get("filename") or "file"), str(d.get("content") or "")
        if not looks_financial(name, text):
            continue
        rows = []
        for line in text.splitlines():
            cells = [c.strip() for c in line.split(" | ")]
            if len(cells) < 2 or line.startswith("[Sheet:"):
                if line.startswith("[Sheet:"):
                    rows.append(line.strip())
                continue
            label = cells[0]
            nums = [c for c in cells[1:] if _NUM.match(c.replace(" ", ""))]
            if label and nums and _FIN_LABEL.search(label):
                rows.append(f"{label}: " + " | ".join(nums[:8]))
            if len(rows) >= max_rows:
                break
        if len([r for r in rows if not r.startswith("[Sheet:")]) >= 3:
            blocks.append(f"FILE {name}:\n" + "\n".join(rows))
    if not blocks:
        return ""
    return ("THE BIDDER'S OWN FINANCIAL MODEL (uploaded by the bidder; these figures are the bidder's evidence). Build the "
            "financial answer from these rows, quoting the file name as the source. Do not add figures that are not here; where "
            "the question needs one that is missing, write [NEEDS INPUT: ...].\n" + "\n\n".join(blocks) + "\n")
