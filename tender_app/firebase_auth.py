"""Firebase Admin SDK — verify ID tokens and upsert users."""
from __future__ import annotations

import json
import os
from typing import Any

from tender_app.config import (
    FIREBASE_CREDENTIALS_JSON,
    FIREBASE_CREDENTIALS_PATH,
    FIREBASE_PROJECT_ID,
    FREE_TRIAL_CREDITS,
)

_firebase_app = None


class AuthError(Exception):
    pass


def init_firebase() -> bool:
    global _firebase_app
    if _firebase_app is not None:
        return True
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        print("WARNING: firebase-admin not installed. Firebase auth disabled.")
        return False

    if firebase_admin._apps:
        _firebase_app = firebase_admin.get_app()
        return True

    cred = None
    if FIREBASE_CREDENTIALS_JSON:
        cred = credentials.Certificate(json.loads(FIREBASE_CREDENTIALS_JSON))
    elif FIREBASE_CREDENTIALS_PATH and os.path.isfile(FIREBASE_CREDENTIALS_PATH):
        cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
    else:
        print("WARNING: Firebase credentials not configured.")
        return False

    options = {"projectId": FIREBASE_PROJECT_ID} if FIREBASE_PROJECT_ID else None
    _firebase_app = firebase_admin.initialize_app(cred, options)
    return True


def verify_id_token(id_token: str) -> dict[str, Any]:
    if not init_firebase():
        raise AuthError("Firebase is not configured on the server")
    from firebase_admin import auth
    import re
    import time

    try:
        return auth.verify_id_token(id_token, check_revoked=True)
    except Exception as ex:
        msg = str(ex)
        if "used too early" in msg.lower():
            diff = 2  # default sleep fallback
            match = re.search(r"(\d+)\s*<\s*(\d+)", msg)
            if match:
                t1 = int(match.group(1))
                t2 = int(match.group(2))
                diff = max(0, t2 - t1)
            
            if 0 <= diff <= 15:
                # Sleep for the skew difference plus 1.0 second leeway
                time.sleep(diff + 1.0)
                try:
                    return auth.verify_id_token(id_token, check_revoked=True)
                except Exception as retry_ex:
                    raise AuthError(f"Invalid or expired token after retry: {retry_ex}") from retry_ex
        raise AuthError(f"Invalid or expired token: {ex}") from ex


def _migrate_username(conn, old: str, new: str) -> None:
    if old == new:
        return
    ph = "%s"
    tables = [
        "analyses", "company_profiles", "fit_scores", "saved_searches",
        "pipeline", "answer_bank", "credit_wallet", "credit_ledger", "subscriptions",
    ]
    cursor = conn.cursor()
    for table in tables:
        try:
            cursor.execute(f"UPDATE {table} SET username = {ph} WHERE username = {ph}", (new, old))
        except Exception as ex:
            print(f"Migration warning ({table}): {ex}")
    try:
        cursor.execute(f"UPDATE users SET legacy_username = {ph} WHERE username = {ph}", (old, new))
    except Exception:
        pass
    cursor.close()


def upsert_firebase_user(
    conn,
    *,
    firebase_uid: str,
    email: str,
    display_name: str,
    company_name: str = "",
) -> str:
    email_lower = (email or firebase_uid).strip().lower()
    ph = "%s"
    cursor = conn.cursor()

    cursor.execute(f"SELECT username FROM users WHERE firebase_uid = {ph}", (firebase_uid,))
    row = cursor.fetchone()
    if row:
        cursor.close()
        return row[0]

    legacy_username = None
    cursor.execute(
        f"SELECT username FROM users WHERE LOWER(COALESCE(email, '')) = {ph} OR LOWER(username) = {ph}",
        (email_lower, email_lower),
    )
    lr = cursor.fetchone()
    if lr:
        legacy_username = lr[0]

    if legacy_username and legacy_username != email_lower:
        _migrate_username(conn, legacy_username, email_lower)

    cursor.execute(f"SELECT id FROM users WHERE LOWER(username) = {ph}", (email_lower,))
    existing = cursor.fetchone()
    placeholder_hash = "firebase-only"

    if existing:
        cursor.execute(
            f"""UPDATE users SET firebase_uid = {ph}, email = {ph}, display_name = {ph}
                WHERE id = {ph}""",
            (firebase_uid, email_lower, display_name or "", existing[0]),
        )
        is_new = False
    else:
        cursor.execute(
            f"""INSERT INTO users (username, password_hash, firebase_uid, email, display_name, legacy_username)
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})""",
            (email_lower, placeholder_hash, firebase_uid, email_lower, display_name or "", legacy_username or ""),
        )
        is_new = True

    if is_new:
        cursor.execute(f"SELECT balance FROM credit_wallet WHERE username = {ph}", (email_lower,))
        if not cursor.fetchone():
            cursor.execute(
                f"INSERT INTO credit_wallet (username, balance) VALUES ({ph}, {ph})",
                (email_lower, FREE_TRIAL_CREDITS),
            )
            cursor.execute(
                f"""INSERT INTO credit_ledger (username, delta, balance_after, reason, ref_id)
                    VALUES ({ph}, {ph}, {ph}, 'free_trial', 'signup')""",
                (email_lower, FREE_TRIAL_CREDITS, FREE_TRIAL_CREDITS),
            )
            cursor.execute(
                f"""INSERT INTO subscriptions (username, plan, status, monthly_credits)
                    VALUES ({ph}, 'free', 'trialing', 0) ON CONFLICT (username) DO NOTHING""",
                (email_lower,),
            )

            # Seed default email notification preferences for new user
            default_email_prefs = [
                ("automated_emails_enabled", "true"),
                ("best_fit_emails_enabled", "true"),
                ("notification_email", email_lower),
                ("email_frequency", "daily"),
                ("min_fit_score", "70"),
            ]
            for pk, pv in default_email_prefs:
                cursor.execute(
                    f"""INSERT INTO user_prefs (username, pref_key, pref_value)
                        VALUES ({ph}, {ph}, {ph})
                        ON CONFLICT (username, pref_key) DO NOTHING""",
                    (email_lower, pk, pv),
                )

            # Send automated welcome email to newly created account
            try:
                from tender_app.email_svc import send_welcome
                send_welcome(email_lower, display_name or email_lower)
            except Exception as ex:
                print(f"[Auth] Failed to send welcome email to {email_lower}: {ex}")

    if company_name:
        profile_text = (
            f"Company profile context for {company_name}. "
            "Customize your core capabilities, experience, and past performance here."
        )
        cursor.execute(
            f"""INSERT INTO company_profiles (username, name, profile_text)
                VALUES ({ph}, {ph}, {ph}) ON CONFLICT (username, name) DO NOTHING""",
            (email_lower, company_name, profile_text),
        )

    conn.commit()
    cursor.close()
    return email_lower
