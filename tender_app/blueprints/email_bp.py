"""Email through Outreach by Aivhub: status, send, refresh.

    /api/email/outreach/status         is it configured, test or live key (masked), credits and mailboxes, daily usage
    /api/email/outreach/send           (POST) one recipient -> /v1/email/send; two or more with the same message -> one
                                       approved campaign; different messages -> one /send each up to the daily cap.
                                       {"dry_run": true} shows the plan and sends nothing.
    /api/email/outreach/refresh        (POST) update stored statuses from Outreach (polling; no webhook needed)
    /api/email/outreach/sends          this user's sends and where each one stands
    /api/email/outreach/suppress       (POST) record an address that must not be emailed again
    /api/email/outreach/permissions    (GET, POST) administrators: who may use the feature

The key is read from the server's configuration only and is never part of any answer, log line or error text.
Using the feature needs a per-user permission (administrators always have it). Mutating requests are
CSRF-protected by the app-wide hook; viewers are blocked from them by the same hook.
"""
from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request, session

from tender_app import email_send as es
from tender_app import email_send_service as svc
from tender_app import growth_studio as gs
from tender_app import outreach_client as oc
from tender_app.blueprints.admin_bp import _is_admin
from tender_app.config import (
    OUTREACH_ALLOW_LIVE,
    OUTREACH_API_BASE,
    OUTREACH_API_KEY,
    OUTREACH_CAMPAIGN_MERGE_FIELDS,
    OUTREACH_CONNECT_URL,
    OUTREACH_MANAGE_URL,
    OUTREACH_WEB_URL,
    OUTREACH_DAILY_SEND_CAP,
    OUTREACH_MAX_BATCH,
    OUTREACH_TIMEOUT_SECONDS,
)
from tender_app.security import rate_limit

email_bp = Blueprint("email_outreach", __name__)

_get_db = None
MAX_RECIPIENTS_IN_REQUEST = 2000

SETUP_HINTS = [
    "Connect a mailbox in Outreach (Email, Mailboxes), set up DKIM and DMARC for it, and start its warm-up.",
    "The Outreach key needs the email:write and email:approve permissions, and the mailbox must be able to send.",
    "A campaign sends inside its sending window (09:00-17:00 UK by default) and only after warm-up, so it is not instant.",
]


def init_email_blueprint(get_db_connection):
    global _get_db
    _get_db = get_db_connection
    return email_bp


def _connect():
    if _get_db is None:
        raise RuntimeError("Email blueprint not initialised with a database connection")
    return _get_db()


class _Http(Exception):
    def __init__(self, message: str, status: int = 400, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra or {}


@email_bp.errorhandler(_Http)
def _http_error(exc: _Http):
    return jsonify({"error": exc.message, **exc.extra}), exc.status


def _username() -> str:
    return (session.get("username") or "").strip()


def _json_body() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise _Http("The request body must be a JSON object")
    return body


def _problem() -> str | None:
    return oc.config_problem(OUTREACH_API_KEY, OUTREACH_ALLOW_LIVE)


def _client() -> "oc.OutreachClient":
    problem = _problem()
    if problem:
        raise _Http(problem, 409)
    return oc.OutreachClient(OUTREACH_API_KEY, OUTREACH_API_BASE, timeout=OUTREACH_TIMEOUT_SECONDS)


def _can_send(conn, username: str) -> bool:
    if not username:
        return False
    if _is_admin():
        return True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM email_send_permissions WHERE username = %s", (username,))
        return cur.fetchone() is not None


def _require_permission(conn) -> str:
    username = _username()
    if not _can_send(conn, username):
        raise _Http("You do not have permission to send email through Outreach. Ask an administrator to allow it.", 403)
    return username


def _usage(conn, key_mode: str | None) -> dict[str, Any]:
    used = svc.sent_today(conn, key_mode or "test")
    return {"daily_cap": OUTREACH_DAILY_SEND_CAP, "sent_today": used,
            "remaining_today": max(0, OUTREACH_DAILY_SEND_CAP - used), "max_per_send": OUTREACH_MAX_BATCH}


# ── status ──────────────────────────────────────────────────────────────────────────────────────

@email_bp.get("/api/email/outreach/status")
def outreach_status():
    """Configured yes/no, key mode, the masked key and the wallet. The key itself is never returned."""
    key = (OUTREACH_API_KEY or "").strip()
    mode = oc.key_mode(key)
    conn = _connect()
    try:
        allowed = _can_send(conn, _username())
        out: dict[str, Any] = {"configured": bool(key), "can_send": allowed, "ready": False, "problem": _problem(),
                               "mode": None, "key_hint": None, "live_allowed": bool(OUTREACH_ALLOW_LIVE),
                               "wallet": None, "usage": None, "setup_hints": SETUP_HINTS,
                               **oc.web_links(OUTREACH_WEB_URL, OUTREACH_CONNECT_URL, OUTREACH_MANAGE_URL)}
        if not allowed:
            out["problem"] = "You do not have permission to send email through Outreach. Ask an administrator to allow it."
            return jsonify(out)
        out["mode"] = mode
        out["key_hint"] = oc.mask_key(key) if mode else None
        out["usage"] = _usage(conn, mode)
        if _problem():
            return jsonify(out)
        try:
            out["wallet"] = oc.wallet_summary(_client().wallets())
            out["ready"] = True
        except oc.OutreachError as exc:
            out["problem"] = exc.message
            out["hint"] = svc.setup_hint(exc)
        return jsonify(out)
    finally:
        conn.close()


# ── send ────────────────────────────────────────────────────────────────────────────────────────

def _recipients_from(body: dict[str, Any]) -> list[Any]:
    recipients = body.get("recipients")
    if not isinstance(recipients, list) or not recipients:
        raise _Http("Add at least one recipient.")
    if len(recipients) > MAX_RECIPIENTS_IN_REQUEST:
        raise _Http(f"That is too many recipients for one request (limit {MAX_RECIPIENTS_IN_REQUEST}).")
    for r in recipients:
        if not isinstance(r, (str, dict)):
            raise _Http("Each recipient must be an email address or an object with email, first_name and company.")
    return recipients


def _describe(plan: dict[str, Any]) -> dict[str, Any]:
    """A plan without the message text per person, for a preview."""
    return {
        "route": plan["route"],
        "single": [i["email"] for i in plan["singles"]],
        "campaigns": [{"recipients": [i["email"] for i in g["items"]], "subject": g["subject"]} for g in plan["campaigns"]],
        "resume": [{"outreach_id": g["outreach_id"], "recipients": [i["email"] for i in g["items"]]} for g in plan["resume"]],
        "daily_limited": [{"email": i["email"], "reason": "Not sent: daily limit reached."} for i in plan["daily_limited"]],
        "skipped": plan["skipped"],
    }


@email_bp.post("/api/email/outreach/send")
@rate_limit(10, 60)
def outreach_send():
    body = _json_body()
    subject = body.get("subject") if isinstance(body.get("subject"), str) else ""
    text = body.get("body") if isinstance(body.get("body"), str) else ""
    recipients = _recipients_from(body)
    dry_run = body.get("dry_run") is True
    conn = _connect()
    try:
        username = _require_permission(conn)
        client = _client()
        key_mode = client.mode
        plan = es.plan_send(
            subject, text, recipients,
            key_mode=key_mode,
            suppressed=lambda emails: svc.suppressed_among(conn, username, emails),
            existing=lambda hashes: svc.existing_sends(conn, hashes),
            sent_today=svc.sent_today(conn, key_mode),
            daily_cap=OUTREACH_DAILY_SEND_CAP,
            max_batch=OUTREACH_MAX_BATCH,
            merge_in_campaign=OUTREACH_CAMPAIGN_MERGE_FIELDS,
        )
        if plan["blockers"]:
            raise _Http(" ".join(plan["blockers"]), 400, {"blockers": plan["blockers"]})
        nothing = not (plan["singles"] or plan["campaigns"] or plan["resume"])
        if dry_run:
            return jsonify({"dry_run": True, "mode": key_mode, **_describe(plan), "usage": _usage(conn, key_mode)})
        if nothing and not plan["daily_limited"]:
            reasons = sorted({s["reason"] for s in plan["skipped"]})
            raise _Http("There is nobody to send to. " + " ".join(reasons), 400, {"skipped": plan["skipped"]})
        result = svc.execute(conn, client, username, plan, with_first_name=OUTREACH_CAMPAIGN_MERGE_FIELDS)
        result["usage"] = _usage(conn, key_mode)
        return jsonify(result)
    finally:
        conn.close()


# ── refresh and history ─────────────────────────────────────────────────────────────────────────

@email_bp.post("/api/email/outreach/refresh")
@rate_limit(20, 60)
def outreach_refresh():
    conn = _connect()
    try:
        username = _require_permission(conn)
        client = _client()
        result = svc.refresh(conn, client, username)
        result["sends"] = svc.list_sends(conn, username, 100)
        result["usage"] = _usage(conn, client.mode)
        return jsonify(result)
    finally:
        conn.close()


@email_bp.get("/api/email/outreach/sends")
def outreach_sends():
    try:
        limit = int(request.args.get("limit") or 100)
    except ValueError:
        raise _Http("limit must be a whole number")
    batch = (request.args.get("batch_id") or "").strip()[:40] or None
    conn = _connect()
    try:
        username = _require_permission(conn)
        return jsonify({"sends": svc.list_sends(conn, username, limit, batch)})
    finally:
        conn.close()


# ── suppression and permission ──────────────────────────────────────────────────────────────────

@email_bp.post("/api/email/outreach/suppress")
def outreach_suppress():
    body = _json_body()
    email = str(body.get("email") or "").strip().lower()
    if not gs.valid_email(email):
        raise _Http("That is not a valid email address.")
    conn = _connect()
    try:
        username = _require_permission(conn)
        svc.add_suppression(conn, email, "opted_out", username)
        conn.commit()
        return jsonify({"ok": True, "email": email})
    finally:
        conn.close()


@email_bp.route("/api/email/outreach/permissions", methods=["GET", "POST"])
def outreach_permissions():
    if not session.get("logged_in") or not _is_admin():
        return jsonify({"error": "Forbidden"}), 403
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if request.method == "POST":
                body = _json_body()
                target = str(body.get("username") or "").strip()
                if not target or len(target) > 255:
                    raise _Http("username is required")
                if body.get("allowed") is False:
                    cur.execute("DELETE FROM email_send_permissions WHERE username = %s", (target,))
                    action = "email_send_revoked"
                else:
                    cur.execute(
                        """INSERT INTO email_send_permissions (username, granted_by) VALUES (%s, %s)
                           ON CONFLICT (username) DO UPDATE SET granted_by = EXCLUDED.granted_by, granted_at = NOW()""",
                        (target, _username()),
                    )
                    action = "email_send_granted"
                cur.execute(
                    "INSERT INTO admin_audit_log (admin_email, action, target_username, details_json) VALUES (%s, %s, %s, %s)",
                    (_username(), action, target, "{}"),
                )
                conn.commit()
            cur.execute("SELECT username, granted_by, granted_at FROM email_send_permissions ORDER BY username")
            rows = [{"username": r[0], "granted_by": r[1], "granted_at": r[2].isoformat() if r[2] else None} for r in cur.fetchall()]
        return jsonify({"permissions": rows})
    finally:
        conn.close()
