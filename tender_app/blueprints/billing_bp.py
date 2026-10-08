"""Billing blueprint — Stripe checkout, portal, webhooks."""
from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from tender_app.billing import (
    cancel_active_subscription,
    confirm_checkout_session,
    create_checkout_session,
    create_portal_session,
    get_billing_status,
    get_public_catalog,
    handle_webhook,
)
from tender_app.credits import get_ledger
from tender_app.security import csrf_required, rate_limit

billing_bp = Blueprint("billing", __name__)

_get_db = None


def init_billing_blueprint(get_db_connection):
    global _get_db
    _get_db = get_db_connection
    return billing_bp


@billing_bp.get("/api/billing/catalog")
def api_billing_catalog():
    return jsonify({"ok": True, **get_public_catalog()})


@billing_bp.get("/api/billing/status")
def api_billing_status():
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    username = session.get("username", "")
    conn = _get_db()
    try:
        status = get_billing_status(conn, username)
        ledger = get_ledger(conn, username, limit=20)
    finally:
        conn.close()
    return jsonify({"ok": True, **status, "ledger": ledger})


@billing_bp.post("/api/billing/checkout")
@csrf_required
@rate_limit(5, 60)
def api_billing_checkout():
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    catalog_key = (body.get("plan") or body.get("catalog_key") or "").strip()
    if not catalog_key:
        return jsonify({"ok": False, "error": "Missing plan"}), 400
    username = session.get("username", "")
    email = session.get("email", username)
    conn = _get_db()
    try:
        url = create_checkout_session(conn, username, email, catalog_key)
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 400
    finally:
        conn.close()
    return jsonify({"ok": True, "url": url})


@billing_bp.post("/api/billing/confirm")
@csrf_required
@rate_limit(10, 60)
def api_billing_confirm():
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        return jsonify({"ok": False, "error": "Missing session_id"}), 400
    username = session.get("username", "")
    conn = _get_db()
    try:
        result = confirm_checkout_session(conn, username, session_id)
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 400
    finally:
        conn.close()
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@billing_bp.post("/api/billing/portal")
@csrf_required
@rate_limit(5, 60)
def api_billing_portal():
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    username = session.get("username", "")
    email = session.get("email", username)
    conn = _get_db()
    try:
        url = create_portal_session(conn, username, email)
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 400
    finally:
        conn.close()
    return jsonify({"ok": True, "url": url})


@billing_bp.post("/api/billing/cancel")
@csrf_required
@rate_limit(5, 60)
def api_billing_cancel():
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    username = session.get("username", "")
    conn = _get_db()
    try:
        result = cancel_active_subscription(conn, username)
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 400
    finally:
        conn.close()
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@billing_bp.post("/api/stripe/webhook")
def api_stripe_webhook():
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    result = handle_webhook(payload, sig, _get_db)
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)
