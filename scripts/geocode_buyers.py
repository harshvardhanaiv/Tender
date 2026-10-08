#!/usr/bin/env python3
"""Fill buyer_locations so the buyer map puts buyers where they are.

Why: the coordinates stored on contract_awards were a single England-wide placeholder
(52.3555, -1.1743) for ~40,000 awards / 2,295 buyers, so the map stacked them all in the
Midlands. Buyers now get their position from this table instead.

Pass A (offline, default): councils. The centre of a council's planning applications (PlanIt
data already in planning_applications) is a good stand-in for "where the council is". A buyer
is matched only when, after dropping words like Council/Borough/County/City, its name is exactly
one planning authority's name ("Surrey County Council" -> "Surrey"). Ambiguous names (two
authorities with the same short name) are skipped.

Pass B (optional, --nominatim N): look up the N most active buyers still without a location in
OpenStreetMap Nominatim, at its 1 request/second limit. A result is kept only if it is inside
the UK/Ireland and its name is a close match for the buyer's name (an area such as a county or council is stored
as 'area_centroid', i.e. approximate); otherwise a 'miss' row is
stored so the buyer is not asked about again. Only public-body names are sent.

Idempotent and safe to re-run. Dry-run by default; --apply writes.

    python scripts/geocode_buyers.py                      # dry run, pass A
    python scripts/geocode_buyers.py --apply              # write pass A
    python scripts/geocode_buyers.py --apply --nominatim 200
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Words that decorate a council's name but are not part of the place name.
NOISE = {"council", "borough", "city", "district", "county", "london", "metropolitan", "royal", "of", "the",
         "and", "unitary", "authority", "corporation", "combined", "county", "shire", "&"}
MIN_APPLICATIONS = 5  # a centroid from a handful of applications is not trustworthy
UA = "TenderFlow-buyer-geocoder/1.0 (public-sector procurement research)"


def place_key(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in NOISE)


def buyer_key(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip().upper()


def council_centroids(cur) -> dict[str, tuple[float, float, str]]:
    """place key -> (lat, lon, authority name) for planning authorities with a unique key."""
    cur.execute("""
        SELECT a.area_name, a.long_name,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY p.latitude),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY p.longitude),
               count(*)
        FROM planning_applications p JOIN planning_areas a ON a.area_id = p.authority_id
        WHERE p.latitude IS NOT NULL AND p.longitude IS NOT NULL
        GROUP BY a.area_id, a.area_name, a.long_name
        HAVING count(*) >= %s
    """, (MIN_APPLICATIONS,))
    by_key: dict[str, list[tuple[float, float, str]]] = {}
    for area, long_name, lat, lon, _n in cur.fetchall():
        for k in {place_key(area), place_key(long_name)}:
            if k:
                by_key.setdefault(k, []).append((float(lat), float(lon), area))
    # ambiguous = the same key points at different places
    return {k: v[0] for k, v in by_key.items()
            if len({(round(x[0], 3), round(x[1], 3)) for x in v}) == 1}


def pass_a(cur, apply: bool) -> int:
    centroids = council_centroids(cur)
    cur.execute("SELECT authority_name, count(*) FROM contract_awards WHERE authority_name IS NOT NULL "
                "AND TRIM(authority_name) <> '' GROUP BY 1")
    rows, seen = [], set()
    for name, n in cur.fetchall():
        key = buyer_key(name)
        if key in seen or "COUNCIL" not in key:  # only things that are actually councils
            continue
        hit = centroids.get(place_key(name))
        if hit:
            seen.add(key)
            rows.append((key, name.strip(), hit[0], hit[1], "council_centroid", hit[2], "planning_applications centroid"))
    print(f"[pass A] {len(centroids)} unambiguous planning authorities; {len(rows)} council buyers matched")
    for r in rows[:6]:
        print(f"    {r[1]!r} -> {r[5]} ({r[2]:.4f}, {r[3]:.4f})")
    if apply and rows:
        cur.executemany("""
            INSERT INTO buyer_locations (authority_key, authority_name, latitude, longitude, accuracy, matched_name, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (authority_key) DO UPDATE SET latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude,
                accuracy = EXCLUDED.accuracy, matched_name = EXCLUDED.matched_name, source = EXCLUDED.source,
                updated_at = CURRENT_TIMESTAMP
            WHERE buyer_locations.accuracy <> 'geocoded'   -- never overwrite a name-verified point
        """, rows)
    return len(rows)


def nominatim(query: str) -> dict | None:
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "limit": 1, "countrycodes": "gb,ie", "addressdetails": 0})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        results = json.loads(resp.read())
    return results[0] if results else None


def pass_b(conn, cur, limit: int, apply: bool) -> None:
    from tender_app.ch_matcher import calculate_name_similarity

    cur.execute("""
        SELECT ca.authority_name, count(*) c FROM contract_awards ca
        WHERE ca.authority_name IS NOT NULL AND TRIM(ca.authority_name) <> ''
          AND NOT EXISTS (SELECT 1 FROM buyer_locations b
                          WHERE b.authority_key = UPPER(TRIM(REGEXP_REPLACE(ca.authority_name, '\\s+', ' ', 'g'))))
        GROUP BY 1 ORDER BY c DESC LIMIT %s
    """, (limit,))
    todo = cur.fetchall()
    print(f"[pass B] {len(todo)} buyers to look up (1 request/second)")
    found = missed = 0
    for i, (name, _c) in enumerate(todo, 1):
        try:
            hit = nominatim(f"{name}, United Kingdom")
        except Exception as exc:  # network/rate-limit: stop, keep what we have
            print(f"  stopping at {i}: {exc}")
            break
        accuracy, lat, lon, matched = "miss", None, None, None
        if hit:
            la, lo = float(hit["lat"]), float(hit["lon"])
            matched = (hit.get("display_name") or "").split(",")[0].strip()
            in_uk_ie = 49.5 <= la <= 61.5 and -11.0 <= lo <= 2.5
            if in_uk_ie and calculate_name_similarity(name, matched) >= 0.6:
                # An administrative area (a county, a council) comes back as the middle of its
                # boundary, not a building: keep it, but as an approximate position.
                is_area = hit.get("class") == "boundary" or "council" in name.lower()
                accuracy, lat, lon = ("area_centroid" if is_area else "geocoded"), la, lo
        found += accuracy != "miss"
        missed += accuracy == "miss"
        if i <= 8 or accuracy != "miss" and found <= 5:
            print(f"    {name[:55]!r} -> {accuracy} {matched or ''}")
        if apply:
            cur.execute("""
                INSERT INTO buyer_locations (authority_key, authority_name, latitude, longitude, accuracy, matched_name, source)
                VALUES (%s, %s, %s, %s, %s, %s, 'nominatim')
                ON CONFLICT (authority_key) DO NOTHING
            """, (buyer_key(name), name.strip(), lat, lon, accuracy, matched))
            conn.commit()
        time.sleep(1.1)  # Nominatim's usage policy: at most 1 request per second
    print(f"[pass B] geocoded {found}, no trustworthy match {missed}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write to buyer_locations (default: dry run)")
    ap.add_argument("--nominatim", type=int, default=0, metavar="N", help="also look up N busiest unlocated buyers")
    args = ap.parse_args()

    from tender_app.db import get_db_connection
    from tender_app.db_ext import create_buyer_locations_table

    conn = get_db_connection()
    cur = conn.cursor()
    create_buyer_locations_table(cur)
    conn.commit()
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}\n")
    pass_a(cur, args.apply)
    if args.apply:
        conn.commit()
    if args.nominatim:
        pass_b(conn, cur, args.nominatim, args.apply)
    if not args.apply:
        conn.rollback()
        print("\nDRY RUN - nothing written. Re-run with --apply.")
    else:
        cur.execute("SELECT accuracy, count(*) FROM buyer_locations GROUP BY 1 ORDER BY 2 DESC")
        print("\nbuyer_locations now:", dict(cur.fetchall()))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
