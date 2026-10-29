"""One company's records come together whatever was searched (Supplier Intelligence S2).

"Capita" showed Capita Business Services Limited with 330 awards and "Capita plc" showed 268: the card adds the records of one
company, but only those that matched the typed text. The search now brings in the other records that share the name key
(supplier_stats.entity_key) before merging, so the totals do not depend on the query. Uses zz_ fixture rows removed in `finally`.
Run: python tests/test_supplier_sibling_merge.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

from tender_app import stats
from tender_app.blueprints import suppliers_bp as sb
from tender_app.db import get_db_connection
from tender_app.entity_merge import entity_name_key, merge_duplicate_suppliers

# (name, company number, awards, value)
ROWS = [
    ("ZZQ Holdings Limited", "09990001", 40, 4_000_000.0),
    ("ZZQ Holdings", "Not available", 10, 1_000_000.0),     # same company, published without a number
    ("ZZQ Holdings Ltd", "09990002", 5, 500_000.0),         # a different company number: stays apart
    ("ZZQ Other Services", "09990003", 3, 300_000.0),
]


def test_name_key_in_python_and_sql_agree():
    import unittest
    try:
        conn = get_db_connection()
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"no database: {exc}")
    for name in ("ZZQ Holdings Limited", "ZZQ Holdings", "Smith & Sons Ltd", "O'Brien (T/A Obs) PLC", "A.B.C. Co"):
        cur = conn.cursor()
        cur.execute("SELECT " + stats.ENTITY_KEY_SQL.format(col="%s"), (name,))
        assert cur.fetchone()[0] == entity_name_key(name), name
    conn.close()


def _clean(conn):
    cur = conn.cursor()
    cur.execute("DELETE FROM supplier_stats WHERE supplier_id IN (SELECT id FROM suppliers WHERE name LIKE 'ZZQ %')")
    cur.execute("DELETE FROM suppliers WHERE name LIKE 'ZZQ %'")
    conn.commit()


def test_siblings_are_found_and_merged_whatever_the_query():
    import unittest
    try:
        conn = get_db_connection()
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"no database: {exc}")
    _clean(conn)
    try:
        cur = conn.cursor()
        stats.ensure_stats_tables(cur)
        ids = {}
        for name, cnum, awards, value in ROWS:
            cur.execute("INSERT INTO suppliers (company_number, name) VALUES (%s, %s) RETURNING id", (cnum if cnum != "Not available" else f"NO_REG_{name}", name))
            ids[name] = sid = cur.fetchone()[0]
            cur.execute(
                f"INSERT INTO supplier_stats (supplier_id, listable, total_awards, total_value, avg_value, entity_key) "
                f"VALUES (%s, TRUE, %s, %s, 0, {stats.ENTITY_KEY_SQL.format(col='%s')})", (sid, awards, value, name))
        conn.commit()

        # a search that matches only the first record still finds the second (same key, no number); the third has another number
        first = [ids["ZZQ Holdings Limited"]]
        siblings = sb._sibling_ids(cur, conn, first)
        assert set(siblings) == {ids["ZZQ Holdings"], ids["ZZQ Holdings Ltd"]}, siblings
        assert ids["ZZQ Other Services"] not in siblings

        def card_for(matched_ids):
            rows, _n = sb._supplier_page_from_stats(cur, conn, None, False, False, False, None, None, id_filter=matched_ids)
            shaped = sb._shape_supplier_rows(cur, rows, False, False)
            sib_rows, _n = sb._supplier_page_from_stats(cur, conn, None, False, False, False, None, None, id_filter=sb._sibling_ids(cur, conn, matched_ids) or [0])
            shaped += sb._shape_supplier_rows(cur, sib_rows, False, False)
            return merge_duplicate_suppliers(shaped)

        by_first = card_for([ids["ZZQ Holdings Limited"]])
        by_second = card_for([ids["ZZQ Holdings"]])
        main = lambda cards: next(c for c in cards if c["company_number"] == "09990001")  # noqa: E731
        assert (main(by_first)["total_awards"], main(by_first)["total_value"]) == (50, 5_000_000.0), "the numberless record is merged in"
        assert (main(by_second)["total_awards"], main(by_second)["total_value"]) == (50, 5_000_000.0), "and the total does not depend on which record matched"
        assert any(c["company_number"] == "09990002" and c["total_awards"] == 5 for c in by_first), "a different company number stays a separate card"
    finally:
        _clean(conn)
        conn.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
