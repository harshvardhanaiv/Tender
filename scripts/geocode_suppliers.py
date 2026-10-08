#!/usr/bin/env python3
"""Fill suppliers.latitude/longitude so the Supplier Intelligence map/proximity filter has
somewhere to plot suppliers and filter by distance (mirrors scripts/geocode_buyers.py's role
for the buyer map, but suppliers have their own, better data source).

Why a different approach than geocode_buyers.py: buyers have no per-record address, only a name,
so that script matches names against council centroids or Nominatim. Suppliers, by contrast,
already carry a free-text 'address' column populated from award notices -- a DB check found
155,822 / 156,359 suppliers (99.7%) have one, and most end in a real UK postcode
(e.g. "14 Parkway, Knaresborough, North Yorkshire, HG5 9DP"). So:

Pass A (default): extract a UK postcode from the address (full unit, or outward code only if
that's all that's present) and resolve it in bulk via postcodes.io (free, unauthenticated, and
explicitly built for exactly this -- up to 100 lookups per request). Full postcodes go to
POST /postcodes, outward-only codes to POST /outcodes.

Pass B (always runs after A, for whatever's left): suppliers with no extractable/resolvable
postcode fall back to a centroid for their 'region' column (the 12 English regions + Scotland/
Wales/NI/Leinster already used elsewhere in this app) -- coarse, but better than nothing, and
flagged as such via geo_accuracy='region_centroid'.

Idempotent and safe to re-run -- only fills rows still missing coordinates. Dry-run by default.

    python scripts/geocode_suppliers.py                 # dry run
    python scripts/geocode_suppliers.py --apply
    python scripts/geocode_suppliers.py --apply --limit 500   # smoke-test on a subset first
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UA = "TenderFlow-supplier-geocoder/1.0 (public-sector procurement research)"
BATCH_SIZE = 100  # postcodes.io's bulk-lookup limit

# UK postcode: 1-2 letters, 1 digit + optional letter/digit, space, 1 digit + 2 letters.
_FULL_POSTCODE_RE = re.compile(r"\b([A-Za-z]{1,2}\d[A-Za-z\d]?)\s*(\d[A-Za-z]{2})\b")
# Outward code only (e.g. a truncated/partial address that still names the district).
_OUTWARD_RE = re.compile(r"\b([A-Za-z]{1,2}\d[A-Za-z\d]?)\b")

# Coarse fallback for suppliers.region when no postcode can be extracted/resolved from the address.
# Same order of accuracy as a UK-city guess (comparable to UK_BUYER_CITY_COORDS in web/app.js) --
# "UK" is left out on purpose: it is too generic to place anywhere.
REGION_CENTROIDS: dict[str, tuple[float, float]] = {
    "LONDON": (51.5074, -0.1278),
    "SOUTH EAST": (51.2362, -0.5704),
    "NORTH WEST": (53.7632, -2.7031),
    "YORKSHIRE AND THE HUMBER": (53.9591, -1.0815),
    "SOUTH WEST": (50.9097, -3.5920),
    "SCOTLAND": (56.4907, -4.2026),
    "EAST OF ENGLAND": (52.2405, 0.7000),
    "WEST MIDLANDS": (52.4862, -1.8904),
    "EAST MIDLANDS": (52.9548, -1.1581),
    "WALES": (52.1307, -3.7837),
    "NORTH EAST": (54.9783, -1.6178),
    "NORTHERN IRELAND": (54.5973, -5.9301),
    "IRELAND - LEINSTER": (53.3498, -6.2603),
}


def extract_postcode(address: str) -> tuple[str | None, str | None]:
    """Returns (full_postcode, outward_code) -- full_postcode is None if only an outward code
    could be found."""
    if not address:
        return None, None
    m = _FULL_POSTCODE_RE.search(address)
    if m:
        return f"{m.group(1).upper()} {m.group(2).upper()}", None
    m = _OUTWARD_RE.search(address)
    if m:
        return None, m.group(1).upper()
    return None, None


def _post_json(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"User-Agent": UA, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def bulk_postcodes(postcodes: list[str]) -> dict[str, tuple[float, float]]:
    """postcode -> (lat, lon) for whatever postcodes.io could resolve."""
    if not postcodes:
        return {}
    try:
        data = _post_json("https://api.postcodes.io/postcodes", {"postcodes": postcodes})
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"    postcodes.io /postcodes batch failed: {exc}")
        return {}
    out = {}
    for row in data.get("result", []):
        r = row.get("result")
        if r and r.get("latitude") is not None and r.get("longitude") is not None:
            out[row["query"]] = (float(r["latitude"]), float(r["longitude"]))
    return out


def bulk_outcodes(outcodes: list[str]) -> dict[str, tuple[float, float]]:
    """outward code -> (lat, lon). postcodes.io has no bulk /outcodes endpoint (only bulk
    /postcodes), so this is a handful of individual GET /outcodes/<code> lookups -- outward-only
    matches (no full postcode in the address) are rare, so the volume here stays small."""
    out: dict[str, tuple[float, float]] = {}
    for code in outcodes:
        req = urllib.request.Request(
            f"https://api.postcodes.io/outcodes/{urllib.parse.quote(code)}",
            headers={"User-Agent": UA},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError):
            continue
        r = data.get("result")
        if r and r.get("latitude") is not None and r.get("longitude") is not None:
            out[code] = (float(r["latitude"]), float(r["longitude"]))
    return out


def pass_a(cur, conn, rows: list[tuple[int, str]], apply: bool) -> tuple[int, list[int]]:
    """rows: (supplier_id, address). Returns (n_geocoded, ids_still_missing)."""
    full_by_id: dict[int, str] = {}
    outward_by_id: dict[int, str] = {}
    for sid, address in rows:
        full, outward = extract_postcode(address or "")
        if full:
            full_by_id[sid] = full
        elif outward:
            outward_by_id[sid] = outward

    geocoded = 0
    still_missing = set(sid for sid, _ in rows)

    ids = list(full_by_id.keys())
    for i in range(0, len(ids), BATCH_SIZE):
        chunk_ids = ids[i:i + BATCH_SIZE]
        resolved = bulk_postcodes([full_by_id[sid] for sid in chunk_ids])
        writes = []
        for sid in chunk_ids:
            hit = resolved.get(full_by_id[sid])
            if hit:
                writes.append((hit[0], hit[1], "postcode", sid))
                still_missing.discard(sid)
                geocoded += 1
        if apply and writes:
            cur.executemany(
                "UPDATE suppliers SET latitude = %s, longitude = %s, geo_accuracy = %s WHERE id = %s",
                writes,
            )
            conn.commit()
        print(f"    [postcode] batch {i // BATCH_SIZE + 1}: {len(writes)}/{len(chunk_ids)} resolved")

    ids = list(outward_by_id.keys())
    for i in range(0, len(ids), BATCH_SIZE):
        chunk_ids = ids[i:i + BATCH_SIZE]
        resolved = bulk_outcodes([outward_by_id[sid] for sid in chunk_ids])
        writes = []
        for sid in chunk_ids:
            hit = resolved.get(outward_by_id[sid])
            if hit:
                writes.append((hit[0], hit[1], "outcode", sid))
                still_missing.discard(sid)
                geocoded += 1
        if apply and writes:
            cur.executemany(
                "UPDATE suppliers SET latitude = %s, longitude = %s, geo_accuracy = %s WHERE id = %s",
                writes,
            )
            conn.commit()
        print(f"    [outcode] batch {i // BATCH_SIZE + 1}: {len(writes)}/{len(chunk_ids)} resolved")

    return geocoded, sorted(still_missing)


def pass_b(cur, conn, rows_by_id: dict[int, str], missing_ids: list[int], apply: bool) -> int:
    """Region-centroid fallback for suppliers pass A couldn't place."""
    writes = []
    for sid in missing_ids:
        region = (rows_by_id.get(sid) or "").strip().upper()
        hit = REGION_CENTROIDS.get(region)
        if hit:
            writes.append((hit[0], hit[1], "region_centroid", sid))
    if apply and writes:
        cur.executemany(
            "UPDATE suppliers SET latitude = %s, longitude = %s, geo_accuracy = %s WHERE id = %s",
            writes,
        )
        conn.commit()
    return len(writes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write coordinates (default: dry run)")
    ap.add_argument("--limit", type=int, default=0, metavar="N", help="only process the first N suppliers still missing coordinates (0 = all)")
    args = ap.parse_args()

    from tender_app.db import get_db_connection

    conn = get_db_connection()
    cur = conn.cursor()
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}\n")

    sql = "SELECT id, address, region FROM suppliers WHERE latitude IS NULL"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    cur.execute(sql)
    all_rows = cur.fetchall()
    print(f"{len(all_rows)} suppliers missing coordinates")
    if not all_rows:
        conn.close()
        return 0

    addr_rows = [(sid, address) for sid, address, _region in all_rows]
    region_by_id = {sid: region for sid, _address, region in all_rows}

    n_a, missing = pass_a(cur, conn, addr_rows, args.apply)
    print(f"\n[pass A] geocoded {n_a} from address postcodes; {len(missing)} still missing")

    n_b = pass_b(cur, conn, region_by_id, missing, args.apply)
    print(f"[pass B] {n_b} filled from region centroid; {len(missing) - n_b} left with no coordinates")

    if not args.apply:
        conn.rollback()
        print("\nDRY RUN - nothing written. Re-run with --apply.")
    else:
        cur.execute("SELECT geo_accuracy, count(*) FROM suppliers WHERE geo_accuracy IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
        print("\nsuppliers geo_accuracy now:", dict(cur.fetchall()))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
