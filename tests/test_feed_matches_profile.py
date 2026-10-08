"""The buyer feed, the buyer search and the buyer profile must show the same figures for the same buyer.

Round 28 item 2.1 (still open after the first fix): searching a buyer's name on the Buyer Intelligence feed showed 55 awards, GBP 75.2m and
a 10.9% repeat rate while its search result and profile said 53, GBP 74.8m and 7.5%. The feed's exact search aggregated the raw rows, the
other paths the de-duplicated ones. The feed now only chooses which buyers match and reads their figures from buyer_stats.
Uses zz_ fixture rows removed in `finally`.
Run: python tests/test_feed_matches_profile.py
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

from tender_app.db import get_db_connection

BUYER = "ZZ Feed Consistency Trust, St. Anne's"
CPV_TEXT = "zzcpvtext widget maintenance"
# a republished notice (same supplier, buyer and date) plus two real awards: 2 awards, not 3 raw rows
ROWS = [("zz_f1", "2026-01-01", 100_000), ("zz_f1", "2026-01-01", 150_000), ("zz_f2", "2026-02-01", 200_000), ("zz_f3", "2026-03-01", 50_000),
        ("zz_f3", "2026-04-01", 60_000), ("zz_f4", "2026-05-01", 70_000)]


def _client():
    from server import create_app
    c = create_app().test_client()
    with c.session_transaction() as s:
        s.update(logged_in=True, username="zz_feed", email="zz_feed@test.invalid", last_activity=time.time(), csrf_token="x")
    return c


def _card(feed):
    return next((c for c in feed.get("cards", []) if c["authority_name"] == BUYER), None)


def test_feed_search_agrees_with_search_and_profile():
    from tender_app.stats import refresh_buyer_stats
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        for sup, date, val in ROWS:
            cur.execute("INSERT INTO contract_awards (authority_name, supplier_name, date_signed, contract_value, cpv_code, cpv_description, source_portal) "
                        "VALUES (%s,%s,%s,%s,'50000000',%s,'Contracts Finder')", (BUYER, sup, date, val, CPV_TEXT))
        conn.commit()
        refresh_buyer_stats(conn)
        c = _client()
        name_q = BUYER.replace(" ", "%20")
        profile = c.get("/api/buyers/" + name_q).get_json()["stats"]
        search = next(b for b in c.get("/api/buyers/search?q=" + name_q).get_json()["buyers"] if b["authority_name"] == BUYER)
        by_name = _card(c.get("/api/buyers/opportunity-feed?q=" + name_q).get_json())
        by_cpv_text = _card(c.get("/api/buyers/opportunity-feed?q=zzcpvtext").get_json())
        fast = _card(c.get("/api/buyers/opportunity-feed?fast=1&q=" + name_q).get_json())
        assert profile["total_contracts"] == 5, f"fixture should de-duplicate 6 rows to 5 awards, profile says {profile['total_contracts']}"
        want = (profile["total_contracts"], float(profile["total_spend"]), profile["repeat_supplier_display"])
        assert (search["total_contracts"], float(search["total_spend"])) == want[:2]
        for label, card in (("feed (name search)", by_name), ("feed (CPV text search)", by_cpv_text), ("feed (fast)", fast)):
            assert card is not None, f"{label}: buyer missing from the feed"
            got = (card["total_contracts"], float(card["total_spend"]), card["repeat_supplier_display"])
            assert got == want, f"{label} shows {got}, the profile shows {want}"
    finally:
        conn.rollback()
        cur.execute("DELETE FROM contract_awards WHERE authority_name = %s", (BUYER,))
        cur.execute("DELETE FROM buyer_stats WHERE authority_name = %s", (BUYER,))
        conn.commit()
        conn.close()
    print("ok    test_feed_search_agrees_with_search_and_profile")


if __name__ == "__main__":
    test_feed_search_agrees_with_search_and_profile()
    print("1 passed, 0 skipped, 0 failed")
