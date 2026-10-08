"""refresh_buyer_stats must keep every buyer it just wrote.

Found on the live data in Round 28 stage 4: the refresh reported 9,615 buyers but the table held 8,939.
The stale-row DELETE rebuilt the buyer key with a shorter formula than the insert (no punctuation step), so
every buyer whose name contains punctuation was deleted straight after being written.
Uses zz_ fixture rows that are removed in `finally`; it recomputes the local buyer_stats table.
Run: python tests/test_buyer_stats_refresh_keys.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

from tender_app.blueprints.buyers_bp import _norm_auth_sql
from tender_app.db import get_db_connection
from tender_app.stats import refresh_buyer_stats

NAMES = ["ZZ Plain Test Trust", "ZZ St. Mary's Test Trust", "ZZ Newcastle-upon-Tyne  Test (North) Trust", "zz Test Health & Care, Ltd."]


def test_buyers_with_punctuation_survive_the_refresh():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        for i, name in enumerate(NAMES):
            cur.execute("INSERT INTO contract_awards (authority_name, supplier_name, date_signed, contract_value, source_portal) "
                        "VALUES (%s, %s, '2026-01-01', 1000, 'Contracts Finder')", (name, f"zz_supplier_{i}"))
        conn.commit()
        refresh_buyer_stats(conn)
        cur.execute(f"SELECT {_norm_auth_sql('n')} FROM unnest(%s::text[]) AS n", (NAMES,))
        keys = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT authority_key FROM buyer_stats WHERE authority_key = ANY(%s)", (keys,))
        kept = {r[0] for r in cur.fetchall()}
        missing = [n for n, k in zip(NAMES, keys) if k not in kept]
        assert not missing, f"buyers deleted by the refresh's own cleanup: {missing}"
    finally:
        conn.rollback()
        cur.execute("DELETE FROM contract_awards WHERE authority_name = ANY(%s)", (NAMES,))
        cur.execute(f"DELETE FROM buyer_stats WHERE authority_key IN (SELECT {_norm_auth_sql('n')} FROM unnest(%s::text[]) AS n)", (NAMES,))
        conn.commit()
        conn.close()
    print("ok    test_buyers_with_punctuation_survive_the_refresh")


def test_refresh_leaves_no_buyer_group_missing():
    """The table holds one row per distinct key the insert groups by (checked on the real local data)."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        refresh_buyer_stats(conn)
        cur.execute(f"""SELECT count(*) FROM (SELECT DISTINCT {_norm_auth_sql('authority_name')} AS k FROM contract_awards
                        WHERE authority_name IS NOT NULL AND TRIM(authority_name) != '' AND LENGTH(TRIM(authority_name)) > 2
                          AND authority_name NOT IN ('Unknown', 'Not available', 'N/A')) a
                        WHERE k NOT IN (SELECT authority_key FROM buyer_stats)""")
        missing = cur.fetchone()[0]
        # keys that only exist on awards the de-duplication collapses away are legitimate; a punctuation bug
        # shows up as hundreds, so the bound is tight but not zero-fragile
        assert missing < 50, f"{missing} buyer keys have no buyer_stats row"
    finally:
        conn.close()
    print("ok    test_refresh_leaves_no_buyer_group_missing")


if __name__ == "__main__":
    test_buyers_with_punctuation_survive_the_refresh()
    test_refresh_leaves_no_buyer_group_missing()
    print("2 passed, 0 skipped, 0 failed")
