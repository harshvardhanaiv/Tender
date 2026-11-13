"""Link the rows that are the same tender on different portals (Civenta fix plan A1).

Matthewsgreen showed as two unlinked rows: ProContract ("Closed 8 Nov", the real deadline after an extension) and
Find a Tender (still "Closed 1 Nov"). Each row now lists the same tender's other notices, and a row whose deadline
is older than another portal's says so, so the page can show the later date instead of a stale "Closed".

Same tender = a shared portal reference (DN831703 in one row's text and the other's id), or the same buyer and a
near-identical title (one spelling slip allowed: "Mathewsgreen" / "Matthewsgreen").
"""
from __future__ import annotations

import datetime as _dt
import re
from difflib import SequenceMatcher
from typing import Any

_REF = re.compile(r"\b(DN\d{5,7}|[A-Z]{2,5}\d{5,9})\b")
_TITLE_STOP = {"the", "and", "of", "for", "to", "a", "an", "in", "on", "at", "by", "with", "notice", "tender",
               "contract", "expression", "interest", "eoi", "invitation", "itt", "rfq", "rfp", "opportunity",
               "preliminary", "market", "engagement", "uk2", "uk4", "uk6"}
_AUTH_STOP = {"the", "of", "and", "council", "borough", "county", "city", "district", "metropolitan", "london",
              "royal", "nhs", "trust", "foundation", "university", "college", "limited", "ltd", "plc", "group"}


def _norm_title(t: str) -> str:
    words = re.findall(r"[a-z0-9]+", (t or "").lower().replace("&", " and "))
    return " ".join(w for w in words if w not in _TITLE_STOP)


def _auth_key(a: str) -> str:
    words = [w for w in re.findall(r"[a-z]+", (a or "").lower()) if w not in _AUTH_STOP and len(w) > 2]
    return words[0] if words else ""


def _refs(row: dict) -> set[str]:
    text = " ".join(str(row.get(k) or "") for k in ("resource_id", "reference", "portal_reference", "title",
                                                    "description", "url", "link"))
    return set(_REF.findall(text.upper()))


def parse_deadline(value: Any) -> _dt.date | None:
    s = str(value or "")
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b", s)
        if not m:
            return None
        d, mo, y = map(int, m.groups())
    try:
        return _dt.date(y, mo, d)
    except ValueError:
        return None


def _same(a: dict, b: dict, ta: str, tb: str) -> bool:
    if _refs(a) & _refs(b):
        return True
    if not ta or not tb:
        return False
    ka, kb = _auth_key(str(a.get("contracting_authority") or "")), _auth_key(str(b.get("contracting_authority") or ""))
    if ka and kb and ka != kb:
        return False
    if ta == tb or (len(min(ta, tb, key=len)) >= 12 and (ta in tb or tb in ta)):
        return True
    return SequenceMatcher(None, ta, tb).ratio() >= 0.88


def link_same_tender(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copies of the rows; a row with the same tender on another portal gains
    `linked_notices` [{source, source_label, title, submission_deadline, url}] and, when another portal's deadline
    is later than its own, `newer_deadline_elsewhere` {date (DD/MM/YYYY), source_label}."""
    out = [dict(r) for r in rows]
    titles = [_norm_title(str(r.get("title") or "")) for r in out]
    by_auth: dict[str, list[int]] = {}
    for i, r in enumerate(out):
        by_auth.setdefault(_auth_key(str(r.get("contracting_authority") or "")), []).append(i)
    pairs: set[tuple[int, int]] = set()
    for idxs in by_auth.values():
        for x in range(len(idxs)):
            for y in range(x + 1, len(idxs)):
                i, j = idxs[x], idxs[y]
                if out[i].get("source") != out[j].get("source") and _same(out[i], out[j], titles[i], titles[j]):
                    pairs.add((i, j))
    # A shared reference links rows even when the buyer's name is written differently.
    ref_rows: dict[str, list[int]] = {}
    for i, r in enumerate(out):
        for ref in _refs(r):
            ref_rows.setdefault(ref, []).append(i)
    for idxs in ref_rows.values():
        for x in range(len(idxs)):
            for y in range(x + 1, len(idxs)):
                i, j = sorted((idxs[x], idxs[y]))
                if out[i].get("source") != out[j].get("source"):
                    pairs.add((i, j))
    links: dict[int, set[int]] = {}
    for i, j in pairs:
        links.setdefault(i, set()).add(j)
        links.setdefault(j, set()).add(i)
    for i, others in links.items():
        row = out[i]
        own = parse_deadline(row.get("submission_deadline"))
        row["linked_notices"] = [{
            "source": out[j].get("source"),
            "source_label": out[j].get("source_label") or out[j].get("source"),
            "title": out[j].get("title"),
            "submission_deadline": out[j].get("submission_deadline"),
            "url": out[j].get("url") or out[j].get("link"),
        } for j in sorted(others)]
        later = [(parse_deadline(out[j].get("submission_deadline")), j) for j in others]
        later = [(d, j) for d, j in later if d and (own is None or d > own)]
        if later:
            d, j = max(later)
            row["newer_deadline_elsewhere"] = {"date": d.strftime("%d/%m/%Y"),
                                               "source_label": out[j].get("source_label") or out[j].get("source")}
    return out
