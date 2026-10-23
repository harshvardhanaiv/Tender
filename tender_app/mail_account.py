"""The user's own email accounts for Growth Studio: storage and the shape the page sees.

Outreach's API cannot add a mailbox to an Outreach account, so a user who has none there sets up their own accounts right in
the Connections window: ordinary SMTP logins (Gmail, Outlook, Zoho or any provider). A user can keep up to MAX_ACCOUNTS; one of
them is the default (what Send from picks when nothing else is chosen) and each has its own daily limit. Growth Studio then sends
from the one chosen. Checking the login and sending are in user_mailer.py; this module only keeps the settings.

The password is kept Fernet-encrypted with MAIL_CRED_KEY (as a pasted Outreach key is) and is never returned by any route
or written to a log. The HTTP side is in blueprints/growth_bp.py.
"""
from __future__ import annotations

from typing import Any

from psycopg2 import errors as pg_errors

from tender_app import user_mailer as um
from tender_app.db_ext import create_user_mail_accounts

MAX_ACCOUNTS = 5

_ready = False
_COLUMNS = ("id, is_default, provider, host, port, security, login, password_enc, from_email, from_name, reply_to, daily_cap, "
            "verified_at, last_error")


def _ensure(conn) -> None:
    global _ready
    if _ready:
        return
    with conn.cursor() as cur:
        create_user_mail_accounts(cur)
    conn.commit()
    _ready = True


def _rows(cur) -> list[dict[str, Any]]:
    names = _COLUMNS.split(", ")
    return [dict(zip(names, r)) for r in cur.fetchall()]


def list_all(conn, username: str) -> list[dict[str, Any]]:
    """Every saved account of the user, the default first, passwords still encrypted."""
    _ensure(conn)
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM user_mail_accounts WHERE username = %s ORDER BY is_default DESC, id", (username,))
        return _rows(cur)


def get(conn, username: str, account_id: int | None = None) -> dict[str, Any] | None:
    """One saved account, password still encrypted (use password_for), or None. Without an id: the default account."""
    accounts = list_all(conn, username)
    if account_id is None:
        return accounts[0] if accounts else None
    return next((a for a in accounts if a["id"] == account_id), None)


def find_by_address(conn, username: str, from_email: str) -> dict[str, Any] | None:
    """The user's account that sends from this address (addresses are compared without regard to case)."""
    want = (from_email or "").strip().lower()
    return next((a for a in list_all(conn, username) if a["from_email"].lower() == want), None)


def password_for(account: dict[str, Any], cred_key: str) -> str:
    return um.decrypt_secret(account["password_enc"], cred_key)


def smtp_settings(account: dict[str, Any]) -> dict[str, Any]:
    """What user_mailer needs to connect and to write the From line."""
    return {k: account[k] for k in ("host", "port", "security", "login", "from_email", "from_name", "reply_to")}


def sent_counts(conn, username: str) -> dict[int, int]:
    """Emails sent in the last 24 hours, by account id (the daily limits count these). Sends recorded before accounts were
    told apart belong to the default account."""
    accounts = list_all(conn, username)
    if not accounts:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT mail_account_id, COUNT(*) FROM growth_outreach_drafts WHERE username = %s AND mode = 'smtp' AND status = 'sent' "
            "AND checked_at >= NOW() - INTERVAL '24 hours' GROUP BY mail_account_id",
            (username,),
        )
        counts = {r[0]: int(r[1]) for r in cur.fetchall()}
    out = {a["id"]: counts.get(a["id"], 0) for a in accounts}
    out[accounts[0]["id"]] += counts.get(None, 0)
    return out


def sent_last_day(conn, username: str, account_id: int | None = None) -> int:
    """Emails sent from one account (default: the default account) in the last 24 hours."""
    counts = sent_counts(conn, username)
    if account_id is None:
        account = get(conn, username)
        account_id = account["id"] if account else None
    return counts.get(account_id, 0)


def public(account: dict[str, Any] | None, sent_today: int = 0) -> dict[str, Any] | None:
    """The account as the page may see it: everything but the password."""
    if not account:
        return None
    return {
        "id": account["id"], "is_default": bool(account["is_default"]),
        "provider": account["provider"], "host": account["host"], "port": account["port"], "security": account["security"],
        "username": account["login"], "from_email": account["from_email"], "from_name": account["from_name"] or "",
        "reply_to": account["reply_to"] or "", "daily_cap": account["daily_cap"], "verified": bool(account["verified_at"]),
        "sent_today": sent_today, "last_error": account["last_error"],
    }


def public_all(conn, username: str) -> list[dict[str, Any]]:
    counts = sent_counts(conn, username)
    return [public(a, counts.get(a["id"], 0)) for a in list_all(conn, username)]


def save(conn, username: str, cleaned: dict[str, Any], password: str, cred_key: str, account_id: int | None = None) -> int:
    """Keep a checked account (its login worked just now) and return its id. With an id that account is changed; without one a
    new account is added, or the account already sending from that address is replaced. The first account is the default."""
    existing = list_all(conn, username)
    target = next((a for a in existing if a["id"] == account_id), None) if account_id is not None else \
        next((a for a in existing if a["from_email"].lower() == cleaned["from_email"].lower()), None)
    if account_id is not None and not target:
        raise um.MailError("That email account is not there any more. Reload and try again.", "no_mail_account")
    if not target and len(existing) >= MAX_ACCOUNTS:
        raise um.MailError(f"You can connect up to {MAX_ACCOUNTS} email accounts. Disconnect one to add another.", "too_many_accounts")
    enc = um.encrypt_secret(password, cred_key)
    values = (cleaned["provider"], cleaned["host"], cleaned["port"], cleaned["security"], cleaned["login"], enc,
              cleaned["from_email"], cleaned["from_name"] or None, cleaned["reply_to"] or None, cleaned["daily_cap"])
    try:
        with conn.cursor() as cur:
            if target:
                cur.execute(
                    """UPDATE user_mail_accounts SET provider = %s, host = %s, port = %s, security = %s, login = %s, password_enc = %s,
                           from_email = %s, from_name = %s, reply_to = %s, daily_cap = %s, verified_at = NOW(), last_error = NULL,
                           updated_at = NOW()
                       WHERE id = %s AND username = %s""",
                    (*values, target["id"], username),
                )
                new_id = target["id"]
            else:
                cur.execute(
                    """INSERT INTO user_mail_accounts (username, is_default, provider, host, port, security, login, password_enc,
                                                       from_email, from_name, reply_to, daily_cap, verified_at, last_error, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NULL, NOW()) RETURNING id""",
                    (username, not existing, *values),
                )
                new_id = cur.fetchone()[0]
    except pg_errors.UniqueViolation:
        conn.rollback()
        raise um.MailError("You already have an email account that sends from that address.", "duplicate_account") from None
    conn.commit()
    return new_id


def set_default(conn, username: str, account_id: int) -> bool:
    """Make one account the default (what Send from picks first). False when the user has no such account."""
    if not any(a["id"] == account_id for a in list_all(conn, username)):
        return False
    with conn.cursor() as cur:
        cur.execute("UPDATE user_mail_accounts SET is_default = (id = %s) WHERE username = %s", (account_id, username))
    conn.commit()
    return True


def note_error(conn, username: str, message: str | None, account_id: int | None = None) -> None:
    """Remember why the last send or test failed (None clears it). Without an id: the default account."""
    account = get(conn, username, account_id)
    if not account:
        return
    with conn.cursor() as cur:
        cur.execute("UPDATE user_mail_accounts SET last_error = %s WHERE id = %s AND username = %s",
                    (message[:300] if message else None, account["id"], username))
    conn.commit()


def delete(conn, username: str, account_id: int | None = None) -> bool:
    """Forget one account (default: the default account). When it was the default, the oldest remaining one takes over."""
    account = get(conn, username, account_id)
    if not account:
        return False
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_mail_accounts WHERE id = %s AND username = %s", (account["id"], username))
        if account["is_default"]:
            cur.execute("UPDATE user_mail_accounts SET is_default = TRUE WHERE id = (SELECT MIN(id) FROM user_mail_accounts WHERE username = %s)",
                        (username,))
    conn.commit()
    return True
