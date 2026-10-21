"""Credit lots: oldest-expiring credits are spent first, expired credits leave the balance,
reminders go out once. Run: python tests/test_credit_expiry.py (needs the db container)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_app import credits  # noqa: E402
from tender_app.credits import (  # noqa: E402
    InsufficientCredits, consume_credits, expire_due_credits, get_balance, get_expiring_lots,
    grant_credits, run_credit_expiry_sweep,
)
from tender_app.db import get_db_connection  # noqa: E402
from tender_app.db_ext import run_saas_migrations  # noqa: E402

U = "zz_credit_expiry_probe@example.test"
U2 = "zz_credit_expiry_probe2@example.test"


def clean(conn):
    cur = conn.cursor()
    for t in ("credit_lots", "credit_ledger", "credit_wallet"):
        cur.execute(f"DELETE FROM {t} WHERE username = ANY(%s)", ([U, U2],))
    conn.commit()
    cur.close()


def age_lot(conn, lot_id, days_from_now):
    cur = conn.cursor()
    cur.execute(
        "UPDATE credit_lots SET expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 day') WHERE id = %s",
        (days_from_now, lot_id),
    )
    conn.commit()
    cur.close()


def lot_ids(conn, user):
    cur = conn.cursor()
    cur.execute("SELECT id FROM credit_lots WHERE username = %s ORDER BY id", (user,))
    ids = [r[0] for r in cur.fetchall()]
    cur.close()
    return ids


def main():
    conn = get_db_connection()
    cur = conn.cursor()
    run_saas_migrations(conn)
    clean(conn)
    try:
        # A grant makes a lot that expires in CREDIT_EXPIRY_DAYS.
        assert grant_credits(conn, U, 100, "subscription_renewal") == 100
        lots = get_expiring_lots(conn, U)
        assert len(lots) == 1 and lots[0]["amount"] == 100
        cur.execute(
            "SELECT expires_at - CURRENT_TIMESTAMP > INTERVAL '89 days' FROM credit_lots WHERE username=%s", (U,)
        )
        assert cur.fetchone()[0] is True

        # Spending takes from the lot that expires first, whatever the grant order.
        grant_credits(conn, U, 50, "pack_purchase")
        a, b = lot_ids(conn, U)
        age_lot(conn, a, 40)
        age_lot(conn, b, 10)  # the pack expires sooner
        assert consume_credits(conn, U, 30, "t") == 120
        left = {r["amount"]: r["expires_at"] for r in get_expiring_lots(conn, U)}
        assert sorted(left) == [20, 100], left  # pack 50 -> 20 first

        # Expired credits come off the balance and leave one ledger row.
        age_lot(conn, b, -1)
        assert expire_due_credits(conn, U) == 20
        assert get_balance(conn, U) == 100
        cur.execute("SELECT delta FROM credit_ledger WHERE username=%s AND reason='credit_expiry'", (U,))
        assert [r[0] for r in cur.fetchall()] == [-20]
        assert expire_due_credits(conn, U) == 0  # nothing twice

        # Expiry applies when spending, and a failed spend still keeps the expiry.
        age_lot(conn, a, -1)
        try:
            consume_credits(conn, U, 1, "t")
            raise AssertionError("expected InsufficientCredits")
        except InsufficientCredits as e:
            assert e.balance == 0
        assert get_balance(conn, U) == 0

        # Credits written straight into the wallet get a lot instead of living forever.
        cur.execute("INSERT INTO credit_wallet (username, balance) VALUES (%s, 7)", (U2,))
        conn.commit()
        assert get_balance(conn, U2) == 7
        assert [l["amount"] for l in get_expiring_lots(conn, U2)] == [7]
        assert consume_credits(conn, U2, 7, "t") == 0

        # Reminder: once per lot, only inside the reminder window.
        sent = []
        import tender_app.email_svc as email_svc
        email_svc.send_credit_expiry_reminder = lambda to, lots: sent.append((to, lots)) or True
        grant_credits(conn, U, 40, "subscription_renewal")
        grant_credits(conn, U, 60, "subscription_renewal")
        near, far = lot_ids(conn, U)[-2:]
        age_lot(conn, near, 5)
        age_lot(conn, far, 60)
        out = run_credit_expiry_sweep(get_db_connection)
        assert out["reminders"] == 1 and sent[0][0] == U and [n for n, _ in sent[0][1]] == [40], (out, sent)
        run_credit_expiry_sweep(get_db_connection)
        assert len(sent) == 1, "reminded twice"
        print("credit expiry: all checks passed")
    finally:
        conn.rollback()
        clean(conn)
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
