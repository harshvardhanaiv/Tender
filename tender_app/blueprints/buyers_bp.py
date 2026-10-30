"""
Buyer Intelligence Blueprint for TenderFlow
Mirror of Supplier Intelligence, keyed by contracting authority instead of supplier.
Includes:
- Buyer Search & Profile Drawer Data with 5+ award sample size floor
- Opportunity Radar (Upcoming renewals table)
- Opportunity Feed (Tier 1 vs Tier 2 Personalization with transparent match scores)
- Proximity search & spatial distance calculation
"""

from __future__ import annotations
from flask import Blueprint, request, jsonify, session
from typing import Any
import json
import re
import math
import time
from tender_app import buyer_fit
from tender_app.grants import grants_for_funder
from datetime import datetime, timedelta

from etenders_scraper.deadline import enrich_deadline_fields
from etenders_scraper.awards import SINGLE_AWARD_STATS_CEILING_GBP, DEDUPED_AWARDS_CTE_SQL, deduped_awards_cte_sql, classify_buyer_type, looks_like_buyer_authority
from tender_app.entity_merge import entity_name_key

buyers_bp = Blueprint("buyers", __name__)

# Global state for database connection (set by init)
_get_db = None

def init_buyers_blueprint(get_db_connection):
    """Initialize blueprint with database connection factory."""
    global _get_db
    _get_db = get_db_connection
    return buyers_bp

def _get_connection():
    """Get database connection from factory."""
    if _get_db is not None:
        return _get_db()
    else:
        raise RuntimeError("Buyers blueprint not initialized with database connection")

def _execute(cursor, conn, sql: str, params: tuple | list = ()):
    """Execute SQL with automatic placeholder conversion for Postgres/SQLite."""
    sql = sql.replace("?", "%s")
    cursor.execute(sql, params)

def _row_to_dict(cursor, row) -> dict[str, Any]:
    """Convert database row to dictionary."""
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return dict(row)
    col_names = [desc[0] for desc in cursor.description] if cursor.description else []
    return dict(zip(col_names, row))

def _norm_auth_sql(expr: str) -> str:
    """SQL expression normalizing an authority name for case/punctuation-insensitive
    matching — the same real buyer (e.g. "Dublin City Council") gets returned with
    inconsistent casing/spacing across portals (Bravo portals in particular tend to
    return ALL CAPS), which fragmented buyer aggregation into duplicate rows before
    every GROUP BY / WHERE authority_name match here went through this helper.

    Originally case/whitespace only; widened to also fold "&" to "AND", strip leading "THE "
    and trailing " COUNCIL", and collapse punctuation."""
    upper = f"UPPER({expr})"
    with_and = f"REPLACE({upper}, '&', ' AND ')"
    clean = f"REGEXP_REPLACE(REGEXP_REPLACE({with_and}, '^THE[[:space:]]+', '', 'g'), '[[:space:]]+COUNCIL$', '', 'g')"
    punct = f"UPPER(TRIM(REGEXP_REPLACE({clean}, '[^A-Z0-9]+', ' ', 'g')))"
    # "London Borough Camden" (the "of" dropped by a portal) is the same buyer as "London Borough of Camden".
    # No "?" in this SQL: _execute() turns every "?" into a parameter placeholder, so the optional group is written (OF )*.
    return f"REGEXP_REPLACE({punct}, '^LONDON BOROUGH (OF )*', 'LONDON BOROUGH OF ')"


_CLOSED_TENDER_STATUSES = {"awarded", "closed", "complete", "completed", "cancelled", "canceled", "withdrawn", "expired"}

# Portals name the same field differently in the raw scrape output (see
# etenders_scraper/sources/*.py) — tried in order, first non-empty wins.
_CONTACT_EMAIL_KEYS = ("contact_email", "email")
_CONTACT_PHONE_KEYS = ("contact_phone", "telephone", "phone")


def _fetch_buyer_contact_info(cursor, conn, authority_name: str) -> dict[str, str]:
    """Best available buyer contact details, pulled from the tenders_master cache of
    already-scraped notices (same source and matching as _fetch_open_opportunities)
    rather than invented. Only ever returns a value that was actually present on a
    notice; no website/address field exists in the canonical schema (etenders_scraper/
    fields.py), so those stay "Not available" rather than being guessed."""
    contact_info = {"email": "Not available", "phone": "Not available",
                     "website": "Not available", "address": "Not available"}
    _execute(cursor, conn, f"""
        SELECT raw_json
        FROM tenders_master
        WHERE {_norm_auth_sql("contracting_authority")} = {_norm_auth_sql("?")}
        ORDER BY updated_at DESC
        LIMIT 200
    """, [authority_name])
    for (raw_json,) in cursor.fetchall():
        try:
            row = json.loads(raw_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        if contact_info["email"] == "Not available":
            for key in _CONTACT_EMAIL_KEYS:
                if row.get(key):
                    contact_info["email"] = row[key]
                    break
        if contact_info["phone"] == "Not available":
            for key in _CONTACT_PHONE_KEYS:
                if row.get(key):
                    contact_info["phone"] = row[key]
                    break
        if contact_info["email"] != "Not available" and contact_info["phone"] != "Not available":
            break
    return contact_info


def _fetch_open_opportunities(cursor, conn, authority_name: str, limit: int = 25) -> list[dict[str, Any]]:
    """Live tenders this buyer has published that can still be bid on.

    Reads the tenders_master cache the live searches populate, so it only knows about
    tenders a search has already surfaced — not the portals' full catalogue. Each row is the
    original search-result row (source, resource_id, title, detail_url, ...) so the frontend
    can hand it straight to loadDetails() and open it like any tender-search result. The
    deadline fields are recomputed here: the stored days_until_deadline/deadline_urgency were
    frozen at the moment of the search and go stale. Tenders with no parseable deadline are
    kept (sorted last) rather than hidden, since many portals omit it."""
    _execute(cursor, conn, f"""
        SELECT raw_json
        FROM tenders_master
        WHERE {_norm_auth_sql("contracting_authority")} = {_norm_auth_sql("?")}
        ORDER BY updated_at DESC
        LIMIT 200
    """, [authority_name])

    opportunities = []
    for (raw_json,) in cursor.fetchall():
        try:
            row = json.loads(raw_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        notice_type = str(row.get("notice_type") or "").lower()
        status = str(row.get("status") or "").strip().lower()
        if "award" in notice_type or status in _CLOSED_TENDER_STATUSES:
            continue
        enrich_deadline_fields(row)
        if row["deadline_urgency"] == "past":
            continue
        opportunities.append(row)

    # Soonest deadline first; rows with no known deadline go last.
    opportunities.sort(key=lambda r: (r["deadline_date"] is None, r["deadline_date"] or ""))
    return opportunities[:limit]


def _open_opportunities_or_empty(cursor, conn, authority_name: str) -> list[dict[str, Any]]:
    """Non-critical: a failure here must not take down the rest of the buyer profile, and
    Postgres needs the aborted transaction rolled back before the connection is reusable."""
    try:
        return _fetch_open_opportunities(cursor, conn, authority_name)
    except Exception:
        import traceback
        traceback.print_exc()
        conn.rollback()
        return []


def haversine_km(lat1: float | None, lon1: float | None, lat2: float | None, lon2: float | None) -> float:
    """Computes Haversine distance in kilometers between two lat/long points."""
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None or (lat1 == 0 and lon1 == 0) or (lat2 == 0 and lon2 == 0):
        return 999999.0
    try:
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c
    except Exception:
        return 999999.0

UK_CITY_COORDS = {
    "london": (51.5074, -0.1278),
    "manchester": (53.4808, -2.2426),
    "birmingham": (52.4862, -1.8904),
    "leeds": (53.8008, -1.5491),
    "glasgow": (55.8642, -4.2518),
    "edinburgh": (55.9533, -3.1883),
    "belfast": (54.5973, -5.9301),
    "dublin": (53.3498, -6.2603),
    "cardiff": (51.4816, -3.1791),
    "bristol": (51.4545, -2.5879),
    "newcastle": (54.9783, -1.6178),
    "sheffield": (53.3811, -1.4701),
    "liverpool": (53.4084, -2.9916),
    "nottingham": (52.9548, -1.1581),
    "southampton": (50.9097, -1.4044),
    "leicester": (52.6369, -1.1398),
    "yorkshire": (53.7997, -1.5491),
    "york": (53.9590, -1.0815),
    "hull": (53.7457, -0.3367),
    "bradford": (53.7960, -1.7594),
    "coventry": (52.4068, -1.5197),
    "derby": (52.9225, -1.4746),
    "plymouth": (50.3755, -4.1427),
    "brighton": (50.8225, -0.1372),
    "norwich": (52.6309, 1.2974),
    "oxford": (51.7520, -1.2577),
    "cambridge": (52.2053, 0.1218),
    "exeter": (50.7184, -3.5339),
    "gloucester": (51.8642, -2.2380),
    "swansea": (51.6214, -3.9436),
    "aberdeen": (57.1497, -2.0943),
    "dundee": (56.4620, -2.9707),
    "cork": (51.8985, -8.4756),
    "galway": (53.2707, -9.0568),
    "limerick": (52.6638, -8.6267)
}

# London's 32 boroughs + the City, keyed by their most distinctive name fragment. Checked
# before UK_CITY_COORDS below: without this, "london" (a substring of almost every London
# borough council's full name, e.g. "London Borough of Merton") matched first and placed
# every one of them at exact central-London coordinates — a borough 10-15km out would show
# as "~0 km away" from a London base point. These are approximate town-hall/centroid
# coordinates, real enough for a "how far away" heuristic even though they're not a
# geocoded delivery address.
LONDON_BOROUGH_COORDS = {
    "barking and dagenham": (51.5540, 0.1305),
    "barnet": (51.6252, -0.1517),
    "bexley": (51.4549, 0.1505),
    "brent": (51.5588, -0.2817),
    "bromley": (51.4039, 0.0198),
    "camden": (51.5290, -0.1255),
    "croydon": (51.3714, -0.0977),
    "ealing": (51.5130, -0.3089),
    "enfield": (51.6521, -0.0807),
    "greenwich": (51.4892, 0.0648),
    "hackney": (51.5450, -0.0553),
    "hammersmith and fulham": (51.4927, -0.2339),
    "haringey": (51.6000, -0.1119),
    "harrow": (51.5898, -0.3346),
    "havering": (51.5779, 0.1834),
    "hillingdon": (51.5352, -0.4482),
    "hounslow": (51.4746, -0.3680),
    "islington": (51.5416, -0.1022),
    "kensington and chelsea": (51.5020, -0.1947),
    "kingston upon thames": (51.4085, -0.3064),
    "lambeth": (51.4607, -0.1163),
    "lewisham": (51.4452, -0.0209),
    "merton": (51.4014, -0.1958),
    "newham": (51.5077, 0.0469),
    "redbridge": (51.5590, 0.0741),
    "richmond upon thames": (51.4479, -0.3260),
    "southwark": (51.5030, -0.0801),
    "sutton": (51.3618, -0.1945),
    "tower hamlets": (51.5150, -0.0172),
    "waltham forest": (51.5908, -0.0134),
    "wandsworth": (51.4571, -0.1910),
    "westminster": (51.4973, -0.1372),
    "city of london": (51.5155, -0.0922),
}

# The stored award coordinates turned out to be one England-wide placeholder (about 40,000 awards,
# 2,295 buyers) that the map presented as a precise location. It is treated as "unknown" everywhere.
PLACEHOLDER_COORD = (52.3555, -1.1743)
# Both coordinates from the same award: MAX(lat) with MAX(lon) could pair two different places.
_REAL_COORD_SQL = (
    "ca.latitude IS NOT NULL AND ca.longitude IS NOT NULL AND NOT "
    f"(ROUND(ca.latitude::numeric, 4) = {PLACEHOLDER_COORD[0]} AND ROUND(ca.longitude::numeric, 4) = {PLACEHOLDER_COORD[1]})"
)


def _is_placeholder_coord(lat: float | None, lon: float | None) -> bool:
    return (lat is not None and lon is not None
            and abs(lat - PLACEHOLDER_COORD[0]) < 0.0005 and abs(lon - PLACEHOLDER_COORD[1]) < 0.0005)


def buyer_location_key(name: str) -> str:
    """Same normalisation as _norm_auth_sql, so keys line up with how buyers are grouped."""
    return re.sub(r"\s+", " ", name or "").strip().upper()


_BUYER_LOC_TTL_S = 600
_buyer_loc_cache: dict[str, Any] = {"at": 0.0, "map": {}}


def _buyer_locations() -> dict[str, tuple[float, float, str]]:
    """authority_key -> (lat, lon, accuracy) from the buyer_locations table, cached for a few
    minutes. Empty (never an error) if the table does not exist yet or the read fails."""
    now = time.time()
    if now - _buyer_loc_cache["at"] < _BUYER_LOC_TTL_S:
        return _buyer_loc_cache["map"]
    loaded: dict[str, tuple[float, float, str]] = {}
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT authority_key, latitude, longitude, accuracy FROM buyer_locations "
                        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
            loaded = {k: (float(la), float(lo), acc) for k, la, lo, acc in cur.fetchall()}
            cur.close()
        finally:
            conn.close()
    except Exception:
        loaded = _buyer_loc_cache["map"]  # keep the last good copy
    _buyer_loc_cache.update(at=now, map=loaded)
    return loaded


def resolve_authority_coords(auth_name: str, existing_lat: float | None, existing_lon: float | None) -> tuple[float, float, bool]:
    """Resolves buyer coordinates using existing DB lat/lon or falling back to authority
    name keyword matching. Returns (lat, lon, is_approximate) — is_approximate is True
    when we fell back to a keyword guess rather than a real stored coordinate, so callers
    can label the resulting distance as approximate instead of presenting a keyword guess
    as precise.

    Matches the LONGEST matching keyword first, not the first dict entry that matches —
    otherwise a generic short name (e.g. "london") would win over a more specific one
    that's also present (e.g. "richmond upon thames" in "London Borough of Richmond upon
    Thames"), collapsing every distinct borough onto the same city-centre point."""
    known = _buyer_locations().get(buyer_location_key(auth_name))
    if known:
        lat, lon, accuracy = known
        return (lat, lon, accuracy != "geocoded")  # a council centroid is a good guess, not an address
    if (existing_lat and existing_lon and (existing_lat != 0 or existing_lon != 0)
            and not _is_placeholder_coord(float(existing_lat), float(existing_lon))):
        return (float(existing_lat), float(existing_lon), False)
    n = (auth_name or "").lower()

    def best_match(coord_map: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
        best_key, best_coords = None, None
        for city, coords in coord_map.items():
            if city in n and (best_key is None or len(city) >= len(best_key)):
                best_key, best_coords = city, coords
        return best_coords

    # London boroughs are checked as their own pass, ahead of the generic city list, so a
    # borough-specific match (e.g. "merton") always wins over the generic "london" that's
    # also a substring of the same name — see the note above.
    match = best_match(LONDON_BOROUGH_COORDS) or best_match(UK_CITY_COORDS)
    if match:
        return (match[0], match[1], True)
    return (0.0, 0.0, True)


# ── BUYER SEARCH & LIST ────────────────────────────────────────────────────

@buyers_bp.route("/api/buyers/search", methods=["GET"])
def search_buyers():
    """
    Search buyers (contracting authorities) by name, region, buyer_type, or related tender titles.
    Returns list of buyers with aggregated stats.
    """
    q = (request.args.get("q") or "").strip()
    buyer_type_filter = (request.args.get("buyer_type") or "").strip()
    sort_by = (request.args.get("sort") or "spend").lower()
    date_preset = (request.args.get("date_range") or "all").lower()
    min_spend = float(request.args.get("min_spend") or 0.0)
    frameworks_only = request.args.get("frameworks_only", "").lower() in ("1", "true")
    
    conn = _get_connection()
    cursor = conn.cursor()
    
    try:
        where_clauses = [
            "ca.authority_name IS NOT NULL",
            "TRIM(ca.authority_name) != ''",
            "LENGTH(TRIM(ca.authority_name)) > 2",
            "ca.authority_name NOT IN ('Unknown', 'Not available', 'N/A')"
        ]
        params = []

        if date_preset != "all":
            days_back = 365 if date_preset == "1" else (730 if date_preset == "2" else 1095)
            cutoff_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            where_clauses.append("ca.date_signed >= ?")
            params.append(cutoff_date)

        if q:
            search_term = f"%{q.lower()}%"
            where_clauses.append("(LOWER(ca.authority_name) LIKE ? OR LOWER(ca.cpv_description) LIKE ? OR LOWER(ca.cpv_code) LIKE ?)")
            params.extend([search_term, search_term, search_term])

        if buyer_type_filter and buyer_type_filter != "all":
            where_clauses.append("ca.buyer_type = ?")
            params.append(buyer_type_filter)

        if frameworks_only:
            where_clauses.append("ca.is_framework = 1")

        where_sql = " AND ".join(where_clauses)

        # The common searches (all dates, not frameworks-only) read buyer_stats, the precomputed de-duplicated figures the
        # feed and the profile also use: the awards only choose WHICH buyers match (name or CPV text). Aggregating every
        # award on each search took 15-30 s once the shared-notice rule was added, and re-derived the numbers a second way.
        from tender_app import stats as _stats   # imported here: stats imports this module for the buyer key
        rows = None
        total_matching = 0
        if date_preset == "all" and not frameworks_only and _stats.is_ready(cursor, "buyer_stats"):
            try:
                keys = None
                if q:
                    _execute(cursor, conn, f"SELECT DISTINCT {_norm_auth_sql('ca.authority_name')} FROM contract_awards ca WHERE {where_sql}", params)
                    keys = [r[0] for r in cursor.fetchall()]
                where = ["COALESCE(total_spend, 0) >= ?"]
                sparams: list[Any] = [min_spend]
                if keys is not None:
                    where.append("authority_key = ANY(?)")
                    sparams.append(keys)
                if buyer_type_filter and buyer_type_filter != "all":
                    where.append("buyer_type = ?")
                    sparams.append(buyer_type_filter)
                order = {"contracts": "total_contracts DESC", "name": "authority_name ASC"}.get(sort_by, "total_spend DESC NULLS LAST")
                where_stats = " AND ".join(where)
                _execute(cursor, conn, f"SELECT COUNT(*) FROM buyer_stats WHERE {where_stats}", sparams)
                total_matching = cursor.fetchone()[0]
                _execute(cursor, conn, f"""
                    SELECT authority_name, buyer_type, total_contracts, total_spend, avg_contract_value, unique_suppliers,
                           latest_award_date, max_renewal_date, latitude, longitude
                    FROM buyer_stats WHERE {where_stats} ORDER BY {order} LIMIT 500""", sparams)
                rows = cursor.fetchall()
            except Exception:
                # buyer_stats is not usable yet (an older table without avg_contract_value, say): aggregate the awards instead
                conn.rollback()
                rows = None

        if rows is None:
            # Framework awards carry a shared ceiling value across every appointed
            # supplier, not a per-buyer spend figure — summing them alongside direct
            # awards inflates total_spend by however many suppliers share the
            # framework (seen live: several buyers showing spend in the hundreds of
            # billions). When frameworks_only is set the user is deliberately looking
            # at ceiling values, so only exclude them from the general spend view.
            spend_expr = "SUM(ca.contract_value)" if frameworks_only else f"SUM(CASE WHEN ca.is_framework = 1 OR ca.contract_value < 0 OR ca.contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN 0 ELSE ca.contract_value END)"
            avg_expr = "AVG(ca.contract_value)" if frameworks_only else f"AVG(CASE WHEN ca.is_framework = 1 OR ca.contract_value < 0 OR ca.contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN NULL ELSE ca.contract_value END)"

            having_sql = f"HAVING MAX(TRIM(ca.authority_name)) != '' AND COUNT(DISTINCT ca.id) > 0 AND {spend_expr} >= ?"
            params.append(min_spend)

            # Group by a case/whitespace-normalized key so the same real authority
            # published under different casing across portals (e.g. Bravo portals
            # returning ALL CAPS) doesn't fragment into duplicate buyer rows — but
            # still display a real, naturally-cased name (prefer a non-all-caps
            # variant if one exists) rather than the normalized key itself.
            query = f"""
                WITH {DEDUPED_AWARDS_CTE_SQL}
                SELECT
                    (ARRAY_AGG(TRIM(ca.authority_name) ORDER BY
                        (TRIM(ca.authority_name) = UPPER(TRIM(ca.authority_name))) ASC,
                        LENGTH(ca.authority_name) DESC,
                        TRIM(ca.authority_name) ASC
                    ))[1] as authority_name,
                    MAX(ca.buyer_type) as buyer_type,
                    COUNT(DISTINCT ca.id) as total_contracts,
                    {spend_expr} as total_spend,
                    {avg_expr} as avg_contract_value,
                    COUNT(DISTINCT ca.supplier_name) as unique_suppliers,
                    MAX(ca.date_signed) as latest_award_date,
                    MAX(ca.contract_end_date) as max_renewal_date,
                    (ARRAY_AGG(ca.latitude ORDER BY ca.date_signed DESC NULLS LAST) FILTER (WHERE {_REAL_COORD_SQL}))[1] as latitude,
                    (ARRAY_AGG(ca.longitude ORDER BY ca.date_signed DESC NULLS LAST) FILTER (WHERE {_REAL_COORD_SQL}))[1] as longitude
                FROM deduped_awards ca
                WHERE {where_sql}
                GROUP BY {_norm_auth_sql("ca.authority_name")}
                {having_sql}
            """

            if sort_by == "contracts":
                query += " ORDER BY total_contracts DESC"
            elif sort_by == "name":
                query += " ORDER BY authority_name ASC"
            else:
                query += " ORDER BY total_spend DESC NULLS LAST"

            # Real total count of matching buyer groups, independent of the LIMIT 500 below
            # (which only bounds how many rows we return for display) — the query previously
            # reported len(buyers) as "total", which silently equalled the display cap once
            # more than 500 buyers matched.
            count_query = f"""
                WITH {DEDUPED_AWARDS_CTE_SQL}
                SELECT COUNT(*) FROM (
                    SELECT {_norm_auth_sql("ca.authority_name")} as norm_name
                    FROM deduped_awards ca
                    WHERE {where_sql}
                    GROUP BY {_norm_auth_sql("ca.authority_name")}
                    {having_sql}
                ) t
            """
            _execute(cursor, conn, count_query, params)
            total_matching = cursor.fetchone()[0]

            query += " LIMIT 500"

            _execute(cursor, conn, query, params)
            rows = cursor.fetchall()

        buyers = []
        for row in rows:
            b = _row_to_dict(cursor, row)
            raw_name = b.get("authority_name") or ""
            auth_name = raw_name.strip().lstrip(".").strip()
            if not auth_name or len(auth_name) <= 2:
                continue
            b_type = b.get("buyer_type") or classify_buyer_type(auth_name)
            
            # buyer_type_filter now handled in SQL query above - removed duplicate filter

            total_c = b.get("total_contracts", 0)
            unique_s = b.get("unique_suppliers", 0)
            
            # Repeat supplier rate (5+ sample floor)
            if total_c >= 5 and unique_s > 0:
                repeat_rate = round(((total_c - unique_s) / total_c) * 100, 1)
                repeat_display = f"{repeat_rate}%"
            else:
                repeat_rate = 0.0
                repeat_display = "Not enough data yet"

            # Same resolution as the map/radar: known buyer location, else a real stored one, else a name guess.
            b_lat, b_lon, coords_approx = resolve_authority_coords(
                auth_name, float(b.get("latitude") or 0.0), float(b.get("longitude") or 0.0))

            buyers.append({
                "authority_name": auth_name,
                "buyer_type": b_type,
                "total_contracts": total_c,
                "total_spend": float(b.get("total_spend") or 0.0),
                "avg_contract_value": float(b.get("avg_contract_value") or 0.0),
                "unique_suppliers": unique_s,
                "repeat_supplier_pct": repeat_rate,
                "repeat_supplier_display": repeat_display,
                "latest_award_date": b.get("latest_award_date"),
                "max_renewal_date": b.get("max_renewal_date"),
                "latitude": round(b_lat, 6) if (b_lat or b_lon) else None,
                "longitude": round(b_lon, 6) if (b_lat or b_lon) else None,
                "coords_approximate": bool(coords_approx) if (b_lat or b_lon) else None,
            })

        return jsonify({"ok": True, "buyers": buyers, "total": total_matching})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


# ── BUYER DETAIL (MODAL DRAWER DATA) ──────────────────────────────────────

# The buyer-first CTE repeats its filter in both UNION branches, and each query then filters once more:
# three `?` placeholders per query, all bound to the same buyer name.
BUYER_CTE_PARAMS = 3

# A buyer's two top vendors change only when award data does, and working them out for 50 cards costs seconds: keep them for 30 minutes.
_TOP_VENDOR_TTL_S = 1800
_top_vendor_cache: dict[str, tuple[float, list[str]]] = {}


def _tidy_top_suppliers(rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """A buyer's winners as one row per company: other public bodies that appear in the supplier field (an NHS trust named
    as a delivery partner) are dropped, and spellings of one name ("Mears Limited" / "MEARS LIMITED") are added together."""
    merged: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = (r.get("supplier_name") or "").strip()
        if not name or looks_like_buyer_authority(name):
            continue
        key = entity_name_key(name) or name.upper()
        cur = merged.get(key)
        if cur is None:
            merged[key] = dict(r, supplier_name=name, _best=r.get("contracts_won") or 0)
            continue
        for f in ("contracts_won", "total_value", "framework_total_ceiling", "framework_appointments"):
            cur[f] = (cur.get(f) or 0) + (r.get(f) or 0)
        if (r.get("last_award_date") or "") > (cur.get("last_award_date") or ""):
            cur["last_award_date"] = r.get("last_award_date")
        if (r.get("contracts_won") or 0) > cur["_best"] and not _shouty(name):
            cur["supplier_name"], cur["_best"] = name, r.get("contracts_won") or 0
        elif _shouty(cur["supplier_name"]) and not _shouty(name):
            cur["supplier_name"] = name
    out = sorted(merged.values(), key=lambda x: (-(x.get("total_value") or 0), -(x.get("framework_total_ceiling") or 0), -(x.get("contracts_won") or 0)))
    for r in out:
        r.pop("_best", None)
    return out[:limit]


def _shouty(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


@buyers_bp.route("/api/buyers/<authority_name>", methods=["GET"])
def get_buyer_detail(authority_name: str):
    """
    Fetch complete buyer profile with aggregated intelligence metrics.
    Applies strict 5+ award sample size floor for repeat supplier rate & SME percentage.
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    try:
        # 1. Basic stats
        # Match on a case/whitespace-normalized key (see _norm_auth_sql) so this
        # resolves the same buyer regardless of which portal's casing convention
        # produced the `authority_name` the caller passed in.
        norm_match_sql = f"{_norm_auth_sql('ca.authority_name')} = {_norm_auth_sql('?')}"
        # De-duplicate only this buyer's awards (the filter runs before the DISTINCT ON, and can use
        # idx_awards_authority_norm_key). De-duplicating every award for each of the queries below made a
        # profile take about 30 s.
        buyer_cte = deduped_awards_cte_sql(f"{_norm_auth_sql('authority_name')} = {_norm_auth_sql('?')}")
        stats_query = f"""
            WITH {buyer_cte}
            SELECT
                COUNT(DISTINCT ca.id) as total_contracts,
                SUM(CASE WHEN ca.is_framework = 1 OR ca.contract_value < 0 OR ca.contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN 0 ELSE ca.contract_value END) as total_spend,
                AVG(CASE WHEN ca.is_framework = 1 OR ca.contract_value < 0 OR ca.contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN NULL ELSE ca.contract_value END) as avg_contract_value,
                SUM(CASE WHEN ca.is_framework = 1 THEN ca.contract_value ELSE 0 END) as framework_total_ceiling,
                COUNT(CASE WHEN ca.is_framework = 1 THEN 1 ELSE NULL END) as framework_appointments,
                MIN(ca.date_signed) as earliest_award,
                MAX(ca.date_signed) as latest_award,
                SUM(CASE WHEN ca.is_competitive = 0 THEN 1 ELSE 0 END) as direct_awards,
                SUM(CASE WHEN ca.is_competitive = 1 THEN 1 ELSE 0 END) as competitive_awards,
                COUNT(DISTINCT ca.supplier_name) as unique_suppliers,
                MAX(ca.buyer_type) as buyer_type,
                (ARRAY_AGG(ca.latitude ORDER BY ca.date_signed DESC NULLS LAST) FILTER (WHERE {_REAL_COORD_SQL}))[1] as latitude,
                (ARRAY_AGG(ca.longitude ORDER BY ca.date_signed DESC NULLS LAST) FILTER (WHERE {_REAL_COORD_SQL}))[1] as longitude
            FROM deduped_awards ca
            WHERE {norm_match_sql}
        """
        _execute(cursor, conn, stats_query, [authority_name] * BUYER_CTE_PARAMS)
        stats_row = cursor.fetchone()

        if not stats_row or not stats_row[0]:
            # Try partial substring match
            like_cte = deduped_awards_cte_sql("LOWER(authority_name) LIKE ?")
            like_query = stats_query.replace(buyer_cte, like_cte).replace(norm_match_sql, "LOWER(ca.authority_name) LIKE ?")
            like = f"%{authority_name.lower().strip()}%"
            _execute(cursor, conn, like_query, [like] * BUYER_CTE_PARAMS)
            stats_row = cursor.fetchone()

        if not stats_row or not stats_row[0]:
            b_type = classify_buyer_type(authority_name)
            return jsonify({
                "ok": True,
                "authority_name": authority_name,
                "buyer_type": b_type,
                "stats": {
                    "total_contracts": 0,
                    "total_spend": 0.0,
                    "avg_contract_value": 0.0,
                    "repeat_supplier_display": "No contract awards tracked yet",
                    "sme_pct_display": "N/A",
                    "direct_awards": 0,
                    "competitive_awards": 0,
                    "region": "UK Public Sector Authority"
                },
                "top_suppliers": [],
                "cpv_breakdown": [],
                "upcoming_renewals": [],
                "open_opportunities": _open_opportunities_or_empty(cursor, conn, authority_name),
                "contact_info": _fetch_buyer_contact_info(cursor, conn, authority_name),
                "grants": grants_for_funder(cursor, authority_name),
            })
        
        stats = _row_to_dict(cursor, stats_row)
        total_contracts = stats.get("total_contracts", 0)
        unique_suppliers = stats.get("unique_suppliers", 0)
        buyer_type = stats.get("buyer_type") or classify_buyer_type(authority_name)

        # Apply 5+ award sample floor
        has_enough_data = total_contracts >= 5
        if has_enough_data and unique_suppliers > 0:
            repeat_supplier_pct = round(((total_contracts - unique_suppliers) / total_contracts) * 100, 1)
            repeat_supplier_display = f"{repeat_supplier_pct}%"
        else:
            repeat_supplier_pct = None
            repeat_supplier_display = "Not enough data yet"

        # 2. Top suppliers awarded to
        # Framework agreements' contract_value is a shared ceiling across every appointed
        # supplier, not this supplier's own spend — historical scrapes duplicated that same
        # ceiling identically across every co-appointee (docs/MULTI_LOT_AWARDS_FIX.md), so
        # summing it in unweighted would rank suppliers by a copy-pasted framework total
        # instead of what the buyer actually paid them. Exclude it here exactly as total_spend
        # / avg_contract_value already do above, and surface it separately instead.
        # A single non-framework award above SINGLE_AWARD_STATS_CEILING_GBP is untrustworthy
        # scraper/source noise (see that constant's docstring in etenders_scraper/awards.py) --
        # excluded from total_value here too, same as every other aggregation path.
        top_suppliers_query = f"""
            WITH {buyer_cte}
            SELECT
                supplier_name,
                COUNT(DISTINCT id) as contracts_won,
                SUM(CASE WHEN is_framework = 1 OR contract_value < 0 OR contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN 0 ELSE contract_value END) as total_value,
                SUM(CASE WHEN is_framework = 1 THEN contract_value ELSE 0 END) as framework_total_ceiling,
                COUNT(CASE WHEN is_framework = 1 THEN 1 END) as framework_appointments,
                MAX(date_signed) as last_award_date
            FROM deduped_awards
            WHERE {_norm_auth_sql("authority_name")} = {_norm_auth_sql("?")}
              AND supplier_name IS NOT NULL
              AND supplier_name != ''
            GROUP BY supplier_name
            ORDER BY total_value DESC NULLS LAST, framework_total_ceiling DESC NULLS LAST, contracts_won DESC
            LIMIT 60
        """
        _execute(cursor, conn, top_suppliers_query, [authority_name] * BUYER_CTE_PARAMS)
        top_suppliers = _tidy_top_suppliers([_row_to_dict(cursor, row) for row in cursor.fetchall()])

        # 3. SME / VCSE ratio
        sme_query = f"""
            WITH {buyer_cte}
            SELECT
                sme_status,
                COUNT(DISTINCT ca.id) as count,
                SUM(ca.contract_value) as total_value
            FROM deduped_awards ca
            LEFT JOIN suppliers s ON ca.supplier_id = s.id
            WHERE {_norm_auth_sql("ca.authority_name")} = {_norm_auth_sql("?")}
            GROUP BY sme_status
        """
        _execute(cursor, conn, sme_query, [authority_name] * BUYER_CTE_PARAMS)
        sme_rows = cursor.fetchall()
        sme_breakdown = [_row_to_dict(cursor, r) for r in sme_rows]
        
        sme_count = sum(s.get("count", 0) for s in sme_breakdown if s.get("sme_status") == "SME")
        if has_enough_data and total_contracts > 0:
            sme_pct = round((sme_count / total_contracts) * 100, 1)
            sme_pct_display = f"{sme_pct}%"
        else:
            sme_pct = None
            sme_pct_display = "Not enough data yet"

        # 4. Sector (CPV) breakdown. Same framework-ceiling exclusion as total_spend above --
        # without it, one framework award in a CPV category inflates that category's total_value
        # by the framework's full ceiling (e.g. a real £0 award shown elsewhere on the page).
        # Awards with no CPV code (common for smaller/advisory contracts) are bucketed under a
        # single "Uncategorized" group rather than dropped -- excluding them previously meant the
        # chart could be missing real, non-framework awards entirely while still totalling £0,
        # since the only rows left visible were the zeroed-out framework ones.
        #
        # "Uncategorized" is still sorted last (see the CASE in ORDER BY), not by count, even
        # though it's included in the same GROUP BY as real sectors. A buyer with few distinct
        # real CPV codes (e.g. 1 award each) but 2+ award with no CPV at all would otherwise let
        # the "don't know" bucket outrank and visually lead the chart as the buyer's #1 "sector"
        # -- confirmed directly against Places for London Limited, where the live Find a Tender
        # OCDS API returns a completely empty tender.classification/items for both of its
        # no-CPV awards (genuine source-data gap, not an extraction bug), yet "Uncategorized"
        # still out-counted both of its real, classified sectors 2-to-1.
        # NB: the title tests use POSITION(), not LIKE '%word%'. This query runs through
        # _execute() with a bound parameter, and psycopg2 reads every literal "%" in the SQL as a
        # format specifier -- LIKE '%financial%' raised "IndexError: list index out of range" on
        # every call, so every buyer's full profile came back as an HTTP 500 and rendered as £0.
        # The inferred sector only applies when the award has NO CPV code: an award with a real
        # code keeps its own description (or "Not available") rather than one guessed from the title.
        cpv_code_expr = """
            CASE
                WHEN cpv_code IS NOT NULL AND cpv_code != '' THEN cpv_code
                WHEN POSITION('financial' IN LOWER(tender_title)) > 0 THEN '66171000'
                WHEN POSITION('risk' IN LOWER(tender_title)) > 0 THEN '79417000'
                WHEN POSITION('legal' IN LOWER(tender_title)) > 0 THEN '79111000'
                WHEN POSITION('construction' IN LOWER(tender_title)) > 0 OR POSITION('building' IN LOWER(tender_title)) > 0 THEN '45000000'
                WHEN POSITION('software' IN LOWER(tender_title)) > 0 OR POSITION('cloud' IN LOWER(tender_title)) > 0 THEN '72000000'
                ELSE 'uncategorized'
            END
        """
        cpv_desc_expr = """
            CASE
                WHEN cpv_code IS NOT NULL AND cpv_code != '' THEN COALESCE(NULLIF(cpv_description, ''), 'Not available')
                WHEN POSITION('financial' IN LOWER(tender_title)) > 0 THEN 'Financial consultancy services'
                WHEN POSITION('risk' IN LOWER(tender_title)) > 0 THEN 'Risk management services'
                WHEN POSITION('legal' IN LOWER(tender_title)) > 0 THEN 'Legal advisory services'
                WHEN POSITION('construction' IN LOWER(tender_title)) > 0 OR POSITION('building' IN LOWER(tender_title)) > 0 THEN 'Construction & building works'
                WHEN POSITION('software' IN LOWER(tender_title)) > 0 OR POSITION('cloud' IN LOWER(tender_title)) > 0 THEN 'IT & software development services'
                ELSE 'Uncategorized / not classified'
            END
        """
        cpv_query = f"""
            WITH {buyer_cte}
            SELECT
                {cpv_code_expr} as cpv_code,
                {cpv_desc_expr} as cpv_description,
                COUNT(DISTINCT id) as count,
                SUM(CASE WHEN is_framework = 1 OR contract_value < 0 OR contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN 0 ELSE contract_value END) as total_value
            FROM deduped_awards
            WHERE {_norm_auth_sql("authority_name")} = {_norm_auth_sql("?")}
            GROUP BY {cpv_code_expr}, {cpv_desc_expr}
            ORDER BY CASE WHEN {cpv_code_expr} = 'uncategorized' THEN 1 ELSE 0 END,
                     count DESC
            LIMIT 8
        """
        _execute(cursor, conn, cpv_query, [authority_name] * BUYER_CTE_PARAMS)
        cpv_breakdown = [_row_to_dict(cursor, row) for row in cursor.fetchall()]

        # 5. Upcoming Renewals for this buyer
        today_str = datetime.now().strftime("%Y-%m-%d")
        renewals_query = f"""
            SELECT
                id,
                tender_title,
                supplier_name,
                contract_value,
                date_signed,
                contract_start_date,
                contract_end_date,
                cpv_code,
                cpv_description,
                notice_url,
                is_framework
            FROM contract_awards
            WHERE {_norm_auth_sql("authority_name")} = {_norm_auth_sql("?")}
              AND contract_end_date IS NOT NULL
              AND contract_end_date != ''
              AND contract_end_date >= ?
            ORDER BY contract_end_date ASC
            LIMIT 15
        """
        _execute(cursor, conn, renewals_query, [authority_name, today_str])
        upcoming_renewals = []
        for r in cursor.fetchall():
            rec = _row_to_dict(cursor, r)
            end_d = rec.get("contract_end_date") or ""
            days_left = _days_until(end_d) if end_d else None
            rec["days_left"] = days_left
            upcoming_renewals.append(rec)

        # 6. Contract history
        history_query = f"""
            SELECT
                id,
                supplier_name,
                tender_title,
                contract_value,
                currency,
                date_signed,
                contract_start_date,
                contract_end_date,
                cpv_code,
                cpv_description,
                procurement_type,
                is_competitive,
                is_framework,
                notice_url
            FROM contract_awards
            WHERE {_norm_auth_sql("authority_name")} = {_norm_auth_sql("?")}
            ORDER BY date_signed DESC NULLS LAST
            LIMIT 50
        """
        _execute(cursor, conn, history_query, [authority_name])
        contract_history = [_row_to_dict(cursor, row) for row in cursor.fetchall()]

        # 6b. Live tenders still open for bids
        open_opportunities = _open_opportunities_or_empty(cursor, conn, authority_name)

        # 7. Honest contact details (no cross-contamination or fabrication) — sourced
        # from the same tenders_master cache as open_opportunities above, never guessed.
        contact_info = _fetch_buyer_contact_info(cursor, conn, authority_name)

        return jsonify({
            "ok": True,
            "authority_name": authority_name,
            "buyer_type": buyer_type,
            "sample_size_floor_met": has_enough_data,
            "stats": {
                "total_contracts": total_contracts,
                "total_spend": float(stats.get("total_spend") or 0.0),
                "avg_contract_value": float(stats.get("avg_contract_value") or 0.0),
                "framework_total_ceiling": float(stats.get("framework_total_ceiling") or 0.0),
                "framework_appointments": stats.get("framework_appointments", 0),
                "earliest_award": stats.get("earliest_award"),
                "latest_award": stats.get("latest_award"),
                "direct_awards": stats.get("direct_awards", 0),
                "competitive_awards": stats.get("competitive_awards", 0),
                "unique_suppliers": unique_suppliers,
                "repeat_supplier_pct": repeat_supplier_pct,
                "repeat_supplier_display": repeat_supplier_display,
                "sme_pct": sme_pct,
                "sme_pct_display": sme_pct_display
            },
            "top_suppliers": top_suppliers,
            "cpv_breakdown": cpv_breakdown,
            "upcoming_renewals": upcoming_renewals,
            "contract_history": contract_history,
            "open_opportunities": open_opportunities,
            "contact_info": contact_info,
            "grants": grants_for_funder(cursor, authority_name),
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


# ── OPPORTUNITY RADAR (TAB 2) ─────────────────────────────────────────────

RADAR_DEFAULT_MIN_DAYS = 60    # a renewal closer than this is too late to influence
RADAR_DEFAULT_MAX_DAYS = 365


def _profile_services(cursor, conn, username: str, profile_id) -> list[str]:
    """CPV divisions the selected (else default) company profile sells in, read from its name and text; [] with no profile or no match."""
    from tender_app import buyer_fit as bf
    try:
        if profile_id and str(profile_id).isdigit():
            _execute(cursor, conn, "SELECT name, profile_text FROM company_profiles WHERE username = ? AND id = ?", [username, int(profile_id)])
            row = cursor.fetchone()
        else:
            row = None
        if not row:
            _execute(cursor, conn, "SELECT name, profile_text FROM company_profiles WHERE username = ? ORDER BY is_default DESC, id DESC LIMIT 1", [username])
            row = cursor.fetchone()
    except Exception:
        conn.rollback()
        return []
    if not row or not (row[1] or "").strip():
        return []
    return bf.infer_divisions(f"{row[0] or ''} {row[1]}")


@buyers_bp.route("/api/buyers/opportunity-radar", methods=["GET"])
def get_opportunity_radar():
    """
    Returns unified table of all upcoming contract renewals across buyers.
    Sortable by days-left, contract value, or buyer name.
    """
    q = (request.args.get("q") or "").strip()
    buyer_type_filter = (request.args.get("buyer_type") or "").strip()
    min_spend = float(request.args.get("min_spend") or 0.0)
    radius_km = float(request.args.get("radius") or 0.0)
    renewals_soon_only = request.args.get("renewals_soon", "").lower() in ("1", "true")
    open_to_new_only = request.args.get("open_to_new", "").lower() in ("1", "true")
    frameworks_only = request.args.get("frameworks_only", "").lower() in ("1", "true")
    sort_by = (request.args.get("sort") or "days_left").lower()  # days_left, value, buyer_name
    # the Buyer page sends the same sort to the radar as to the feed; best_match and soonest_renewal mean "ends soonest" here
    if sort_by not in ("days_left", "value", "highest_value", "buyer_name", "name", "spend", "best_match", "soonest_renewal"):
        return jsonify({"ok": False, "error": "sort must be one of: days_left, value, buyer_name"}), 422
    # How far ahead, which categories and which page: a company engages 2 to 12 months before a contract ends, in its own CPV divisions.
    try:
        min_days = max(0, int(request.args.get("min_days") or 0))
        max_days = int(request.args["max_days"]) if request.args.get("max_days") not in (None, "") else None
        page = max(1, int(request.args.get("page") or 1))
        per_page = min(max(int(request.args.get("per_page") or 0), 0), 300)
    except ValueError:
        return jsonify({"ok": False, "error": "min_days, max_days, page and per_page must be whole numbers"}), 422
    cpv_divisions = [d for d in re.split(r"[,\s]+", request.args.get("cpv") or "") if re.fullmatch(r"\d{2}", d)]
    # The radar opens on the selected profile's own services and on renewals 60 to 365 days out; "All categories" and "Ending soon" are
    # filters that widen it (re-test 3 B9). A cpv, min_days or max_days in the request still wins.
    all_categories = request.args.get("all_categories", "").lower() in ("1", "true", "yes")
    ending_soon = request.args.get("ending_soon", "").lower() in ("1", "true", "yes")
    if not request.args.get("min_days"):
        min_days = 0 if ending_soon else RADAR_DEFAULT_MIN_DAYS
    if max_days is None:
        max_days = RADAR_DEFAULT_MAX_DAYS
    services: list[str] = []

    user_lat_param = request.args.get("user_lat")
    user_lon_param = request.args.get("user_lon")
    try:
        user_lat = float(user_lat_param) if user_lat_param is not None else 51.5074
        user_lon = float(user_lon_param) if user_lon_param is not None else -0.1278
    except (ValueError, TypeError):
        user_lat, user_lon = (51.5074, -0.1278)

    conn = _get_connection()
    cursor = conn.cursor()

    try:
        if not cpv_divisions and not all_categories:
            cpv_divisions = _profile_services(cursor, conn, request.args.get("username") or session.get("username") or session.get("user")
                                              or session.get("email") or "admin", request.args.get("profile_id"))
            services = list(cpv_divisions)
        from tender_app import stats as _stats
        today_str = (_uk_today() + timedelta(days=min_days)).strftime("%Y-%m-%d")
        # the de-duplicated awards when that table is built: one row per award, and a framework's value never shown as an order's
        use_table = _stats.award_values_ready(cursor)
        source_sql = "deduped_awards ca" if use_table else "contract_awards ca"
        with_sql = f"WITH {_stats.deduped_cte(cursor)} " if use_table else ""

        where_clauses = [
            "ca.contract_end_date IS NOT NULL",
            "ca.contract_end_date != ''",
            "ca.contract_end_date >= ?"
        ]
        params = [today_str]
        if max_days is not None:
            where_clauses.append("ca.contract_end_date <= ?")
            params.append((_uk_today() + timedelta(days=max_days)).strftime("%Y-%m-%d"))
        if cpv_divisions:
            where_clauses.append("LEFT(ca.cpv_code, 2) = ANY(?)")
            params.append(cpv_divisions)

        if q:
            search_pattern = f"%{q.lower()}%"
            where_clauses.append("(LOWER(ca.authority_name) LIKE ? OR LOWER(COALESCE(ca.tender_title, '')) LIKE ? OR LOWER(COALESCE(ca.cpv_description, '')) LIKE ? OR LOWER(COALESCE(ca.cpv_code, '')) LIKE ? OR LOWER(COALESCE(ca.supplier_name, '')) LIKE ?)")
            params.extend([search_pattern, search_pattern, search_pattern, search_pattern, search_pattern])

        if buyer_type_filter and buyer_type_filter != "all":
            where_clauses.append("LOWER(TRIM(ca.buyer_type)) = LOWER(TRIM(?))")
            params.append(buyer_type_filter)

        if min_spend > 0:
            where_clauses.append("ca.contract_value >= ?")
            params.append(min_spend)

        if frameworks_only:
            where_clauses.append("ca.is_framework = 1")

        if renewals_soon_only:
            cutoff = (datetime.now() + timedelta(days=180)).strftime("%Y-%m-%d")
            where_clauses.append("ca.contract_end_date <= ?")
            params.append(cutoff)

        where_sql = " AND ".join(where_clauses)

        query = f"""
            {with_sql}SELECT
                ca.id,
                ca.authority_name,
                ca.buyer_type,
                ca.tender_title,
                ca.supplier_name as current_supplier,
                ca.contract_value,
                ca.contract_start_date,
                ca.contract_end_date,
                ca.cpv_code,
                ca.cpv_description,
                ca.is_framework,
                ca.notice_url,
                ca.latitude,
                ca.longitude
            FROM {source_sql}
            WHERE {where_sql}
        """

        # a renewal with no CPV code cannot be matched to anyone's services: it never leads the list (181 of the first 300 rows had none)
        no_cpv_last = "(ca.cpv_code IS NULL OR TRIM(ca.cpv_code) = '') ASC, "
        if sort_by in ("value", "highest_value", "spend"):
            query += f" ORDER BY {no_cpv_last}ca.contract_value DESC NULLS LAST, ca.id"
        elif sort_by in ("buyer_name", "name"):
            query += f" ORDER BY {no_cpv_last}ca.authority_name ASC, ca.id"
        else:
            query += f" ORDER BY {no_cpv_last}ca.contract_end_date ASC, ca.id"

        # A distance filter needs per-row distance, which is computed in Python below, so capping
        # the query first would drop real matches before that filter saw them: with a radius we
        # fetch everything. Without one, the response is simply the first `row_cap` rows in SQL
        # order, so fetch only those (85,000 upcoming renewals took ~5 s to build and discard) and
        # count the rest separately. fast=1 asks for a smaller first screenful.
        row_cap = per_page or (60 if request.args.get("fast", "").lower() in ("1", "true", "yes") else 300)
        sql_total = None
        if radius_km <= 0:
            _execute(cursor, conn, f"{with_sql}SELECT COUNT(*) FROM {source_sql} WHERE {where_sql}", list(params))
            sql_total = cursor.fetchone()[0]
            query += f" LIMIT {int(row_cap)} OFFSET {int((page - 1) * row_cap)}"
        _execute(cursor, conn, query, params)
        rows = cursor.fetchall()

        cols = [desc[0] for desc in cursor.description] if cursor.description else []
        row_dicts = [dict(zip(cols, r)) if not hasattr(r, "keys") else dict(r) for r in rows]

        renewals = []
        now = datetime.now()

        for rec in row_dicts:
            auth_name = rec.get("authority_name") or ""
            b_type = rec.get("buyer_type") or classify_buyer_type(auth_name)

            raw_lat = float(rec.get("latitude") or 0.0)
            raw_lon = float(rec.get("longitude") or 0.0)
            b_lat, b_lon, coords_approx = resolve_authority_coords(auth_name, raw_lat, raw_lon)
            dist_km = haversine_km(user_lat, user_lon, b_lat, b_lon)
            if radius_km > 0 and dist_km > radius_km:
                continue

            end_d = rec.get("contract_end_date") or ""
            days_left = _days_until(end_d) if end_d else None
            days_left = 999 if days_left is None else days_left

            renewals.append({
                "id": rec.get("id"),
                "authority_name": auth_name,
                "buyer_type": b_type,
                "tender_title": rec.get("tender_title") or "Public Sector Contract",
                "current_supplier": rec.get("current_supplier") or "Not disclosed",
                "contract_value": float(rec.get("contract_value") or 0.0),
                "value_published": rec.get("contract_value") is not None and float(rec.get("contract_value") or 0) > 0,
                "contract_start_date": rec.get("contract_start_date") or "Not specified",
                "contract_end_date": end_d,
                "days_left": days_left,
                "distance_km": round(dist_km, 1) if dist_km < 9999 else None,
                "distance_approximate": coords_approx if dist_km < 9999 else None,
                "cpv_code": rec.get("cpv_code") or "",
                "cpv_description": rec.get("cpv_description") if rec.get("cpv_description") not in (None, "", "Not available") else (
                    _bf_label(rec.get("cpv_code")) if rec.get("cpv_code") else "Not available"),
                "is_framework": bool(rec.get("is_framework")),
                "notice_url": rec.get("notice_url") or "#"
            })

        total_matches = sql_total if sql_total is not None else len(renewals)

        if radius_km > 0:
            renewals = renewals[(page - 1) * row_cap: page * row_cap]
        return jsonify({
            "ok": True,
            "renewals": renewals,
            "total": total_matches,
            "page": page,
            "pages": max(1, -(-total_matches // row_cap)) if row_cap else 1,
            "fast": row_cap < 300,
            "approximate": False,  # a fast response is the same list, just shorter
            "scope": {
                "services": services, "service_labels": [buyer_fit.division_label(d) for d in services],
                "all_categories": all_categories or not services, "ending_soon": ending_soon,
                "min_days": min_days, "max_days": max_days,
            },
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


# ── OPPORTUNITY FEED (TAB 1) — TIERED PERSONALIZATION ─────────────────────

def _bf_label(cpv_code: str | None) -> str:
    """A readable category for a CPV code whose description the notice left out: the division's name."""
    from tender_app import buyer_fit
    code = str(cpv_code or "").strip()
    return buyer_fit.division_label(code[:2]).capitalize() if len(code) >= 2 and code[:2].isdigit() else "Not available"


def _uk_today() -> "datetime.date":
    """Today's date in the UK. Day counts are date minus date: subtracting a clock time dropped a day ("2 days" for a date 3 days away)."""
    from tender_app.uk_time import london_now
    return london_now().date()


def _days_until(day: str | None) -> int | None:
    """Whole days from today (UK) to a YYYY-MM-DD date; None when it is not a date."""
    try:
        return (datetime.strptime(str(day)[:10], "%Y-%m-%d").date() - _uk_today()).days
    except (ValueError, TypeError):
        return None


# A short name people type for a body whose published name is long: the search looks for the long one.
BUYER_ACRONYMS = {
    "tfl": "transport for london", "mod": "ministry of defence", "moj": "ministry of justice", "dwp": "department for work and pensions",
    "hmrc": "hm revenue", "dfe": "department for education", "dhsc": "department of health and social care", "nhse": "nhs england",
    "ccs": "crown commercial service", "ypo": "yorkshire purchasing organisation", "espo": "eastern shires purchasing organisation",
    "dsit": "department for science, innovation and technology", "desnz": "department for energy security",
    "defra": "department for environment, food and rural affairs", "fco": "foreign, commonwealth", "fcdo": "foreign, commonwealth",
    "cqc": "care quality commission", "dvla": "driver and vehicle licensing", "hmcts": "hm courts",
}
BUYER_SORTS = ("best_match", "spend", "highest_value", "soonest_renewal", "days_left", "name", "buyer_name")


def _name_relevance(name: str, term: str) -> int:
    """0 = the typed name is the whole name, 1 = it starts the name or is a whole word in it, 2 = inside a word."""
    n, t = " ".join(name.lower().split()), " ".join(term.lower().split())
    if not t:
        return 2
    plain = re.sub(r"^the\s+", "", n)
    if n == t or plain == t:
        return 0
    if n.startswith(t) or plain.startswith(t):
        return 1
    return 1 if re.search(r"(^|[^a-z0-9])" + re.escape(t) + r"([^a-z0-9]|$)", n) else 2


def _buyer_items_from_stats(cursor, conn, q: str, buyer_type_filter: str, min_spend: float, keys: list[str] | None = None,
                            division: str | None = None):
    """Feed rows from buyer_stats: the same fields the live aggregate returns, without
    aggregating every award. Searching matches the buyer's name only; text on the awards (CPV
    descriptions) is what the exact path adds."""
    where = ["total_spend >= ?"]
    params: list[Any] = [min_spend]
    if keys is not None:  # the exact search picked these buyers; their figures still come from buyer_stats
        if not keys:
            return []
        where.append("authority_key = ANY(?)")
        params.append(list(keys))
    if division:  # a CPV code or a service word: the buyers with awards in that division
        where.append("jsonb_exists(cpv_shares, ?)")
        params.append(division)
    elif q:
        where.append("LOWER(authority_name) LIKE ?")
        params.append(f"%{q.lower()}%")
    if buyer_type_filter and buyer_type_filter != "all":
        where.append("LOWER(TRIM(buyer_type)) = LOWER(TRIM(?))")
        params.append(buyer_type_filter)
    _execute(cursor, conn, f"""
        SELECT authority_name, buyer_type, total_contracts, total_spend, unique_suppliers,
               next_renewal_date, latitude, longitude, cpv_divisions, cpv_shares, next_renewal_actionable,
               to_jsonb(buyer_stats) -> 'renewals_by_division' AS renewals_by_division,  -- null until the first rebuild adds the column
               to_jsonb(buyer_stats) -> 'cpv_spend' AS cpv_spend, to_jsonb(buyer_stats) ->> 'next_renewal_title' AS next_renewal_title
        FROM buyer_stats WHERE {" AND ".join(where)}
    """, params)
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


_buyer_names_cache: dict[str, Any] = {"at": 0.0, "names": []}


def _buyer_did_you_mean(cursor, conn, q: str) -> dict[str, str] | None:
    """A near-miss spelling of a buyer name or one of its words ("Mancester" -> "Manchester"), from the buyers that let the most
    contracts. None when nothing is close. Names are kept for 30 minutes."""
    import difflib
    now = time.time()
    if now - _buyer_names_cache["at"] > 1800 or not _buyer_names_cache["names"]:
        try:
            _execute(cursor, conn, "SELECT authority_name FROM buyer_stats WHERE total_contracts >= 10 ORDER BY total_contracts DESC LIMIT 6000", [])
            _buyer_names_cache["names"] = [r[0] for r in cursor.fetchall() if r[0]]
            _buyer_names_cache["at"] = now
        except Exception:
            return None
    term = " ".join(q.lower().split())
    if len(term) < 4:
        return None
    words: dict[str, str] = {}
    for name in _buyer_names_cache["names"]:
        for w in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", name):
            words.setdefault(w.lower(), w)
    hit = difflib.get_close_matches(term, list(words), n=1, cutoff=0.8)
    if hit and hit[0] != term:
        return {"name": words[hit[0]].title() if words[hit[0]].isupper() else words[hit[0]]}
    lowered = {n.lower(): n for n in _buyer_names_cache["names"]}
    hit = difflib.get_close_matches(term, list(lowered), n=1, cutoff=0.8)
    return {"name": lowered[hit[0]]} if hit and hit[0] != term else None


@buyers_bp.route("/api/buyers/opportunity-feed", methods=["GET"])
def get_opportunity_feed():
    """
    Returns personalized recommendation cards for the Opportunity Feed tab.
    
    Personalization Tiers:
    - Tier 1 (preferred): Matches user's Pipeline 'Won' tenders (shared CPV/sector code and region).
    - Tier 2 (fallback when user has 0 won tenders): Matches user's Selected Company Profile (declared trade/sector and region).
    
    Top banner text adapts dynamically per tier.
    Match scores are calculated using a documented formula.
    """
    # The frontend never actually sends a `username` query param (see refreshBuyerIntelligenceView
    # in app.js), so this fell back to the literal string "demo_user" on every request — and no
    # real account's company_profiles rows are stored under that username, so the profile lookup
    # below always missed and every user saw "No company profile selected" regardless of which
    # profile was active elsewhere in the app. Read it from the session instead, matching how
    # profiles_bp.py (which owns the company_profiles table) resolves the same user.
    username = request.args.get("username") or session.get("username") or session.get("user") or session.get("email") or "admin"
    q = (request.args.get("q") or "").strip()
    category_filter = (request.args.get("category") or "all").lower()  # all, renewals, similar, open_to_new, near_you
    buyer_type_filter = (request.args.get("buyer_type") or "").strip()
    min_spend = float(request.args.get("min_spend") or 0.0)
    radius_km = float(request.args.get("radius") or 0.0)
    user_lat_param = request.args.get("user_lat")
    user_lon_param = request.args.get("user_lon")
    try:
        user_lat = float(user_lat_param) if user_lat_param is not None else 51.5074
        user_lon = float(user_lon_param) if user_lon_param is not None else -0.1278
    except (ValueError, TypeError):
        user_lat, user_lon = (51.5074, -0.1278)

    renewals_soon_only = request.args.get("renewals_soon", "").lower() in ("1", "true")
    open_to_new_only = request.args.get("open_to_new", "").lower() in ("1", "true")
    frameworks_only = request.args.get("frameworks_only", "").lower() in ("1", "true")
    sort_by = (request.args.get("sort") or "best_match").lower()
    if sort_by not in BUYER_SORTS:
        return jsonify({"ok": False, "error": "sort must be one of: " + ", ".join(BUYER_SORTS)}), 422
    try:
        min_match = max(0, min(100, int(request.args.get("min_match") or 0)))
    except ValueError:
        min_match = 0
    fast = request.args.get("fast", "").lower() in ("1", "true", "yes")
    show_all = request.args.get("show_all", "").lower() in ("1", "true", "yes")
    # What was typed: an acronym stands for its long name ("TfL"), a CPV code or a service word asks for buyers that buy it, and a
    # short word matches whole words only ("MOD" is not "modern").
    from tender_app import buyer_fit as _bf
    typed = q
    search_division = None
    if q:
        low_q = q.lower()
        if low_q in BUYER_ACRONYMS:
            q = BUYER_ACRONYMS[low_q]
        elif re.fullmatch(r"\d{2,8}", q):
            search_division = q[:2]
        else:
            words_div = _bf.infer_divisions(q) if len(q.split()) <= 3 and low_q in {w for ws in _bf.SECTOR_WORDS.values() for w in ws} else []
            if words_div:
                search_division = words_div[0]

    conn = _get_connection()
    cursor = conn.cursor()

    try:
        from tender_app import stats as _stats
        _stats.ensure_fresh_async(_get_connection)  # background; never blocks this request

        # 1. Fetch User Context for Personalization
        won_cpvs = set()
        user_region = "UK"
        company_name = "Your Company"
        company_sectors = []
        company_divisions: list[str] = []

        # Check Pipeline for Won Tenders (Tier 1)
        try:
            _execute(cursor, conn, """
                SELECT p.tender_key, p.title, p.contracting_authority 
                FROM pipeline p
                WHERE p.username = ? AND LOWER(p.stage) = 'won'
            """, [username])
            won_rows = cursor.fetchall()
            for r in won_rows:
                t_key, t_title, t_auth = r[0], r[1], r[2]
                if t_key:
                    _execute(cursor, conn, "SELECT cpv_code, country FROM tenders_master WHERE id = ?", [t_key])
                    tm_row = cursor.fetchone()
                    if tm_row:
                        if tm_row[0]: won_cpvs.add(tm_row[0][:2])
                        if tm_row[1] and tm_row[1] != 'UK': user_region = tm_row[1]
                if t_auth:
                    _execute(cursor, conn, "SELECT cpv_code FROM contract_awards WHERE authority_name = ? AND cpv_code != '' LIMIT 3", [t_auth])
                    ca_rows = cursor.fetchall()
                    for ca_r in ca_rows:
                        if ca_r[0]: won_cpvs.add(ca_r[0][:2])
        except Exception as _p_err:
            conn.rollback()

        # Check Company Profile for Fallback (Tier 2)
        has_profile = False
        profile_id_param = request.args.get("profile_id")
        try:
            cp_row = None
            if profile_id_param and str(profile_id_param).isdigit():
                _execute(cursor, conn, """
                    SELECT name, profile_text, meta_json
                    FROM company_profiles
                    WHERE username = ? AND id = ?
                """, [username, int(profile_id_param)])
                cp_row = cursor.fetchone()
            if not cp_row:
                _execute(cursor, conn, """
                    SELECT name, profile_text, meta_json
                    FROM company_profiles
                    WHERE username = ?
                    ORDER BY is_default DESC, id DESC LIMIT 1
                """, [username])
                cp_row = cursor.fetchone()
            if cp_row:
                cp_dict = _row_to_dict(cursor, cp_row)
                company_name = cp_dict.get("name") or company_name
                sec = cp_dict.get("profile_text") or ""
                if sec: 
                    company_sectors = [s.strip().lower() for s in sec.split() if len(s.strip()) > 3]
                    has_profile = True  # Only set true if profile_text is not empty
                    company_divisions = _bf.infer_divisions(f"{company_name} {sec}")
        except Exception:
            conn.rollback()

        # Determine Tier - only apply personalization if user has won CPVs OR a valid company profile
        if len(won_cpvs) > 0:
            tier = 1
            banner_text = f"Personalised for {company_name} — using your Pipeline win history, region and sector"
        elif has_profile:
            tier = 2
            banner_text = f"Personalised for {company_name} — using your company profile"
        else:
            tier = 0  # No personalization - show all buyers
            banner_text = "All Buyers — No company profile selected"

        # 2. Query Buyers with Aggregates
        today_str = _uk_today().strftime("%Y-%m-%d")
        actionable_str = (_uk_today() + timedelta(days=60)).strftime("%Y-%m-%d")  # the first renewal a company can still act on
        horizon_str = (_uk_today() + timedelta(days=365)).strftime("%Y-%m-%d")  # renewals later than this earn no points
        # Instant path: read the precomputed buyer_stats (about 50 ms instead of ~5 s). Exact unless
        # the request searches text, which then only matches buyer names (approximate=true); the
        # client follows up without fast=1 to get the full answer in the background. Framework-only
        # views need per-award filtering, so they always take the live path below.
        use_stats = (not frameworks_only) and _stats.is_ready(cursor, "buyer_stats") and (fast or not q or bool(search_division))
        approximate = bool(use_stats and q and not search_division)
        buyer_items = _buyer_items_from_stats(cursor, conn, q, buyer_type_filter, min_spend, division=search_division) if use_stats else None
        rows_from_stats = buyer_items is not None  # figures (and cpv_divisions) came from buyer_stats
        if buyer_items is None:
        
            where_clauses = [
                "ca.authority_name IS NOT NULL",
                "TRIM(ca.authority_name) != ''",
                "LENGTH(TRIM(ca.authority_name)) > 2",
                "ca.authority_name NOT IN ('Unknown', 'Not available', 'N/A')"
            ]
            params = []

            if q:
                # When searching, match on authority_name or CPV
                search_pattern = f"%{q.lower()}%"
                where_clauses.append("(LOWER(ca.authority_name) LIKE ? OR LOWER(COALESCE(ca.cpv_description, '')) LIKE ? OR LOWER(COALESCE(ca.cpv_code, '')) LIKE ?)")
                params.extend([search_pattern, search_pattern, search_pattern])

            if buyer_type_filter and buyer_type_filter != "all":
                where_clauses.append("LOWER(TRIM(ca.buyer_type)) = LOWER(TRIM(?))")
                params.append(buyer_type_filter)

            if frameworks_only:
                where_clauses.append("ca.is_framework = 1")

            where_sql = " AND ".join(where_clauses)

            # The exact text search also matches CPV text, which buyer_stats does not hold, so the awards only
            # choose WHICH buyers match; their figures are read from buyer_stats, the same de-duplicated numbers the
            # buyer's search result and profile show. (This path used to aggregate raw rows: 55 awards and a 10.9%
            # repeat rate on the card against 53 and 7.5% on the profile.)
            if q and not search_division and not frameworks_only and _stats.is_ready(cursor, "buyer_stats"):
                _execute(cursor, conn, f"SELECT DISTINCT {_norm_auth_sql('ca.authority_name')} FROM contract_awards ca WHERE {where_sql}", params)
                matched_keys = [r[0] for r in cursor.fetchall()]
                buyer_items = _buyer_items_from_stats(cursor, conn, "", buyer_type_filter, min_spend, keys=matched_keys)
                rows_from_stats = True

        if buyer_items is None:
            # A framework/call-off's contract_value is a shared ceiling across every supplier
            # appointed to it, not spend this buyer has actually committed on any one deal —
            # excluded here exactly like /api/buyers/search and /api/buyers/<name> already do,
            # so this card's Total Spend agrees with the buyer's own profile modal instead of
            # running £100M+ ahead of it. Unless the user has filtered to frameworks-only, in
            # which case that IS the spend they asked to see.
            # A single non-framework award above SINGLE_AWARD_STATS_CEILING_GBP is untrustworthy
            # scraper/source noise (same guard as /api/buyers/search, /api/buyers/<name>, and
            # buyer_stats' own refresh) -- this was the one place still missing it, which is
            # exactly why this card's total could run into the billions while the profile modal
            # (and this feed's own "fast" pass from buyer_stats) showed the correct, excluded figure.
            spend_expr = "SUM(ca.contract_value)" if frameworks_only else f"SUM(CASE WHEN ca.is_framework = 1 OR ca.contract_value < 0 OR ca.contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN 0 ELSE ca.contract_value END)"

            # Group by a case/whitespace-normalized key (see _norm_auth_sql) so the same
            # real buyer published under different casing across portals doesn't fragment
            # into duplicate cards — matches how /api/buyers/search already groups.
            query = f"""
                WITH {_stats.deduped_cte(cursor)}
                SELECT
                    (ARRAY_AGG(TRIM(ca.authority_name) ORDER BY
                        (TRIM(ca.authority_name) = UPPER(TRIM(ca.authority_name))) ASC,
                        LENGTH(ca.authority_name) DESC,
                        TRIM(ca.authority_name) ASC
                    ))[1] as authority_name,
                    MAX(ca.buyer_type) as buyer_type,
                    COUNT(DISTINCT ca.id) as total_contracts,
                    {spend_expr} as total_spend,
                    COUNT(DISTINCT ca.supplier_name) as unique_suppliers,
                    MIN(CASE WHEN ca.contract_end_date >= '{actionable_str}' THEN ca.contract_end_date ELSE NULL END) as next_renewal_date,
                    ARRAY_AGG(DISTINCT LEFT(ca.cpv_code, 2)) FILTER (WHERE ca.cpv_code IS NOT NULL AND ca.cpv_code <> '') as cpv_divisions,
                    (ARRAY_AGG(ca.latitude ORDER BY ca.date_signed DESC NULLS LAST) FILTER (WHERE {_REAL_COORD_SQL}))[1] as latitude,
                    (ARRAY_AGG(ca.longitude ORDER BY ca.date_signed DESC NULLS LAST) FILTER (WHERE {_REAL_COORD_SQL}))[1] as longitude
                FROM deduped_awards ca
                WHERE {where_sql}
                GROUP BY {_norm_auth_sql("ca.authority_name")}
                HAVING COUNT(DISTINCT ca.id) > 0 AND {spend_expr} >= ?
            """
            params.append(min_spend)

            _execute(cursor, conn, query, params)
            rows = cursor.fetchall()
            cols = [desc[0] for desc in cursor.description] if cursor.description else []
            buyer_items = [dict(zip(cols, row)) if not hasattr(row, "keys") else dict(row) for row in rows]

        cards = []
        now = datetime.now()
        hidden_no_sector = 0
        hidden_no_location = 0  # dropped by the distance filter only because the buyer has no known location

        for b in buyer_items:
            # Strip leading/trailing whitespace and stray punctuation (e.g. leading '.')
            raw_name = b.get("authority_name") or ""
            auth_name = raw_name.strip().lstrip(".").strip()
            # Hard gate: skip any card without a real, named buyer
            if not auth_name or len(auth_name) <= 2:
                continue
            b_type = b.get("buyer_type") or classify_buyer_type(auth_name)
            # a search of three letters or fewer matches whole words only: "MOD" is not "modern", "ibm" is not "gibmaroc"
            if q and not search_division and len(q) <= 3 and q.isalpha() and not re.search(
                    r"(^|[^a-z0-9])" + re.escape(q.lower()) + r"([^a-z0-9]|$)", auth_name.lower()):
                continue

            total_c = b.get("total_contracts", 0)
            unique_s = b.get("unique_suppliers", 0)
            total_sp = float(b.get("total_spend") or 0.0)

            # Sample size floor logic for repeat supplier rate
            if total_c >= 5 and unique_s > 0:
                repeat_rate = round(((total_c - unique_s) / total_c) * 100, 1)
                repeat_display = f"{repeat_rate}%"
            else:
                repeat_rate = 0.0
                repeat_display = "Not enough data yet"

            if open_to_new_only and (repeat_rate >= 35.0 or total_c < 5):
                continue

            # Proximity
            raw_lat = float(b.get("latitude") or 0.0)
            raw_lon = float(b.get("longitude") or 0.0)
            b_lat, b_lon, coords_approx = resolve_authority_coords(auth_name, raw_lat, raw_lon)
            dist_km = haversine_km(user_lat, user_lon, b_lat, b_lon)

            if radius_km > 0 and dist_km > radius_km:
                if dist_km >= 9999:
                    hidden_no_location += 1
                continue

            # Renewal date
            next_ren = (b.get("next_renewal_actionable") if rows_from_stats else None) or b.get("next_renewal_date") or ""
            if rows_from_stats and next_ren and next_ren < today_str:
                next_ren = ""  # stats predate today; the next one is unknown until the next refresh
            # With a company profile, only a renewal in the company's own services counts, and the contract is named (re-test 2 B2).
            renewal_contract = ""
            renewal_value = None
            _mine = list(dict.fromkeys(sorted(won_cpvs) + company_divisions))
            _rbd = b.get("renewals_by_division") if rows_from_stats else None
            # Renewal points only for a contract in the company's own services, within the next 12 months (re-test 3 B2).
            renewal_scoped = bool(tier and _mine and isinstance(_rbd, dict))
            if renewal_scoped:
                _best = None
                for _div in _mine:
                    _e = _rbd.get(_div)
                    if isinstance(_e, dict) and _e.get("date") and today_str <= _e["date"] <= horizon_str and (_best is None or _e["date"] < _best["date"]):
                        _best = _e
                next_ren = _best["date"] if _best else ""
                renewal_contract = (_best.get("title") or "").strip() if _best else ""
                renewal_value = _best.get("value") if _best else None
            elif next_ren:
                # no profile services to scope it by: the buyer's first renewal, named from the stats (empty until the next rebuild)
                renewal_contract = (b.get("next_renewal_title") or "").strip() if rows_from_stats else ""
            days_until_renewal = 999
            if next_ren:
                _d = _days_until(next_ren)
                if _d is not None and _d >= 0:
                    days_until_renewal = _d
                else:
                    next_ren = ""

            if renewals_soon_only and (days_until_renewal > 180 or days_until_renewal < 60 or not next_ren):
                continue

            # Match Score Formula Calculation
            # 1. CPV/Sector score (0 to 100)
            cpv_match = False
            sector_note = ""
            my_divisions = list(dict.fromkeys(sorted(won_cpvs) + company_divisions))
            div_awards = 0
            if tier == 0:
                # No personalization - neutral score for all buyers
                cpv_score = 70.0
            elif my_divisions:
                shares = b.get("cpv_shares") if rows_from_stats else None
                labels = ", ".join(_bf.division_label(d) for d in my_divisions[:3])
                if isinstance(shares, dict):
                    share, div_awards, all_awards = _bf.sector_share(shares, my_divisions)
                    sp_share, sp_mine, sp_all = _bf.spend_share(b.get("cpv_spend") if rows_from_stats else None, my_divisions)
                    if sp_all > 0:   # scored on the share of its spend, which spreads buyers far wider than a count of awards
                        cpv_score = _bf.sector_score_by_spend(sp_share, div_awards)
                        sector_note = (f"{round(sp_share * 100)}% of its spend ({_bf.gbp_short(sp_mine)} of {_bf.gbp_short(sp_all)}) is in {labels}; "
                                       f"{div_awards} of its {all_awards} contracts with a CPV code." if div_awards
                                       else f"None of its {all_awards} contracts with a CPV code are in {labels}.")
                    else:
                        cpv_score = _bf.sector_score(share, div_awards)
                        sector_note = (f"{div_awards} of its {all_awards} contracts with a CPV code are in {labels}." if div_awards
                                       else f"None of its {all_awards} contracts with a CPV code are in {labels}.")
                    cpv_match = div_awards > 0
                else:  # the live path holds no per-buyer mix: the divisions it buys in
                    buyer_cpvs = b.get("cpv_divisions") or []
                    cpv_match = any(str(c)[:2] in my_divisions for c in buyer_cpvs)
                    cpv_score = 70.0 if cpv_match else 0.0
                    sector_note = f"Buys in {labels}." if cpv_match else f"Nothing it buys is in {labels}."
                if cpv_score == 0 and not show_all and not search_division:
                    hidden_no_sector += 1
                    continue
            else:
                cpv_score = 50.0
                sector_note = "Add what you sell to your company profile (software, construction, care...) to score sector match."

            # 2. Proximity score (0 to 100)
            prox_score = max(0.0, 100.0 - (dist_km * 0.5)) if dist_km < 9999 else 60.0

            # 3. Renewal score (0 to 100)
            if renewal_scoped:
                renew_score = _bf.renewal_points(days_until_renewal if days_until_renewal != 999 else None, renewal_value)
            else:
                renew_score = _bf.renewal_score(days_until_renewal if days_until_renewal != 999 else None)

            # 4. Openness score (0 to 100)
            openness_score = 100.0 - repeat_rate if total_c >= 5 else 60.0

            # Weighted Formula: 35% Sector + 25% Proximity + 25% Renewal + 15% Openness
            final_match = round((0.35 * cpv_score) + (0.25 * prox_score) + (0.25 * renew_score) + (0.15 * openness_score))
            final_match = min(98, max(5, final_match))
            if min_match and final_match < min_match:
                continue

            # Assign Recommendation Tags
            tags = []
            if 60 <= days_until_renewal <= 180:
                tags.append("Renewal due soon")
            if cpv_match:
                tags.append("Similar to your wins")
            if repeat_rate < 35.0 and total_c >= 5:
                tags.append("Open to new suppliers")
            if dist_km <= (radius_km if radius_km > 0 else 50.0):
                tags.append("Near you")
            if not tags:
                tags.append("Recommended buyer")

            # Filter by Category Chip if selected
            if category_filter == "renewals" and "Renewal due soon" not in tags:
                continue
            if category_filter == "similar" and "Similar to your wins" not in tags:
                continue
            if category_filter == "open_to_new" and "Open to new suppliers" not in tags:
                continue
            if category_filter == "near_you" and "Near you" not in tags:
                continue

            # Build Transparent Explanation Text
            signals = []
            if 60 <= days_until_renewal <= 180:
                signals.append(f"{(renewal_contract + ' renews') if renewal_contract else 'Contract renewal'} {_bf.day_phrase(days_until_renewal)} ({next_ren}), early enough to engage")
            if sector_note and cpv_match:
                signals.append(sector_note.rstrip("."))
            elif tier == 1 and cpv_match:
                signals.append("Matches CPV sectors from your historical won tenders in Pipeline")
            if repeat_rate < 35.0 and total_c >= 5:
                signals.append(f"Low repeat-supplier rate ({repeat_display}) across {total_c} contracts indicates strong openness to new vendors")
            if dist_km <= (radius_km if radius_km > 0 else 50.0):
                signals.append(f"Located within your local operating radius (about {int(round(dist_km * 0.621371))} miles)")

            why_seeing_this = f"This recommendation is produced because: " + "; ".join(signals) + "."

            cards.append({
                "authority_name": auth_name,
                "buyer_type": b_type,
                "match_score": final_match,
                "category_tags": tags,
                "total_spend": total_sp,
                "total_contracts": total_c,
                "unique_suppliers": unique_s,
                "repeat_supplier_pct": repeat_rate,
                "repeat_supplier_display": repeat_display,
                "next_renewal_date": next_ren or "None scheduled",
                "days_until_renewal": days_until_renewal if days_until_renewal != 999 else None,
                "distance_km": round(dist_km, 1) if dist_km < 9999 else None,
                "distance_approximate": coords_approx if dist_km < 9999 else None,
                "latitude": round(b_lat, 6) if (b_lat or b_lon) else None,
                "longitude": round(b_lon, 6) if (b_lat or b_lon) else None,
                "coords_approximate": coords_approx,
                "top_suppliers": [],
                "why_seeing_this": why_seeing_this,
                "fit_factors": buyer_fit.factors(
                    cpv_score=cpv_score, prox_score=prox_score, renew_score=renew_score, openness_score=openness_score, tier=tier,
                    cpv_match=cpv_match, dist_km=dist_km if dist_km < 9999 else None,
                    days_until_renewal=days_until_renewal if days_until_renewal != 999 else None,
                    next_renewal=next_ren, repeat_display=repeat_display, total_contracts=total_c, sector_note=sector_note,
                    renewal_contract=renewal_contract, renewal_scoped=renewal_scoped),
                "division_awards": div_awards,
                "engagement": buyer_fit.engagement(
                    cpv_score=cpv_score, prox_score=prox_score, renew_score=renew_score, openness_score=openness_score, total_contracts=total_c),
            })

        # Sort cards
        if sort_by in ("spend", "highest_value"):
            cards.sort(key=lambda x: x["total_spend"], reverse=True)
        elif sort_by in ("soonest_renewal", "days_left"):
            cards.sort(key=lambda x: (x["days_until_renewal"] is None, x["days_until_renewal"] if x["days_until_renewal"] is not None else 99999))
        elif sort_by in ("name", "buyer_name"):
            cards.sort(key=lambda x: x["authority_name"].lower())
        elif search_division:
            cards.sort(key=lambda x: (-x["division_awards"], -x["total_contracts"]))
        elif q:
            cards.sort(key=lambda x: (_name_relevance(x["authority_name"], q), -x["total_contracts"], -x["match_score"]))
        else:
            cards.sort(key=lambda x: x["match_score"], reverse=True)

        total_matches = len(cards)
        top_cards = cards[:50]

        # Batch populate top 2 suppliers only for the returned top cards
        _now = time.time()
        for c in top_cards:
            _hit = _top_vendor_cache.get(c["authority_name"])
            if _hit and _now - _hit[0] < _TOP_VENDOR_TTL_S:
                c["top_suppliers"] = list(_hit[1])
        auth_names_for_sup = [c["authority_name"] for c in top_cards if c["total_contracts"] > 0
                              and not (_top_vendor_cache.get(c["authority_name"]) and _now - _top_vendor_cache[c["authority_name"]][0] < _TOP_VENDOR_TTL_S)]
        if auth_names_for_sup:
            # Match on the normalized key (see _norm_auth_sql), not an exact TRIM() string —
            # `authority_name` above is just one casing/whitespace variant the ARRAY_AGG
            # happened to pick as this buyer's representative label, so an exact match here
            # silently dropped every award recorded under a different-cased variant of the
            # same buyer, and which variant got picked (and so which suppliers surfaced)
            # could differ request to request. `ORDER BY sv DESC, supplier_name ASC` makes
            # ties between equal-value suppliers deterministic too.
            _execute(cursor, conn, f"SELECT t.n, {_norm_auth_sql('t.n')} FROM UNNEST(?::text[]) AS t(n)", [auth_names_for_sup])
            norm_to_auth = {norm: n for n, norm in cursor.fetchall()}
            placeholders = ",".join([_norm_auth_sql("?")] * len(auth_names_for_sup))
            # Exclude framework ceilings from the ranking value for the same reason as
            # top_suppliers_query in get_buyer_detail() above — that figure is duplicated
            # identically across every co-appointed supplier, so ranking by it just surfaces
            # whichever framework co-appointees happen to be in the query results first.
            # The same de-duplicated awards and the same tidy-up as the buyer profile, so the card and the profile name the same vendors (QA B8).
            sup_sql = f"""
                WITH {_stats.deduped_cte(cursor)}
                SELECT {_norm_auth_sql("authority_name")} as an_norm, supplier_name,
                       COUNT(DISTINCT id) as contracts_won,
                       SUM(CASE WHEN is_framework = 1 OR contract_value < 0 OR contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN 0 ELSE contract_value END) as total_value,
                       SUM(CASE WHEN is_framework = 1 THEN contract_value ELSE 0 END) as framework_total_ceiling,
                       COUNT(CASE WHEN is_framework = 1 THEN 1 END) as framework_appointments,
                       MAX(date_signed) as last_award_date
                FROM deduped_awards
                WHERE {_norm_auth_sql("authority_name")} IN ({placeholders}) AND supplier_name IS NOT NULL AND supplier_name != ''
                GROUP BY {_norm_auth_sql("authority_name")}, supplier_name
            """
            try:
                _execute(cursor, conn, sup_sql, auth_names_for_sup)
                by_buyer = {}
                for row in cursor.fetchall():
                    rec = _row_to_dict(cursor, row)
                    by_buyer.setdefault(norm_to_auth.get(rec["an_norm"], rec["an_norm"]), []).append(rec)
                top_sup_map = {an: [r["supplier_name"] for r in _tidy_top_suppliers(rows, limit=2)] for an, rows in by_buyer.items()}
                for c in top_cards:
                    if c["authority_name"] in auth_names_for_sup:
                        c["top_suppliers"] = top_sup_map.get(c["authority_name"], [])
                        _top_vendor_cache[c["authority_name"]] = (_now, list(c["top_suppliers"]))
                if len(_top_vendor_cache) > 5000:
                    _top_vendor_cache.clear()
            except Exception as _sup_err:
                pass

        did_you_mean = None
        if q and not top_cards:
            did_you_mean = _buyer_did_you_mean(cursor, conn, q)
        return jsonify({
            "ok": True,
            "tier": tier,
            "banner_text": banner_text,
            "did_you_mean": did_you_mean,
            "cards": top_cards,
            "total_matches": total_matches,
            "hidden_no_sector": hidden_no_sector,
            "hidden_no_location": hidden_no_location,
            "typed": typed,
            "searched_for": q if q != typed else "",
            "fast": bool(use_stats),
            "approximate": approximate,
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()
