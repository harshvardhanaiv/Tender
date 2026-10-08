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

import threading
import time
from datetime import datetime
from typing import Any, Callable

from etenders_scraper.awards import (
    PLACEHOLDER_COORDS,
    UK_IE_SOURCE_PORTALS,
    SINGLE_AWARD_STATS_CEILING_GBP,
    DEDUPED_AWARDS_CTE_SQL,
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_buyer_stats_spend ON buyer_stats (total_spend DESC)")


def refresh_supplier_stats(conn) -> int:
    """Recompute supplier_stats. Awards reach a supplier by supplier_id or by company number.

    The old query expressed that as `s.id = a.supplier_id OR s.company_number = a.company_number`,
    an OR the planner can only run as a nested loop. Two hash joins give the same links.
    """
    cur = conn.cursor()
    cur.execute(f"""
        WITH {DEDUPED_AWARDS_CTE_SQL},
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
                   COALESCE(SUM(CASE WHEN (is_framework IS NULL OR is_framework = 0) AND contract_value <= {SINGLE_AWARD_STATS_CEILING_GBP} THEN contract_value ELSE 0 END), 0) AS total_value,
                   COALESCE(AVG(CASE WHEN (is_framework IS NULL OR is_framework = 0) AND contract_value <= {SINGLE_AWARD_STATS_CEILING_GBP} THEN contract_value END), 0) AS avg_value,
                   COALESCE(SUM(CASE WHEN is_framework = 1 THEN contract_value ELSE 0 END), 0) AS fw_ceiling,
                   COUNT(CASE WHEN is_framework = 1 THEN 1 END) AS fw_count,
                   MAX(date_signed) AS latest
            FROM links GROUP BY sid
        )
        INSERT INTO supplier_stats
            (supplier_id, listable, total_awards, total_value, avg_value,
             framework_total_ceiling, framework_appointments_count, latest_award_date, refreshed_at)
        SELECT s.id, {_LISTABLE_SQL},
               COALESCE(g.total_awards, 0), COALESCE(g.total_value, 0), COALESCE(g.avg_value, 0),
               COALESCE(g.fw_ceiling, 0), COALESCE(g.fw_count, 0), g.latest, CURRENT_TIMESTAMP
        FROM suppliers s LEFT JOIN agg g ON g.sid = s.id
        ON CONFLICT (supplier_id) DO UPDATE SET
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


def refresh_buyer_stats(conn) -> int:
    """Recompute buyer_stats, grouped by the same normalised name the buyer pages group by."""
    today = datetime.now().strftime("%Y-%m-%d")
    cur = conn.cursor()
    cur.execute(f"""
        WITH {DEDUPED_AWARDS_CTE_SQL}
        INSERT INTO buyer_stats
            (authority_key, authority_name, buyer_type, total_contracts, total_spend, total_spend_all,
             unique_suppliers, next_renewal_date, latest_award_date, max_renewal_date,
             latitude, longitude, cpv_divisions, refreshed_at, avg_contract_value)
        SELECT k,
               (ARRAY_AGG(TRIM(authority_name) ORDER BY
                    (TRIM(authority_name) = UPPER(TRIM(authority_name))) ASC,
                    LENGTH(authority_name) DESC, TRIM(authority_name) ASC))[1],
               MAX(buyer_type),
               COUNT(*),
               SUM(CASE WHEN is_framework = 1 OR contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN 0 ELSE contract_value END),
               SUM(contract_value),
               COUNT(DISTINCT supplier_name),
               MIN(CASE WHEN contract_end_date >= %s THEN contract_end_date END),
               MAX(date_signed),
               MAX(contract_end_date),
               (ARRAY_AGG(latitude ORDER BY date_signed DESC NULLS LAST) FILTER (WHERE {_REAL_COORD_SQL}))[1],
               (ARRAY_AGG(longitude ORDER BY date_signed DESC NULLS LAST) FILTER (WHERE {_REAL_COORD_SQL}))[1],
               ARRAY_AGG(DISTINCT LEFT(cpv_code, 2)) FILTER (WHERE cpv_code IS NOT NULL AND cpv_code <> ''),
               CURRENT_TIMESTAMP,
               AVG(CASE WHEN is_framework = 1 OR contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN NULL ELSE contract_value END)
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
            refreshed_at = EXCLUDED.refreshed_at, avg_contract_value = EXCLUDED.avg_contract_value
    """, (today,))
    n = cur.rowcount
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
        out["suppliers"] = refresh_supplier_stats(conn)
        out["supplier_seconds"] = round(time.time() - t0, 1)
        t0 = time.time()
        out["buyers"] = refresh_buyer_stats(conn)
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
            return False
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
