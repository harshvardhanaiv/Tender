#!/usr/bin/env python3
"""One-off data repairs found in the 2026-09-21 data audit. UPDATE-only: never deletes rows.

  1. tenders_master   - value / closing_date / published_date / url / country were blank on every
                        row because the cache writer read keys the scrapers don't emit (fixed in
                        server.py upsert_tenders). Rebuild them from raw_json.
  2. suppliers.region - normalize "UKI - London" style labels to plain names, and infer a region
                        from the address postcode for the ~82% of suppliers stored as just "UK".
                        Postcode areas that straddle two regions (KT, TW, CR, ...) are left as "UK"
                        rather than guessed.
  3. contract_awards  - negative contract_value -> NULL; date_signed outside 2000..next year -> NULL
                        (1900-03-01 / 2034-10-24 style entry errors); the shared England-wide
                        placeholder latitude/longitude -> NULL (see geocode_buyers.py).

Each step is idempotent. Dry-run by default; --apply commits.

    python scripts/data_quality_cleanup.py            # dry run
    python scripts/data_quality_cleanup.py --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Keep in sync with server.py _SOURCE_COUNTRY.
SOURCE_COUNTRY = {
    "etenders_ie": "Ireland", "etenders_ni": "Northern Ireland", "sell2wales": "Wales",
    "pcs": "Scotland", "find_tender": "United Kingdom", "contracts_finder": "United Kingdom",
    "sam_gov": "United States", "canadabuys": "Canada", "eu_ted": "European Union",
    "austender": "Australia", "gebiz": "Singapore", "gets_nz": "New Zealand",
    "boamp": "France", "bund": "Germany",
}

# ITL1 letter (3rd character of a UKxxx code) -> region name.
ITL1 = {
    "C": "North East", "D": "North West", "E": "Yorkshire and The Humber", "F": "East Midlands",
    "G": "West Midlands", "H": "East of England", "I": "London", "J": "South East",
    "K": "South West", "L": "Wales", "M": "Scotland", "N": "Northern Ireland",
}

# Postcode area (letters before the first digit) -> region. Areas that span regions are omitted.
_AREAS = {
    "London": "E EC N NW SE SW W WC BR CR HA IG RM SM UB",
    "North East": "DH DL NE SR TS",
    "North West": "BB BL CA CW FY L LA M OL PR SK WA WN",
    "Yorkshire and The Humber": "BD DN HD HG HU HX LS S WF YO",
    "East Midlands": "DE LE LN NG NN",
    "West Midlands": "B CV DY ST WS WV WR TF",
    "East of England": "AL CB CM CO IP LU NR PE SG SS",
    "South East": "BN CT GU HP ME MK OX PO RG RH SL SO TN",
    "South West": "BA BH BS DT EX GL PL SN SP TA TQ TR",
    "Wales": "CF LD LL NP SA",
    "Scotland": "AB DD DG EH FK G HS IV KA KW KY ML PA PH ZE",
    "Northern Ireland": "BT",
}
AREA_REGION = {area: region for region, areas in _AREAS.items() for area in areas.split()}
POSTCODE_RE = re.compile(r"\b([A-Z]{1,2})[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}\b")
NUTS_LABEL_RE = re.compile(r"^UK([A-Z])\w*\s*-\s*(.+)$")


def run_batched(conn, sql: str, params: list[tuple], batch: int = 500, key_index: int = -1) -> None:
    """executemany in small, id-ordered transactions. A background backfill updates the same
    tables, so one big transaction deadlocks with it; small ones only wait briefly, and a batch
    that loses a deadlock or lock wait is simply retried."""
    import time
    from psycopg2 import errors
    from psycopg2.extras import execute_batch

    params = sorted(params, key=lambda p: p[key_index])
    cur = conn.cursor()
    cur.execute("SET lock_timeout = '5s';")
    conn.commit()
    for i in range(0, len(params), batch):
        chunk = params[i:i + batch]
        for attempt in range(8):
            try:
                execute_batch(cur, sql, chunk, page_size=len(chunk))  # one round trip per batch, not per row
                conn.commit()
                break
            except (errors.DeadlockDetected, errors.LockNotAvailable):
                conn.rollback()
                cur.execute("SET lock_timeout = '5s';")
                time.sleep(0.5 * (attempt + 1))
        else:
            raise RuntimeError(f"batch starting at {i} kept conflicting with a concurrent writer")


def tenders_master(conn, cur, apply: bool) -> None:
    cur.execute("SELECT id, portal_id, raw_json FROM tenders_master "
                "WHERE COALESCE(value,'')='' OR COALESCE(closing_date,'')='' OR COALESCE(url,'')='' OR COALESCE(country,'')=''")
    todo = cur.fetchall()
    updates = []
    for tid, portal, raw in todo:
        try:
            r = json.loads(raw) if raw else {}
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        updates.append((
            str(r.get("value") or r.get("estimated_value") or r.get("estimated_value_eur") or ""),
            str(r.get("closing_date") or r.get("deadline") or r.get("submission_deadline") or r.get("deadline_date") or ""),
            str(r.get("published_date") or r.get("date_published") or ""),
            str(r.get("url") or r.get("link") or r.get("detail_url") or ""),
            str(r.get("country") or SOURCE_COUNTRY.get(portal or "", "")),
            tid,
        ))
    print(f"[tenders_master] {len(todo):,} rows with blank typed columns; {len(updates):,} rebuildable from raw_json")
    if apply:
        run_batched(
            conn,
            "UPDATE tenders_master SET "
            "value = COALESCE(NULLIF(value,''), %s), closing_date = COALESCE(NULLIF(closing_date,''), %s), "
            "published_date = COALESCE(NULLIF(published_date,''), %s), url = COALESCE(NULLIF(url,''), %s), "
            "country = COALESCE(NULLIF(country,''), %s) WHERE id = %s",
            updates,
        )


def supplier_regions(conn, cur, apply: bool) -> None:
    cur.execute("SELECT id, region, address FROM suppliers")
    changes: list[tuple[str, int]] = []
    normalized = inferred = 0
    for sid, region, address in cur.fetchall():
        region = (region or "").strip()
        new = None
        m = NUTS_LABEL_RE.match(region)
        if m:
            # "UKI - London" (ITL1) -> "London"; "UKK24 - Devon CC" (ITL3) -> its ITL1 region
            new = m.group(2).strip() if len(region.split("-")[0].strip()) == 3 else ITL1.get(m.group(1), region)
            normalized += 1
        elif region.upper() in ("", "UK") and address:
            hits = POSTCODE_RE.findall(str(address).upper())
            area = hits[-1] if hits else None
            if area and area in AREA_REGION:
                new = AREA_REGION[area]
                inferred += 1
        if new and new != region:
            changes.append((new, sid))
    print(f"[suppliers.region] {normalized:,} labels normalized, {inferred:,} inferred from postcode "
          f"({len(changes):,} rows change)")
    if apply and changes:
        run_batched(conn, "UPDATE suppliers SET region = %s WHERE id = %s", changes)


def award_sanity(conn, cur, apply: bool) -> None:
    max_year = datetime.now().year + 1
    cur.execute("SELECT count(*) FROM contract_awards WHERE contract_value < 0")
    neg = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM contract_awards WHERE date_signed ~ '^[0-9]{4}' "
                "AND (left(date_signed,4)::int < 2000 OR left(date_signed,4)::int > %s)", (max_year,))
    bad_dates = cur.fetchone()[0]
    print(f"[contract_awards] {neg:,} negative values -> NULL; {bad_dates:,} implausible dates -> NULL")
    if apply:
        cur.execute("UPDATE contract_awards SET contract_value = NULL WHERE contract_value < 0")
        cur.execute("UPDATE contract_awards SET date_signed = NULL WHERE date_signed ~ '^[0-9]{4}' "
                    "AND (left(date_signed,4)::int < 2000 OR left(date_signed,4)::int > %s)", (max_year,))


def award_placeholder_coords(conn, cur, apply: bool) -> None:
    """~40,000 awards (2,295 buyers) carry the same England-wide point, 52.3555 / -1.1743. It is
    not where any of them is, and the buyer map showed it as an exact location. Unknown is honest."""
    cur.execute("SELECT id FROM contract_awards WHERE latitude IS NOT NULL "
                "AND ROUND(latitude::numeric, 4) = 52.3555 AND ROUND(longitude::numeric, 4) = -1.1743")
    ids = [r[0] for r in cur.fetchall()]
    print(f"[contract_awards] {len(ids):,} awards at the placeholder coordinate -> NULL")
    if apply and ids:
        run_batched(conn, "UPDATE contract_awards SET latitude = NULL, longitude = NULL WHERE id = %s", [(i,) for i in ids])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="commit changes (default: dry run)")
    args = ap.parse_args()

    from tender_app.db import get_db_connection

    conn = get_db_connection()
    cur = conn.cursor()
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}\n")
    tenders_master(conn, cur, args.apply)
    supplier_regions(conn, cur, args.apply)
    award_sanity(conn, cur, args.apply)
    award_placeholder_coords(conn, cur, args.apply)
    if args.apply:
        conn.commit()
        print("\nCOMMITTED.")
    else:
        conn.rollback()
        print("\nDRY RUN - nothing changed. Re-run with --apply.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
