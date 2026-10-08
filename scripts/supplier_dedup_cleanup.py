#!/usr/bin/env python3
"""Removes wrong and duplicate Supplier Intelligence data caused by the non-deterministic
fallback company_number bug (fixed in etenders_scraper/awards.py, _fallback_company_number).

Two categories of bad `suppliers` rows are handled. Only rows WITHOUT a real Companies
House / SC / NI / OC number are ever touched — a supplier with a real CH number can't be
a duplicate of another (the UNIQUE constraint on company_number already prevents that),
so this script never merges or deletes real, verified supplier records.

1. Junk-named rows (name is a placeholder like "Not Awarded", "Unknown", "TBC", ...):
   not real suppliers. Deleted, along with their contract_awards rows (those aren't real
   contract awards either).

2. Duplicate rows (exact same supplier name, but a different auto-generated CF/UK
   fallback company_number because the process restarted between scrapes): merged.
   For each group we keep one canonical row — the one with the most populated contact
   fields (address/email/phone/website), tie-broken by lowest id (first seen) — repoint
   every contract_awards row from the duplicates onto the canonical supplier, then delete
   the duplicate supplier rows.

Always run --dry-run (the default) first and read the summary before --apply.

Usage:
    python scripts/supplier_dedup_cleanup.py                          # dry run, local .env
    python scripts/supplier_dedup_cleanup.py --apply                  # apply, local .env
    python scripts/supplier_dedup_cleanup.py --production             # dry run, .env.production
    python scripts/supplier_dedup_cleanup.py --production --apply     # apply, .env.production
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Kept in sync with the ingest-time junk-name filters in etenders_scraper/awards.py
# and scripts/contracts_finder_sync.py.
JUNK_NAMES = {
    "live scraped supplier", "supplier", "unknown", "n/a", "none", "tbc",
    "not applicable", "contract value", "contract", "not awarded", "no award",
    "award not made", "please refer to weblink", "refer to weblink", "various",
    "see website", "please see website", "redacted", "withheld for security reasons",
}


SUPPLIER_COLUMNS = "id, company_number, name, address, email, phone, website, region, sme_status, vcse_status, created_at"


def _pick_canonical(rows: list[tuple]) -> tuple:
    """rows: list of tuples shaped like SUPPLIER_COLUMNS.
    Prefer the row with the most populated contact fields; tie-break on lowest id."""
    def score(r):
        address, email, phone, website = r[3], r[4], r[5], r[6]
        populated = sum(1 for v in (address, email, phone, website) if v and str(v).strip())
        return (-populated, r[0])
    return sorted(rows, key=score)[0]


def plan(all_rows: list[tuple]) -> tuple[list[int], dict[str, list[tuple]]]:
    """Pure, DB-independent dedupe plan from rows shaped like SUPPLIER_COLUMNS.
    Returns (junk_ids, dup_groups) — used both to apply changes and to preview the
    logical post-dedupe state (e.g. for a cross-database sync dry run)."""
    from tender_app.ch_matcher import normalize_ch_company_number

    junk_ids: list[int] = []
    fallback_groups: dict[str, list[tuple]] = defaultdict(list)

    for row in all_rows:
        sid, cnum, name = row[0], row[1], row[2]
        name_norm = (name or "").strip().lower()
        if name_norm in JUNK_NAMES:
            junk_ids.append(sid)
            continue
        if normalize_ch_company_number(cnum):
            continue  # real, verified CH/SC/NI/OC number — never touched
        fallback_groups[name_norm].append(row)

    dup_groups = {k: v for k, v in fallback_groups.items() if len(v) > 1}
    return junk_ids, dup_groups


def logical_post_dedupe_rows(all_rows: list[tuple]) -> list[tuple]:
    """The supplier rows that would remain (canonical, deduped, junk-free) after a
    cleanup run — computed without touching the database, so it's accurate even in
    a dry run and independent of whether the dedupe was actually applied yet."""
    junk_ids, dup_groups = plan(all_rows)
    junk_ids_set = set(junk_ids)
    dup_row_ids = {r[0] for group in dup_groups.values() for r in group}
    kept = [r for r in all_rows if r[0] not in junk_ids_set and r[0] not in dup_row_ids]
    kept.extend(_pick_canonical(group) for group in dup_groups.values())
    return kept


def run(conn, apply_changes: bool) -> None:
    cur = conn.cursor()
    cur.execute(f"SELECT {SUPPLIER_COLUMNS} FROM suppliers;")
    all_rows = cur.fetchall()
    print(f"Loaded {len(all_rows)} supplier rows.\n")

    junk_ids, dup_groups = plan(all_rows)

    # --- Junk-named suppliers -------------------------------------------------
    print("=" * 72)
    print(f"JUNK-NAMED SUPPLIERS: {len(junk_ids)} rows to delete (and their contract_awards)")
    print("=" * 72)
    if junk_ids:
        cur.execute("SELECT COUNT(*) FROM contract_awards WHERE supplier_id = ANY(%s);", (junk_ids,))
        junk_award_count = cur.fetchone()[0]
        print(f"  contract_awards rows to delete: {junk_award_count}")
        if apply_changes:
            cur.execute("DELETE FROM contract_awards WHERE supplier_id = ANY(%s);", (junk_ids,))
            cur.execute("DELETE FROM suppliers WHERE id = ANY(%s);", (junk_ids,))
            print("  Applied.")
        else:
            print("  (dry run — nothing deleted)")

    # --- Duplicate-name groups --------------------------------------------------
    print("\n" + "=" * 72)
    print(f"DUPLICATE-NAME GROUPS: {len(dup_groups)} groups, "
          f"{sum(len(v) for v in dup_groups.values())} rows involved")
    print("=" * 72)

    total_merged = 0
    for idx, (name_norm, group) in enumerate(sorted(dup_groups.items(), key=lambda kv: -len(kv[1])), 1):
        canonical = _pick_canonical(group)
        dup_ids = [r[0] for r in group if r[0] != canonical[0]]
        print(f"  {len(group):>4}x {name_norm!r} -> keep id {canonical[0]} ({canonical[2]!r}), "
              f"merge {len(dup_ids)} duplicate(s)")
        if apply_changes:
            cur.execute(
                "UPDATE contract_awards SET supplier_id = %s, company_number = %s, supplier_name = %s "
                "WHERE supplier_id = ANY(%s);",
                (canonical[0], canonical[1], canonical[2], dup_ids),
            )
            cur.execute("DELETE FROM suppliers WHERE id = ANY(%s);", (dup_ids,))
            if idx % 200 == 0:
                conn.commit()
        total_merged += len(dup_ids)

    print(f"\nTotal duplicate supplier rows {'merged' if apply_changes else 'that would be merged'}: {total_merged}")

    if apply_changes:
        conn.commit()
        print("\nCOMMITTED.")
    else:
        conn.rollback()
        print("\nDRY RUN — no changes were made. Re-run with --apply to commit.")

    cur.execute("SELECT COUNT(*) FROM suppliers;")
    print(f"\nSuppliers remaining after this {'run' if apply_changes else 'dry run would leave'}: {cur.fetchone()[0] if apply_changes else len(all_rows) - len(junk_ids) - total_merged}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually commit changes (default is dry run)")
    parser.add_argument("--production", action="store_true", help="Load .env.production instead of .env")
    args = parser.parse_args()

    if args.production:
        from dotenv import load_dotenv
        load_dotenv(".env.production", override=True)
        print("Loaded .env.production\n")

    if args.production and args.apply:
        confirm = input("About to APPLY changes to the PRODUCTION database. Type 'yes' to continue: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            return 1

    from tender_app.db import get_db_connection

    conn = get_db_connection()
    print(f"Connected to {os.environ.get('DB_HOST')}:{os.environ.get('DB_PORT')}/{os.environ.get('DB_NAME')}\n")

    try:
        run(conn, apply_changes=args.apply)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
