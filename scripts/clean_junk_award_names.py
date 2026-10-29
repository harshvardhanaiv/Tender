#!/usr/bin/env python3
"""Repair award rows whose buyer or winner is page text, not a name (Find a Tender "Contract details" notices).

  authority_name  "one.1) Name and addressesI.1) Name and addresses" (a section heading read as the buyer)
                  -> "UK Public Sector Authority", the label used when a notice names no buyer.
  supplier_name   "is PwC, with a committed" (the middle of a sentence read as the winner)
                  -> the row is removed: it names no company, so it cannot be anyone's win. Rows removed are listed first.

New awards are checked the same way on ingestion (etenders_scraper.awards.is_junk_*). Dry run by default; --apply commits.

    python scripts/clean_junk_award_names.py
    python scripts/clean_junk_award_names.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etenders_scraper.awards import is_junk_authority_name, is_junk_supplier_name  # noqa: E402
from tender_app.db import get_db_connection  # noqa: E402

FALLBACK_AUTHORITY = "UK Public Sector Authority"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, authority_name FROM contract_awards WHERE authority_name ~* %s OR authority_name ~* %s",
                (r"name\s+and\s+address", r"^\s*(one|[ivx]+|\d+)\.\d+\)"))
    authorities = [(i, a) for i, a in cur.fetchall() if is_junk_authority_name(a)]
    cur.execute("SELECT id, supplier_name, authority_name, tender_title FROM contract_awards WHERE supplier_name ~ %s",
                (r"^(is|are|was|were|has|have|will|and|being|which|that|by)\s+\S",))
    suppliers = [r for r in cur.fetchall() if is_junk_supplier_name(r[1])]

    print(f"buyer names to replace: {len(authorities)}")
    print(f"winner names that are not names (rows to remove): {len(suppliers)}")
    for i, name, authority, title in suppliers[:25]:
        print(f"  #{i} {name!r}  buyer={authority!r}  {str(title)[:50]!r}")

    if not args.apply:
        print("dry run: nothing changed (use --apply)")
        return
    if authorities:
        cur.execute("UPDATE contract_awards SET authority_name = %s WHERE id = ANY(%s)", (FALLBACK_AUTHORITY, [i for i, _ in authorities]))
    if suppliers:
        cur.execute("DELETE FROM contract_awards WHERE id = ANY(%s)", ([r[0] for r in suppliers],))
    conn.commit()
    print("applied. Rebuild the stats tables (they refresh on their own within the hour, or on the next supplier request).")


if __name__ == "__main__":
    main()
