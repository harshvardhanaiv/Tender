"""Read side of grant_awards (UKRI Gateway to Research grants, see gtr_import.py).

Grants are reported next to a supplier's or buyer's contracts but are never added to their totals."""
from __future__ import annotations

import re
from typing import Any

from etenders_scraper.awards import supplier_name_key

SUPPLIER_LIST_LIMIT = 50
FUNDER_LIST_LIMIT = 25
FUNDER_TOP_RECIPIENTS = 10

_GRANT_COLUMNS = (
    "id", "gtr_project_id", "supplier_name", "funder", "programme", "title", "abstract", "status",
    "value_gbp", "start_date", "end_date", "url",
)
_SELECT = ", ".join(f"g.{c}" for c in _GRANT_COLUMNS)


def _item(row: tuple) -> dict[str, Any]:
    d = dict(zip(_GRANT_COLUMNS, row))
    d["value_gbp"] = float(d["value_gbp"]) if d["value_gbp"] is not None else None
    abstract = (d.get("abstract") or "").strip()
    d["abstract"] = abstract if len(abstract) <= 400 else abstract[:397].rstrip() + "..."
    return d


def _table_exists(cursor: Any) -> bool:
    cursor.execute("SELECT to_regclass('public.grant_awards') IS NOT NULL")
    return bool(cursor.fetchone()[0])


def grants_for_supplier(cursor: Any, supplier_id: int | None, supplier_name: str | None) -> dict[str, Any]:
    try:
        return _grants_for_supplier(cursor, supplier_id, supplier_name)
    except Exception as exc:  # grants are extra: a failure here must not take the whole profile down
        print(f"[grants] supplier lookup failed: {exc}")
        cursor.connection.rollback()
        return {"count": 0, "total_value": 0.0, "items": []}


def _grants_for_supplier(cursor: Any, supplier_id: int | None, supplier_name: str | None) -> dict[str, Any]:
    """Grants held by one supplier: linked by supplier id, or by the same name once punctuation is ignored."""
    empty = {"count": 0, "total_value": 0.0, "items": []}
    if not _table_exists(cursor):
        return empty
    key = supplier_name_key(supplier_name or "")
    if not supplier_id and not key:
        return empty
    where = "g.supplier_id = %s OR g.name_key = %s" if supplier_id and key else (
        "g.supplier_id = %s" if supplier_id else "g.name_key = %s")
    params = [p for p in (supplier_id, key if key else None) if p is not None]
    cursor.execute(
        f"SELECT COUNT(*), COALESCE(SUM(g.value_gbp), 0) FROM grant_awards g WHERE {where}", params
    )
    count, total = cursor.fetchone()
    if not count:
        return empty
    cursor.execute(
        f"SELECT {_SELECT} FROM grant_awards g WHERE {where} ORDER BY g.start_date DESC NULLS LAST, g.id LIMIT %s",
        params + [SUPPLIER_LIST_LIMIT],
    )
    return {"count": int(count), "total_value": float(total or 0), "items": [_item(r) for r in cursor.fetchall()]}


def _funder_like(authority_name: str | None) -> str | None:
    """'Innovate UK', 'innovate uk (UKRI)' and 'UKRI - Innovate UK' all name the funder 'Innovate UK'."""
    text = re.sub(r"[^a-z0-9 ]", " ", (authority_name or "").lower())
    text = re.sub(r"\s+", " ", text).strip()
    return "Innovate UK" if "innovate uk" in text else None


def grants_for_funder(cursor: Any, authority_name: str | None) -> dict[str, Any] | None:
    try:
        return _grants_for_funder(cursor, authority_name)
    except Exception as exc:
        print(f"[grants] funder lookup failed: {exc}")
        cursor.connection.rollback()
        return None


def _grants_for_funder(cursor: Any, authority_name: str | None) -> dict[str, Any] | None:
    """What a funder has granted, or None when this buyer is not a funder we hold grants for."""
    funder = _funder_like(authority_name)
    if not funder or not _table_exists(cursor):
        return None
    cursor.execute(
        "SELECT COUNT(*), COALESCE(SUM(value_gbp), 0), COUNT(DISTINCT name_key), MIN(start_date), MAX(start_date) "
        "FROM grant_awards WHERE funder = %s",
        (funder,),
    )
    count, total, recipients, first, last = cursor.fetchone()
    if not count:
        return None
    cursor.execute(
        f"SELECT {_SELECT} FROM grant_awards g WHERE g.funder = %s "
        "ORDER BY g.start_date DESC NULLS LAST, g.id LIMIT %s",
        (funder, FUNDER_LIST_LIMIT),
    )
    items = [_item(r) for r in cursor.fetchall()]
    cursor.execute(
        "SELECT MIN(supplier_name), COUNT(*), COALESCE(SUM(value_gbp), 0) FROM grant_awards WHERE funder = %s "
        "GROUP BY name_key ORDER BY SUM(value_gbp) DESC NULLS LAST LIMIT %s",
        (funder, FUNDER_TOP_RECIPIENTS),
    )
    top = [{"supplier_name": n, "count": int(c), "total_value": float(v or 0)} for n, c, v in cursor.fetchall()]
    return {
        "funder": funder, "count": int(count), "total_value": float(total or 0), "recipients": int(recipients),
        "first_start": first, "last_start": last, "items": items, "top_recipients": top,
    }
