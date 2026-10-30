"""Precomputed supplier and buyer aggregates, so Supplier / Buyer Intelligence open instantly.

Why: the pages used to aggregate every award on every request. The supplier list joined all
suppliers to all awards through an OR condition (about 25 s), and the buyer feed ran an
ordered ARRAY_AGG / COUNT(DISTINCT) over every award (about 5 s). Ranking and totals do not
need to be recomputed per request, so they are kept here and refreshed in the background.

    supplier_stats   one row per supplier: award counts and value totals (direct awards and
                     frameworks kept apart, as the list has always done)
    buyer_stats      one row per normalised buyer name: spend, contract count, distinct
                     suppliers, next renewal, a paired coordinate and the CPV divisions it buys

Readers never wait for a refresh: a refresh is one transaction of upserts (MVCC keeps the old
rows visible until it commits), and it runs on a background thread. A request that finds the
tables empty falls back to the original slow path; the next one is fast.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from etenders_scraper.awards import (
    PLACEHOLDER_COORDS,
    UK_IE_SOURCE_PORTALS,
    SINGLE_AWARD_STATS_CEILING_GBP,
    DEDUPED_AWARDS_CTE_SQL,
    PANEL_MIN_SUPPLIERS,
    PANEL_MIN_VALUE,
)

from tender_app.blueprints.buyers_bp import _norm_auth_sql

STATS_TTL_S = 6 * 3600  # award data changes in batches; a few hours of staleness is invisible in rankings
_PORTALS = ", ".join(f"'{p}'" for p in UK_IE_SOURCE_PORTALS)

# Kept identical to suppliers_bp.SUPPLIER_NAME_NOT_PLACEHOLDER_SQL / JOIN_SUPPLIER_AWARD_SQL, which
# decide what counts as a real supplier and which awards belong to it. (Postgres's regex
# word-boundary escape is \y, not \b -- \b is backspace here.)
_LISTABLE_SQL = r"""(
    s.name !~* '(see|refer to)[a-z ,'']{0,45}attach'
    AND s.name !~* '^(na|n/a|tbc|tbd|unknown|confidential|not disclosed|not available|not named|withheld|redacted|to be confirmed|pending|none|supplier|contract|contract value|live scraped supplier|not applicable|not awarded|no award|award not made|please refer to weblink|refer to weblink|see website|please see website|withheld for security reasons)$'
    AND s.name !~* '(please see|please refer)'
    AND s.name !~* '^(as per|information withheld|awarded supplier details|confidential information|confidential / sensitive)'
    AND s.name !~* 'commercially confidential'
    AND s.name !~* '^(failed tender|tender (abandoned|withdrawn|cancelled|failed)|no award|not awarded|cancelled|abandoned|no (suitable )?bids?)'
    AND TRIM(s.name) !~* '^various$'
    AND s.name !~* '\ynhs\b.*?\b(foundation\s+trust|trust|ft|icb|board)\y'
    AND s.name !~* '\ynhs\s+[a-z &'',]{0,40}(foundation\s+trust|trust|ft|icb)\y'
    AND s.name !~* '\yclinical\s+commissioning\s+group\y'
    AND s.name !~* '\yintegrated\s+care\s+board\y'
    AND s.name !~* '\yhealth\s+board\y'
    AND s.name !~* '\y(borough|city|county|district|parish|town)\s+council\y'
    AND s.name !~* '\ycombined\s+authority\y'
    AND s.name !~* '\ypolice\s+and\s+crime\s+commissioner\y'
    AND s.name !~* '\yconstabulary\y'
    AND s.name !~* '\yfire\s+and\s+rescue\s+(service|authority)\y'
    AND LENGTH(TRIM(s.name)) <= 120
    AND s.name !~ '\y[a-z]{2,}\.\s+[A-Z][a-z]{2,}'
    AND NOT (
        s.name ~* '^(will|for the|to|capabilities|framework per|provision and|implementation of|various|multiple providers|all successful bidders|all as per)(\s|$)'
        AND s.name !~* '\y(ltd|limited|llp|plc|inc|llc|cic|corp|co|gmbh|b\.?v\.?|s\.?a\.?|s\.?r\.?l\.?|s\.?l\.?u\.?|c\.?i\.?c\.?)\.?$'
        AND (
            s.name ~* '^(various|multiple providers|all successful bidders|all as per)(\s|$)'
            OR LENGTH(TRIM(s.name)) > 40
            OR s.name ~* '\y(provide|provides|providing|provided|supply|supplies|servicing|allow|allows|satisfy|satisfies|lead|covered|published|viewed|held|undertake)\y'
        )
    )
)"""
# A public body that is published as a supplier ("NHS England", "University Of Exeter"): its name has no company suffix, it is a
# buyer with plenty of contracts and suppliers of its own, and it is that far more often than it is a winner. It belongs in Buyer
# intelligence, so it is left out of the supplier list (a legal entity such as "JISC Services Limited" stays).
_BUYER_LIKE_SQL = rf"""(
    s.name !~* '\y(ltd|limited|plc|llp|llc|inc|cic|corp|co|gmbh)\.?$'
    AND EXISTS (SELECT 1 FROM buyer_stats b WHERE b.authority_key = {_norm_auth_sql('s.name')}
                AND b.total_contracts >= 20 AND b.unique_suppliers >= 10 AND b.total_contracts > 3 * COALESCE(g.total_awards, 0))
)"""

_VALID_COMPANY_NUMBER_SQL = """(
    s.company_number IS NOT NULL AND s.company_number != ''
    AND s.company_number NOT IN ('Not available', 'Procurem', '—', '-', 'None')
    AND SUBSTR(s.company_number, 1, 7) != 'NO_REG_'
)"""
_REAL_COORD_SQL = (
    "latitude IS NOT NULL AND longitude IS NOT NULL AND NOT "
    f"(ROUND(latitude::numeric, 4) = {PLACEHOLDER_COORDS[0]} AND ROUND(longitude::numeric, 4) = {PLACEHOLDER_COORDS[1]})"
)

TABLES = ("supplier_stats", "buyer_stats")

# The de-duplicated, value-corrected awards (etenders_scraper.awards.deduped_awards_cte_sql) kept as a table. Computing that
# CTE costs ~15 s because it sorts and windows every award, and it used to run for every supplier search and profile; as a
# table (rebuilt with the stats below) a search reads a few thousand rows in about a second, and the list, the profile and
# the stats all read the same rows, so their numbers cannot differ.
AWARD_VALUES_TABLE = "award_values"
_AWARD_VALUES_INDEXES = (("supplier_id", "supplier_id"), ("company_number", "company_number"), ("date_signed", "date_signed"), ("auth_key", "auth_key"))
# NOT MATERIALIZED: a CTE read twice is copied in full (450,000 wide rows, 1 s per search); inlined, the planner reads only the
# rows of the suppliers that matched, through the table's own indexes.
_MATERIALISED_CTE = f"deduped_awards AS NOT MATERIALIZED (SELECT * FROM {AWARD_VALUES_TABLE})"


def award_values_ready(cursor) -> bool:
    try:
        cursor.execute(f"SELECT to_regclass('public.{AWARD_VALUES_TABLE}') IS NOT NULL")
        return bool(cursor.fetchone()[0])
    except Exception:
        cursor.connection.rollback()
        return False


def deduped_cte(cursor, live_sql: str = DEDUPED_AWARDS_CTE_SQL) -> str:
    """The `deduped_awards` CTE: the table when it has been built, else the live (slow) computation."""
    return _MATERIALISED_CTE if award_values_ready(cursor) else live_sql


def refresh_award_values(conn) -> int:
    """Rebuild the award_values table from the live CTE and swap it in. Readers keep the old table until the swap."""
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {AWARD_VALUES_TABLE}_new")
    cur.execute(f"CREATE TABLE {AWARD_VALUES_TABLE}_new AS WITH {DEDUPED_AWARDS_CTE_SQL} SELECT d.*, CURRENT_TIMESTAMP AS refreshed_at FROM deduped_awards d")
    for name, column in _AWARD_VALUES_INDEXES:
        cur.execute(f"CREATE INDEX idx_{AWARD_VALUES_TABLE}_{name}_new ON {AWARD_VALUES_TABLE}_new ({column})")
    cur.execute(f"ANALYZE {AWARD_VALUES_TABLE}_new")
    cur.execute(f"SELECT COUNT(*) FROM {AWARD_VALUES_TABLE}_new")
    rows = cur.fetchone()[0]
    cur.execute(f"DROP TABLE IF EXISTS {AWARD_VALUES_TABLE}")
    cur.execute(f"ALTER TABLE {AWARD_VALUES_TABLE}_new RENAME TO {AWARD_VALUES_TABLE}")
    for name, _ in _AWARD_VALUES_INDEXES:
        cur.execute(f"ALTER INDEX idx_{AWARD_VALUES_TABLE}_{name}_new RENAME TO idx_{AWARD_VALUES_TABLE}_{name}")
    conn.commit()
    return rows


# The name key tender_app.entity_merge.entity_name_key computes in Python, as SQL: case, punctuation, "&"/"and" and legal-form
# words ignored. Stored on supplier_stats so every record of one company can be found whatever the search was, which is what
# keeps a merged card's totals the same for "Capita" and "Capita plc". (\y is Postgres's word boundary.)
ENTITY_KEY_SQL = (
    r"""REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE("""
    r"""REPLACE(UPPER(REPLACE(TRIM({col}), '&', ' AND ')), 'UNITED KINGDOM', 'UK'), '^LOT\s*[0-9]+\s*\([^)]*\)\s*:\s*', '', 'g'), """
    r"""'\(\s*(APPOINTED TO )?LOTS?\s*[0-9A-Z ,&/-]*\)', '', 'g'), """
    r"""'\(\s*(T/A|TRADING AS)[^)]*\)| (T/A|TRADING AS) .*$', '', 'g'), """
    r"""'\y(LTD|LIMITED|PLC|LLP|LLC|INC)\.?\s*\([^)]*\)\s*$', '\1', 'g'), """
    r"""'[^A-Z0-9]+', ' ', 'g'), """
    r"""'\y(LTD|LIMITED|PLC|LLP|LLC|INC|CORP|CO)\y', ' ', 'g'), '\yAND\y|\s+', '', 'g')"""
)


def ensure_stats_tables(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS supplier_stats (
            supplier_id                  INTEGER PRIMARY KEY,
            listable                     BOOLEAN NOT NULL DEFAULT TRUE,
            total_awards                 INTEGER NOT NULL DEFAULT 0,
            total_value                  NUMERIC NOT NULL DEFAULT 0,
            avg_value                    NUMERIC NOT NULL DEFAULT 0,
            framework_total_ceiling      NUMERIC NOT NULL DEFAULT 0,
            framework_appointments_count INTEGER NOT NULL DEFAULT 0,
            latest_award_date            TEXT,
            refreshed_at                 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("ALTER TABLE supplier_stats ADD COLUMN IF NOT EXISTS entity_key TEXT")
    cursor.execute("ALTER TABLE supplier_stats ADD COLUMN IF NOT EXISTS region_norm TEXT")        # one of tender_app.regions.REGIONS, or "UK"
    cursor.execute("ALTER TABLE supplier_stats ADD COLUMN IF NOT EXISTS brand_key TEXT")          # leading name shared by related records
    cursor.execute("ALTER TABLE supplier_stats ADD COLUMN IF NOT EXISTS group_large BOOLEAN NOT NULL DEFAULT FALSE")  # part of a plc: not an SME
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_supplier_stats_region ON supplier_stats (region_norm)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_supplier_stats_brand ON supplier_stats (brand_key) WHERE brand_key <> ''")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_supplier_stats_entity ON supplier_stats (entity_key) WHERE entity_key <> ''")
    # Typo-tolerant search ("sercco" finds Serco) needs trigram matching. A database that does not allow the extension just
    # goes without "did you mean"; the savepoint keeps that failure from undoing the DDL above.
    cursor.execute("SAVEPOINT trgm")
    try:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_awards_supname_trgm ON contract_awards USING gin (LOWER(supplier_name) gin_trgm_ops)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_supplier_stats_entity_trgm ON supplier_stats USING gin (entity_key gin_trgm_ops)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_suppliers_name_trgm ON suppliers USING gin (LOWER(name) gin_trgm_ops)")
        cursor.execute("RELEASE SAVEPOINT trgm")
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT trgm")
    # The default list: real suppliers ranked by value, then awards.
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_supplier_stats_rank
        ON supplier_stats (total_value DESC, total_awards DESC) WHERE listable
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS buyer_stats (
            authority_key     TEXT PRIMARY KEY,
            authority_name    TEXT NOT NULL,
            buyer_type        TEXT,
            total_contracts   INTEGER NOT NULL DEFAULT 0,
            total_spend       NUMERIC,      -- excludes framework ceilings, as the feed does; NULL if no award has a value
            total_spend_all   NUMERIC,      -- includes them
            unique_suppliers  INTEGER NOT NULL DEFAULT 0,
            next_renewal_date TEXT,
            latest_award_date TEXT,
            max_renewal_date  TEXT,
            latitude          DOUBLE PRECISION,
            longitude         DOUBLE PRECISION,
            cpv_divisions     TEXT[],
            refreshed_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # average value of the direct contracts that state one, so the buyer search can read it instead of re-aggregating
    cursor.execute("ALTER TABLE buyer_stats ADD COLUMN IF NOT EXISTS avg_contract_value NUMERIC")
    # what the buyer buys, as {CPV division: awards}, so a buyer can be scored by its spend in a company's sector rather than by its name;
    # and the first contract end at least 60 days away, the earliest a company can still act on.
    cursor.execute("ALTER TABLE buyer_stats ADD COLUMN IF NOT EXISTS cpv_shares JSONB")
    cursor.execute("ALTER TABLE buyer_stats ADD COLUMN IF NOT EXISTS next_renewal_actionable TEXT")
    cursor.execute("ALTER TABLE buyer_stats ADD COLUMN IF NOT EXISTS renewals_by_division JSONB")
    # {CPV division: GBP spent} (direct contracts, sane values) so sector fit can be scored on the share of a buyer's SPEND, not just its
    # award count; and the title of the buyer's first actionable renewal, so every renewal reason can name its contract (re-test 3 B1, B2).
    # NULL until the next stats rebuild; the buyer feed falls back to award counts and an unnamed contract until then.
    cursor.execute("ALTER TABLE buyer_stats ADD COLUMN IF NOT EXISTS cpv_spend JSONB")
    cursor.execute("ALTER TABLE buyer_stats ADD COLUMN IF NOT EXISTS next_renewal_title TEXT")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_buyer_stats_spend ON buyer_stats (total_spend DESC)")


def refresh_supplier_stats(conn, from_table: bool = False) -> int:
    """Recompute supplier_stats. Awards reach a supplier by supplier_id or by company number.

    The old query expressed that as `s.id = a.supplier_id OR s.company_number = a.company_number`,
    an OR the planner can only run as a nested loop. Two hash joins give the same links.
    """
    cur = conn.cursor()
    cur.execute(f"""
        WITH {deduped_cte(cur) if from_table else DEDUPED_AWARDS_CTE_SQL},
        links AS (
            SELECT a.supplier_id AS sid, a.id AS aid, a.contract_value, a.is_framework, a.date_signed
            FROM deduped_awards a
            WHERE a.supplier_id IS NOT NULL AND a.source_portal IN ({_PORTALS})
            UNION ALL
            SELECT s.id, a.id, a.contract_value, a.is_framework, a.date_signed
            FROM suppliers s
            JOIN deduped_awards a ON a.company_number = s.company_number
            WHERE {_VALID_COMPANY_NUMBER_SQL}
              AND a.supplier_id IS DISTINCT FROM s.id
              AND a.source_portal IN ({_PORTALS})
        ),
        agg AS (
            SELECT sid,
                   COUNT(aid) AS total_awards,
                   COALESCE(SUM(CASE WHEN (is_framework IS NULL OR is_framework = 0) AND contract_value BETWEEN 0 AND {SINGLE_AWARD_STATS_CEILING_GBP} THEN contract_value ELSE 0 END), 0) AS total_value,
                   COALESCE(AVG(CASE WHEN (is_framework IS NULL OR is_framework = 0) AND contract_value BETWEEN 0 AND {SINGLE_AWARD_STATS_CEILING_GBP} THEN contract_value END), 0) AS avg_value,
                   COALESCE(SUM(CASE WHEN is_framework = 1 THEN contract_value ELSE 0 END), 0) AS fw_ceiling,
                   COUNT(CASE WHEN is_framework = 1 THEN 1 END) AS fw_count,
                   MAX(date_signed) AS latest
            FROM links GROUP BY sid
        )
        INSERT INTO supplier_stats
            (supplier_id, listable, total_awards, total_value, avg_value,
             framework_total_ceiling, framework_appointments_count, latest_award_date, refreshed_at, entity_key)
        SELECT s.id, ({_LISTABLE_SQL} AND NOT {_BUYER_LIKE_SQL}),
               COALESCE(g.total_awards, 0), COALESCE(g.total_value, 0), COALESCE(g.avg_value, 0),
               COALESCE(g.fw_ceiling, 0), COALESCE(g.fw_count, 0), g.latest, CURRENT_TIMESTAMP,
               {ENTITY_KEY_SQL.format(col='s.name')}
        FROM suppliers s LEFT JOIN agg g ON g.sid = s.id
        ON CONFLICT (supplier_id) DO UPDATE SET entity_key = EXCLUDED.entity_key,
            listable = EXCLUDED.listable, total_awards = EXCLUDED.total_awards, total_value = EXCLUDED.total_value,
            avg_value = EXCLUDED.avg_value, framework_total_ceiling = EXCLUDED.framework_total_ceiling,
            framework_appointments_count = EXCLUDED.framework_appointments_count,
            latest_award_date = EXCLUDED.latest_award_date, refreshed_at = EXCLUDED.refreshed_at
    """)
    n = cur.rowcount
    # Suppliers that no longer exist (merged / deleted) leave stale rows behind.
    cur.execute("DELETE FROM supplier_stats st WHERE NOT EXISTS (SELECT 1 FROM suppliers s WHERE s.id = st.supplier_id)")
    conn.commit()
    return n


def _large_name_regex():
    """Whole-word match on the known large enterprises. is_known_large_enterprise matches substrings, so "ey" and "sap" would
    flag "Honey Ltd" and "Sapphire Ltd"; a flag that spreads to every record of a name must not do that."""
    from tender_app.supplier_classifier import KNOWN_LARGE_ENTERPRISES

    words = sorted({w for w in KNOWN_LARGE_ENTERPRISES if len(w) >= 4 or w == "ibm"}, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b|\bplc\b", re.I)


def refresh_supplier_attributes(conn) -> int:
    """Region (one ITL1 name), the leading-name key that relates records, and whether a record belongs to a plc group.

    Done in Python because the region and name rules live there (tender_app.regions), then written back in batches."""
    from psycopg2.extras import execute_values

    from tender_app.regions import brand_key, itl1_region

    cur = conn.cursor()
    cur.execute("SELECT s.id, s.name, s.region, s.address FROM suppliers s JOIN supplier_stats st ON st.supplier_id = s.id")
    large = _large_name_regex()
    rows = cur.fetchall()
    data = [(sid, itl1_region(region, address), brand_key(name)) for sid, name, region, address in rows]
    large_brands = sorted({b for (_sid, _region, b), (_i, name, _r, _a) in zip(data, rows) if b and large.search(name or "")})
    execute_values(
        cur,
        "UPDATE supplier_stats st SET region_norm = v.region, brand_key = v.brand FROM (VALUES %s) AS v(id, region, brand) WHERE st.supplier_id = v.id",
        data, page_size=5000)
    # A record that shares its leading name with a plc or a known large enterprise ("Bunzl T/A Greenham Ltd" next to "Bunzl UK Ltd")
    # is part of a large group, not an SME.
    cur.execute("UPDATE supplier_stats SET group_large = (brand_key = ANY(%s))", (large_brands,))
    conn.commit()
    return len(data)


def refresh_buyer_stats(conn, from_table: bool = False) -> int:
    """Recompute buyer_stats, grouped by the same normalised name the buyer pages group by."""
    today = datetime.now().strftime("%Y-%m-%d")
    cur = conn.cursor()
    ensure_stats_tables(cur)   # columns added since the table was built (cpv_spend, next_renewal_title) exist before they are written
    cur.execute(f"""
        WITH {deduped_cte(cur) if from_table else DEDUPED_AWARDS_CTE_SQL}
        INSERT INTO buyer_stats
            (authority_key, authority_name, buyer_type, total_contracts, total_spend, total_spend_all,
             unique_suppliers, next_renewal_date, latest_award_date, max_renewal_date,
             latitude, longitude, cpv_divisions, refreshed_at, avg_contract_value, next_renewal_actionable)
        SELECT k,
               (ARRAY_AGG(TRIM(authority_name) ORDER BY
                    (TRIM(authority_name) = UPPER(TRIM(authority_name))) ASC,
                    LENGTH(authority_name) DESC, TRIM(authority_name) ASC))[1],
               MAX(buyer_type),
               COUNT(*),
               SUM(CASE WHEN is_framework = 1 OR contract_value < 0 OR contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN 0 ELSE contract_value END),
               SUM(CASE WHEN contract_value < 0 THEN 0 ELSE contract_value END),
               COUNT(DISTINCT supplier_name),
               MIN(CASE WHEN contract_end_date >= %s THEN contract_end_date END),
               MAX(date_signed),
               MAX(contract_end_date),
               (ARRAY_AGG(latitude ORDER BY date_signed DESC NULLS LAST) FILTER (WHERE {_REAL_COORD_SQL}))[1],
               (ARRAY_AGG(longitude ORDER BY date_signed DESC NULLS LAST) FILTER (WHERE {_REAL_COORD_SQL}))[1],
               ARRAY_AGG(DISTINCT LEFT(cpv_code, 2)) FILTER (WHERE cpv_code IS NOT NULL AND cpv_code <> ''),
               CURRENT_TIMESTAMP,
               AVG(CASE WHEN is_framework = 1 OR contract_value < 0 OR contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN NULL ELSE contract_value END),
               MIN(CASE WHEN contract_end_date >= %s THEN contract_end_date END)
        FROM (
            SELECT *, {_norm_auth_sql('authority_name')} AS k
            FROM deduped_awards
            WHERE authority_name IS NOT NULL AND TRIM(authority_name) != '' AND LENGTH(TRIM(authority_name)) > 2
              AND authority_name NOT IN ('Unknown', 'Not available', 'N/A')
        ) ca
        GROUP BY k
        ON CONFLICT (authority_key) DO UPDATE SET
            authority_name = EXCLUDED.authority_name, buyer_type = EXCLUDED.buyer_type,
            total_contracts = EXCLUDED.total_contracts, total_spend = EXCLUDED.total_spend,
            total_spend_all = EXCLUDED.total_spend_all, unique_suppliers = EXCLUDED.unique_suppliers,
            next_renewal_date = EXCLUDED.next_renewal_date, latest_award_date = EXCLUDED.latest_award_date,
            max_renewal_date = EXCLUDED.max_renewal_date, latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude, cpv_divisions = EXCLUDED.cpv_divisions,
            refreshed_at = EXCLUDED.refreshed_at, avg_contract_value = EXCLUDED.avg_contract_value,
            next_renewal_actionable = EXCLUDED.next_renewal_actionable
    """, (today, (datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d")))
    n = cur.rowcount
    cur.execute(f"""
        WITH {deduped_cte(cur) if from_table else DEDUPED_AWARDS_CTE_SQL}
        UPDATE buyer_stats b SET cpv_shares = t.shares
        FROM (SELECT k, jsonb_object_agg(div, n) AS shares FROM (
                  SELECT {_norm_auth_sql('authority_name')} AS k, LEFT(cpv_code, 2) AS div, COUNT(*) AS n
                  FROM deduped_awards
                  WHERE authority_name IS NOT NULL AND cpv_code ~ '^[0-9][0-9]' AND cpv_code NOT LIKE '00%'
                  GROUP BY 1, 2) d GROUP BY k) t
        WHERE b.authority_key = t.k
    """)
    cur.execute("UPDATE buyer_stats SET cpv_shares = '{}'::jsonb WHERE cpv_shares IS NULL")  # no CPV on any award: empty, not unknown
    cur.execute(f"""
        WITH {deduped_cte(cur) if from_table else DEDUPED_AWARDS_CTE_SQL}
        UPDATE buyer_stats b SET cpv_spend = t.spend
        FROM (SELECT k, jsonb_object_agg(div, v) AS spend FROM (
                  SELECT {_norm_auth_sql('authority_name')} AS k, LEFT(cpv_code, 2) AS div,
                         SUM(CASE WHEN is_framework = 1 OR contract_value < 0 OR contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN 0 ELSE COALESCE(contract_value, 0) END) AS v
                  FROM deduped_awards
                  WHERE authority_name IS NOT NULL AND cpv_code ~ '^[0-9][0-9]' AND cpv_code NOT LIKE '00%'
                  GROUP BY 1, 2) d GROUP BY k) t
        WHERE b.authority_key = t.k
    """)
    cur.execute("UPDATE buyer_stats SET cpv_spend = '{}'::jsonb WHERE cpv_spend IS NULL")
    cur.execute(f"""
        WITH {deduped_cte(cur) if from_table else DEDUPED_AWARDS_CTE_SQL}
        UPDATE buyer_stats b SET next_renewal_title = t.ttl
        FROM (SELECT DISTINCT ON (k) k, LEFT(tender_title, 80) AS ttl FROM (
                  SELECT {_norm_auth_sql('authority_name')} AS k, tender_title, contract_end_date
                  FROM deduped_awards
                  WHERE authority_name IS NOT NULL AND contract_end_date >= %s AND tender_title IS NOT NULL AND TRIM(tender_title) <> '') x
              ORDER BY k, contract_end_date, tender_title) t
        WHERE b.authority_key = t.k
    """, ((datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d"),))
    # The first renewal a company can still act on, per CPV division, with the contract's title: a renewal counts for a buyer only
    # when it is in the company's own services (re-test 2 B2: a hospital's ophthalmic maintenance scored full marks for a BI firm).
    cur.execute(f"""
        WITH {deduped_cte(cur) if from_table else DEDUPED_AWARDS_CTE_SQL}
        UPDATE buyer_stats b SET renewals_by_division = t.r
        FROM (SELECT k, jsonb_object_agg(div, jsonb_build_object('date', d, 'title', ttl, 'value', val)) AS r FROM (
                  SELECT DISTINCT ON (k, div) k, div, d, ttl, val FROM (
                      SELECT {_norm_auth_sql('authority_name')} AS k, LEFT(cpv_code, 2) AS div, contract_end_date AS d, LEFT(tender_title, 80) AS ttl,
                             CASE WHEN is_framework = 1 OR contract_value < 0 OR contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN NULL ELSE contract_value END AS val
                      FROM deduped_awards
                      WHERE authority_name IS NOT NULL AND cpv_code ~ '^[0-9][0-9]' AND cpv_code NOT LIKE '00%%' AND contract_end_date >= %s) x
                  ORDER BY k, div, d) y GROUP BY k) t
        WHERE b.authority_key = t.k
    """, ((datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d"),))
    cur.execute("UPDATE buyer_stats SET renewals_by_division = '{}'::jsonb WHERE renewals_by_division IS NULL")
    # Stale rows (a buyer whose awards were all removed) are found with the SAME key the insert above groups by.
    # This used to repeat a shorter copy of the formula without the punctuation step, so every buyer whose name
    # has punctuation ("St. Mary's NHS Trust", "Newcastle-upon-Tyne ...") was deleted right after being written.
    cur.execute(f"""
        DELETE FROM buyer_stats WHERE authority_key NOT IN (
            SELECT DISTINCT {_norm_auth_sql('authority_name')}
            FROM contract_awards WHERE authority_name IS NOT NULL)
    """)
    conn.commit()
    return n


def refresh_all(get_connection: Callable[[], Any]) -> dict[str, float | int]:
    """Refresh both tables; returns row counts and seconds taken."""
    out: dict[str, float | int] = {}
    conn = get_connection()
    try:
        cur = conn.cursor()
        ensure_stats_tables(cur)
        conn.commit()
        t0 = time.time()
        out["award_values"] = refresh_award_values(conn)
        out["award_values_seconds"] = round(time.time() - t0, 1)
        t0 = time.time()
        out["suppliers"] = refresh_supplier_stats(conn, from_table=True)
        out["attributes"] = refresh_supplier_attributes(conn)
        out["supplier_seconds"] = round(time.time() - t0, 1)
        t0 = time.time()
        out["buyers"] = refresh_buyer_stats(conn, from_table=True)
        out["buyer_seconds"] = round(time.time() - t0, 1)
    finally:
        conn.close()
    return out


# ── lazy background refresh ────────────────────────────────────────────────────────────
_state = {"running": False}
_state_lock = threading.Lock()
_last_check = {"at": 0.0}


def age_seconds(cursor, table: str) -> float | None:
    """Seconds since the table was last refreshed, or None if it is empty / missing."""
    try:
        cursor.execute(f"SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MAX(refreshed_at))) FROM {table}")
        row = cursor.fetchone()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:
        cursor.connection.rollback()
        return None


def ensure_fresh_async(get_connection: Callable[[], Any], cursor=None) -> None:
    """Start a background refresh if the stats are missing or older than STATS_TTL_S.

    Cheap to call on every request (it checks at most once a minute) and never blocks it.
    """
    now = time.time()
    if now - _last_check["at"] < 60:
        return
    _last_check["at"] = now
    with _state_lock:
        if _state["running"]:
            return

    def _needs() -> bool:
        conn = get_connection()
        try:
            cur = conn.cursor()
            ensure_stats_tables(cur)
            conn.commit()
            for t in TABLES:
                age = age_seconds(cur, t)
                if age is None or age > STATS_TTL_S:
                    return True
            if not award_values_ready(cur):  # first start after this table was introduced: build it now, not in up to 6 hours
                return True
            # a table built before the panel-notice rule: it still holds large shared values that rule now withholds
            cur.execute(f"SELECT EXISTS (SELECT 1 FROM {AWARD_VALUES_TABLE} WHERE shared_n >= {PANEL_MIN_SUPPLIERS} AND contract_value_notice >= {PANEL_MIN_VALUE} AND NOT COALESCE(ceiling_copy, FALSE) AND COALESCE(is_framework, 0) = 0 LIMIT 1)")
            if cur.fetchone()[0]:
                return True
            # buyer_stats rows written before the buyer's CPV mix and its first actionable renewal were stored
            cur.execute("SELECT EXISTS (SELECT 1 FROM buyer_stats WHERE cpv_shares IS NULL AND total_contracts > 0 LIMIT 1) AND EXISTS (SELECT 1 FROM contract_awards WHERE cpv_code ~ '^[0-9][0-9]' LIMIT 1)")
            if cur.fetchone()[0]:
                return True
            cur.execute("SELECT EXISTS (SELECT 1 FROM buyer_stats WHERE renewals_by_division IS NULL AND total_contracts > 0 LIMIT 1)")
            if cur.fetchone()[0]:  # rows written before the per-division renewal was stored
                return True
            # values under GBP 1 (a notice value split into fractions) are now withheld; trading names in brackets after Ltd/plc now
            # merge; a postcode now decides the region. Each needs one rebuild on a table built before it.
            cur.execute(f"SELECT EXISTS (SELECT 1 FROM {AWARD_VALUES_TABLE} WHERE contract_value > 0 AND contract_value < 1 LIMIT 1)")
            if cur.fetchone()[0]:
                return True
            cur.execute(
                r"""SELECT EXISTS (SELECT 1 FROM supplier_stats st JOIN suppliers s ON s.id = st.supplier_id
                    WHERE s.name ~* '\y(LTD|LIMITED|PLC|LLP|LLC|INC)\.?\s*\([^)]*\)\s*$'
                      AND st.entity_key IS DISTINCT FROM """ + ENTITY_KEY_SQL.format(col="s.name") + " LIMIT 1)")
            if cur.fetchone()[0]:
                return True
            # rows written before entity_key existed, or with the first version of it (lot suffixes not yet removed)
            cur.execute("SELECT EXISTS (SELECT 1 FROM supplier_stats WHERE entity_key IS NULL OR region_norm IS NULL OR entity_key LIKE '%APPOINTEDTOLOT%' LIMIT 1)")
            return bool(cur.fetchone()[0])
        finally:
            conn.close()

    def _run() -> None:
        try:
            if _needs():
                res = refresh_all(get_connection)
                print(f"[stats] refreshed: {res}")
        except Exception as exc:  # never let a refresh problem reach a request
            print(f"[stats] refresh failed: {exc}")
        finally:
            with _state_lock:
                _state["running"] = False

    with _state_lock:
        if _state["running"]:
            return
        _state["running"] = True
    threading.Thread(target=_run, name="stats-refresh", daemon=True).start()


def is_ready(cursor, table: str) -> bool:
    """True if the table has rows (so a request can be served from it)."""
    try:
        cursor.execute(f"SELECT EXISTS (SELECT 1 FROM {table} LIMIT 1)")
        return bool(cursor.fetchone()[0])
    except Exception:
        cursor.connection.rollback()
        return False
