#!/usr/bin/env python3
"""Read-only data-quality report for Supplier Intelligence (suppliers + contract_awards).

Quantifies the fallout of the non-deterministic fallback company_number bug (see
_fallback_company_number in etenders_scraper/awards.py): every time the award scraper
process restarted, Python's randomized hash() minted a *different* fake CF/UK company
number for the same supplier name, so the suppliers.company_number UNIQUE constraint
never caught the repeat and a fresh duplicate row got inserted instead.

Run this against local and production and compare the two outputs directly (the report
is deterministic and read-only, so it's safe to run on prod without --dry-run/--apply):

    python scripts/supplier_dedup_report.py                  # local (.env)
    python scripts/supplier_dedup_report.py --production      # prod (.env.production)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production", action="store_true", help="Load .env.production instead of .env")
    args = parser.parse_args()

    if args.production:
        from dotenv import load_dotenv
        load_dotenv(".env.production", override=True)
        print("Loaded .env.production\n")

    from tender_app.ch_matcher import normalize_ch_company_number
    from tender_app.db import get_db_connection

    conn = get_db_connection()
    cur = conn.cursor()
    print(f"Connected to {os.environ.get('DB_HOST')}:{os.environ.get('DB_PORT')}/{os.environ.get('DB_NAME')}\n")

    cur.execute("SELECT id, company_number, name FROM suppliers;")
    rows = cur.fetchall()
    total = len(rows)

    junk_ids: list[int] = []
    fallback_groups: dict[str, list[int]] = defaultdict(list)
    real_count = 0

    for sid, cnum, name in rows:
        name_norm = (name or "").strip().lower()
        if name_norm in JUNK_NAMES:
            junk_ids.append(sid)
            continue
        if normalize_ch_company_number(cnum):
            real_count += 1
            continue
        fallback_groups[name_norm].append(sid)

    dup_groups = {k: v for k, v in fallback_groups.items() if len(v) > 1}
    dup_row_count = sum(len(v) for v in dup_groups.values())
    would_remove_dupes = dup_row_count - len(dup_groups)  # keep 1 canonical per group

    cur.execute("SELECT COUNT(*) FROM contract_awards;")
    total_awards = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM contract_awards WHERE lower(trim(supplier_name)) = ANY(%s);",
        (list(JUNK_NAMES),),
    )
    junk_awards = cur.fetchone()[0]

    print("=" * 72)
    print("SUPPLIERS")
    print("=" * 72)
    print(f"Total rows:                                         {total:>8}")
    print(f"  Real Companies-House-numbered suppliers:          {real_count:>8}")
    print(f"  Fallback-ID rows (no real CH/SC/NI/OC number):    {sum(len(v) for v in fallback_groups.values()):>8}")
    print(f"    of which sit in a duplicate-name group:         {dup_row_count:>8}  ({len(dup_groups)} groups)")
    print(f"    rows a cleanup would remove (keep 1/group):     {would_remove_dupes:>8}")
    print(f"  Junk-named rows (e.g. 'Not Awarded'):             {len(junk_ids):>8}")

    if dup_groups:
        print("\nTop 15 largest duplicate-name groups:")
        for name_norm, ids in sorted(dup_groups.items(), key=lambda kv: -len(kv[1]))[:15]:
            print(f"  {len(ids):>4}x  {name_norm!r}")

    print("\n" + "=" * 72)
    print("CONTRACT_AWARDS")
    print("=" * 72)
    print(f"Total rows:                                         {total_awards:>8}")
    print(f"  Rows tied to a junk supplier name:                {junk_awards:>8}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
