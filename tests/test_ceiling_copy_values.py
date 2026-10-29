"""A call-off published with its framework's value is not money won (Supplier Intelligence S1).

Eleven small MoD repair call-offs, each stamped with the framework's GBP 317m, made one supplier look like a GBP 3bn winner. The shared
award rule (etenders_scraper.awards.deduped_awards_cte_sql) now treats the same large value repeated on several call-offs of one buyer as
the framework ceiling: the award still counts, its value does not. Frameworks themselves, other procedures and small repeated values are
left alone. Uses zz_ fixture rows removed in `finally`.
Run: python tests/test_ceiling_copy_values.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

from etenders_scraper.awards import CEILING_COPY_SQL, DEDUPED_AWARDS_CTE_SQL
from tender_app.db import get_db_connection

BUYER = "ZZ Ceiling Buyer"
CALL_OFF = "Framework / Call-off"
# (group, supplier, value, procurement_type, is_framework, how many rows)
GROUPS = [
    ("big4", "zz_a", 317_000_000, CALL_OFF, 0, 4),      # the MSS/142 case: flagged
    ("pair", "zz_b", 50_000_000, CALL_OFF, 0, 2),       # two is a coincidence: kept
    ("small4", "zz_c", 210_000, CALL_OFF, 0, 4),        # small values need five matches: kept
    ("small5", "zz_d", 200_000, CALL_OFF, 0, 5),        # five identical orders of 200k: flagged
    ("open3", "zz_e", 5_000_000, "Open competition", 0, 3),  # an ordinary procedure: kept
    ("fw3", "zz_f", 80_000_000, CALL_OFF, 1, 3),        # the framework notices themselves keep their ceiling: kept
]


def _load(conn):
    cur = conn.cursor()
    n = 0
    for group, sup, val, ptype, fw, count in GROUPS:
        for i in range(count):
            n += 1
            cur.execute(
                "INSERT INTO contract_awards (authority_name, supplier_name, tender_title, cpv_code, date_signed, contract_value, is_framework, procurement_type, notice_url, source_portal, currency) "
                "VALUES (%s,%s,%s,'72000000',%s,%s,%s,%s,%s,'Contracts Finder','GBP')",
                (BUYER, f"{sup}_{i}", f"zz {group} {i}", f"2026-02-{(i % 27) + 1:02d}", val, fw, ptype, f"https://zz.invalid/ceiling/{group}/{i}"))
    conn.commit()


def _clean(conn):
    cur = conn.cursor()
    cur.execute("DELETE FROM contract_awards WHERE authority_name = %s", (BUYER,))
    conn.commit()


def test_sql_rule_is_tiered():
    assert "1000000" in CEILING_COPY_SQL and ">= 3" in CEILING_COPY_SQL and "100000" in CEILING_COPY_SQL and ">= 5" in CEILING_COPY_SQL
    assert "Framework / Call-off" in CEILING_COPY_SQL and "COALESCE(q.is_framework, 0) = 0" in CEILING_COPY_SQL


def test_repeated_framework_values_are_not_won_value():
    import unittest
    try:
        conn = get_db_connection()
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"no database: {exc}")
    _clean(conn)
    try:
        _load(conn)
        cur = conn.cursor()
        cur.execute(
            f"""WITH {DEDUPED_AWARDS_CTE_SQL}
                SELECT split_part(tender_title, ' ', 2) AS grp, COUNT(*) AS n, COUNT(*) FILTER (WHERE ceiling_copy) AS flagged,
                       COUNT(contract_value) AS valued, MAX(contract_value_notice) AS notice_value
                FROM deduped_awards WHERE authority_name = %s GROUP BY 1 ORDER BY 1""", (BUYER,))
        got = {r[0]: r[1:] for r in cur.fetchall()}
        assert got["big4"][:3] == (4, 4, 0), got["big4"]             # all four flagged, no value left
        assert got["big4"][3] == 317_000_000, "the published value is still kept for reference"
        assert got["pair"][:3] == (2, 0, 2), got["pair"]
        assert got["small4"][:3] == (4, 0, 4), got["small4"]
        assert got["small5"][:3] == (5, 5, 0), got["small5"]
        assert got["open3"][:3] == (3, 0, 3), got["open3"]
        assert got["fw3"][:3] == (3, 0, 3), got["fw3"]
        # what a supplier would be credited with: only the values that are really theirs
        cur.execute(
            f"""WITH {DEDUPED_AWARDS_CTE_SQL}
                SELECT COALESCE(SUM(contract_value) FILTER (WHERE COALESCE(is_framework, 0) = 0), 0) FROM deduped_awards WHERE authority_name = %s""", (BUYER,))
        assert float(cur.fetchone()[0]) == 2 * 50_000_000 + 4 * 210_000 + 3 * 5_000_000
    finally:
        _clean(conn)
        conn.close()


PANEL_BUYER = "ZZ Panel Buyer"


def test_a_panel_notice_value_is_not_split_into_made_up_shares():
    """Five or more suppliers on one notice at one identical value (a marketplace / panel) have no value of their own: the
    GBP 1.1bn children's-residential marketplace named 30 providers at GBP 1.1bn each, and splitting gave each a share nobody
    published. Two to four suppliers stay a split joint award; a small panel is left alone."""
    import unittest
    try:
        conn = get_db_connection()
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"no database: {exc}")
    cur = conn.cursor()
    cur.execute("DELETE FROM contract_awards WHERE authority_name = %s", (PANEL_BUYER,))
    try:
        for notice, n, val in (("p5", 5, 20_000_000), ("p4", 4, 20_000_000), ("small6", 6, 400_000)):
            for i in range(n):
                cur.execute(
                    "INSERT INTO contract_awards (authority_name, supplier_name, tender_title, cpv_code, date_signed, contract_value, is_framework, procurement_type, notice_url, source_portal, currency) "
                    "VALUES (%s,%s,%s,'72000000','2026-02-01',%s,0,'Restricted procedure',%s,'Contracts Finder','GBP')",
                    (PANEL_BUYER, f"zz_panel_{notice}_{i}", f"zz {notice}", val, f"https://zz.invalid/panel/{notice}"))
        conn.commit()
        cur.execute(
            f"""WITH {DEDUPED_AWARDS_CTE_SQL}
                SELECT split_part(tender_title, ' ', 2), COUNT(*), COUNT(*) FILTER (WHERE ceiling_copy), COALESCE(SUM(contract_value), 0)
                FROM deduped_awards WHERE authority_name = %s GROUP BY 1 ORDER BY 1""", (PANEL_BUYER,))
        got = {r[0]: r[1:] for r in cur.fetchall()}
        assert got["p5"] == (5, 5, 0), got["p5"]
        assert got["p4"][:2] == (4, 0) and float(got["p4"][2]) == 20_000_000, got["p4"]
        assert got["small6"][:2] == (6, 0), got["small6"]
    finally:
        cur.execute("DELETE FROM contract_awards WHERE authority_name = %s", (PANEL_BUYER,))
        conn.commit()
        conn.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
