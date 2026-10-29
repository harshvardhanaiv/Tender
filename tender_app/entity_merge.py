"""Read-time merge of supplier rows that are the same company published under different spellings.

"XMA LIMITED" / "XMA Limited" / "Xma Ltd" are separate supplier rows (the stored names are never rewritten, they are the keys
people search by), so lists showed the company two or three times with its awards split between them. This folds the
rows of one result page: same name once case, punctuation, "&"/"and" and legal-form suffixes (Ltd, Limited, PLC, LLP ...)
are ignored. Two rows are only merged when they cannot be different companies: they share a Companies House number or at
most one of them carries one. Two different real company numbers under one name are left apart.
"""
from __future__ import annotations

import re
from typing import Any

_SUFFIX_RE = re.compile(r"\b(LTD|LIMITED|PLC|LLP|LLC|INC|CORP|CO)\b")
# Text a notice adds after the company's name: "(appointed to Lot 4)", "Lot 1 (Gas): Gazprom Energy", "Bunzl UK Ltd T/A Bunzl Cleaning",
# "Capita Business Services Ltd (Trading as NAFIC)". The company is the same with or without it.
_LOT_SUFFIX_RE = re.compile(r"\(\s*(APPOINTED TO )?LOTS?\s*[0-9A-Z ,&/-]*\)")
_LOT_PREFIX_RE = re.compile(r"^LOT\s*[0-9]+\s*\([^)]*\)\s*:\s*")
_TRADING_AS_RE = re.compile(r"\(\s*(T/A|TRADING AS)[^)]*\)| (T/A|TRADING AS) .*$")


# "Sports and Leisure Management Limited (Everyone Active)": a bracket after the legal form is a trading name. A bracket before it
# ("Aspire Services (Leeds) LTD", "Carillion (AMBS) Ltd") names a different body and is kept.
_BRACKETED_TRADING_NAME_RE = re.compile(r"\b(LTD|LIMITED|PLC|LLP|LLC|INC)\.?\s*\([^)]*\)\s*$")


def entity_name_key(name: str | None) -> str:
    text = str(name or "").replace("&", " and ").upper().replace("UNITED KINGDOM", "UK")
    text = _LOT_PREFIX_RE.sub("", text.strip())
    text = _TRADING_AS_RE.sub("", _LOT_SUFFIX_RE.sub("", text))
    text = _BRACKETED_TRADING_NAME_RE.sub(r"\1", text)
    text = re.sub(r"[^A-Z0-9]+", " ", text).strip()
    text = _SUFFIX_RE.sub(" ", text)
    return re.sub(r"[\s]+", "", re.sub(r"\bAND\b", " ", text))


def numbers_compatible(a: str, b: str) -> bool:
    """Two Companies House numbers that can be the same company: equal, or two neighbouring digits swapped (IBM United Kingdom's
    00741598 and 00741958). A single different digit is NOT enough: companies are registered in sequence, so 09990001 and
    09990002 are two companies."""
    a, b = str(a or "").strip(), str(b or "").strip()
    if a == b:
        return True
    if len(a) != len(b) or len(a) != 8 or not (a.isalnum() and b.isalnum()):
        return False
    diff = [i for i in range(8) if a[i] != b[i]]
    return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]


def _real_number(row: dict[str, Any]) -> str:
    num = str(row.get("company_number") or "").strip()
    return "" if not num or num.lower() == "not available" else num


def _all_compatible(nums: set[str]) -> bool:
    ordered = sorted(nums)
    return all(numbers_compatible(ordered[0], n) for n in ordered[1:])


def merge_duplicate_suppliers(suppliers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[tuple[str, dict[str, Any]]] = []
    for s in suppliers:
        key = entity_name_key(s.get("name"))
        if not key:
            order.append(("", s))
            continue
        placed = False
        for g in groups.get(key, []):
            nums = {_real_number(x) for x in g if _real_number(x)} | ({_real_number(s)} - {""})
            if len(nums) <= 1 or _all_compatible(nums):
                g.append(s)
                placed = True
                break
        if not placed:
            groups.setdefault(key, []).append([s])
            order.append((key, groups[key][-1]))
    out: list[dict[str, Any]] = []
    for key, item in order:
        if not key or isinstance(item, dict):
            out.append(item)
            continue
        if len(item) == 1:
            out.append(item[0])
            continue
        survivor = dict(sorted(item, key=lambda r: (not _real_number(r), -float(r.get("total_value") or 0)))[0])
        for f in ("total_awards", "direct_awards", "framework_appointments_count"):
            survivor[f] = sum(int(r.get(f) or 0) for r in item)
        survivor["total_value"] = sum(float(r.get("total_value") or 0) for r in item)
        survivor["framework_total_ceiling"] = sum(float(r.get("framework_total_ceiling") or 0) for r in item)
        n = survivor["direct_awards"]
        survivor["avg_value"] = survivor["total_value"] / n if n else survivor.get("avg_value", 0.0)
        if not _real_number(survivor):
            survivor["company_number"] = next((_real_number(r) for r in item if _real_number(r)), "Not available")
        survivor["merged_names"] = sorted({str(r.get("name") or "") for r in item})
        survivor["merged_numbers"] = sorted({_real_number(r) for r in item if _real_number(r)})  # more than one: a mistyped number
        survivor["merged_ids"] = [r.get("id") for r in item]
        out.append(survivor)
    return out
