"""Credit metering decorator for Flask routes."""
from __future__ import annotations

from functools import wraps

from flask import jsonify, session, request

from tender_app.config import LOW_CREDIT_THRESHOLD
from tender_app.credits import InsufficientCredits, consume_credits, refund_credits
from tender_app.email_svc import send_low_credit


def require_credits(cost: int, reason: str, get_db_connection, skip_if=None):
    """Deduct credits before handler; refund on 5xx failure.

    `skip_if` is an optional zero-arg callable evaluated per request; when it returns
    True the handler runs without charging (auth and role checks still apply). Used by
    Planning Leads so paging through one query's results is not charged per page.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            username = session.get("username", "")
            if not username:
                return jsonify({"error": "Unauthorized"}), 401
            
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                ph = "%s"
                cursor.execute(f"SELECT role FROM users WHERE username = {ph}", (username,))
                row = cursor.fetchone()
                role = row[0] if (row and row[0]) else "member"
                cursor.close()
            except Exception:
                role = "member"

            if role == "viewer":
                conn.close()
                return jsonify({"error": "Forbidden: viewers cannot perform actions that consume credits", "code": "ROLE_FORBIDDEN"}), 403

            # Skip credit consumption if this is a progressive search status poll (already paid for)
            is_search_poll = (reason == "search" and bool(request.args.get("jobId")))
            skip_charge = is_search_poll or cost <= 0 or bool(skip_if and skip_if())

            deducted = False
            try:
                if not skip_charge:
                    consume_credits(conn, username, cost, reason)
                    deducted = True
                conn.close()
                result = fn(*args, **kwargs)
                # Check low balance after success
                if deducted:
                    conn2 = get_db_connection()
                    try:
                        from tender_app.credits import get_balance
                        bal = get_balance(conn2, username)
                        if bal <= LOW_CREDIT_THRESHOLD:
                            send_low_credit(session.get("email", username), bal)
                    finally:
                        conn2.close()
                return result
            except InsufficientCredits as ex:
                conn.close()
                return jsonify({
                    "error": "Insufficient credits",
                    "code": "INSUFFICIENT_CREDITS",
                    "balance": ex.balance,
                    "required": ex.required,
                }), 402
            except Exception:
                if deducted:
                    try:
                        rconn = get_db_connection()
                        refund_credits(rconn, username, cost, f"refund_{reason}")
                        rconn.close()
                    except Exception:
                        pass
                conn.close()
                raise

        return wrapper

    return decorator
