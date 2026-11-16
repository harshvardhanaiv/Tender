"""Read buyer Excel workbooks (.xlsx/.xlsm) as text, with no third-party dependency.

Each sheet becomes a block headed ``[Sheet: <name> | <kind>]`` followed by its cell grid as pipe-separated rows,
so the bid writer sees every line item, quantity and answer. ``kind`` is one of
``price_schedule`` (Qty / Unit price / Total columns), ``clarifications`` (Question / Answer columns),
``specification`` or ``other``. Fix plan N1.
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

_PRICE_HDR = re.compile(r"\b(unit\s*price|price|rate|total|amount|cost)\b", re.I)
_QTY_HDR = re.compile(r"\b(qty|quantity|units?|no\.?)\b", re.I)
_QA_Q = re.compile(r"\b(questions?|quer(?:y|ies)|clarifications?)\b", re.I)
_QA_A = re.compile(r"\b(answers?|responses?|repl(?:y|ies))\b", re.I)


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "")
    n = 0
    for ch in (letters.group(0) if letters else "A"):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.parse(z.open("xl/sharedStrings.xml")).getroot()
    except Exception:
        return []
    return ["".join(t.text or "" for t in si.iter(_M + "t")) for si in root.findall("m:si", _NS)]


def classify_sheet(rows: list[list[str]]) -> str:
    # A Question/Answer header decides first (its answers may mention prices); look at the first rows only.
    top = " ".join(" ".join(r) for r in rows[:6])
    if _QA_Q.search(top) and _QA_A.search(top):
        return "clarifications"
    head = " ".join(" ".join(r) for r in rows[:8])
    if _PRICE_HDR.search(head) and _QTY_HDR.search(head):
        return "price_schedule"
    if re.search(r"\b(specification|requirement|item|description)\b", head, re.I):
        return "specification"
    return "other"


def read_workbook(data: bytes) -> list[dict]:
    """[{name, kind, rows}] for every non-empty sheet."""
    out: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared = _shared_strings(z)
        wb = ET.parse(z.open("xl/workbook.xml")).getroot()
        rels = {r.get("Id"): r.get("Target") for r in
                ET.parse(z.open("xl/_rels/workbook.xml.rels")).getroot()}
        for sh in wb.iter(_M + "sheet"):
            target = rels.get(sh.get(_REL)) or ""
            path = target.lstrip("/") if target.startswith("/") else "xl/" + target
            try:
                root = ET.parse(z.open(path)).getroot()
            except Exception:
                continue
            rows: list[list[str]] = []
            for row in root.iter(_M + "row"):
                cells: dict[int, str] = {}
                for c in row.findall("m:c", _NS):
                    t = c.get("t")
                    v = c.find("m:v", _NS)
                    if t == "s" and v is not None and v.text is not None:
                        try:
                            val = shared[int(v.text)]
                        except Exception:
                            val = ""
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(_M + "t"))
                    else:
                        val = (v.text or "") if v is not None else ""
                    val = re.sub(r"\s+", " ", val).strip()
                    if val:
                        cells[_col_index(c.get("r") or "A")] = val
                if cells:
                    width = max(cells) + 1
                    rows.append([cells.get(i, "") for i in range(width)])
            if rows:
                out.append({"name": sh.get("name") or "Sheet", "kind": classify_sheet(rows), "rows": rows})
    return out


_QA_FILENAME = re.compile(r"questions?\s*(?:&|and|\+)\s*answers?|\bq\s*&\s*a\b|clarification", re.I)


def xlsx_to_text(data: bytes, max_chars: int = 120000, filename: str = "") -> str:
    try:
        sheets = read_workbook(data)
    except Exception:
        return ""
    # A workbook called "Questions & Answers 1.xlsx" is the buyer's clarification log whatever its header row says
    # (release 5: the Chemonics Q&A answers were not applied).
    if filename and _QA_FILENAME.search(filename):
        for s in sheets:
            if s["kind"] in ("other", "specification"):
                s["kind"] = "clarifications"
    parts: list[str] = []
    for s in sheets:
        body = "\n".join(" | ".join(r).rstrip(" |") for r in s["rows"])
        parts.append(f"[Sheet: {s['name']} | {s['kind']}]\n{body}")
    return "\n\n".join(parts)[:max_chars]


def sheet_summary(data: bytes) -> list[dict]:
    """Sheet names and kinds only (for the pre-generation checklist)."""
    try:
        return [{"name": s["name"], "kind": s["kind"], "rows": len(s["rows"])} for s in read_workbook(data)]
    except Exception:
        return []


_QA_BLOCK = re.compile(r"\[Sheet: ([^\]|]+?) \| clarifications\]\n(.*?)(?=\n\n\[Sheet: |\Z)", re.S)


def clarification_sheets_block(documents, limit: int = 9000) -> str:
    """The buyer's published Q&A rows, verbatim, for the writer notes. Spreadsheet answers override the tender text,
    so they must reach the writer even when the model's own extraction missed them (release 5)."""
    parts: list[str] = []
    for d in documents or []:
        for m in _QA_BLOCK.finditer(str((d or {}).get("content") or "")):
            parts.append("[%s]\n%s" % (m.group(1).strip(), m.group(2).strip()))
    if not parts:
        return ""
    body = "\n\n".join(parts)[:limit]
    return ("\nBUYER'S PUBLISHED QUESTIONS AND ANSWERS (from the buyer's spreadsheet). These answers OVERRIDE the tender text: "
            "apply every ruling (VAT or price basis, installation, delivery phasing, who supplies what) and never contradict one:\n"
            + body + "\n")
