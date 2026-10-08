"""The de-duplication must pick the same row whichever way it is asked.

Live check after the Round 28 deploy: the Ministry of Defence card read GBP 23.3bn and its profile GBP 22.7bn (Ministry of Justice GBP 80m, Home Office
-GBP 13m), with identical contract counts. Several rows can share supplier, buyer, date AND value (the same award from two notices, or one flagged
framework). `DISTINCT ON ... ORDER BY value DESC` then picked an arbitrary one of them, and the stats refresh (all awards) and the buyer profile
(one buyer's awards) run different query plans, so they could pick different rows. Which row is kept changes which notice's shared value it splits,
so the totals drifted. The lowest id now wins the tie, in both paths.
Uses zz_ fixture rows removed in `finally`.
Run: python tests/test_dedupe_tie_break.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

from etenders_scraper.awards import DEDUPED_AWARDS_CTE_SQL, deduped_awards_cte_sql
from tender_app.db import get_db_connection

BUYER = "ZZ Tie Break Buyer"


def _clean(conn):
    cur = conn.cursor()
    cur.execute("DELETE FROM contract_awards WHERE authority_name = %s", (BUYER,))
    conn.commit()


def _insert(cur, notice):
    cur.execute("INSERT INTO contract_awards (authority_name, supplier_name, tender_title, cpv_code, date_signed, contract_value, is_framework, notice_url, source_portal, currency) "
                "VALUES (%s,'zz_tie_supplier','zz tie','72000000','2026-03-01',1000000,0,%s,'Contracts Finder','GBP') RETURNING id", (BUYER, f"https://zz.invalid/tie/{notice}"))
    return cur.fetchone()[0]


def test_a_tie_keeps_the_lowest_id_in_both_paths():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        ids = [_insert(cur, str(n)) for n in range(8)]
        first = min(ids)
        # Rewrite the rows newest-first so the table holds them in reverse id order: an unordered tie then keeps a later row
        # (with the old ORDER BY it kept the 5th of 8 here, checked on the fixture below), and only the id tie-break keeps the first.
        for i in sorted(ids):
            cur.execute("UPDATE contract_awards SET tender_title = 'zz tie again' WHERE id = %s", (i,))
        for i in sorted(ids, reverse=True):
            cur.execute("UPDATE contract_awards SET tender_title = 'zz tie again 2' WHERE id = %s", (i,))
        conn.commit()

        cur.execute(f"WITH {DEDUPED_AWARDS_CTE_SQL} SELECT id FROM deduped_awards WHERE authority_name = %s", (BUYER,))
        everyone = [r[0] for r in cur.fetchall()]
        cur.execute(f"WITH {deduped_awards_cte_sql('authority_name = %s')} SELECT id FROM deduped_awards WHERE authority_name = %s", (BUYER, BUYER, BUYER))
        one_buyer = [r[0] for r in cur.fetchall()]
        assert everyone == [first], f"all-awards path kept {everyone}, expected the lowest id {first}"
        assert one_buyer == [first], f"one-buyer path kept {one_buyer}, expected the lowest id {first}"
        # (Without the tie-break the old ORDER BY kept the 5th of these 8 on the machine this was written on, but which row an
        # unordered tie keeps depends on the table's physical layout, so that is not asserted: this test pins the rule, and the
        # source check below pins that the ORDER BY carries it.)
        print("ok    test_a_tie_keeps_the_lowest_id_in_both_paths")
    finally:
        _clean(conn)
        conn.close()


def test_the_supplier_page_breaks_ties_the_same_way():
    """The supplier detail page de-duplicates in Python; it kept whichever tied row it met first, so it could disagree with supplier_stats
    (BAE Systems Surface Ships: card GBP 2,268m, detail page GBP 2,426m on live)."""
    from tender_app.blueprints.suppliers_bp import _dedup_recurring_awards
    def award(i):
        return {"id": i, "authority_name": "ZZ Tie Buyer", "date_signed": "2026-03-01", "contract_value": 1_000_000, "is_framework": 0}
    for order in ([9, 5, 7], [5, 9, 7], [7, 9, 5]):
        kept = _dedup_recurring_awards([award(i) for i in order])
        assert [a["id"] for a in kept] == [5], f"order {order} kept {[a['id'] for a in kept]}, expected the lowest id 5"
    higher = {**award(9), "contract_value": 2_000_000}
    assert [a["id"] for a in _dedup_recurring_awards([award(5), higher])] == [9]   # a higher value still wins over a lower id
    print("ok    test_the_supplier_page_breaks_ties_the_same_way")


def test_the_order_by_names_the_tie_break():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    src = (root / "etenders_scraper" / "awards.py").read_text(encoding="utf-8")
    assert "a.contract_value DESC NULLS LAST, a.id" in src, "the dedupe's ORDER BY lost its id tie-break"
    # Market Radar and Growth Studio keep their own copy of the same de-duplication; it must break ties the same way
    for rel in ("tender_app/market_radar.py", "tender_app/growth_studio.py"):
        assert "c.date_signed, c.contract_value DESC NULLS LAST, c.id" in (root / rel).read_text(encoding="utf-8"), f"{rel} lost its id tie-break"
    print("ok    test_the_order_by_names_the_tie_break")


if __name__ == "__main__":
    test_a_tie_keeps_the_lowest_id_in_both_paths()
    test_the_supplier_page_breaks_ties_the_same_way()
    test_the_order_by_names_the_tie_break()
    print("3 passed, 0 skipped, 0 failed")
