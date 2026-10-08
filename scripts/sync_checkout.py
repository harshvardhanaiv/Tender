"""Fulfill a paid Stripe Checkout session into the local credit wallet.

Usage:
  docker compose exec etenders-scraper python scripts/sync_checkout.py --latest user@example.com
  docker compose exec etenders-scraper python scripts/sync_checkout.py cs_test_...
"""
from __future__ import annotations

import os
import sys

try:
    import stripe
except ImportError:
    print("pip install stripe", file=sys.stderr)
    raise SystemExit(1)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tender_app.billing import confirm_checkout_session, _stripe_client  # noqa: E402


def _load_key() -> str:
    key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if key:
        return key
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("STRIPE_SECRET_KEY="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("STRIPE_SECRET_KEY not found")


def _stripe_get(obj, key: str):
    if not obj:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    try:
        return obj[key]
    except (KeyError, TypeError):
        return getattr(obj, key, None)


def _get_db_connection():
    """Connect using the same env vars as Docker / production."""
    try:
        import psycopg2
    except ImportError:
        raise SystemExit("psycopg2 is required (run inside Docker or pip install psycopg2-binary)")

    host = os.environ.get("DB_HOST", "localhost")
    port = int(os.environ.get("DB_PORT", "5440"))
    return psycopg2.connect(
        host=host,
        port=port,
        dbname=os.environ.get("DB_NAME", "postgres"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
    )


def _latest_paid_session(email: str):
    stripe.api_key = _load_key()
    sessions = stripe.checkout.Session.list(limit=20)
    for s in sessions.auto_paging_iter():
        details = s.customer_details
        session_email = (_stripe_get(details, "email") or "").lower()
        if s.payment_status == "paid" and session_email == email.lower():
            return s
    return None


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)

    stripe.api_key = _load_key()
    _stripe_client()

    if sys.argv[1] == "--latest":
        email = sys.argv[2] if len(sys.argv) > 2 else ""
        if not email:
            raise SystemExit("Usage: python scripts/sync_checkout.py --latest user@example.com")
        session = _latest_paid_session(email)
        if not session:
            raise SystemExit(f"No paid checkout session found for {email}")
        session_id = session.id
        username = _stripe_get(session.metadata, "username") or email.lower()
        print(f"Using session {session_id} for {username}")
    else:
        session_id = sys.argv[1].strip()
        checkout = stripe.checkout.Session.retrieve(session_id)
        username = _stripe_get(checkout.metadata, "username") or ""
        if not username:
            raise SystemExit("Session metadata has no username")

    conn = _get_db_connection()
    try:
        result = confirm_checkout_session(conn, True, username, session_id)
    finally:
        conn.close()
    print(result)


if __name__ == "__main__":
    main()
