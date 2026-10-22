"""Read side of the Charity Commission register (see charity_import.py).

A supplier that is a registered charity is a VCSE organisation by definition; the register also gives the
charity number, latest income and the charity's own contact details."""
from __future__ import annotations

from typing import Any, Iterable

REGISTER_URL = "https://register-of-charities.charitycommission.gov.uk/en/charity-search/-/charity-details/{}"


def _tables_exist(cursor: Any) -> bool:
    cursor.execute("SELECT to_regclass('public.supplier_charity') IS NOT NULL AND to_regclass('public.charity_register') IS NOT NULL")
    return bool(cursor.fetchone()[0])


def public_charity(row: tuple) -> dict[str, Any]:
    (number, name, income, expenditure, year_end, postcode, phone, email, website, activities, kind) = row
    site = (website or "").strip()
    if site and not site.lower().startswith(("http://", "https://")):
        site = "https://" + site
    return {
        "charity_number": int(number),
        "name": name,
        "income": float(income) if income is not None else None,
        "expenditure": float(expenditure) if expenditure is not None else None,
        "year_end": year_end,
        "postcode": postcode,
        "phone": phone,
        "email": email,
        "website": site or None,
        "activities": activities,
        "match": kind,
        "url": REGISTER_URL.format(int(number)),
    }


def charities_for_suppliers(cursor: Any, supplier_ids: Iterable[int | None]) -> dict[int, dict[str, Any]]:
    """supplier id -> its charity record, for the suppliers that are registered charities. Empty (never an
    error) when the register has not been imported or the lookup fails, so a profile always loads."""
    ids = sorted({int(i) for i in supplier_ids if i})
    if not ids:
        return {}
    conn = cursor.connection
    cur = conn.cursor()  # a cursor of its own: the caller still reads rows through cursor.description
    try:
        if not _tables_exist(cur):
            return {}
        cur.execute(
            """
            SELECT sc.supplier_id, c.charity_number, c.name, c.income, c.expenditure, c.year_end, c.postcode,
                   c.phone, c.email, c.website, c.activities, sc.match_kind
            FROM supplier_charity sc JOIN charity_register c ON c.charity_number = sc.charity_number
            WHERE sc.supplier_id = ANY(%s)
            """,
            (ids,),
        )
        return {int(r[0]): public_charity(r[1:]) for r in cur.fetchall()}
    except Exception as exc:
        print(f"[charities] lookup failed: {exc}")
        conn.rollback()
        return {}
    finally:
        cur.close()


def charity_for_supplier(cursor: Any, supplier_id: int | None) -> dict[str, Any] | None:
    return charities_for_suppliers(cursor, [supplier_id]).get(int(supplier_id)) if supplier_id else None
