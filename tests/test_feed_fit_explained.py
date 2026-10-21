"""The buyer feed explains each match score and can filter by it.

Each card carries fit_factors (four weighted components with a sentence each) and an estimated engagement likelihood, and
?min_match=N drops cards scoring below N. Uses a zz_ fixture buyer removed in `finally`.
Run: python tests/test_feed_fit_explained.py   (needs the dev database)"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("ENABLE_EMAIL_SCHEDULER", "0")
os.environ.setdefault("ENABLE_SCHEDULERS", "0")

BUYER = "ZZ Fit Explained Borough Council"


def test_cards_explain_the_score_and_min_match_filters():
    from server import create_app
    from tender_app.db import get_db_connection
    from tender_app.stats import refresh_buyer_stats
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        for i in range(6):
            cur.execute("INSERT INTO contract_awards (authority_name, supplier_name, date_signed, contract_value, cpv_code, cpv_description, source_portal) "
                        "VALUES (%s,%s,%s,%s,'50000000','zzfitexplained repairs','Contracts Finder')", (BUYER, f"zz_s{i}", f"2026-0{i + 1}-01", 100_000 + i))
        conn.commit()
        refresh_buyer_stats(conn)
        c = create_app().test_client()
        with c.session_transaction() as s:
            s.update(logged_in=True, username="zz_fitexp", email="zz_fitexp@test.invalid", last_activity=time.time(), csrf_token="x")
        q = "q=" + BUYER.replace(" ", "%20")
        card = next(x for x in c.get(f"/api/buyers/opportunity-feed?{q}").get_json()["cards"] if x["authority_name"] == BUYER)
        assert [f["key"] for f in card["fit_factors"]] == ["sector", "proximity", "renewal", "openness"], card["fit_factors"]
        assert all(f["note"] and 0 <= f["score"] <= 100 for f in card["fit_factors"])
        assert card["engagement"]["level"] in ("high", "medium", "low") and 5 <= card["engagement"]["percent"] <= 95
        score = card["match_score"]
        keep = c.get(f"/api/buyers/opportunity-feed?{q}&min_match={score}").get_json()["cards"]
        drop = c.get(f"/api/buyers/opportunity-feed?{q}&min_match={score + 1}").get_json()["cards"]
        assert any(x["authority_name"] == BUYER for x in keep), "a card at exactly the minimum stays"
        assert not any(x["authority_name"] == BUYER for x in drop), "a card below the minimum goes"
        assert c.get(f"/api/buyers/opportunity-feed?{q}&min_match=abc").status_code == 200   # junk is ignored, not an error
    finally:
        conn.rollback()
        cur.execute("DELETE FROM contract_awards WHERE authority_name = %s", (BUYER,))
        cur.execute("DELETE FROM buyer_stats WHERE authority_name = %s", (BUYER,))
        conn.commit()
        conn.close()
    print("ok    test_cards_explain_the_score_and_min_match_filters")


if __name__ == "__main__":
    test_cards_explain_the_score_and_min_match_filters()
    print("1 passed, 0 skipped, 0 failed")
