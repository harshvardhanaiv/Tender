#!/usr/bin/env python3
"""Audit & Purge: suppliers whose name looks like a buyer authority, not a company.

A public body (NHS trust, ICB, council, police/fire authority, ...) is never a real
supplier -- see looks_like_buyer_authority() in etenders_scraper/awards.py, which now
rejects these at ingestion time for new awards. This script surfaces any that were
already ingested before that guard existed, and can automatically set listable = FALSE.

Usage:
    python scripts/audit_buyer_as_supplier.py                  # Audit local database
    python scripts/audit_buyer_as_supplier.py --fix            # Apply fix to local database
    python scripts/audit_buyer_as_supplier.py --production --fix # Apply fix to production DB
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production", action="store_true", help="Load .env.production instead of .env")
    parser.add_argument("--fix", action="store_true", help="Automatically set listable = FALSE for buyer authorities")
    args = parser.parse_args()

    if args.production:
        try:
            from dotenv import load_dotenv
            load_dotenv(".env.production", override=True)
            print("Loaded .env.production\n")
        except ImportError:
            pass

    from etenders_scraper.awards import looks_like_buyer_authority

    # Connect to DB (Postgres or SQLite)
    db_host = os.environ.get("DB_HOST")
    if db_host:
        import psycopg2
        conn = psycopg2.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            port=os.environ.get("DB_PORT", "5432"),
            dbname=os.environ.get("DB_NAME", "postgres"),
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASSWORD", "root")
        )
        is_pg = True
    else:
        db_path = os.environ.get("SQLITE_DB_PATH", "users.db")
        conn = sqlite3.connect(db_path)
        is_pg = False

    cur = conn.cursor()
    print(f"Connected to {'PostgreSQL ' + str(db_host) if is_pg else 'SQLite ' + os.environ.get('SQLITE_DB_PATH', 'users.db')}\n")

    cur.execute("SELECT id, company_number, name FROM suppliers;")
    rows = cur.fetchall()
    total = len(rows)

    flagged: list[tuple[int, str, str]] = []
    for sid, cnum, name in rows:
        if name and looks_like_buyer_authority(name):
            flagged.append((sid, cnum, name))

    print("=" * 72)
    print("BUYER-AS-SUPPLIER AUDIT & CLEANUP")
    print("=" * 72)
    print(f"Total supplier rows:                                {total:>8}")
    print(f"  Flagged as looking like a buyer authority:        {len(flagged):>8}")

    if flagged:
        print("\nFlagged buyer authority records:")
        for sid, cnum, name in flagged[:25]:
            safe_name = name.encode("ascii", errors="replace").decode("ascii")
            print(f"  #{sid:<8} {cnum or '(none)':<15} {safe_name!r}")
        if len(flagged) > 25:
            print(f"  ... and {len(flagged) - 25} more")

        if args.fix:
            flagged_ids = [f[0] for f in flagged]
            print(f"\n[FIX MODE]: Marking {len(flagged_ids)} buyer authority records as non-listable (listable = FALSE)...")

            # Update supplier_stats table if exists
            try:
                cur.execute("CREATE TABLE IF NOT EXISTS supplier_stats (supplier_id INTEGER PRIMARY KEY, listable INTEGER NOT NULL DEFAULT 1);")
            except Exception:
                pass

            if is_pg:
                cur.execute("UPDATE supplier_stats SET listable = FALSE WHERE supplier_id = ANY(%s);", (flagged_ids,))
            else:
                for chunk_idx in range(0, len(flagged_ids), 500):
                    chunk = flagged_ids[chunk_idx:chunk_idx+500]
                    qmarks = ",".join("?" * len(chunk))
                    cur.execute(f"INSERT INTO supplier_stats (supplier_id, listable) VALUES {','.join(['(?, 0)'] * len(chunk))} ON CONFLICT(supplier_id) DO UPDATE SET listable = 0;", chunk)
            
            conn.commit()
            print(f"Successfully marked {len(flagged_ids)} buyer authority suppliers as non-listable!")
        else:
            print("\nRun with --fix to automatically set listable = FALSE for these records in supplier_stats.")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
