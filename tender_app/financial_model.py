"""The financial answer: no outcome the numbers don't show, and a spreadsheet model to fill in (fix plan M8).

Matthewsgreen Q4 (40% of the score) came back as a well-structured template with ~100 [NEEDS INPUT] gaps and one
real figure, yet said the plan "reaches a small surplus by Year 3" / "break-even by Year 3". This module
- turns such outcome claims into [CONFIRM] items unless the bidder's own model backs them (flag_unsupported_outcomes);
- builds an .xlsx model: an Inputs sheet listing the buyer's published figures (sinking fund, cost shares) and every
  gap the answer left, and a 10-year projection whose totals, net position and break-even year are formulas, so the
  outcome comes from the numbers the bidder enters, never from the model text.
"""
from __future__ import annotations

import io
import re
from typing import Any

from tender_app.bid_writer import PLACEHOLDER_RE

YEARS = 10
_FIN_Q = re.compile(r"financ|business plan|budget|income|cost|cash ?flow|viab|sustainab|pric|revenue|profit", re.I)
# Tested one sentence at a time: a lazy prefix over a long line was quadratic (8 s on a 380k-character bid).
_OUTCOME_CORE = re.compile(
    r"\b(?:break[- ]?even|(?:small |modest |annual |operating )?surplus|profit(?:able|ability)?|"
    r"self[- ](?:sustaining|financing|funding)|financially (?:sustainable|viable|self-sufficient)|"
    r"cover(?:s|ing)? (?:its|all|our|the centre's) (?:own )?(?:running )?costs)\b[^.!?\n]*"
    r"\b(?:by|from|in|within|after)\s+(?:the\s+)?(?:end of\s+)?(?:year|yr|month|the (?:first|second|third))\b",
    re.I,
)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


def is_financial_question(text: str) -> bool:
    return bool(_FIN_Q.search(text or ""))


def flag_unsupported_outcomes(text: str) -> str:
    """'The plan reaches break-even by Year 3.' -> [CONFIRM: ...] for the bidder to keep only if their model shows
    it. Sentences already inside a tag are left alone."""
    if not text:
        return text
    protected = [(m.start(), m.end()) for m in PLACEHOLDER_RE.finditer(text)]

    def repl(m: re.Match) -> str:
        if not _OUTCOME_CORE.search(m.group(0)) or any(a <= m.start() < b for a, b in protected):
            return m.group(0)
        sentence = m.group(0).strip()
        lead = m.group(0)[: len(m.group(0)) - len(m.group(0).lstrip())]
        inner = sentence.rstrip(".!? ").replace("[", "(").replace("]", ")").replace('"', "'")[:200]
        return f'{lead}[CONFIRM: keep only if your financial model shows it: "{inner}"]'

    return _SENTENCE.sub(repl, text)


# ---------------------------------------------------------------------------
# Inputs from the buyer's documents and from the answer's gaps
# ---------------------------------------------------------------------------

_MONEY = re.compile(r"(?:£|\bGBP)\s?(\d[\d,]*(?:\.\d+)?)\s*(k|m|million)?\b", re.I)
_PCT_OF = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%\s+(?:of\s+)?(?:the\s+)?([a-z][a-z ,/&-]{3,80}?)(?:\s+(?:costs?|charges?|bills?))", re.I)
_INCOME_WORDS = re.compile(r"income|hire|booking|revenue|rent(?:al)? (?:from|received|income)|grant|donation|"
                           r"fundrais|sponsor|membership|fees? (?:from|received)|sales|café|cafe|bar|room", re.I)


def _num(raw: str, unit: str | None) -> float:
    v = float(raw.replace(",", ""))
    u = (unit or "").lower()
    return v * 1000 if u == "k" else v * 1_000_000 if u in ("m", "million") else v


def _buyer_texts(stage1: dict | None) -> list[tuple[str, str]]:
    s1 = stage1 or {}
    out = []
    # Terms first (their wording names the item: "Sinking fund: operator contributes GBP 2,500 per annum"), then
    # clarification answers ("Yes, GBP 2,500 per annum") for anything the terms did not state.
    for key in ("special_conditions", "financial_requirements", "mandatory_requirements"):
        for item in s1.get(key) or []:
            if isinstance(item, dict):
                out.append((" ".join(str(v) for v in item.values() if v), key.replace("_", " ")))
            elif item:
                out.append((str(item), key.replace("_", " ")))
    for c in s1.get("published_clarifications") or []:
        if isinstance(c, dict):
            out.append((f"{c.get('question') or ''} {c.get('answer') or ''}".strip(), str(c.get("source") or "Clarification answer")))
    cv = s1.get("contract_value")
    if isinstance(cv, dict) and cv.get("estimated"):
        out.append((f"Contract value {cv.get('estimated')}", "contract value"))
    return out


def buyer_inputs(stage1: dict | None) -> list[dict]:
    """Figures the buyer published: fixed annual amounts and percentage shares of a cost."""
    rows: list[dict] = []
    seen: set = set()
    for text, src in _buyer_texts(stage1):
        for sentence in re.split(r"(?<=[.;])\s+", text):
            for m in _PCT_OF.finditer(sentence):
                label = re.sub(r"\s+", " ", m.group(2)).strip(" ,")
                key = ("pct", label.lower(), m.group(1))
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"kind": "share", "label": f"Your share of {label} costs", "pct": float(m.group(1)) / 100,
                             "source": f"{src}: {sentence.strip()[:160]}"})
            if re.search(r"\b(?:per (?:annum|year)|a year|annual|p\.?a\.?|each year|yearly)\b", sentence, re.I):
                for m in _MONEY.finditer(sentence):
                    val = _num(m.group(1), m.group(2))
                    key = ("fixed", round(val, 2))
                    if key in seen or val <= 0:
                        continue
                    seen.add(key)
                    label = re.sub(r"\s+", " ", sentence.strip())[:90]
                    rows.append({"kind": "fixed", "label": label, "value": val, "source": f"{src}: {sentence.strip()[:160]}"})
    return rows[:20]


def gap_inputs(answer: str) -> list[dict]:
    """Every [NEEDS INPUT: ...] in the financial answer becomes an input row, income or cost by its wording."""
    rows, seen = [], set()
    for m in PLACEHOLDER_RE.finditer(answer or ""):
        tag = m.group(0)[1:-1]
        if not tag.upper().startswith("NEEDS INPUT"):
            continue
        label = re.sub(r"^NEEDS INPUT:\s*", "", tag, flags=re.I).strip()
        k = label.lower()
        if k in seen or len(label) < 3:
            continue
        seen.add(k)
        is_pct = bool(re.search(r"%|percent|rate of|utilisation|occupancy|growth|inflation", label, re.I))
        rows.append({"kind": "income" if _INCOME_WORDS.search(label) else "cost", "label": label[:120],
                     "is_pct": is_pct})
    return rows[:80]


# ---------------------------------------------------------------------------
# The workbook
# ---------------------------------------------------------------------------

def build_model_xlsx(title: str, company: str, stage1: dict | None, answer: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ins = wb.active
    ins.title = "Inputs"
    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="1E3A8A")
    head_font = Font(bold=True, color="FFFFFF")
    enter_fill = PatternFill("solid", fgColor="FEF9C3")
    ins.append([f"Financial model - {title}"])
    ins["A1"].font = Font(bold=True, size=14)
    ins.append([f"{company} - yellow cells are yours to fill in. Totals, net position and break-even are formulas: "
                "the bid should state only what they show."])
    ins.append([])
    ins.append(["Assumption", "Year 1 value (£ or %)", "Annual growth %", "Type", "Where it comes from / what to enter"])
    for c in range(1, 6):
        ins.cell(row=4, column=c).fill = head_fill
        ins.cell(row=4, column=c).font = head_font

    lines: list[dict] = []  # model lines: {"label", "kind" income|cost, "formula" for Year 1 (sheet Inputs refs)}
    r = 5
    general = [("Inflation / cost growth (default for every line)", 0.03, "Enter your assumption; used where a line has no growth of its own")]
    infl_row = r
    for label, val, src in general:
        ins.append([label, val, None, "assumption", src])
        ins.cell(row=r, column=2).number_format = "0.0%"
        ins.cell(row=r, column=2).fill = enter_fill
        r += 1

    for b in buyer_inputs(stage1):
        if b["kind"] == "fixed":
            ins.append([b["label"], b["value"], None, "cost (buyer)", b["source"]])
            ins.cell(row=r, column=2).number_format = "£#,##0"
            lines.append({"label": b["label"], "kind": "cost", "y1": f"Inputs!B{r}", "growth": f"Inputs!C{r}"})
            r += 1
        else:
            ins.append([f"Base annual cost for: {b['label'][14:]}", None, None, "cost base (enter)", "Enter the full annual cost before your share"])
            ins.cell(row=r, column=2).number_format = "£#,##0"
            ins.cell(row=r, column=2).fill = enter_fill
            base = r
            r += 1
            ins.append([f"{b['label']} (share)", b["pct"], None, "share (buyer)", b["source"]])
            ins.cell(row=r, column=2).number_format = "0%"
            lines.append({"label": b["label"], "kind": "cost", "y1": f"Inputs!B{base}*Inputs!B{r}", "growth": f"Inputs!C{base}"})
            r += 1

    for g in gap_inputs(answer):
        ins.append([g["label"], None, None, ("rate / %" if g["is_pct"] else g["kind"]), "Gap left in the bid answer"])
        ins.cell(row=r, column=2).fill = enter_fill
        ins.cell(row=r, column=2).number_format = "0.0%" if g["is_pct"] else "£#,##0"
        if not g["is_pct"]:
            lines.append({"label": g["label"], "kind": g["kind"], "y1": f"Inputs!B{r}", "growth": f"Inputs!C{r}"})
        r += 1
    for _ in range(5):  # spare rows for lines the answer did not name
        ins.append(["(add your own line)", None, None, "income or cost", ""])
        ins.cell(row=r, column=2).fill = enter_fill
        lines.append({"label": f"=Inputs!A{r}", "kind": "cost" if _ % 2 else "income", "y1": f"Inputs!B{r}", "growth": f"Inputs!C{r}"})
        r += 1
    for col, width in zip("ABCDE", (62, 20, 16, 18, 90)):
        ins.column_dimensions[col].width = width
    ins.freeze_panes = "A5"

    m = wb.create_sheet("Model")
    m.append(["Line"] + [f"Year {y}" for y in range(1, YEARS + 1)])
    for c in range(1, YEARS + 2):
        m.cell(row=1, column=c).fill = head_fill
        m.cell(row=1, column=c).font = head_font
    row = 2

    def block(kind: str, heading: str) -> tuple[int, int]:
        nonlocal row
        m.cell(row=row, column=1, value=heading).font = bold
        row += 1
        first = row
        for ln in [x for x in lines if x["kind"] == kind]:
            m.cell(row=row, column=1, value=ln["label"])
            for y in range(1, YEARS + 1):
                col = get_column_letter(y + 1)
                if y == 1:
                    f = f"=IFERROR({ln['y1']},0)"
                else:
                    prev = get_column_letter(y)
                    f = f"={prev}{row}*(1+IF({ln['growth']}=\"\",Inputs!$B${infl_row},{ln['growth']}))"
                m[f"{col}{row}"] = f
                m[f"{col}{row}"].number_format = "£#,##0"
            row += 1
        return first, row - 1

    inc = block("income", "Income")
    inc_total = row
    m.cell(row=row, column=1, value="Total income").font = bold
    for y in range(1, YEARS + 1):
        col = get_column_letter(y + 1)
        m[f"{col}{row}"] = f"=SUM({col}{inc[0]}:{col}{inc[1]})" if inc[1] >= inc[0] else 0
        m[f"{col}{row}"].number_format = "£#,##0"
    row += 2
    cost = block("cost", "Costs")
    cost_total = row
    m.cell(row=row, column=1, value="Total costs").font = bold
    for y in range(1, YEARS + 1):
        col = get_column_letter(y + 1)
        m[f"{col}{row}"] = f"=SUM({col}{cost[0]}:{col}{cost[1]})" if cost[1] >= cost[0] else 0
        m[f"{col}{row}"].number_format = "£#,##0"
    row += 2
    net = row
    m.cell(row=row, column=1, value="Net position (income - costs)").font = bold
    for y in range(1, YEARS + 1):
        col = get_column_letter(y + 1)
        m[f"{col}{row}"] = f"={col}{inc_total}-{col}{cost_total}"
        m[f"{col}{row}"].number_format = "£#,##0;[Red]-£#,##0"
    row += 1
    cum = row
    m.cell(row=row, column=1, value="Cumulative position").font = bold
    for y in range(1, YEARS + 1):
        col = get_column_letter(y + 1)
        m[f"{col}{row}"] = f"={col}{net}" if y == 1 else f"={get_column_letter(y)}{cum}+{col}{net}"
        m[f"{col}{row}"].number_format = "£#,##0;[Red]-£#,##0"
    row += 2
    last = get_column_letter(YEARS + 1)
    m.cell(row=row, column=1, value="First year with a surplus").font = bold
    m.cell(row=row, column=2, value=(f'=IF(SUM(B{inc_total}:{last}{inc_total})=0,"Enter your figures first",'
                                     f'IFERROR("Year "&MATCH(TRUE,INDEX(B{net}:{last}{net}>0,0),0),"None in {YEARS} years"))'))
    row += 1
    m.cell(row=row, column=1, value="Cumulative break-even year").font = bold
    m.cell(row=row, column=2, value=(f'=IF(SUM(B{inc_total}:{last}{inc_total})=0,"Enter your figures first",'
                                     f'IFERROR("Year "&MATCH(TRUE,INDEX(B{cum}:{last}{cum}>=0,0),0),"None in {YEARS} years"))'))
    row += 2
    m.cell(row=row, column=1, value="Quote only these results in the bid. Every outcome sentence in the draft was turned "
                                    "into a [CONFIRM] item until this model supports it.").alignment = Alignment(wrap_text=True)
    m.column_dimensions["A"].width = 60
    for y in range(1, YEARS + 1):
        m.column_dimensions[get_column_letter(y + 1)].width = 13
    m.freeze_panes = "B2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def financial_answer(bid_response: dict | None) -> tuple[str, str]:
    """(question number, answer) of the most heavily weighted financial question, or ("", "")."""
    best, best_w = ("", ""), -1.0
    for q in (bid_response or {}).get("itt_question_responses") or []:
        if not isinstance(q, dict) or not is_financial_question(f"{q.get('question_text') or ''}"):
            continue
        w = re.findall(r"\d+(?:\.\d+)?", str(q.get("weighting") or ""))
        weight = float(w[0]) if w else 0.0
        if weight > best_w:
            best, best_w = (str(q.get("question_number") or ""), str(q.get("response") or "")), weight
    return best


def model_inputs_summary(stage1: dict | None, answer: str) -> dict[str, Any]:
    return {"buyer_figures": len(buyer_inputs(stage1)), "gaps": len(gap_inputs(answer))}


# ---------------------------------------------------------------------------
# Release 7: the financial question's own brief
# ---------------------------------------------------------------------------

def is_heavy_financial(q: dict) -> bool:
    """A scored question that is financial and carries real weight (25% or more), or any financial one when no
    weightings are given."""
    text = str(q.get("text") or q.get("question_text") or "")
    if not is_financial_question(text):
        return False
    w = q.get("weight_pct")
    return w is None or float(w) >= 25


def financial_question_block(q: dict, stage1: dict | None, has_model_file: bool) -> str:
    """Extra instructions for a heavily weighted financial question (Matthewsgreen Q4, 40% of the marks). It lists the
    buyer's own published figures with their source, sets out the structure of a scorable financial answer, and forbids
    any outcome (surplus, break-even, return) the figures given do not show."""
    figures = []
    for r in buyer_inputs(stage1):
        if r["kind"] == "share":
            figures.append(f"- {r['label']}: {round(r['pct'] * 100, 1):g}% (source: {r['source']})")
        else:
            figures.append(f"- {r['label']}: GBP {r['value']:,.0f} (source: {r['source']})")
    lines = ["FINANCIAL ANSWER BRIEF (this question carries a large share of the marks; answer it as a financial plan):"]
    if figures:
        lines.append("Figures the BUYER published. Use each one, quote its source, and build the cost lines from them:")
        lines += figures
    lines.append("Structure, in this order, using tables: (1) assumptions table (each assumption with its source: buyer "
                 "document, clarification, or the bidder's own file); (2) income by stream; (3) costs by line, including the "
                 "buyer's fixed and shared costs above; (4) annual surplus or deficit for the full term; (5) reserves and how "
                 "the sinking fund or reserve is met; (6) sensitivity: income down 20%, costs up 10%; (7) who monitors it "
                 "and how it is reported.")
    if has_model_file:
        lines.append("The bidder's own financial model is above under THE BIDDER'S OWN FINANCIAL MODEL: take income, staffing "
                     "and cost figures from it, name the file as the source, and calculate nothing it does not support.")
    else:
        lines.append("The bidder has not supplied a financial model. Every income, wage, price, occupancy or investment figure "
                     "of the bidder's is therefore [NEEDS INPUT: ...], one tag per figure, never an estimate. Do not state a "
                     "surplus, break-even point, return or growth rate: say the plan will show it once the bidder's inputs are "
                     "entered in the financial model workbook. Do not repeat the same tag for a figure already tagged.")
    lines.append("Open the answer with a direct statement of what the financial plan shows, in the buyer's words, then the tables.")
    return "\n".join(lines) + "\n"
