"""Credit wallet — atomic consume, grant, refund."""
from __future__ import annotations

import os
from typing import Any, Callable

# Every grant (plan credits, packs, signup, admin, refunds) lives in a lot that expires
# after this many days. Spending takes from the lot that expires first.
CREDIT_EXPIRY_DAYS = int(os.environ.get("CREDIT_EXPIRY_DAYS", "90"))
EXPIRY_REMINDER_DAYS = 14


class InsufficientCredits(Exception):
    def __init__(self, balance: int, required: int):
        self.balance = balance
        self.required = required
        super().__init__(f"Insufficient credits: have {balance}, need {required}")


def _settle(cursor, username: str, balance: int) -> tuple[int, int]:
    """Inside the caller's transaction, with the wallet row locked and `balance` just read
    from it: give credits that have no lot (written straight into credit_wallet, or held
    before lots existed) a lot, then expire lots past their date. Returns
    (balance, expired)."""
    ph = "%s"
    cursor.execute(
        f"SELECT COALESCE(SUM(amount_left), 0) FROM credit_lots WHERE username = {ph}",
        (username,),
    )
    unlotted = balance - int(cursor.fetchone()[0])
    if unlotted > 0:
        cursor.execute(
            f"""INSERT INTO credit_lots (username, amount_left, source, expires_at)
                VALUES ({ph}, {ph}, 'unlotted', CURRENT_TIMESTAMP + ({ph} * INTERVAL '1 day'))""",
            (username, unlotted, CREDIT_EXPIRY_DAYS),
        )
    cursor.execute(
        f"""SELECT id, amount_left FROM credit_lots
            WHERE username = {ph} AND amount_left > 0 AND expires_at <= CURRENT_TIMESTAMP
            FOR UPDATE""",
        (username,),
    )
    due = cursor.fetchall()
    expired = sum(int(r[1]) for r in due)
    if expired <= 0:
        return balance, 0
    cursor.execute(
        f"UPDATE credit_lots SET amount_left = 0 WHERE id = ANY({ph})",
        ([int(r[0]) for r in due],),
    )
    new_balance = max(0, balance - expired)
    cursor.execute(
        f"UPDATE credit_wallet SET balance = {ph}, updated_at = CURRENT_TIMESTAMP WHERE username = {ph}",
        (new_balance, username),
    )
    cursor.execute(
        f"""INSERT INTO credit_ledger (username, delta, balance_after, reason, ref_id)
            VALUES ({ph}, {ph}, {ph}, 'credit_expiry', '')""",
        (username, -(balance - new_balance), new_balance),
    )
    return new_balance, balance - new_balance


def expire_due_credits(conn, username: str) -> int:
    """Take expired credits off the wallet (one 'credit_expiry' ledger row). Returns how
    many expired."""
    ph = "%s"
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"SELECT balance FROM credit_wallet WHERE username = {ph} FOR UPDATE", (username,)
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return 0
        _, expired = _settle(cursor, username, int(row[0]))
        conn.commit()
        return expired
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def get_expiring_lots(conn, username: str, within_days: int | None = None) -> list[dict]:
    """Live lots, soonest expiry first: [{amount, expires_at}]."""
    ph = "%s"
    cursor = conn.cursor()
    sql = f"""SELECT amount_left, expires_at FROM credit_lots
              WHERE username = {ph} AND amount_left > 0 AND expires_at > CURRENT_TIMESTAMP"""
    params: list = [username]
    if within_days is not None:
        sql += f" AND expires_at <= CURRENT_TIMESTAMP + ({ph} * INTERVAL '1 day')"
        params.append(within_days)
    cursor.execute(sql + " ORDER BY expires_at, id", params)
    rows = cursor.fetchall()
    cursor.close()
    return [{"amount": int(r[0]), "expires_at": str(r[1])} for r in rows]


def get_balance(conn, username: str) -> int:
    expire_due_credits(conn, username)
    ph = "%s"
    cursor = conn.cursor()
    cursor.execute(f"SELECT balance FROM credit_wallet WHERE username = {ph}", (username,))
    row = cursor.fetchone()
    cursor.close()
    return int(row[0]) if row else 0


def ensure_wallet(conn, username: str, initial: int = 0) -> None:
    ph = "%s"
    cursor = conn.cursor()
    cursor.execute(
        f"INSERT INTO credit_wallet (username, balance) VALUES ({ph}, {ph}) ON CONFLICT DO NOTHING",
        (username, initial),
    )
    conn.commit()
    cursor.close()


def grant_credits(
    conn,
    username: str,
    amount: int,
    reason: str,
    ref_id: str = "",
) -> int:
    """Add credits; returns new balance."""
    if amount <= 0:
        return get_balance(conn, username)
    ph = "%s"
    cursor = conn.cursor()
    ensure_wallet(conn, username)
    cursor.execute(
        f"""UPDATE credit_wallet SET balance = balance + {ph}, updated_at = CURRENT_TIMESTAMP
            WHERE username = {ph} RETURNING balance""",
        (amount, username),
    )
    new_balance = int(cursor.fetchone()[0])
    cursor.execute(
        f"""INSERT INTO credit_lots (username, amount_left, source, expires_at)
            VALUES ({ph}, {ph}, {ph}, CURRENT_TIMESTAMP + ({ph} * INTERVAL '1 day'))""",
        (username, amount, reason, CREDIT_EXPIRY_DAYS),
    )
    cursor.execute(
        f"""INSERT INTO credit_ledger (username, delta, balance_after, reason, ref_id)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph})""",
        (username, amount, new_balance, reason, ref_id),
    )
    conn.commit()
    cursor.close()
    return new_balance


def consume_credits(
    conn,
    username: str,
    cost: int,
    reason: str,
    ref_id: str = "",
) -> int:
    """Atomically deduct credits. Raises InsufficientCredits if balance too low."""
    if cost <= 0:
        return get_balance(conn, username)
    ph = "%s"
    cursor = conn.cursor()
    ensure_wallet(conn, username)

    cursor.execute(
        f"SELECT balance FROM credit_wallet WHERE username = {ph} FOR UPDATE",
        (username,),
    )

    row = cursor.fetchone()
    balance = int(row[0]) if row else 0
    balance, _ = _settle(cursor, username, balance)
    if balance < cost:
        # Commit, not roll back: the expiry just applied must stick.
        conn.commit()
        cursor.close()
        raise InsufficientCredits(balance, cost)

    remaining = cost
    cursor.execute(
        f"""SELECT id, amount_left FROM credit_lots
            WHERE username = {ph} AND amount_left > 0
            ORDER BY expires_at, id FOR UPDATE""",
        (username,),
    )
    for lot_id, left in cursor.fetchall():
        if remaining <= 0:
            break
        take = min(int(left), remaining)
        cursor.execute(
            f"UPDATE credit_lots SET amount_left = amount_left - {ph} WHERE id = {ph}",
            (take, lot_id),
        )
        remaining -= take

    new_balance = balance - cost
    cursor.execute(
        f"UPDATE credit_wallet SET balance = {ph}, updated_at = CURRENT_TIMESTAMP WHERE username = {ph}",
        (new_balance, username),
    )
    cursor.execute(
        f"""INSERT INTO credit_ledger (username, delta, balance_after, reason, ref_id)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph})""",
        (username, -cost, new_balance, reason, ref_id),
    )
    conn.commit()
    cursor.close()
    return new_balance


def refund_credits(
    conn,
    username: str,
    amount: int,
    reason: str,
    ref_id: str = "",
) -> int:
    return grant_credits(conn, username, amount, reason, ref_id)


def credit_guard(
    get_db_connection: Callable[[], Any],
    username: str,
    cost: int,
    reason: str,
    ref_id: str = "",
):
    """Context manager pattern via helper — returns (conn, new_balance) or raises."""
    conn = get_db_connection()
    try:
        new_bal = consume_credits(conn, username, cost, reason, ref_id)
        return conn, new_bal
    except Exception:
        conn.close()
        raise


def get_ledger(conn, username: str, limit: int = 50) -> list[dict]:
    ph = "%s"
    cursor = conn.cursor()
    cursor.execute(
        f"""SELECT id, delta, balance_after, reason, ref_id, created_at
            FROM credit_ledger WHERE username = {ph}
            ORDER BY created_at DESC LIMIT {limit}""",
        (username,),
    )
    rows = cursor.fetchall()
    cursor.close()
    return [
        {
            "id": r[0],
            "delta": r[1],
            "balance_after": r[2],
            "reason": r[3],
            "ref_id": r[4],
            "created_at": str(r[5]),
        }
        for r in rows
    ]


def run_credit_expiry_sweep(get_db_connection: Callable[[], Any]) -> dict:
    """Daily job: expire due credits for everyone, then email a reminder once per lot
    that will expire within EXPIRY_REMINDER_DAYS."""
    from tender_app.email_svc import send_credit_expiry_reminder

    conn = get_db_connection()
    out = {"expired_users": 0, "reminders": 0}
    try:
        ph = "%s"
        cur = conn.cursor()
        cur.execute(
            """SELECT DISTINCT username FROM credit_lots
               WHERE amount_left > 0 AND expires_at <= CURRENT_TIMESTAMP"""
        )
        due_users = [r[0] for r in cur.fetchall()]
        for u in due_users:
            if expire_due_credits(conn, u):
                out["expired_users"] += 1

        cur.execute(
            f"""SELECT username, id, amount_left, expires_at FROM credit_lots
                WHERE amount_left > 0 AND reminded_at IS NULL
                  AND expires_at > CURRENT_TIMESTAMP
                  AND expires_at <= CURRENT_TIMESTAMP + ({ph} * INTERVAL '1 day')
                ORDER BY username, expires_at""",
            (EXPIRY_REMINDER_DAYS,),
        )
        by_user: dict[str, list] = {}
        for u, lot_id, left, exp in cur.fetchall():
            by_user.setdefault(u, []).append((lot_id, int(left), exp))
        for u, lots in by_user.items():
            cur.execute(
                f"SELECT email FROM users WHERE username = {ph} OR email = {ph} LIMIT 1", (u, u)
            )
            row = cur.fetchone()
            to = (row[0] if row and row[0] else u) or ""
            if "@" not in to:
                continue
            if send_credit_expiry_reminder(to, [(n, e) for _, n, e in lots]):
                cur.execute(
                    f"UPDATE credit_lots SET reminded_at = CURRENT_TIMESTAMP WHERE id = ANY({ph})",
                    ([i for i, _, _ in lots],),
                )
                conn.commit()
                out["reminders"] += 1
        cur.close()
    finally:
        conn.close()
    return out
