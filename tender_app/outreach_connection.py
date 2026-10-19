"""Which Outreach key a TenderFlow user works with, and whether they are connected at all.

Until now every user shared the one key in OUTREACH_API_KEY. Now each user can connect and disconnect Outreach themselves
from the Connections window, without leaving TenderFlow:

  * own        the user pasted their own Outreach key; it is kept Fernet-encrypted (MAIL_CRED_KEY, as mailbox passwords are)
               and is never returned by any route or written to a log;
  * shared     no choice made: the organisation's key from the server settings, as before;
  * disconnected  the user pressed Disconnect: Outreach is off for them, even if the server has a key;
  * none       no key anywhere: not connected.

Disconnect removes the user's own key (and switches the shared key off for them); Connect with a key replaces it; Connect
with "use the organisation's connection" clears the switch. This module is only rules and storage: the HTTP side is in
blueprints/growth_bp.py and asking Outreach whether a key works is outreach_client.py.
"""
from __future__ import annotations

from typing import Any

from tender_app import outreach_client as oc
from tender_app import user_mailer as um

OWN, SHARED, DISCONNECTED, NONE = "own", "shared", "disconnected", "none"
_ready = False


def ensure_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_outreach_connections (
            username     VARCHAR(255) PRIMARY KEY,
            key_enc      TEXT,
            key_hint     VARCHAR(40),
            disabled     BOOLEAN NOT NULL DEFAULT FALSE,
            connected_at TIMESTAMP,
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # how the key was obtained: 'key' (pasted) or 'partner' (signed in through the partner programme, so Disconnect also
    # tells Outreach)
    cursor.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'user_outreach_connections' AND column_name = 'via'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE user_outreach_connections ADD COLUMN IF NOT EXISTS via VARCHAR(10) NOT NULL DEFAULT 'key'")


def _ensure(conn) -> None:
    global _ready
    if _ready:
        return
    with conn.cursor() as cur:
        ensure_table(cur)
    conn.commit()
    _ready = True


def resolve(conn, username: str, server_key: str, cred_key: str) -> dict[str, Any]:
    """{"source", "key", "hint", "error"}: the key to use for this user ("" when none), where it came from, and an error
    when a saved key cannot be read (the server's MAIL_CRED_KEY changed)."""
    server_key = (server_key or "").strip()
    _ensure(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT key_enc, key_hint, disabled, via FROM user_outreach_connections WHERE username = %s", (username,))
        row = cur.fetchone()
    if row and row[2]:
        return {"source": DISCONNECTED, "key": "", "hint": None, "error": None, "via": None}
    if row and row[0]:
        try:
            key = um.decrypt_secret(row[0], cred_key)
        except um.MailError:
            return {"source": OWN, "key": "", "hint": row[1], "via": row[3], "error": "Your saved Outreach key could not be read. Connect again to save it afresh."}
        return {"source": OWN, "key": key, "hint": row[1], "error": None, "via": row[3]}
    if server_key:
        return {"source": SHARED, "key": server_key, "hint": oc.mask_key(server_key) if oc.key_mode(server_key) else None, "error": None, "via": None}
    return {"source": NONE, "key": "", "hint": None, "error": None, "via": None}


def effective_key(username: str, server_key: str, cred_key: str) -> str:
    """The key to use for `username` ("" when they are disconnected or nothing is configured). Opens its own connection."""
    from tender_app.db import get_db_connection

    conn = get_db_connection()
    try:
        return resolve(conn, username, server_key, cred_key)["key"]
    finally:
        conn.close()


def store_own(conn, username: str, key: str, cred_key: str, via: str = "key") -> None:
    """Keep the user's own key, encrypted. Raises um.MailError when the server cannot encrypt (MAIL_CRED_KEY missing)."""
    via = "partner" if via == "partner" else "key"
    token = um.encrypt_secret(key, cred_key)
    _ensure(conn)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO user_outreach_connections (username, key_enc, key_hint, disabled, via, connected_at, updated_at)
               VALUES (%s, %s, %s, FALSE, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT (username) DO UPDATE SET key_enc = EXCLUDED.key_enc, key_hint = EXCLUDED.key_hint, disabled = FALSE,
                   via = EXCLUDED.via, connected_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP""",
            (username, token, oc.mask_key(key), via),
        )
    conn.commit()


def use_shared(conn, username: str) -> None:
    """Go back to the organisation's connection: forget the user's own key and switch Outreach back on."""
    _ensure(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_outreach_connections WHERE username = %s", (username,))
    conn.commit()


def disconnect(conn, username: str, server_key: str) -> None:
    """Forget the user's own key. With a server key behind it the user is marked disconnected (so the shared key is not
    quietly used for them); with none there is nothing to switch off, so the row goes."""
    _ensure(conn)
    with conn.cursor() as cur:
        if (server_key or "").strip():
            cur.execute(
                """INSERT INTO user_outreach_connections (username, key_enc, key_hint, disabled, updated_at)
                   VALUES (%s, NULL, NULL, TRUE, CURRENT_TIMESTAMP)
                   ON CONFLICT (username) DO UPDATE SET key_enc = NULL, key_hint = NULL, disabled = TRUE, updated_at = CURRENT_TIMESTAMP""",
                (username,),
            )
        else:
            cur.execute("DELETE FROM user_outreach_connections WHERE username = %s", (username,))
    conn.commit()


def key_problem(key: str, allow_live: bool) -> str | None:
    """Why a key the user typed cannot be used, before Outreach is asked. The key is never quoted."""
    text = (key or "").strip()
    if not text:
        return "Paste your Outreach API key."
    if oc.key_mode(text) is None:
        return "That is not an Outreach key. It starts with sk_test_ or sk_live_."
    if oc.key_mode(text) == "live" and not allow_live:
        return "Live keys are switched off on this server. Use an sk_test_ key."
    if len(text) > 200 or any(c.isspace() for c in text):
        return "That is not an Outreach key."
    return None
