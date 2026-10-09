"""Auth blueprint — Firebase session exchange."""
from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from tender_app.firebase_auth import AuthError, upsert_firebase_user, verify_id_token
from tender_app.security import ensure_csrf_token, rate_limit

auth_bp = Blueprint("auth", __name__)

_get_db = None


def init_auth_blueprint(get_db_connection):
    global _get_db
    _get_db = get_db_connection
    return auth_bp


@auth_bp.post("/api/auth/firebase")
@rate_limit(10, 60)
def api_auth_firebase():
    body = request.get_json(silent=True) or {}
    id_token = (body.get("idToken") or body.get("id_token") or "").strip()
    company_name = (body.get("company_name") or "").strip()
    if not id_token:
        return jsonify({"ok": False, "error": "Missing ID token"}), 400
    try:
        claims = verify_id_token(id_token)
    except AuthError as ex:
        return jsonify({"ok": False, "error": str(ex)}), 401

    uid = claims.get("uid", "")
    email = (claims.get("email") or "").strip()
    name = (claims.get("name") or claims.get("display_name") or "").strip()
    if not email and not uid:
        return jsonify({"ok": False, "error": "Token missing email"}), 400

    conn = _get_db()
    try:
        cursor = conn.cursor()
        ph = "%s"
        cursor.execute(f"SELECT username FROM users WHERE firebase_uid = {ph}", (uid,))
        existed = cursor.fetchone() is not None
        cursor.close()

        username = upsert_firebase_user(
            conn, 
            firebase_uid=uid,
            email=email,
            display_name=name,
            company_name=company_name if not existed else "",
        )
    finally:
        conn.close()

    session.clear()
    session["logged_in"] = True
    session["username"] = username
    session["firebase_uid"] = uid
    session["email"] = email.lower() if email else username
    csrf = ensure_csrf_token()

    # The welcome email is sent once, from upsert_firebase_user, when it creates the account.

    return jsonify({"ok": True, "username": username, "csrf_token": csrf})


@auth_bp.post("/api/auth/forgot-password")
@rate_limit(5, 60)
def api_auth_forgot_password():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "Please enter a valid email address."}), 400

    # 1. Check if user exists in local database
    user_exists = False
    conn = _get_db()
    try:
        cursor = conn.cursor()
        ph = "%s"
        cursor.execute(f"SELECT username, email FROM users WHERE LOWER(email) = {ph} OR LOWER(username) = {ph}", (email, email))
        row = cursor.fetchone()
        if row:
            user_exists = True
        cursor.close()
    except Exception as ex:
        print(f"[Forgot Password DB Check Error] {ex}")
    finally:
        conn.close()

    # 2. Check in Firebase Auth if not found in DB
    from tender_app.firebase_auth import init_firebase
    fb_ok = init_firebase()
    if not user_exists and fb_ok:
        try:
            from firebase_admin import auth as fb_auth
            fb_user = fb_auth.get_user_by_email(email)
            if fb_user:
                user_exists = True
        except Exception:
            pass

    if not user_exists:
        return jsonify({
            "ok": False,
            "error": "No user exists with this email address. Please sign up."
        }), 404

    # 3. Generate Password Reset Link
    reset_link = None
    if fb_ok:
        try:
            from firebase_admin import auth as fb_auth
            reset_link = fb_auth.generate_password_reset_link(email)
        except Exception as ex:
            print(f"[Forgot Password Reset Link Error] {ex}")

    if not reset_link:
        from tender_app.config import FIREBASE_AUTH_DOMAIN
        if FIREBASE_AUTH_DOMAIN:
            reset_link = f"https://{FIREBASE_AUTH_DOMAIN}"

    # 4. Send email via SMTP service
    from tender_app.email_svc import send_password_reset_email
    sent = send_password_reset_email(email, reset_link or "https://tenderflow.co.uk")
    if not sent:
        return jsonify({
            "ok": False,
            "error": "Could not deliver password reset email. Please try again later or contact support."
        }), 500

    return jsonify({
        "ok": True,
        "message": "Password reset email sent. Please check your inbox (and spam folder) for the reset link."
    })


@auth_bp.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@auth_bp.get("/api/me")
def api_me():
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    username = session.get("username", "")
    from tender_app.credits import get_balance
    from tender_app.billing import get_billing_status
    from tender_app.config import LOW_CREDIT_THRESHOLD, ADMIN_EMAILS

    conn = _get_db()
    try:
        balance = get_balance(conn, username)
        billing = get_billing_status(conn, username)
        # Retrieve user role from database
        cursor = conn.cursor()
        ph = "%s"
        cursor.execute(f"SELECT role FROM users WHERE username = {ph}", (username,))
        row = cursor.fetchone()
        role = row[0] if (row and row[0]) else "member"
        cursor.close()
    finally:
        conn.close()

    # Admin emails override role checks to prevent lockouts
    email_lower = session.get("email", username).lower()
    if email_lower in ADMIN_EMAILS:
        role = "admin"

    return jsonify({
        "ok": True,
        "username": username,
        "email": session.get("email", username),
        "balance": balance,
        "low_credit": balance <= LOW_CREDIT_THRESHOLD,
        "plan": billing.get("plan", "free"),
        "subscription_status": billing.get("status", "trialing"),
        "has_stripe_customer": billing.get("has_stripe_customer", False),
        "subscription_legacy": billing.get("subscription_legacy", False),
        "billing_interval": billing.get("billing_interval"),
        "monthly_credits": billing.get("monthly_credits", 0),
        "role": role,
        "is_admin": role == "admin",
        "csrf_token": ensure_csrf_token(),
    })


@auth_bp.get("/api/config/public")
def api_public_config():
    import os
    from tender_app.config import ENABLE_BUYER_WORKSPACE, ENABLE_GROWTH_STUDIO, LOW_CREDIT_THRESHOLD, STRIPE_PUBLISHABLE_KEY
    from tender_app.security import ensure_csrf_token

    if session.get("logged_in"):
        ensure_csrf_token()

    return jsonify({
        "firebase": {
            "apiKey": os.environ.get("FIREBASE_WEB_API_KEY", ""),
            "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
            "projectId": os.environ.get("FIREBASE_PROJECT_ID", ""),
            "appId": os.environ.get("FIREBASE_APP_ID", ""),
        },
        "stripePublishableKey": STRIPE_PUBLISHABLE_KEY,
        "lowCreditThreshold": LOW_CREDIT_THRESHOLD,
        "features": {"buyerWorkspace": ENABLE_BUYER_WORKSPACE, "growthStudio": ENABLE_GROWTH_STUDIO},
        "csrf_token": session.get("csrf_token") if session.get("logged_in") else None,
    })
