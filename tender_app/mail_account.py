"""The user's own email account for Growth Studio: storage and the shape the page sees.

Outreach's API cannot add a mailbox to an Outreach account, so a user who has none there sets up their own account right in
the Connections window: an ordinary SMTP login (Gmail, Outlook, Zoho or any provider). Growth Studio then sends from it.
Checking the login and sending are in user_mailer.py; this module only keeps the settings.

The password is kept Fernet-encrypted with MAIL_CRED_KEY (as a pasted Outreach key is) and is never returned by any route
or written to a log. The HTTP side is in blueprints/growth_bp.py.
"""
from __future__ import annotations

from typing import Any

from tender_app import user_mailer as um
from tender_app.db_ext import create_user_mail_accounts

_ready = False
_COLUMNS = "provider, host, port, security, login, password_enc, from_email, from_name, reply_to, daily_cap, verified_at, last_error"


def _ensure(conn) -> None:
    global _ready
    if _ready:
        return
    with conn.cursor() as cur:
        create_user_mail_accounts(cur)
    conn.commit()
    _ready = True


def get(conn, username: str) -> dict[str, Any] | None:
    """The saved account, password still encrypted (use password_for), or None."""
    _ensure(conn)
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM user_mail_accounts WHERE username = %s", (username,))
        row = cur.fetchone()
    return dict(zip(_COLUMNS.split(", "), row)) if row else None


def password_for(account: dict[str, Any], cred_key: str) -> str:
    return um.decrypt_secret(account["password_enc"], cred_key)


def smtp_settings(account: dict[str, Any]) -> dict[str, Any]:
    """What user_mailer needs to connect and to write the From line."""
    return {k: account[k] for k in ("host", "port", "security", "login", "from_email", "from_name", "reply_to")}


def sent_last_day(conn, username: str) -> int:
    """Emails sent from the account in the last 24 hours (the daily limit counts these)."""
    _ensure(conn)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM growth_outreach_drafts WHERE username = %s AND mode = 'smtp' AND status = 'sent' "
            "AND checked_at >= NOW() - INTERVAL '24 hours'",
            (username,),
        )
        return int(cur.fetchone()[0])


def public(account: dict[str, Any] | None, sent_today: int = 0) -> dict[str, Any] | None:
    """The account as the page may see it: everything but the password."""
    if not account:
        return None
    return {
        "provider": account["provider"], "host": account["host"], "port": account["port"], "security": account["security"],
        "username": account["login"], "from_email": account["from_email"], "from_name": account["from_name"] or "",
        "reply_to": account["reply_to"] or "", "daily_cap": account["daily_cap"], "verified": bool(account["verified_at"]),
        "sent_today": sent_today, "last_error": account["last_error"],
    }


def save(conn, username: str, cleaned: dict[str, Any], password: str, cred_key: str) -> None:
    """Keep a checked account (its login worked just now), replacing any earlier one."""
    _ensure(conn)
    enc = um.encrypt_secret(password, cred_key)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO user_mail_accounts (username, provider, host, port, security, login, password_enc, from_email,
                                               from_name, reply_to, daily_cap, verified_at, last_error, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NULL, NOW())
               ON CONFLICT (username) DO UPDATE SET provider = EXCLUDED.provider, host = EXCLUDED.host, port = EXCLUDED.port,
                   security = EXCLUDED.security, login = EXCLUDED.login, password_enc = EXCLUDED.password_enc,
                   from_email = EXCLUDED.from_email, from_name = EXCLUDED.from_name, reply_to = EXCLUDED.reply_to,
                   daily_cap = EXCLUDED.daily_cap, verified_at = NOW(), last_error = NULL, updated_at = NOW()""",
            (username, cleaned["provider"], cleaned["host"], cleaned["port"], cleaned["security"], cleaned["login"], enc,
             cleaned["from_email"], cleaned["from_name"] or None, cleaned["reply_to"] or None, cleaned["daily_cap"]),
        )
    conn.commit()


def note_error(conn, username: str, message: str | None) -> None:
    """Remember why the last send or test failed (None clears it)."""
    _ensure(conn)
    with conn.cursor() as cur:
        cur.execute("UPDATE user_mail_accounts SET last_error = %s WHERE username = %s", (message[:300] if message else None, username))
    conn.commit()


def delete(conn, username: str) -> bool:
    _ensure(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_mail_accounts WHERE username = %s", (username,))
        gone = cur.rowcount > 0
    conn.commit()
    return gone
