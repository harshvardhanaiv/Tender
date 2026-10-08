"""A buyer profile must not de-duplicate the whole awards table for every query.

Found on the live site in Round 28 follow-up: /api/buyers/<name> took ~27 s (locally ~31 s). Each of its queries ran the
de-duplication CTE over all ~500k awards and only then filtered to one buyer, and the index meant to help (on the old
narrower name key) no longer matched the widened key. The CTE now filters to the buyer first and an index covers the key.
Run: python tests/test_buyer_detail_performance.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

from etenders_scraper.awards import DEDUPED_AWARDS_CTE_SQL, deduped_awards_cte_sql
from tender_app.blueprints.buyers_bp import _norm_auth_sql
from tender_app.db import get_db_connection
from tender_app.db_ext import migrate_contract_awards_table

BUYER = "ZZ Perf Test, St. Mary's Trust"


def test_unfiltered_cte_is_unchanged_and_filter_is_applied_before_dedup():
    assert deduped_awards_cte_sql() == DEDUPED_AWARDS_CTE_SQL
    f = deduped_awards_cte_sql("authority_name = 'x'")
    assert "FROM contract_awards\n              WHERE authority_name = 'x') a" in f, "the filter must sit inside the DISTINCT ON subquery"
    assert "WHERE a.date_signed IS NULL AND (authority_name = 'x')" in f
    print("ok    test_unfiltered_cte_is_unchanged_and_filter_is_applied_before_dedup")


def test_filtering_first_gives_the_same_rows_as_filtering_after():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # duplicates (same supplier, buyer, date), a dateless row, another buyer, and an alternate spelling of the buyer
        rows = [(BUYER, "zz_a", "2026-01-01", 100000), (BUYER, "zz_a", "2026-01-01", 150000), (BUYER.upper(), "zz_a", "2026-01-01", 120000),
                (BUYER, "zz_b", "2026-02-01", 200000), (BUYER, "zz_c", None, 5000), ("ZZ Other Buyer", "zz_a", "2026-01-01", 1)]
        for a, s, d, v in rows:
            cur.execute("INSERT INTO contract_awards (authority_name, supplier_name, date_signed, contract_value, source_portal) VALUES (%s,%s,%s,%s,'Contracts Finder')", (a, s, d, v))
        conn.commit()
        key = _norm_auth_sql("%s")
        match = f"{_norm_auth_sql('authority_name')} = {key}"
        after = f"WITH {DEDUPED_AWARDS_CTE_SQL} SELECT id FROM deduped_awards WHERE {match} ORDER BY id"
        first = f"WITH {deduped_awards_cte_sql(match)} SELECT id FROM deduped_awards WHERE {match} ORDER BY id"
        cur.execute(after, (BUYER,)); a_ids = [r[0] for r in cur.fetchall()]
        cur.execute(first, (BUYER, BUYER, BUYER)); f_ids = [r[0] for r in cur.fetchall()]
        assert a_ids and a_ids == f_ids, f"filter-first gave different rows: {a_ids} vs {f_ids}"
    finally:
        conn.rollback()
        cur.execute("DELETE FROM contract_awards WHERE authority_name IN (%s, %s, 'ZZ Other Buyer')", (BUYER, BUYER.upper()))
        conn.commit()
        conn.close()
    print("ok    test_filtering_first_gives_the_same_rows_as_filtering_after")


def test_the_buyer_key_has_an_index_the_planner_can_use():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        migrate_contract_awards_table(cur)
        conn.commit()
        cur.execute("SET enable_seqscan = off")
        cur.execute(f"EXPLAIN SELECT id FROM contract_awards WHERE {_norm_auth_sql('authority_name')} = {_norm_auth_sql('%s')}", ("London Borough of Camden",))
        plan = "\n".join(r[0] for r in cur.fetchall())
        assert "idx_awards_buyer_key" in plan, f"the buyer-key index is not used:\n{plan}"
    finally:
        conn.rollback()
        conn.close()
    print("ok    test_the_buyer_key_has_an_index_the_planner_can_use")


def test_a_changed_key_expression_gets_a_fresh_index_and_the_stale_one_is_dropped():
    """CREATE INDEX IF NOT EXISTS keeps a same-named index built from an OLD expression, which then silently stops matching."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_awards_buyer_key_zzstale ON contract_awards (id)")
        conn.commit()
        migrate_contract_awards_table(cur)
        conn.commit()
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'contract_awards' AND indexname LIKE 'idx_awards_buyer_key%'")
        names = [r[0] for r in cur.fetchall()]
        assert "idx_awards_buyer_key_zzstale" not in names, f"a stale buyer-key index was left behind: {names}"
        assert len(names) == 1, names
    finally:
        cur.execute("DROP INDEX IF EXISTS idx_awards_buyer_key_zzstale")
        conn.commit()
        conn.close()
    print("ok    test_a_changed_key_expression_gets_a_fresh_index_and_the_stale_one_is_dropped")


def test_a_busy_buyer_profile_loads_quickly():
    from server import create_app
    c = create_app().test_client()
    with c.session_transaction() as s:
        s.update(logged_in=True, username="zz_perf", email="zz_perf@test.invalid", last_activity=time.time(), csrf_token="x")
    t = time.time()
    r = c.get("/api/buyers/London%20Borough%20of%20Camden%20Council")
    took = time.time() - t
    assert r.status_code == 200 and r.get_json().get("stats", {}).get("total_contracts", 0) > 50
    assert took < 8, f"a buyer profile took {took:.1f}s (it was ~30s before the buyer-first de-duplication)"
    print(f"ok    test_a_busy_buyer_profile_loads_quickly ({took:.1f}s)")


if __name__ == "__main__":
    test_unfiltered_cte_is_unchanged_and_filter_is_applied_before_dedup()
    test_filtering_first_gives_the_same_rows_as_filtering_after()
    test_the_buyer_key_has_an_index_the_planner_can_use()
    test_a_changed_key_expression_gets_a_fresh_index_and_the_stale_one_is_dropped()
    test_a_busy_buyer_profile_loads_quickly()
    print("5 passed, 0 skipped, 0 failed")
