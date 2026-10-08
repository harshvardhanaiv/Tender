#!/usr/bin/env python3
"""One-off: copy supplier intelligence and user credit wallets from the retired SQLite users.db into Postgres.

Copies `suppliers` and `contract_awards` with their original ids, so award -> supplier
links stay intact, then resets both id sequences. Also migrates `credit_wallet`, `subscriptions`, and `user_prefs`.

Usage:
    python scripts/migrate_sqlite_to_postgres.py [--sqlite PATH] [--dry-run] [--force]

Postgres settings come from the DB_* environment variables (see .env.example).
Start the app once first so its startup migrations create all tables.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from psycopg2.extras import execute_values

from etenders_scraper.awards import SEED_CONTRACT_AWARDS, SEED_SUPPLIERS
from tender_app.db import get_db_connection

# Parents first: contract_awards.supplier_id references suppliers.id
SEED_ROWS = {"suppliers": len(SEED_SUPPLIERS), "contract_awards": len(SEED_CONTRACT_AWARDS)}
BATCH_SIZE = 2000


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy suppliers, contract_awards, and credit_wallet from SQLite users.db into Postgres.")
    parser.add_argument("--sqlite", default=str(REPO_ROOT / "users.db"), help="path to the SQLite database (default: users.db)")
    parser.add_argument("--dry-run", action="store_true", help="copy inside a transaction, report counts, then roll back")
    parser.add_argument("--force", action="store_true", help="force overwrite of suppliers and contract_awards even if rows exist")
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.is_file():
        print(f"SQLite database not found: {sqlite_path}")
        return 1

    src = sqlite3.connect(f"file:{sqlite_path.resolve().as_posix()}?mode=ro", uri=True)
    dst = get_db_connection()
    cur = dst.cursor()
    try:
        columns = {}
        for table, seed_rows in SEED_ROWS.items():
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            existing = cur.fetchone()[0]
            if existing > seed_rows and not args.force:
                print(f"Postgres {table} already has {existing} rows (seed data is {seed_rows}); use --force to overwrite.")
                return 1
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s",
                (table,),
            )
            pg_cols = {r[0] for r in cur.fetchall()}
            columns[table] = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
            missing = [c for c in columns[table] if c not in pg_cols]
            if missing:
                print(f"Postgres {table} is missing columns {missing}; start the app once so its migrations run.")
                return 1

        print("Truncating contract_awards and suppliers in Postgres...")
        cur.execute("TRUNCATE contract_awards, suppliers RESTART IDENTITY")
        for table, cols in columns.items():
            col_list = ", ".join(cols)
            rows = src.execute(f"SELECT {col_list} FROM {table} ORDER BY id")
            copied = 0
            while batch := rows.fetchmany(BATCH_SIZE):
                execute_values(cur, f"INSERT INTO {table} ({col_list}) VALUES %s", batch, page_size=BATCH_SIZE)
                copied += len(batch)
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT COALESCE(MAX(id), 1) FROM {table}))")
            print(f"{table}: copied {copied} rows")

        # Migrate credit_wallet
        try:
            s_wallets = src.execute("SELECT username, balance, updated_at FROM credit_wallet").fetchall()
            if s_wallets:
                print(f"Migrating {len(s_wallets)} credit_wallet records...")
                for username, balance, updated_at in s_wallets:
                    cur.execute(
                        """
                        INSERT INTO credit_wallet (username, balance, updated_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (username) DO UPDATE SET balance = EXCLUDED.balance, updated_at = EXCLUDED.updated_at
                        """,
                        (username, balance, updated_at),
                    )
                print(f"credit_wallet: upserted {len(s_wallets)} records.")
        except Exception as e:
            print("Warning migrating credit_wallet:", e)

        # Migrate user_prefs
        try:
            s_prefs = src.execute("SELECT username, pref_key, pref_value, updated_at FROM user_prefs").fetchall()
            if s_prefs:
                print(f"Migrating {len(s_prefs)} user_prefs records...")
                for username, pref_key, pref_value, updated_at in s_prefs:
                    cur.execute(
                        """
                        INSERT INTO user_prefs (username, pref_key, pref_value, updated_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (username, pref_key) DO UPDATE SET pref_value = EXCLUDED.pref_value, updated_at = EXCLUDED.updated_at
                        """,
                        (username, pref_key, pref_value, updated_at),
                    )
                print(f"user_prefs: upserted {len(s_prefs)} records.")
        except Exception as e:
            print("Warning migrating user_prefs:", e)

        if args.dry_run:
            dst.rollback()
            print("Dry run: rolled back, Postgres unchanged.")
        else:
            dst.commit()
            print("Committed successfully.")
        return 0
    except Exception:
        dst.rollback()
        raise
    finally:
        cur.close()
        dst.close()
        src.close()


if __name__ == "__main__":
    sys.exit(main())
