"""Credit wallet — atomic consume, grant, refund."""
from __future__ import annotations

from typing import Any, Callable


class InsufficientCredits(Exception):
    def __init__(self, balance: int, required: int):
        self.balance = balance
        self.required = required
        super().__init__(f"Insufficient credits: have {balance}, need {required}")


def get_balance(conn, username: str) -> int:
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
    if balance < cost:
        conn.rollback()
        cursor.close()
        raise InsufficientCredits(balance, cost)

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
