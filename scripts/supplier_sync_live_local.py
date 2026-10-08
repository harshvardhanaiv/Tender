#!/usr/bin/env python3
"""Cleans wrong/duplicate Supplier Intelligence data on BOTH databases, then syncs
suppliers + contract_awards so each side has whatever the other has (no data loss
in either direction).

Requires a network path to BOTH databases at once from wherever you run this (e.g.
an SSH tunnel exposing production's admin port locally: `ssh -L 15440:localhost:5440
user@tender.civenta.co.uk`, then pass --b-port 15440).

Steps:
  1. Dedupe + junk-removal on side A and side B independently (same logic as
     supplier_dedup_cleanup.py — see that file's docstring for exactly what counts
     as "wrong"/"duplicate": only rows WITHOUT a real Companies House / SC / NI / OC
     number are ever touched; verified real suppliers are never merged or deleted).
  2. Diff suppliers between A and B (matched by real CH number when available, else
     by exact normalized name) and insert whichever rows are missing on each side.
  3. Diff contract_awards between A and B (matched by notice_url + supplier_name —
     the same key the app's own ingest-time dedup uses) and insert whichever award
     rows are missing on each side, resolved onto the (now-synced) supplier rows.

Always run without --apply first and read the summary.

Usage:
    python scripts/supplier_sync_live_local.py \\
        --a-host 127.0.0.1 --a-port 5440 --a-user postgres --a-password <password> --a-label local \\
        --b-host 127.0.0.1 --b-port 15440 --b-user postgres --b-password <password> --b-label live \\
        --apply
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg2
from psycopg2.extras import execute_values

import supplier_dedup_cleanup as dedupe_mod  # reuses JUNK_NAMES, _pick_canonical, run()

# Commit per batch so a dropped connection to the remote DB loses at most one batch.
BATCH_SIZE = 500


def connect(host: str, port: int, dbname: str, user: str, password: str):
    return psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password, connect_timeout=10,
                            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5)


def supplier_key(cnum: str | None, name: str | None):
    from tender_app.ch_matcher import normalize_ch_company_number
    real = normalize_ch_company_number(cnum)
    if real:
        return ("CH", real)
    return ("NAME", (name or "").strip().lower())


def load_suppliers(conn):
    """Column order matches dedupe_mod.SUPPLIER_COLUMNS so its plan()/
    logical_post_dedupe_rows() helpers can be reused directly here."""
    cur = conn.cursor()
    cur.execute(f"SELECT {dedupe_mod.SUPPLIER_COLUMNS} FROM suppliers;")
    return cur.fetchall()


def sync_suppliers(conn_a, conn_b, label_a: str, label_b: str, apply_changes: bool) -> tuple[list, list]:
    """Returns (logical_rows_a, logical_rows_b) post-dedupe — used by sync_awards
    to resolve destination supplier ids, so it doesn't need a second round trip."""
    # logical_post_dedupe_rows() reflects the post-dedupe state regardless of
    # whether --apply actually committed it yet, so this preview is always accurate.
    logical_a = dedupe_mod.logical_post_dedupe_rows(load_suppliers(conn_a))
    logical_b = dedupe_mod.logical_post_dedupe_rows(load_suppliers(conn_b))
    keys_b = {supplier_key(r[1], r[2]) for r in logical_b}
    keys_a = {supplier_key(r[1], r[2]) for r in logical_a}

    missing_in_b = [r for r in logical_a if supplier_key(r[1], r[2]) not in keys_b]
    missing_in_a = [r for r in logical_b if supplier_key(r[1], r[2]) not in keys_a]

    print("\n" + "=" * 72)
    print("SUPPLIER SYNC")
    print("=" * 72)
    print(f"{label_a}: {len(logical_a)} suppliers (post-dedupe) | {label_b}: {len(logical_b)} suppliers (post-dedupe)")
    print(f"Missing in {label_b} (present in {label_a}): {len(missing_in_b)}")
    print(f"Missing in {label_a} (present in {label_b}): {len(missing_in_a)}")

    def insert(conn, rows, label):
        cur = conn.cursor()
        values = [r[1:10] for r in rows]
        for i in range(0, len(values), BATCH_SIZE):
            execute_values(cur, """
                INSERT INTO suppliers (company_number, name, address, email, phone, website, region, sme_status, vcse_status)
                VALUES %s
                ON CONFLICT (company_number) DO NOTHING
            """, values[i:i + BATCH_SIZE])
            conn.commit()
            print(f"  {label}: {min(i + BATCH_SIZE, len(values))}/{len(values)} suppliers committed", flush=True)
        print(f"  Inserted {len(rows)} suppliers into {label}.")

    if apply_changes:
        insert(conn_b, missing_in_b, label_b)
        insert(conn_a, missing_in_a, label_a)
        # Re-read so award-sync resolves ids against what's actually now in each DB.
        logical_a = dedupe_mod.logical_post_dedupe_rows(load_suppliers(conn_a))
        logical_b = dedupe_mod.logical_post_dedupe_rows(load_suppliers(conn_b))
    else:
        print("  (dry run — nothing inserted)")

    return logical_a, logical_b


def load_awards(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT id, supplier_id, company_number, supplier_name, authority_name, tender_title,
               cpv_code, cpv_description, contract_value, currency, date_signed,
               contract_duration, procurement_type, is_competitive, notice_type,
               source_portal, notice_url, contract_start_date, contract_end_date,
               is_framework, buyer_type, latitude, longitude
        FROM contract_awards;
    """)
    return cur.fetchall()


def award_key(notice_url: str | None, supplier_name: str | None):
    return ((notice_url or "").strip(), (supplier_name or "").strip().lower())


def load_supplier_id_map(conn):
    """Maps supplier_key() -> id for the destination side, post-supplier-sync."""
    m = {}
    for r in load_suppliers(conn):
        m[supplier_key(r[1], r[2])] = r[0]
    return m


def _loose_name(name: str | None) -> str:
    """Lowercase alphanumerics only, so '-' vs '–' or spacing differences still match."""
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def relink_orphan_awards(conn, label: str, apply_changes: bool) -> None:
    """Awards whose supplier row was deleted (FK is ON DELETE SET NULL, e.g. by an
    earlier dedupe) keep their company_number/supplier_name — re-attach them to
    the supplier that now carries that key."""
    id_map = load_supplier_id_map(conn)
    cur = conn.cursor()
    cur.execute("SELECT id, company_number, supplier_name FROM contract_awards WHERE supplier_id IS NULL;")
    orphans = cur.fetchall()
    fixes = [(id_map[k], aid) for aid, cnum, sname in orphans if (k := supplier_key(cnum, sname)) in id_map]
    print(f"{label}: {len(orphans)} awards with no supplier_id, {len(fixes)} can be relinked")
    if not apply_changes:
        return
    for i in range(0, len(fixes), BATCH_SIZE):
        execute_values(cur, """
            UPDATE contract_awards a SET supplier_id = v.sid
            FROM (VALUES %s) AS v(sid, aid)
            WHERE a.id = v.aid AND a.supplier_id IS NULL
        """, fixes[i:i + BATCH_SIZE])
        conn.commit()
    print(f"  Relinked {len(fixes)} awards on {label}.")


def sync_awards(conn_a, conn_b, label_a: str, label_b: str, apply_changes: bool) -> None:
    print("\n" + "=" * 72)
    print("ORPHAN AWARD RELINK")
    print("=" * 72)
    relink_orphan_awards(conn_a, label_a, apply_changes)
    relink_orphan_awards(conn_b, label_b, apply_changes)

    rows_a = load_awards(conn_a)
    rows_b = load_awards(conn_b)
    keys_b = {award_key(r[16], r[3]) for r in rows_b}
    keys_a = {award_key(r[16], r[3]) for r in rows_a}

    missing_in_b = [r for r in rows_a if award_key(r[16], r[3]) not in keys_b]
    missing_in_a = [r for r in rows_b if award_key(r[16], r[3]) not in keys_a]

    print("\n" + "=" * 72)
    print("CONTRACT_AWARDS SYNC")
    print("=" * 72)
    print(f"{label_a}: {len(rows_a)} awards | {label_b}: {len(rows_b)} awards")
    print(f"Missing in {label_b} (present in {label_a}): {len(missing_in_b)}")
    print(f"Missing in {label_a} (present in {label_b}): {len(missing_in_a)}")

    if not apply_changes:
        print("  (dry run — nothing inserted)")
        return

    def insert_missing(src_conn, dest_conn, rows, dest_label):
        id_map = load_supplier_id_map(dest_conn)
        src_suppliers = {r[0]: r for r in load_suppliers(src_conn)}
        cur = dest_conn.cursor()
        unlinked = 0
        values = []
        for r in rows:
            (_id, sup_id, cnum, sname, authority_name, tender_title, cpv_code, cpv_description,
             contract_value, currency, date_signed, contract_duration, procurement_type,
             is_competitive, notice_type, source_portal, notice_url, contract_start_date,
             contract_end_date, is_framework, buyer_type, latitude, longitude) = r
            dest_sup_id = id_map.get(supplier_key(cnum, sname))
            if dest_sup_id is None:
                # Fall back to the award's own supplier row on the source side, but only
                # when it is genuinely the same company — placeholder numbers like
                # 'Companies House' have linked unrelated awards to one row.
                src_sup = src_suppliers.get(sup_id)
                if src_sup and _loose_name(src_sup[2]) == _loose_name(sname):
                    dest_sup_id = id_map.get(supplier_key(src_sup[1], src_sup[2]))
            if dest_sup_id is None:
                unlinked += 1  # still copied, with supplier_id NULL, so no award is lost
            values.append((dest_sup_id, cnum, sname, authority_name, tender_title, cpv_code, cpv_description,
                           contract_value, currency, date_signed, contract_duration, procurement_type,
                           is_competitive, notice_type, source_portal, notice_url, contract_start_date,
                           contract_end_date, is_framework, buyer_type, latitude, longitude))
        for i in range(0, len(values), BATCH_SIZE):
            execute_values(cur, """
                INSERT INTO contract_awards (
                    supplier_id, company_number, supplier_name, authority_name, tender_title,
                    cpv_code, cpv_description, contract_value, currency, date_signed,
                    contract_duration, procurement_type, is_competitive, notice_type,
                    source_portal, notice_url, contract_start_date, contract_end_date,
                    is_framework, buyer_type, latitude, longitude
                ) VALUES %s
            """, values[i:i + BATCH_SIZE])
            dest_conn.commit()
            print(f"  {dest_label}: {min(i + BATCH_SIZE, len(values))}/{len(values)} awards committed", flush=True)
        print(f"  Inserted {len(values)} awards into {dest_label} ({unlinked} with no matching supplier, supplier_id NULL).")

    insert_missing(conn_a, conn_b, missing_in_b, label_b)
    insert_missing(conn_b, conn_a, missing_in_a, label_a)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for side in ("a", "b"):
        parser.add_argument(f"--{side}-host", required=True)
        parser.add_argument(f"--{side}-port", type=int, default=5432)
        parser.add_argument(f"--{side}-db", default="postgres")
        parser.add_argument(f"--{side}-user", default="postgres")
        parser.add_argument(f"--{side}-password", required=True)
        parser.add_argument(f"--{side}-label", default=side.upper())
    parser.add_argument("--apply", action="store_true", help="Actually commit changes (default is dry run)")
    parser.add_argument("--skip-dedupe", action="store_true", help="Skip the per-side dedupe/junk-removal step")
    args = parser.parse_args()

    if args.apply:
        confirm = input(f"About to APPLY changes to BOTH '{args.a_label}' and '{args.b_label}' "
                         f"databases. Type 'yes' to continue: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            return 1

    conn_a = connect(args.a_host, args.a_port, args.a_db, args.a_user, args.a_password)
    conn_b = connect(args.b_host, args.b_port, args.b_db, args.b_user, args.b_password)
    print(f"Connected to {args.a_label} ({args.a_host}:{args.a_port}/{args.a_db}) "
          f"and {args.b_label} ({args.b_host}:{args.b_port}/{args.b_db})\n")

    try:
        if not args.skip_dedupe:
            print("#" * 72)
            print(f"# DEDUPE — {args.a_label}")
            print("#" * 72)
            dedupe_mod.run(conn_a, apply_changes=args.apply)

            print("\n" + "#" * 72)
            print(f"# DEDUPE — {args.b_label}")
            print("#" * 72)
            dedupe_mod.run(conn_b, apply_changes=args.apply)

        sync_suppliers(conn_a, conn_b, args.a_label, args.b_label, args.apply)
        sync_awards(conn_a, conn_b, args.a_label, args.b_label, args.apply)

        if args.apply:
            print("\nCOMMITTED on both sides.")
        else:
            print("\nDRY RUN — no changes were made anywhere. Re-run with --apply to commit.")
    finally:
        conn_a.close()
        conn_b.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
