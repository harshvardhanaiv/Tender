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
_QA_Q = re.compile(r"\b(question|query|clarification)\b", re.I)
_QA_A = re.compile(r"\b(answer|response|reply)\b", re.I)


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
    head = " ".join(" ".join(r) for r in rows[:8])
    if _QA_Q.search(head) and _QA_A.search(head):
        return "clarifications"
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


def xlsx_to_text(data: bytes, max_chars: int = 120000) -> str:
    try:
        sheets = read_workbook(data)
    except Exception:
        return ""
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
