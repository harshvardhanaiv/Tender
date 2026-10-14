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


def entity_name_key(name: str | None) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", " ", str(name or "").replace("&", " and ")).upper().strip()
    text = _SUFFIX_RE.sub(" ", text)
    return re.sub(r"[\s]+", "", re.sub(r"\bAND\b", " ", text))


def _real_number(row: dict[str, Any]) -> str:
    num = str(row.get("company_number") or "").strip()
    return "" if not num or num.lower() == "not available" else num


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
            if len(nums) <= 1:
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
        survivor["merged_ids"] = [r.get("id") for r in item]
        out.append(survivor)
    return out
