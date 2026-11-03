"""Supplier Intelligence: the AI Summary costs 1 credit and the BD Playbook 3, and a user with no credits gets a 402 (not a free AI call).

Run: python tests/test_supplier_credits.py   (needs the dev database; the zz- user and its credits are removed in `finally`)"""
import os
import sys
import time
import unittest

os.environ.setdefault("ENABLE_SCHEDULERS", "0")
os.environ.setdefault("ENABLE_EMAIL_SCHEDULER", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_app.config import CREDIT_COST_BD_PLAYBOOK, CREDIT_COST_SUPPLIER_SUMMARY  # noqa: E402

USER = "zz_supplier_credits@test.invalid"


def test_the_costs_are_one_and_three():
    assert (CREDIT_COST_SUPPLIER_SUMMARY, CREDIT_COST_BD_PLAYBOOK) == (1, 3)


def test_summary_and_playbook_are_charged_and_refused_without_credits():
    import server
    from server import create_app
    from tender_app.credits import get_balance, grant_credits
    from tender_app.db import get_db_connection
    from tender_app.db_ext import run_saas_migrations
    try:
        conn = get_db_connection()
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"no database: {exc}")
    run_saas_migrations(conn)
    conn.commit()
    cur = conn.cursor()
    cur.execute("SELECT supplier_id FROM contract_awards WHERE supplier_id IS NOT NULL "
                "AND source_portal IN ('Contracts Finder', 'Find a Tender') LIMIT 1")
    row = cur.fetchone()
    conn.commit()   # no open transaction while the endpoints use their own connections
    if not row:
        raise unittest.SkipTest("no supplier with awards in this database")
    sid = row[0]
    app = create_app()
    app.config["TESTING"] = True
    real_json, real_api = server.call_chat_json, server.call_chat_api
    server.call_chat_json = lambda **kw: {}
    server.call_chat_api = lambda *a, **kw: "{}"
    try:
        c = app.test_client()
        with c.session_transaction() as s:
            s.update(logged_in=True, username=USER, email=USER, last_activity=time.time(), csrf_token="x")
        for url in (f"/api/suppliers/{sid}/summary", f"/api/suppliers/{sid}/bd-playbook"):
            r = c.get(url)
            assert r.status_code == 402 and r.get_json()["code"] == "INSUFFICIENT_CREDITS", (url, r.status_code)
        grant_credits(conn, USER, 1000, "test")   # well above the low-balance email threshold
        assert c.get(f"/api/suppliers/{sid}/summary").status_code == 200
        assert get_balance(conn, USER) == 999
        assert c.get(f"/api/suppliers/{sid}/bd-playbook").status_code == 200
        assert get_balance(conn, USER) == 996
    finally:
        server.call_chat_json, server.call_chat_api = real_json, real_api
        conn.rollback()
        for t in ("credit_lots", "credit_ledger", "credit_wallet"):
            cur.execute(f"DELETE FROM {t} WHERE username = %s", (USER,))
        conn.commit()
        conn.close()


if __name__ == "__main__":
    unittest.main()
