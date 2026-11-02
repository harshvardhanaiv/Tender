#!/usr/bin/env python3
"""Fill the tender_notices index from a portal feed (docs/PORTAL_INDEX_DESIGN.md, steps 1 and 2). Local database by default.

    python scripts/index_portals.py contracts_finder --days 2 --max-pages 3
    python scripts/index_portals.py find_tender --days 2 --max-pages 3
    python scripts/index_portals.py pcs --max-pages 8
    python scripts/index_portals.py status

Search reads the table for the scraped portals (pcs), and the server schedules the pcs run itself; this script is for a first fill or a manual run.
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
    ap.add_argument("what", choices=["contracts_finder", "find_tender", "pcs", "status"])
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--until-days", type=int, default=0, help="end of the window, days ago (a backfill walks back in windows)")
    ap.add_argument("--stages", default=None, help='Contracts Finder stage filter, for example "tender"')
    ap.add_argument("--backfill", action="store_true", help="pcs: do not stop at the first page of notices already stored")
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
    if args.what == "pcs":
        # --max-pages is the number of result pages (10 notices each); a backfill passes --backfill to read past the stored notices
        print(ti.ingest_pcs(get_db_connection, max_pages=args.max_pages or 8, stop_at_known=not args.backfill))
        return
    fn = ti.ingest_contracts_finder if args.what == "contracts_finder" else ti.ingest_find_tender
    extra = {"until_days": args.until_days, "stages": args.stages} if args.what == "contracts_finder" else {}
    print(fn(get_db_connection, days=args.days, max_pages=args.max_pages, **extra))


if __name__ == "__main__":
    main()
