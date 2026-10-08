"""Admin blueprint — user management, credit adjustments."""
from __future__ import annotations

import json

from flask import Blueprint, jsonify, request, session

from tender_app.config import ADMIN_EMAILS
from tender_app.credits import get_balance, grant_credits
from tender_app.security import csrf_required

admin_bp = Blueprint("admin", __name__)

_get_db = None


def init_admin_blueprint(get_db_connection):
    global _get_db
    _get_db = get_db_connection
    return admin_bp


def _is_admin() -> bool:
    email = (session.get("email") or session.get("username") or "").lower()
    if email in ADMIN_EMAILS:
        return True
    conn = _get_db()
    cursor = conn.cursor()
    ph = "%s"
    cursor.execute(f"SELECT role FROM users WHERE LOWER(username) = {ph} OR LOWER(email) = {ph}", (email, email))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row and row[0] == "admin":
        return True
    return False


@admin_bp.get("/api/admin/users")
def api_admin_users():
    if not session.get("logged_in") or not _is_admin():
        return jsonify({"error": "Forbidden"}), 403
    conn = _get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.username, u.email, u.display_name, u.created_at,
               COALESCE(w.balance, 0), COALESCE(s.plan, 'free'), COALESCE(s.status, 'trialing'),
               COALESCE(u.role, 'member')
        FROM users u
        LEFT JOIN credit_wallet w ON w.username = u.username
        LEFT JOIN subscriptions s ON s.username = u.username
        ORDER BY u.created_at DESC LIMIT 200
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    users = [
        {
            "username": r[0], "email": r[1], "display_name": r[2],
            "created_at": str(r[3]), "balance": r[4], "plan": r[5], "status": r[6],
            "role": r[7],
        }
        for r in rows
    ]
    return jsonify({"ok": True, "users": users})


@admin_bp.post("/api/admin/role")
@csrf_required
def api_admin_update_role():
    if not session.get("logged_in") or not _is_admin():
        return jsonify({"error": "Forbidden"}), 403
    body = request.get_json(silent=True) or {}
    target = (body.get("username") or "").strip().lower()
    role = (body.get("role") or "").strip().lower()
    
    if not target or role not in ("admin", "member", "viewer"):
        return jsonify({"ok": False, "error": "username and valid role (admin, member, viewer) required"}), 400

    conn = _get_db()
    try:
        cursor = conn.cursor()
        ph = "%s"
        cursor.execute(f"UPDATE users SET role = {ph} WHERE LOWER(username) = {ph} OR LOWER(email) = {ph}", (role, target, target))
        cursor.execute(
            f"""INSERT INTO admin_audit_log (admin_email, action, target_username, details_json)
                VALUES ({ph}, 'update_role', {ph}, {ph})""",
            (session.get("email", ""), target, json.dumps({"role": role})),
        )
        conn.commit()
        cursor.close()
    finally:
        conn.close()
    return jsonify({"ok": True, "role": role})


@admin_bp.post("/api/admin/credits")
@csrf_required
def api_admin_grant_credits():
    if not session.get("logged_in") or not _is_admin():
        return jsonify({"error": "Forbidden"}), 403
    body = request.get_json(silent=True) or {}
    target = (body.get("username") or "").strip().lower()
    amount = int(body.get("amount") or 0)
    reason = (body.get("reason") or "admin_adjustment").strip()
    if not target or amount == 0:
        return jsonify({"ok": False, "error": "username and non-zero amount required"}), 400

    conn = _get_db()
    try:
        new_bal = grant_credits(conn, target, amount, reason, "admin")
        ph = "%s"
        cursor = conn.cursor()
        cursor.execute(
            f"""INSERT INTO admin_audit_log (admin_email, action, target_username, details_json)
                VALUES ({ph}, 'grant_credits', {ph}, {ph})""",
            (session.get("email", ""), target, json.dumps({"amount": amount, "reason": reason})),
        )
        conn.commit()
        cursor.close()
    finally:
        conn.close()
    return jsonify({"ok": True, "balance": new_bal})
