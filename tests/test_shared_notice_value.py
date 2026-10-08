"""One notice value is counted once, however many supplier rows carry it.

Round 28 follow-up (Camden at GBP 1.36bn): a notice naming several suppliers is stored as one row per supplier and each row
carries the notice's single value, so "Young People Pathway" (GBP 20.9m, seven rows) counted GBP 146m and a GBP 110m retrofit
counted four times. Buyer pages, stats, Market Radar and the supplier pages now share one rule: rows of the same buyer and notice
with an identical value split that value between them. Frameworks are untouched.
Uses zz_ fixture rows removed in `finally`.
Run: python tests/test_shared_notice_value.py
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

from etenders_scraper.awards import AWARD_COLUMNS, DEDUPED_AWARDS_CTE_SQL
from tender_app.db import get_db_connection

BUYER = "ZZ Shared Value Buyer"
URL = "https://zz.invalid/shared/"
# (notice, supplier, value, date, is_framework)
ROWS = [
    ("A", "zz_s1", 9_000_000, "2026-03-01", 0), ("A", "zz_s2", 9_000_000, "2026-03-01", 0), ("A", "zz_s3", 9_000_000, "2026-03-01", 0),
    ("B", "zz_s1", 1_000_000, "2026-03-02", 0),
    ("C", "zz_s1", 500_000, "2026-03-03", 0), ("C", "zz_s2", 700_000, "2026-03-03", 0),
    ("D", "zz_s1", 4_000_000, None, 0), ("D", "zz_s1", 4_000_000, None, 0), ("D", "zz_s2", 4_000_000, None, 0), ("D", "zz_s2", 4_000_000, None, 0),
    ("E", "zz_s1", 50_000_000, "2026-03-04", 1), ("E", "zz_s2", 50_000_000, "2026-03-04", 1), ("E", "zz_s3", 50_000_000, "2026-03-04", 1),
]
EXPECTED_SPEND = 9_000_000 + 1_000_000 + 500_000 + 700_000 + 4_000_000   # A once, B, C as published, D once, frameworks not spend


def _load(conn):
    cur = conn.cursor()
    for notice, sup, val, date, fw in ROWS:
        cur.execute("INSERT INTO contract_awards (authority_name, supplier_name, tender_title, cpv_code, date_signed, contract_value, is_framework, notice_url, source_portal, currency) "
                    "VALUES (%s,%s,%s,'72000000',%s,%s,%s,%s,'Contracts Finder','GBP')", (BUYER, sup, f"zz notice {notice}", date, val, fw, URL + notice))
    conn.commit()


def _clean(conn):
    cur = conn.cursor()
    cur.execute("DELETE FROM contract_awards WHERE authority_name = %s", (BUYER,))
    cur.execute("DELETE FROM buyer_stats WHERE authority_name = %s", (BUYER,))
    conn.commit()


def test_award_columns_list_matches_the_table():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'contract_awards' ORDER BY ordinal_position")
    cols = tuple(r[0] for r in cur.fetchall())
    conn.close()
    assert cols == AWARD_COLUMNS, f"contract_awards columns changed; update AWARD_COLUMNS in etenders_scraper/awards.py\n table: {cols}\n list:  {AWARD_COLUMNS}"
    print("ok    test_award_columns_list_matches_the_table")


def test_the_cte_counts_a_shared_value_once():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _load(conn)
        cur.execute(f"""WITH {DEDUPED_AWARDS_CTE_SQL}
                        SELECT tender_title, count(*), sum(contract_value), max(contract_value_notice), max(shared_n)
                        FROM deduped_awards WHERE authority_name = %s GROUP BY 1 ORDER BY 1""", (BUYER,))
        by = {r[0].split()[-1]: r[1:] for r in cur.fetchall()}
        assert by["A"] == (3, 9_000_000, 9_000_000, 3), by["A"]          # three suppliers, one GBP 9m notice
        assert by["B"][1] == 1_000_000 and by["B"][3] == 1
        assert by["C"][1] == 1_200_000 and by["C"][3] == 1                # different values: not shared
        assert by["D"][1] == 4_000_000 and by["D"][3] == 4, by["D"]       # dateless duplicates and two suppliers: once
        assert by["E"][1] == 150_000_000 and by["E"][3] == 1, by["E"]     # frameworks keep their ceilings
        cur.execute(f"""WITH {DEDUPED_AWARDS_CTE_SQL} SELECT sum(CASE WHEN is_framework = 1 THEN 0 ELSE contract_value END)
                        FROM deduped_awards WHERE authority_name = %s""", (BUYER,))
        assert float(cur.fetchone()[0]) == EXPECTED_SPEND
    finally:
        _clean(conn)
        conn.close()
    print("ok    test_the_cte_counts_a_shared_value_once")


def test_buyer_profile_stats_and_radar_agree():
    from server import create_app
    from tender_app import market_radar as mr
    from tender_app.stats import refresh_buyer_stats
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _load(conn)
        c = create_app().test_client()
        with c.session_transaction() as s:
            s.update(logged_in=True, username="zz_shared", email="zz_shared@test.invalid", last_activity=time.time(), csrf_token="x")
        j = c.get("/api/buyers/" + BUYER.replace(" ", "%20")).get_json()
        assert float(j["stats"]["total_spend"]) == EXPECTED_SPEND, f"buyer profile: {j['stats']['total_spend']}"
        refresh_buyer_stats(conn)
        cur.execute("SELECT total_spend FROM buyer_stats WHERE authority_name = %s", (BUYER,))
        assert float(cur.fetchone()[0]) == EXPECTED_SPEND, "buyer_stats disagrees with the profile"
        cat = mr.resolve_category(None, "7200", None, {})
        rows, _ = mr.fetch_award_rows(cur, cat, "all", "all")
        mine = [r for r in rows if r["authority_name"] == BUYER and not r["is_framework"]]
        spend = sum(float(r["contract_value"]) for r in mine if r["contract_value"] is not None)
        # Market Radar only reads dated awards (window filter), so the dateless notice D is not in it
        assert spend == EXPECTED_SPEND - 4_000_000, f"market radar summed {spend}"
        a_rows = [r for r in mine if r["tender_title"].endswith(" A")]
        assert len(a_rows) == 3 and all(float(r["contract_value"]) == 3_000_000 for r in a_rows)
    finally:
        _clean(conn)
        conn.close()
    print("ok    test_buyer_profile_stats_and_radar_agree")


def test_supplier_pages_apportion_the_same_way():
    from tender_app.blueprints import suppliers_bp as sb
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _load(conn)
        cur.execute(f"SELECT contract_awards.id, tender_title, {sb._SHARED_N_SELECT} FROM contract_awards WHERE authority_name = %s", (BUYER,))
        n_by_title = {}
        for _id, title, n in cur.fetchall():
            n_by_title.setdefault(title.split()[-1], set()).add(n)
        assert n_by_title == {"A": {3}, "B": {1}, "C": {1}, "D": {4}, "E": {1}}, n_by_title
    finally:
        _clean(conn)
        conn.close()
    rows = [{"authority_name": "X", "date_signed": "2026-01-01", "contract_value": 9_000_000.0, "shared_n": 3, "is_framework": 0},
            {"authority_name": "Y", "date_signed": "2026-01-01", "contract_value": 50_000_000.0, "shared_n": 1, "is_framework": 1}]
    out = sb._dedup_recurring_awards(rows)
    assert out[0]["contract_value"] == 3_000_000 and out[0]["contract_value_notice"] == 9_000_000
    assert out[1]["contract_value"] == 50_000_000, "a framework ceiling is never split"
    print("ok    test_supplier_pages_apportion_the_same_way")


if __name__ == "__main__":
    test_award_columns_list_matches_the_table()
    test_the_cte_counts_a_shared_value_once()
    test_buyer_profile_stats_and_radar_agree()
    test_supplier_pages_apportion_the_same_way()
    print("4 passed, 0 skipped, 0 failed")
