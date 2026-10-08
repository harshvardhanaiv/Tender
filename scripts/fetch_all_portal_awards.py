#!/usr/bin/env python3
"""Fetch real Supplier & Buyer Intelligence award data from every UK/Ireland portal
that can actually supply it, and ingest it into the `suppliers` / `contract_awards`
tables.

Covers 4 of the 6 registered UK/Ireland portals (etenders_scraper/sources/registry.py):
  - Contracts Finder      (scrape_live_contracts_finder_awards)
  - Find a Tender         (scrape_live_find_tender_awards)
  - Sell2Wales            (scrape_live_bravo_awards)
  - Public Contracts Scotland (scrape_live_bravo_awards)

eTenders Ireland and eTenders NI are deliberately NOT scraped here. A live check
(2026-09) confirmed neither portal's public Contract Award Notice view or
downloadable notice PDF ever exposes the winning supplier's name for ordinary
(non-TED/above-threshold) awards — only award value/date. Building a supplier
pipeline there would mean inventing a winner, which this codebase must never do
(see etenders_scraper/awards.py module docstring and ingest_award_record()).

All ingestion goes through ingest_award_record(), which already applies:
  - Companies House number validation & normalization
  - 3-layer deduplication (notice_url+supplier, OCID, buyer+supplier+value+month)
  - Contact-domain cross-contamination checks
  - A value sanity guardrail (rejects >£10B / negative values)

Usage:
    python scripts/fetch_all_portal_awards.py                    # all 4 portals, local DB
    python scripts/fetch_all_portal_awards.py --dry-run           # fetch + parse only, no DB writes
    python scripts/fetch_all_portal_awards.py --portals sell2wales,pcs
    python scripts/fetch_all_portal_awards.py --limit 100 --days-back 60
    python scripts/fetch_all_portal_awards.py --production        # writes to .env.production DB
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SKIPPED_PORTALS = {
    "etenders_ie": "eTenders Ireland — winning supplier's name is not published in scrapable form (confirmed live, 2026-09).",
    "etenders_ni": "eTenders NI — same platform/limitation as eTenders Ireland, plus a per-search captcha.",
}


def _portal_jobs(limit: int, days_back: int) -> dict[str, Callable[[Any], list[dict[str, Any]]]]:
    from etenders_scraper.awards import (
        scrape_live_contracts_finder_awards,
        scrape_live_find_tender_awards,
        scrape_live_bravo_awards,
    )

    return {
        "contracts_finder": lambda conn: scrape_live_contracts_finder_awards(limit=limit, days_back=days_back, conn=conn),
        "find_tender": lambda conn: scrape_live_find_tender_awards(max_records=limit, conn=conn),
        "sell2wales": lambda conn: scrape_live_bravo_awards("sell2wales", "https://www.sell2wales.gov.wales", "Sell2Wales", limit=limit, conn=conn),
        "pcs": lambda conn: scrape_live_bravo_awards("pcs", "https://www.publiccontractsscotland.gov.uk", "Public Contracts Scotland", limit=limit, conn=conn),
    }


def run(conn, *, portals: list[str], limit: int, days_back: int, run_audit: bool) -> int:
    jobs = _portal_jobs(limit=limit, days_back=days_back)

    unknown = [p for p in portals if p not in jobs and p not in SKIPPED_PORTALS]
    if unknown:
        print(f"Unknown portal(s): {', '.join(unknown)}. Valid: {', '.join(list(jobs) + list(SKIPPED_PORTALS))}")
        return 1

    results: dict[str, int] = {}
    errors: dict[str, str] = {}

    for portal in portals:
        if portal in SKIPPED_PORTALS:
            print(f"\n[{portal}] SKIPPED — {SKIPPED_PORTALS[portal]}")
            continue

        print(f"\n[{portal}] Fetching live award notices...")
        try:
            records = jobs[portal](conn)
            results[portal] = len(records)
            print(f"[{portal}] Processed {len(records)} award record(s) with a genuine published supplier.")
        except Exception as ex:
            errors[portal] = str(ex)
            print(f"[{portal}] ERROR: {ex}")

    if conn and run_audit:
        print("\nRunning contact-info quality audit on affected suppliers...")
        try:
            from tender_app.supplier_data_auditor import audit_and_clean_supplier_contacts
            cleaned = audit_and_clean_supplier_contacts(conn)
            print(f"Cleaned {cleaned} cross-contaminated contact record(s).")
        except Exception as ex:
            print(f"Audit step failed (non-fatal): {ex}")

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for portal, count in results.items():
        print(f"  {portal:20s} {count:>6d} record(s) processed")
    for portal, err in errors.items():
        print(f"  {portal:20s} ERROR: {err}")
    for portal, reason in SKIPPED_PORTALS.items():
        if portal in portals:
            print(f"  {portal:20s} skipped")

    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM suppliers;")
        sup_count = cursor.fetchone()[0]
        cursor.execute("SELECT source_portal, COUNT(*) FROM contract_awards GROUP BY source_portal ORDER BY 2 DESC;")
        print(f"\nTotal suppliers in DB: {sup_count}")
        print("Total contract_awards by portal:")
        for source_portal, count in cursor.fetchall():
            print(f"  {source_portal:28s} {count}")

    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--portals",
        default="contracts_finder,find_tender,sell2wales,pcs",
        help="Comma-separated portal ids to run (default: the 4 real ones). "
             "Valid: contracts_finder, find_tender, sell2wales, pcs, etenders_ie, etenders_ni "
             "(the last two are always skipped with a reason — see module docstring).",
    )
    parser.add_argument("--limit", type=int, default=50, help="Max notices to fetch per portal (default: 50)")
    parser.add_argument("--days-back", type=int, default=30, help="Contracts Finder lookback window in days (default: 30)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse only — do not write to the database")
    parser.add_argument("--no-audit", action="store_true", help="Skip the post-run contact-info quality audit")
    parser.add_argument("--production", action="store_true", help="Load .env.production instead of .env")
    args = parser.parse_args()

    portals = [p.strip() for p in args.portals.split(",") if p.strip()]

    if args.production:
        from dotenv import load_dotenv
        load_dotenv(".env.production", override=True)
        print("Loaded .env.production\n")
        if not args.dry_run:
            confirm = input("About to write LIVE data to the PRODUCTION database. Type 'yes' to continue: ")
            if confirm.strip().lower() != "yes":
                print("Aborted.")
                return 1

    conn = None
    if not args.dry_run:
        from tender_app.db import get_db_connection
        conn = get_db_connection()
        print(f"Connected to {os.environ.get('DB_HOST')}:{os.environ.get('DB_PORT')}/{os.environ.get('DB_NAME')}")
    else:
        print("DRY RUN — fetching and parsing only, nothing will be written to the database.")

    try:
        return run(conn, portals=portals, limit=args.limit, days_back=args.days_back, run_audit=not args.no_audit)
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
