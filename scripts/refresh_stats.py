#!/usr/bin/env python3
"""Rebuild supplier_stats and buyer_stats (the precomputed aggregates behind Supplier and Buyer
Intelligence). The app also does this itself in the background when the stats are older than
six hours; run this after a big load or a merge/cleanup to refresh immediately.

    python scripts/refresh_stats.py            # rebuild
    python scripts/refresh_stats.py --dry-run  # say what a rebuild would change; writes nothing

A rebuild only replaces derived tables, so there is nothing to revert: running it again rebuilds from the awards. Safe to run while the app is serving: readers keep seeing the old rows until the new ones commit.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_app.db import get_db_connection  # noqa: E402
from tender_app.stats import refresh_all  # noqa: E402

def dry_run() -> None:
    """What is stored now, and which columns a rebuild fills in (cpv_spend and next_renewal_title are new in re-test 3 B1/B2)."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        for table in ("supplier_stats", "buyer_stats"):
            cur.execute(f"SELECT COUNT(*), MIN(refreshed_at), MAX(refreshed_at) FROM {table}")
            print(table, cur.fetchone())
        for col in ("cpv_shares", "renewals_by_division", "cpv_spend", "next_renewal_title"):
            cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'buyer_stats' AND column_name = %s", (col,))
            if not cur.fetchone():
                print(f"buyer_stats.{col}: column does not exist yet (a rebuild creates it)")
                continue
            cur.execute(f"SELECT COUNT(*) FILTER (WHERE {col} IS NULL), COUNT(*) FROM buyer_stats")
            print(f"buyer_stats.{col}: empty in %s of %s rows" % cur.fetchone())
        print("DRY RUN: nothing was changed. Run without --dry-run to rebuild (about 5 minutes).")
    finally:
        conn.close()


if __name__ == "__main__":
    if "--dry-run" in sys.argv[1:]:
        dry_run()
    else:
        print(refresh_all(get_db_connection))
