#!/usr/bin/env python3
"""Sync Planning Leads (planning_applications & planning_harvest_state) between local and live Postgres DBs,
OR dump local planning applications into an SQL file ready for import on Live.

Usage:
  1. Dump local planning applications to SQL file:
     python scripts/planning_sync_live_local.py --dump data/planning_sync.sql

  2. Direct sync from Local DB to Live DB (over SSH tunnel or direct IP):
     python scripts/planning_sync_live_local.py \\
         --a-host 127.0.0.1 --a-port 5440 --a-user postgres --a-password <password> --a-label local \\
         --b-host 127.0.0.1 --b-port 15440 --b-user postgres --b-password <password> --b-label live \\
         --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import psycopg2
from psycopg2.extras import execute_values
from etenders_scraper.planning.harvester import UPSERT_COLUMNS


DUMP_BATCH = 250  # rows per INSERT statement in the --dump file


def upsert_conflict_clause() -> str:
    """ON CONFLICT clause shared by --dump and --apply. A row that exists on both sides is only
    overwritten when the incoming copy is NEWER (by last_changed); otherwise live's own newer
    harvest results (decisions, states, scores) would be rolled back to the local copy."""
    update_set = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in UPSERT_COLUMNS if c != "id")
    return (f'ON CONFLICT ("id") DO UPDATE SET {update_set} '
            'WHERE planning_applications."last_changed" IS NULL '
            'OR EXCLUDED."last_changed" > planning_applications."last_changed"')


def connect_db(host: str, port: int, dbname: str, user: str, password: str):
    return psycopg2.connect(
        host=host, port=port, dbname=dbname, user=user, password=password, connect_timeout=10
    )


def dump_local_to_sql(output_file: str, host="127.0.0.1", port=5440, dbname="postgres", user="postgres", password=None):
    if not password:
        password = os.environ.get("LOCAL_DB_PASSWORD", "postgres")
    conn = connect_db(host, port, dbname, user, password)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM planning_applications;")
    total_apps = cur.fetchone()[0]
    print(f"[*] Found {total_apps} local planning applications in database.")

    if total_apps == 0:
        print("[!] No local planning applications found to export.")
        return

    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cols = ", ".join(f'"{c}"' for c in UPSERT_COLUMNS)
    query = f"SELECT {cols} FROM planning_applications;"
    cur.execute(query)
    rows = cur.fetchall()

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("-- Planning Leads SQL Dump for Live PostgreSQL database\n")
        f.write("BEGIN;\n\n")

        # Create table / indices if not existing
        f.write("""
CREATE TABLE IF NOT EXISTS planning_applications (
    id TEXT PRIMARY KEY,
    uid TEXT,
    planning_portal_id TEXT,
    authority TEXT NOT NULL,
    authority_id TEXT,
    country TEXT,
    region TEXT,
    description TEXT,
    address TEXT,
    postcode TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    app_size TEXT,
    app_state TEXT,
    app_type TEXT,
    n_dwellings INTEGER,
    low_value_reason TEXT,
    applicant_name TEXT,
    applicant_company TEXT,
    agent_name TEXT,
    agent_company TEXT,
    agent_address TEXT,
    case_officer TEXT,
    ward_name TEXT,
    start_date DATE,
    decided_date DATE,
    consultation_end_date DATE,
    target_decision_date DATE,
    decision TEXT,
    docs_url TEXT,
    detail_url TEXT,
    planit_url TEXT,
    lead_score INTEGER NOT NULL DEFAULT 0,
    raw_json JSONB,
    last_changed TIMESTAMP WITH TIME ZONE,
    fetched_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
\n""")

        # Batch write planning applications
        col_names_str = ", ".join(f'"{c}"' for c in UPSERT_COLUMNS)
        update_set_str = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in UPSERT_COLUMNS if c != "id")

        # Many rows per INSERT: psql sends one statement per network round trip, so one row per statement
        # (32k statements) took hours against the remote database; DUMP_BATCH rows per statement is ~250x fewer.
        for start in range(0, len(rows), DUMP_BATCH):
            tuples = []
            for row in rows[start:start + DUMP_BATCH]:
                formatted_vals = []
                for val in row:
                    if val is None:
                        formatted_vals.append("NULL")
                    elif isinstance(val, bool):
                        formatted_vals.append("TRUE" if val else "FALSE")
                    elif isinstance(val, (int, float)):
                        formatted_vals.append(str(val))
                    elif isinstance(val, dict):
                        json_str = json.dumps(val).replace("'", "''")
                        formatted_vals.append(f"'{json_str}'::jsonb")
                    else:
                        escaped = str(val).replace("'", "''").replace("\\", "\\\\")
                        formatted_vals.append(f"'{escaped}'")
                tuples.append("(" + ", ".join(formatted_vals) + ")")
            f.write(f'INSERT INTO planning_applications ({col_names_str}) VALUES\n' + ",\n".join(tuples) + f'\n{upsert_conflict_clause()};\n')

        f.write("\nCOMMIT;\n")

    print(f"[OK] Successfully exported {len(rows)} planning applications to: {out_path.resolve()}")


def sync_db_to_db(args):
    print(f"[*] Connecting to Source DB A ({args.a_label}: {args.a_host}:{args.a_port})...")
    conn_a = connect_db(args.a_host, args.a_port, args.a_dbname, args.a_user, args.a_password)
    print(f"[*] Connecting to Target DB B ({args.b_label}: {args.b_host}:{args.b_port})...")
    conn_b = connect_db(args.b_host, args.b_port, args.b_dbname, args.b_user, args.b_password)

    # Ensure target tables exist
    from tender_app.db_ext import run_saas_migrations
    run_saas_migrations(conn_b)

    cols = ", ".join(f'"{c}"' for c in UPSERT_COLUMNS)
    query = f"SELECT {cols} FROM planning_applications;"

    cur_a = conn_a.cursor()
    cur_a.execute(query)
    rows_a = cur_a.fetchall()
    print(f"[*] Loaded {len(rows_a)} planning applications from {args.a_label}.")

    if not args.apply:
        print(f"[DRY-RUN] Would upsert {len(rows_a)} applications into {args.b_label}. Run with --apply to execute.")
        return

    cur_b = conn_b.cursor()
    col_names_str = ", ".join(f'"{c}"' for c in UPSERT_COLUMNS)
    update_set_str = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in UPSERT_COLUMNS if c != "id")
    upsert_sql = f'INSERT INTO planning_applications ({col_names_str}) VALUES %s {upsert_conflict_clause()};'

    execute_values(cur_b, upsert_sql, rows_a, page_size=500)

    # Also sync planning_harvest_state
    cur_a.execute("SELECT source, last_success_at, last_run_at, last_status, last_error, requests_today, requests_date, cursor_state FROM planning_harvest_state;")
    hrows = cur_a.fetchall()
    if hrows:
        h_sql = """INSERT INTO planning_harvest_state (source, last_success_at, last_run_at, last_status, last_error, requests_today, requests_date, cursor_state)
VALUES %s ON CONFLICT (source) DO UPDATE SET
    last_success_at = EXCLUDED.last_success_at, last_run_at = EXCLUDED.last_run_at,
    last_status = EXCLUDED.last_status, last_error = EXCLUDED.last_error,
    requests_today = EXCLUDED.requests_today, requests_date = EXCLUDED.requests_date,
    cursor_state = EXCLUDED.cursor_state;"""
        execute_values(cur_b, h_sql, hrows)

    conn_b.commit()
    print(f"[OK] Successfully synced {len(rows_a)} planning applications into {args.b_label} database!")


def main():
    parser = argparse.ArgumentParser(description="Sync or Dump Planning Leads for Live deployment")
    parser.add_argument("--dump", type=str, help="Export local DB planning applications to specified .sql file path")
    parser.add_argument("--a-host", default="127.0.0.1")
    parser.add_argument("--a-port", type=int, default=5440)
    parser.add_argument("--a-dbname", default="postgres")
    parser.add_argument("--a-user", default="postgres")
    parser.add_argument("--a-password", default=os.environ.get("LOCAL_DB_PASSWORD", ""))
    parser.add_argument("--a-label", default="local")
    parser.add_argument("--b-host", default="127.0.0.1")
    parser.add_argument("--b-port", type=int, default=15440)
    parser.add_argument("--b-dbname", default="postgres")
    parser.add_argument("--b-user", default="postgres")
    parser.add_argument("--b-password", default=os.environ.get("LIVE_DB_PASSWORD", ""))
    parser.add_argument("--b-label", default="live")
    parser.add_argument("--apply", action="store_true", help="Execute sync to target DB B")

    args = parser.parse_args()

    if args.dump:
        dump_local_to_sql(args.dump)
    else:
        sync_db_to_db(args)


if __name__ == "__main__":
    main()
