#!/usr/bin/env python3
"""Fill the tender_notices index from a portal feed (docs/PORTAL_INDEX_DESIGN.md, steps 1 and 2). Local database by default.

    python scripts/index_portals.py contracts_finder --days 2 --max-pages 3
    python scripts/index_portals.py find_tender --days 2 --max-pages 3
    python scripts/index_portals.py status

Nothing reads the table yet, and nothing here is scheduled: run it by hand.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_app import tender_index as ti  # noqa: E402
from tender_app.db import get_db_connection  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["contracts_finder", "find_tender", "status"])
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--max-pages", type=int, default=None)
    args = ap.parse_args()
    if args.what == "status":
        conn = get_db_connection()
        cur = conn.cursor()
        ti.ensure_tables(cur)
        conn.commit()
        for row in ti.index_status(cur):
            print(row)
        conn.close()
        return
    fn = ti.ingest_contracts_finder if args.what == "contracts_finder" else ti.ingest_find_tender
    print(fn(get_db_connection, days=args.days, max_pages=args.max_pages))


if __name__ == "__main__":
    main()
