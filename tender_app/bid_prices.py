"""Release 12: the bidder's own prices fill the buyer's price schedule.

Chemonics' commercial section had no figures, only 60 "[NEEDS INPUT: unit price]" rows: TenderFlow never invents a price,
and there was nowhere to give one. The bid page now shows a "Your prices" form built from the buyer's price schedule
(one box per line item) and also accepts pasted "item: price" lines. These functions turn that input into unit prices
per row, and the commercial table and workbook use them. Pure functions; no model call, no credits.
"""
from __future__ import annotations

import re

_NUM = re.compile(r"(?:GBP|£|\$|USD|EUR|€)?\s*(-?\d[\d,]*(?:\.\d+)?)\s*(?:GBP|USD|EUR)?\s*$", re.I)
_PRICE_COL = re.compile(r"unit\s*price|price|rate|total|amount", re.I)
_QTY_COL = re.compile(r"\b(qty|quantity|units?)\b", re.I)


def to_number(value) -> float | None:
    """'£1,250.50' -> 1250.5. None for anything that is not a plain amount."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    m = _NUM.match(str(value or "").strip())
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def label_columns(header: list) -> list:
    """The columns that describe a line item (not its price or total), at most three."""
    return [i for i in range(len(header)) if not _PRICE_COL.search(str(header[i]))][:3]


def qty_column(header: list):
    return next((i for i, h in enumerate(header) if _QTY_COL.search(str(h))), None)


def row_label(header: list, row: list) -> str:
    """'D1 - Laptop 14in' style label from the item columns, quantity left out."""
    q = qty_column(header)
    parts = [str(row[i]).strip() for i in label_columns(header) if i < len(row) and i != q and str(row[i]).strip()]
    return " - ".join(parts)[:160]


def parse_price_text(text: str) -> dict:
    """Pasted lines "Laptop 14in: 650" / "D1 | 650" / "Laptop 14in - £650.00" -> {normalised label: 650.0}."""
    out: dict = {}
    for line in str(text or "").splitlines():
        line = line.strip().strip("|").strip()
        if not line:
            continue
        m = re.match(r"^(.*?)\s*(?:[:|\t=]|\s[-–—]\s)\s*((?:GBP|£|\$|USD|EUR|€)?\s*-?\d[\d,]*(?:\.\d+)?\s*(?:GBP|USD|EUR)?)\s*$", line, re.I)
        if not m or not m.group(1).strip():
            continue
        v = to_number(m.group(2))
        if v is not None and v >= 0:
            out[_norm(m.group(1))] = v
    return out


def unit_prices(schedules, prices: dict | None = None, price_text: str = "") -> list:
    """[[price or None per row] per schedule]. `prices` is keyed "schedule:row" (from the form); pasted lines match a
    row by its item code (first column) or by its label, exactly or when every word of the pasted name is in it."""
    by_pos = {}
    for k, v in (prices or {}).items():
        m = re.match(r"^(\d+):(\d+)$", str(k))
        n = to_number(v)
        if m and n is not None and n >= 0:
            by_pos[(int(m.group(1)), int(m.group(2)))] = n
    pasted = parse_price_text(price_text)
    out = []
    for si, sch in enumerate(schedules or []):
        header = [str(h) for h in (sch.get("header") or [])]
        rows = sch.get("rows") or []
        col = []
        for ri, r in enumerate(rows):
            v = by_pos.get((si, ri))
            if v is None and pasted:
                label = _norm(row_label(header, r))
                code = _norm(r[0]) if r else ""
                v = pasted.get(label) if label in pasted else (pasted.get(code) if code and code in pasted else None)
                if v is None:
                    words = set(label.split())
                    hits = [p for k, p in pasted.items() if k and set(k.split()) <= words]
                    v = hits[0] if len(hits) == 1 else None
            col.append(v)
        out.append(col)
    return out


def fmt_gbp(v: float) -> str:
    return f"GBP {v:,.2f}"


def priced_count(units: list) -> tuple[int, int]:
    flat = [v for col in units for v in col]
    return sum(1 for v in flat if v is not None), len(flat)
