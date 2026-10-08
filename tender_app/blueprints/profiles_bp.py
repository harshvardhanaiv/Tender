"""Profiles blueprint — company profiles, saved searches, pipeline, answer bank."""
from __future__ import annotations

import json
import os
import re
import time

from flask import Blueprint, jsonify, request, session, send_file
from werkzeug.utils import secure_filename

profiles_bp = Blueprint("profiles", __name__)

_get_db = None
_PIPELINE_STAGES = ("watching", "bidding", "submitted", "won", "lost")


def init_profiles_blueprint(get_db_connection):
    global _get_db
    _get_db = get_db_connection
    return profiles_bp


@profiles_bp.get("/api/company-profiles")
def api_get_company_profiles():
    username = session.get("username", "admin")
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, profile_text, meta_json, COALESCE(is_default, FALSE) FROM company_profiles WHERE username = %s ORDER BY is_default DESC, name ASC",
            (username,),
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        profiles = [
            {
                "id": r[0],
                "name": r[1],
                "profile_text": r[2],
                "meta_json": r[3] or "{}",
                "is_default": bool(r[4]),
            }
            for r in rows
        ]
        return jsonify({"ok": True, "profiles": profiles})
    except Exception as e:
        print("Failed to fetch company profiles:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.post("/api/company-profiles")
def api_save_company_profile():
    body = request.get_json(silent=True) or {}
    profile_id = body.get("id")
    name = (body.get("name") or "").strip()
    profile_text = (body.get("profile_text") or "").strip()
    meta_json = (body.get("meta_json") or "").strip()
    is_default = bool(body.get("is_default", False))
    if not name or not profile_text:
        return jsonify({"ok": False, "error": "Name and profile text are required"}), 400
    username = session.get("username", "admin")
    try:
        conn = _get_db()
        cursor = conn.cursor()

        # If user has no existing profiles, this new profile becomes default automatically
        cursor.execute("SELECT COUNT(*) FROM company_profiles WHERE username = %s", (username,))
        existing_count = cursor.fetchone()[0]
        if existing_count == 0 or (profile_id and existing_count == 1):
            is_default = True

        if is_default:
            cursor.execute("UPDATE company_profiles SET is_default = FALSE WHERE username = %s", (username,))

        if profile_id:
            cursor.execute(
                "UPDATE company_profiles SET name = %s, profile_text = %s, meta_json = %s, is_default = %s WHERE id = %s AND username = %s",
                (name, profile_text, meta_json, is_default, profile_id, username),
            )
        else:
            cursor.execute(
                "INSERT INTO company_profiles (username, name, profile_text, meta_json, is_default) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (username, name, profile_text, meta_json, is_default),
            )
            profile_id = cursor.fetchone()[0]

        # Safety check: ensure at least one profile is default for this user
        cursor.execute("SELECT id FROM company_profiles WHERE username = %s AND is_default = TRUE", (username,))
        if not cursor.fetchone():
            cursor.execute("""
                UPDATE company_profiles SET is_default = TRUE 
                WHERE id = (SELECT id FROM company_profiles WHERE username = %s ORDER BY id ASC LIMIT 1)
            """, (username,))
            is_default = True

        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"ok": True, "id": profile_id, "is_default": is_default})
    except Exception as e:
        print("Failed to save company profile:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.post("/api/company-profiles/<int:profile_id>/set-default")
def api_set_default_company_profile(profile_id: int):
    username = session.get("username", "admin")
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM company_profiles WHERE id = %s AND username = %s", (profile_id, username))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return jsonify({"ok": False, "error": "Company profile not found"}), 404
        comp_name = row[0]
        cursor.execute("UPDATE company_profiles SET is_default = FALSE WHERE username = %s", (username,))
        cursor.execute("UPDATE company_profiles SET is_default = TRUE WHERE id = %s AND username = %s", (profile_id, username))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"ok": True, "id": profile_id, "name": comp_name, "is_default": True})
    except Exception as e:
        print("Failed to set default company profile:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.delete("/api/company-profiles/<int:profile_id>")
def api_delete_company_profile(profile_id: int):
    username = session.get("username", "admin")
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM company_profiles WHERE id = %s AND username = %s", (profile_id, username))
        # Ensure a remaining profile is set as default if none is currently default
        cursor.execute("SELECT id FROM company_profiles WHERE username = %s AND is_default = TRUE", (username,))
        if not cursor.fetchone():
            cursor.execute("""
                UPDATE company_profiles SET is_default = TRUE 
                WHERE id = (SELECT id FROM company_profiles WHERE username = %s ORDER BY id ASC LIMIT 1)
            """, (username,))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        print("Failed to delete company profile:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


def _format_iso(val):
    if not val:
        return ""
    from datetime import datetime
    if isinstance(val, datetime):
        return val.isoformat() + "Z"
    val_str = str(val).strip()
    if " " in val_str:
        val_str = val_str.replace(" ", "T")
    if not val_str.endswith("Z"):
        val_str += "Z"
    return val_str


@profiles_bp.route("/api/saved-searches", methods=["GET", "POST"])
def api_saved_searches():
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    if request.method == "GET":
        conn = _get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, query, scope, filters_json, created_at FROM saved_searches WHERE username=%s ORDER BY created_at DESC",
            (username,),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        result = [
            {"id": r[0], "name": r[1], "query": r[2], "scope": r[3],
             "filters": json.loads(r[4]) if r[4] else {}, "created_at": _format_iso(r[5])}
            for r in rows
        ]
        return jsonify({"searches": result})
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    query = (body.get("query") or "").strip()
    scope = (body.get("scope") or "all").strip()
    filters = body.get("filters") or {}
    if not name or not query:
        return jsonify({"error": "name and query are required"}), 400
    conn = _get_db()
    cur = conn.cursor()
    try:
        # Delete existing saved search with same name to prevent constraint/duplicate issues
        cur.execute("DELETE FROM saved_searches WHERE username = %s AND name = %s", (username, name))

        cur.execute(
            """INSERT INTO saved_searches (username, name, query, scope, filters_json)
               VALUES (%s,%s,%s,%s,%s)
               RETURNING id""",
            (username, name, query, scope, json.dumps(filters)),
        )
        new_id = cur.fetchone()[0]
        conn.commit()

        # Carry over discovery cache from a matching recent search, if any, so
        # [NEW] badges and instant results survive the switch to a saved preset.
        try:
            cur.execute(
                "SELECT id FROM recent_searches WHERE username = %s AND LOWER(query) = LOWER(%s) AND LOWER(scope) = LOWER(%s) ORDER BY searched_at DESC, id DESC LIMIT 1",
                (username, query, scope),
            )
            r_row = cur.fetchone()
            if r_row and new_id:
                from tender_app.saved_search_cache import copy_recent_cache_to_saved
                copy_recent_cache_to_saved(_get_db, r_row[0], new_id)
        except Exception:
            pass
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"error": str(e)}), 500
    cur.close()
    conn.close()
    return jsonify({"ok": True, "id": new_id})


@profiles_bp.route("/api/saved-searches/<int:search_id>", methods=["DELETE"])
def api_delete_saved_search(search_id):
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        from tender_app.saved_search_cache import invalidate_saved_search_cache
        invalidate_saved_search_cache(_get_db, search_id)
    except Exception:
        pass
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM saved_searches WHERE id=%s AND username=%s", (search_id, username))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})


@profiles_bp.route("/api/saved-searches/<int:search_id>/results", methods=["GET"])
def api_get_saved_search_results(search_id: int):
    """Fast path: cached results for this saved search, auto-populated on first access."""
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    from tender_app.saved_search_cache import get_cached_saved_search_results
    res = get_cached_saved_search_results(_get_db, search_id, username=username)
    if not res.get("ok"):
        return jsonify(res), 404
    return jsonify(res)


@profiles_bp.route("/api/saved-searches/<int:search_id>/refresh", methods=["POST"])
def api_refresh_saved_search_results(search_id: int):
    """Re-runs the search live, diffs against the cache, and returns the accumulated results."""
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    from tender_app.saved_search_cache import refresh_saved_search_cache, get_cached_saved_search_results
    ref_res = refresh_saved_search_cache(_get_db, search_id, username=username, force_live=True)
    if not ref_res.get("ok"):
        return jsonify(ref_res), 500
    res = get_cached_saved_search_results(_get_db, search_id, username=username)
    return jsonify(res)


@profiles_bp.route("/api/saved-searches/<int:search_id>/sync-cache", methods=["POST"])
def api_sync_saved_search_cache(search_id: int):
    """Writes rows the browser already fetched straight into the cache — no re-scrape,
    so the cache always holds the complete result set the user actually saw, not a
    time-boxed re-scrape's partial snapshot."""
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    rows = body.get("rows")
    if not isinstance(rows, list):
        return jsonify({"error": "rows must be a list"}), 400
    from tender_app.saved_search_cache import sync_search_cache_from_rows
    res = sync_search_cache_from_rows(_get_db, search_id, rows, search_type="saved", username=username)
    if not res.get("ok"):
        return jsonify(res), 404
    return jsonify(res)


@profiles_bp.route("/api/recent-searches", methods=["GET", "POST", "DELETE"])
def api_recent_searches():
    """GET returns up to 50 recent searches for the user.
    POST accepts {searches: [{q, scope}, ...]} and non-destructively upserts them (updating
    searched_at, keeping the row's id, email alert toggle, and cache when it already exists).
    DELETE removes a specific recent search (if q is provided) or all recent searches (if no q),
    and purges their result cache."""
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401

    conn = _get_db()
    cur = conn.cursor()

    if request.method == "DELETE":
        q = request.args.get("q", "").strip()
        scope = request.args.get("scope", "").strip()
        try:
            if q:
                cur.execute(
                    "SELECT id FROM recent_searches WHERE username = %s AND LOWER(query) = LOWER(%s) AND LOWER(scope) = LOWER(%s)",
                    (username, q, scope or "all"),
                )
            else:
                cur.execute("SELECT id FROM recent_searches WHERE username = %s", (username,))
            del_ids = [dr[0] for dr in cur.fetchall()]
            if del_ids:
                from tender_app.saved_search_cache import invalidate_recent_search_cache
                for did in del_ids:
                    try:
                        invalidate_recent_search_cache(_get_db, did)
                    except Exception:
                        pass

            if q:
                cur.execute(
                    "DELETE FROM recent_searches WHERE username = %s AND LOWER(query) = LOWER(%s) AND LOWER(scope) = LOWER(%s)",
                    (username, q, scope or "all"),
                )
            else:
                cur.execute("DELETE FROM recent_searches WHERE username = %s", (username,))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({"ok": True})
        except Exception as e:
            conn.rollback()
            cur.close()
            conn.close()
            return jsonify({"error": str(e)}), 500

    if request.method == "GET":
        try:
            cur.execute(
                "SELECT id, query, scope, searched_at, email_alerts_enabled, last_refreshed_at "
                "FROM recent_searches WHERE username=%s ORDER BY searched_at DESC, id DESC LIMIT 50",
                (username,),
            )
            rows = cur.fetchall()
            result = [
                {
                    "id": r[0], "q": r[1], "scope": r[2], "searchedAt": _format_iso(r[3]),
                    "emailAlertsEnabled": bool(r[4]), "lastRefreshedAt": _format_iso(r[5]),
                }
                for r in rows
            ]
            cur.close()
            conn.close()
            return jsonify({"searches": result})
        except Exception as e:
            cur.close()
            conn.close()
            return jsonify({"error": str(e)}), 500

    # POST — non-destructive upsert. Existing (query, scope) rows keep their id,
    # email alert toggle, and cache — only searched_at is bumped. New ones are
    # inserted with alerts on by default. Rows not in the incoming list are untouched.
    body = request.get_json(silent=True) or {}
    searches = body.get("searches", [])
    if not isinstance(searches, list):
        cur.close()
        conn.close()
        return jsonify({"error": "searches must be a list"}), 400

    try:
        incoming = []
        seen_keys = set()
        for item in searches[:50]:
            q = (item.get("q") or "").strip()
            scope = (item.get("scope") or "all").strip()
            if not q:
                continue
            key = (q.lower(), scope.lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            incoming.append((q, scope))

        for (q, scope) in incoming:
            cur.execute(
                "SELECT id FROM recent_searches WHERE username = %s AND LOWER(query) = LOWER(%s) AND LOWER(scope) = LOWER(%s)",
                (username, q, scope),
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    "UPDATE recent_searches SET searched_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (existing[0],),
                )
            else:
                cur.execute(
                    "INSERT INTO recent_searches (username, query, scope, searched_at, email_alerts_enabled) "
                    "VALUES (%s, %s, %s, CURRENT_TIMESTAMP, TRUE)",
                    (username, q, scope),
                )

        if incoming:
            # Prune beyond 50 most recently searched per user
            cur.execute(
                """DELETE FROM recent_searches WHERE username = %s AND id NOT IN (
                    SELECT id FROM (
                        SELECT id FROM recent_searches WHERE username = %s
                        ORDER BY searched_at DESC, id DESC LIMIT 50
                    ) as temp
                )""",
                (username, username),
            )

        conn.commit()

        # Return the updated list so the client gets durable IDs immediately
        cur.execute(
            "SELECT id, query, scope, searched_at, email_alerts_enabled, last_refreshed_at "
            "FROM recent_searches WHERE username=%s ORDER BY searched_at DESC, id DESC LIMIT 50",
            (username,),
        )
        rows = cur.fetchall()
        result = [
            {
                "id": r[0], "q": r[1], "scope": r[2], "searchedAt": _format_iso(r[3]),
                "emailAlertsEnabled": bool(r[4]), "lastRefreshedAt": _format_iso(r[5]),
            }
            for r in rows
        ]
        cur.close()
        conn.close()
        return jsonify({"ok": True, "searches": result})
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"error": str(e)}), 500


@profiles_bp.route("/api/recent-searches/<int:search_id>/results", methods=["GET"])
def api_get_recent_search_results(search_id: int):
    """Fast path: cached results for this recent search, auto-populated on first access."""
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    from tender_app.saved_search_cache import get_cached_recent_search_results
    res = get_cached_recent_search_results(_get_db, search_id, username=username)
    if not res.get("ok"):
        return jsonify(res), 404
    return jsonify(res)


@profiles_bp.route("/api/recent-searches/<int:search_id>/refresh", methods=["POST"])
def api_refresh_recent_search_results(search_id: int):
    """Re-runs the search live, diffs against the cache, and returns the accumulated results."""
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    from tender_app.saved_search_cache import refresh_recent_search_cache, get_cached_recent_search_results
    ref_res = refresh_recent_search_cache(_get_db, search_id, username=username, force_live=True)
    if not ref_res.get("ok"):
        return jsonify(ref_res), 500
    res = get_cached_recent_search_results(_get_db, search_id, username=username)
    return jsonify(res)


@profiles_bp.route("/api/recent-searches/<int:search_id>/sync-cache", methods=["POST"])
def api_sync_recent_search_cache(search_id: int):
    """Writes rows the browser already fetched straight into the cache — no re-scrape,
    so the cache always holds the complete result set the user actually saw, not a
    time-boxed re-scrape's partial snapshot."""
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    rows = body.get("rows")
    if not isinstance(rows, list):
        return jsonify({"error": "rows must be a list"}), 400
    from tender_app.saved_search_cache import sync_search_cache_from_rows
    res = sync_search_cache_from_rows(_get_db, search_id, rows, search_type="recent", username=username)
    if not res.get("ok"):
        return jsonify(res), 404
    return jsonify(res)


@profiles_bp.route("/api/recent-searches/<int:search_id>/toggle-alert", methods=["POST"])
def api_toggle_recent_search_alert(search_id: int):
    """Flips whether this recent search's results feed the daily email digest
    (only the last 4 recent searches with alerts on are ever emailed)."""
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    conn = _get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT email_alerts_enabled, query FROM recent_searches WHERE id = %s AND username = %s",
            (search_id, username),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return jsonify({"error": "Recent search not found"}), 404

        new_val = not bool(row[0])
        query_val = row[1]

        cur.execute(
            "UPDATE recent_searches SET email_alerts_enabled = %s WHERE id = %s AND username = %s",
            (new_val, search_id, username),
        )
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"ok": True, "email_alerts_enabled": new_val, "query": query_val})
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"error": str(e)}), 500




@profiles_bp.route("/api/user-prefs", methods=["GET", "POST"])
def api_user_prefs():
    """GET returns all preferences for the user as a {key: value} dict.
    POST accepts {prefs: {key: value, ...}} and upserts them."""
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401

    conn = _get_db()
    cur = conn.cursor()

    if request.method == "GET":
        try:
            cur.execute(
                "SELECT pref_key, pref_value FROM user_prefs WHERE username=%s",
                (username,),
            )
            rows = cur.fetchall()
            prefs = {r[0]: r[1] for r in rows}
            cur.close()
            conn.close()
            return jsonify({"ok": True, "prefs": prefs})
        except Exception as e:
            cur.close()
            conn.close()
            return jsonify({"error": str(e)}), 500

    # POST — upsert prefs
    body = request.get_json(silent=True) or {}
    prefs = body.get("prefs", {})
    if not isinstance(prefs, dict):
        cur.close()
        conn.close()
        return jsonify({"error": "prefs must be an object"}), 400

    try:
        for key, value in prefs.items():
            key = str(key)[:100]
            value = str(value) if value is not None else None
            cur.execute(
                """INSERT INTO user_prefs (username, pref_key, pref_value, updated_at)
                   VALUES (%s, %s, %s, NOW())
                   ON CONFLICT (username, pref_key)
                   DO UPDATE SET pref_value = EXCLUDED.pref_value, updated_at = NOW()""",
                (username, key, value),
            )
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"error": str(e)}), 500


def _hash_otp(code: str, salt: str) -> str:
    import hashlib
    return hashlib.sha256(f"{salt}:{code}".encode("utf-8")).hexdigest()


def _generate_otp() -> tuple[str, str, str]:
    """Generate a random 4-digit code (0000-9999), salt, and sha256 hash."""
    import random, secrets
    code = f"{random.randint(0, 9999):04d}"
    salt = secrets.token_hex(8)
    otp_hash = _hash_otp(code, salt)
    return code, salt, otp_hash


def _get_user_recipients(cur, username: str, internal: bool = False) -> list[dict[str, Any]]:
    import json
    p_query = (
        "SELECT pref_value FROM user_prefs WHERE username = %s AND pref_key = 'notification_recipients'"
    )
    try:
        cur.execute(p_query, (username,))
        row = cur.fetchone()
    except Exception:
        row = None

    stored = []
    if row and row[0]:
        try:
            stored = json.loads(row[0])
            if not isinstance(stored, list):
                stored = []
        except Exception:
            stored = []

    recipients = []
    emails_seen = set()

    for item in stored:
        if isinstance(item, dict) and item.get("email"):
            em = str(item["email"]).strip().lower()
            if em and em not in emails_seen:
                emails_seen.add(em)
                is_prim = bool(item.get("primary"))
                
                # Check for example.com or known invalid domains
                is_example = em.endswith("@example.com") or em.endswith("@test.com")
                is_verified = bool(item.get("verified")) and not is_example
                is_bounced = bool(item.get("hard_bounced")) or is_example
                bounce_reason = item.get("bounce_reason") or ("550 5.1.1 Example/test domain" if is_example else None)

                expires_at = float(item.get("expires_at") or 0)
                attempts = int(item.get("attempts") or 0)
                max_attempts = int(item.get("max_attempts") or 5)
                is_expired = bool(expires_at and time.time() > expires_at)
                is_locked = bool(attempts >= max_attempts)

                rec_obj = {
                    "email": em,
                    "verified": is_verified,
                    "primary": is_prim,
                    "hard_bounced": is_bounced,
                    "bounce_reason": bounce_reason,
                    "expires_at": expires_at,
                    "is_expired": is_expired,
                    "is_locked": is_locked,
                    "attempts": attempts,
                    "max_attempts": max_attempts,
                    "attempts_left": max(0, max_attempts - attempts),
                    "has_pending_otp": bool(item.get("otp_hash") and not is_verified and not is_bounced and not is_expired and not is_locked),
                    "created_at": item.get("created_at", 0),
                    "verified_at": item.get("verified_at"),
                    "resend_count": int(item.get("resend_count") or 0),
                    "resend_window_start": float(item.get("resend_window_start") or 0),
                }

                if internal:
                    rec_obj["otp_hash"] = item.get("otp_hash", "")
                    rec_obj["otp_salt"] = item.get("otp_salt", "")

                recipients.append(rec_obj)

    return recipients


def _save_user_recipients(cur, conn, username: str, recipients: list[dict[str, Any]]) -> None:
    import json
    rec_json = json.dumps(recipients)
    cur.execute(
        """INSERT INTO user_prefs (username, pref_key, pref_value, updated_at)
           VALUES (%s, 'notification_recipients', %s, NOW())
           ON CONFLICT (username, pref_key)
           DO UPDATE SET pref_value = EXCLUDED.pref_value, updated_at = NOW()""",
        (username, rec_json),
    )
    conn.commit()


@profiles_bp.post("/api/email-settings/recipients/add")
def api_add_recipient():
    import threading
    from tender_app.email_svc import send_verification_code_email
    username = session.get("username") or session.get("user") or session.get("email") or "admin"
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email or "." not in email:
        return jsonify({"ok": False, "error": "Please enter a valid email address"}), 400

    if email.endswith("@example.com") or email.endswith("@test.com"):
        return jsonify({"ok": False, "error": "Cannot use example or test domain email addresses for notifications"}), 400

    conn = _get_db()
    cur = conn.cursor()
    try:
        recipients = _get_user_recipients(cur, username, internal=True)
        existing = next((r for r in recipients if r["email"] == email), None)

        now = time.time()
        code, salt, otp_hash = _generate_otp()

        if existing:
            if existing.get("verified"):
                cur.close()
                conn.close()
                return jsonify({"ok": False, "error": f"'{email}' is already verified in your notification list"}), 400
            
            # Refresh OTP for the unverified recipient
            existing["otp_hash"] = otp_hash
            existing["otp_salt"] = salt
            existing["expires_at"] = now + 600  # 10 minutes
            existing["attempts"] = 0
            existing["hard_bounced"] = False
            existing["bounce_reason"] = None
            _save_user_recipients(cur, conn, username, recipients)
            cur.close()
            conn.close()

            threading.Thread(target=send_verification_code_email, args=(email, code), daemon=True).start()
            return jsonify({
                "ok": True,
                "message": f"4-digit verification code sent to {email}. Valid for 10 minutes.",
                "email": email
            })

        if len(recipients) >= 5:
            cur.close()
            conn.close()
            return jsonify({"ok": False, "error": "Maximum limit of 5 notification email recipients reached"}), 400

        recipients.append({
            "email": email,
            "verified": False,
            "primary": False,
            "otp_hash": otp_hash,
            "otp_salt": salt,
            "expires_at": now + 600,  # 10 minutes
            "attempts": 0,
            "max_attempts": 5,
            "resend_count": 1,
            "resend_window_start": now,
            "created_at": now,
            "hard_bounced": False,
            "bounce_reason": None,
        })
        _save_user_recipients(cur, conn, username, recipients)
        cur.close()
        conn.close()

        threading.Thread(target=send_verification_code_email, args=(email, code), daemon=True).start()
        return jsonify({
            "ok": True,
            "message": f"4-digit verification code sent to {email}. Valid for 10 minutes.",
            "email": email
        })
    except Exception as ex:
        try: cur.close()
        except Exception: pass
        try: conn.close()
        except Exception: pass
        print(f"[API Add Recipient Error] {ex}", flush=True)
        return jsonify({"ok": False, "error": str(ex)}), 500


@profiles_bp.post("/api/email-settings/recipients/verify")
def api_verify_recipient():
    username = session.get("username") or session.get("user") or session.get("email") or "admin"
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    code = (body.get("code") or "").strip()
    if not email or not code:
        return jsonify({"ok": False, "error": "Missing email or verification code"}), 400

    conn = _get_db()
    cur = conn.cursor()
    try:
        recipients = _get_user_recipients(cur, username, internal=True)
        target = next((r for r in recipients if r["email"] == email), None)
        if not target:
            cur.close()
            conn.close()
            return jsonify({"ok": False, "error": "Recipient email not found"}), 404
        if target.get("verified"):
            cur.close()
            conn.close()
            return jsonify({"ok": True, "message": "Email is already verified"})
        
        now = time.time()
        expires_at = float(target.get("expires_at") or 0)
        if expires_at and now > expires_at:
            cur.close()
            conn.close()
            return jsonify({"ok": False, "error": "Verification code has expired. Please click 'Resend' for a new code."}), 400

        attempts = int(target.get("attempts") or 0)
        max_attempts = int(target.get("max_attempts") or 5)
        if attempts >= max_attempts:
            cur.close()
            conn.close()
            return jsonify({"ok": False, "error": "Maximum attempts reached for this code. Please click 'Resend' for a new code."}), 400

        expected_hash = target.get("otp_hash")
        salt = target.get("otp_salt") or ""
        input_hash = _hash_otp(code, salt)

        if not expected_hash or input_hash != expected_hash:
            target["attempts"] = attempts + 1
            _save_user_recipients(cur, conn, username, recipients)
            cur.close()
            conn.close()
            remaining = max(0, max_attempts - target["attempts"])
            if remaining == 0:
                return jsonify({"ok": False, "error": "Invalid code. Maximum attempts reached. Please request a new code."}), 400
            return jsonify({"ok": False, "error": f"Invalid 4-digit code. {remaining} attempt(s) remaining."}), 400

        target["verified"] = True
        target["verified_at"] = now
        target["otp_hash"] = ""
        target["otp_salt"] = ""
        target["attempts"] = 0
        target["hard_bounced"] = False
        target["bounce_reason"] = None
        _save_user_recipients(cur, conn, username, recipients)
        cur.close()
        conn.close()
        return jsonify({"ok": True, "message": f"'{email}' verified successfully! Automated alert digests are now active."})
    except Exception as ex:
        try: cur.close()
        except Exception: pass
        try: conn.close()
        except Exception: pass
        print(f"[API Verify Recipient Error] {ex}", flush=True)
        return jsonify({"ok": False, "error": str(ex)}), 500


@profiles_bp.post("/api/email-settings/recipients/resend")
def api_resend_recipient_code():
    import threading
    from tender_app.email_svc import send_verification_code_email
    username = session.get("username") or session.get("user") or session.get("email") or "admin"
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    if not email:
        return jsonify({"ok": False, "error": "Missing email"}), 400

    conn = _get_db()
    cur = conn.cursor()
    try:
        recipients = _get_user_recipients(cur, username, internal=True)
        target = next((r for r in recipients if r["email"] == email), None)
        if not target:
            cur.close()
            conn.close()
            return jsonify({"ok": False, "error": "Recipient email not found"}), 404
        if target.get("verified"):
            cur.close()
            conn.close()
            return jsonify({"ok": True, "message": "Email is already verified"})

        now = time.time()
        window_start = float(target.get("resend_window_start") or 0)
        resend_count = int(target.get("resend_count") or 0)

        # Rate limit: max 3 resends per 15 minutes (900 seconds)
        if window_start and (now - window_start) < 900 and resend_count >= 3:
            remaining_mins = max(1, int((900 - (now - window_start)) / 60))
            cur.close()
            conn.close()
            return jsonify({
                "ok": False,
                "error": f"Too many resend attempts. Please wait {remaining_mins} minute(s) before requesting another code."
            }), 429

        if not window_start or (now - window_start) >= 900:
            target["resend_window_start"] = now
            target["resend_count"] = 0

        code, salt, otp_hash = _generate_otp()
        target["otp_hash"] = otp_hash
        target["otp_salt"] = salt
        target["expires_at"] = now + 600  # 10 minutes
        target["attempts"] = 0
        target["resend_count"] = int(target.get("resend_count") or 0) + 1
        _save_user_recipients(cur, conn, username, recipients)
        cur.close()
        conn.close()

        threading.Thread(target=send_verification_code_email, args=(email, code), daemon=True).start()
        return jsonify({"ok": True, "message": f"New 4-digit verification code sent to {email}"})
    except Exception as ex:
        try: cur.close()
        except Exception: pass
        try: conn.close()
        except Exception: pass
        print(f"[API Resend Recipient Code Error] {ex}", flush=True)
        return jsonify({"ok": False, "error": str(ex)}), 500


@profiles_bp.delete("/api/email-settings/recipients")
def api_delete_recipient():
    username = session.get("username") or session.get("user") or session.get("email") or "admin"
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    if not email:
        return jsonify({"ok": False, "error": "Missing email"}), 400

    conn = _get_db()
    cur = conn.cursor()
    try:
        recipients = _get_user_recipients(cur, username, internal=True)
        target = next((r for r in recipients if r["email"] == email), None)
        if not target:
            cur.close()
            conn.close()
            return jsonify({"ok": False, "error": "Recipient not found"}), 404
        
        filtered = [r for r in recipients if r["email"] != email]
        _save_user_recipients(cur, conn, username, filtered)
        cur.close()
        conn.close()
        return jsonify({"ok": True, "message": f"Recipient '{email}' removed"})
    except Exception as ex:
        try: cur.close()
        except Exception: pass
        try: conn.close()
        except Exception: pass
        print(f"[API Delete Recipient Error] {ex}", flush=True)
        return jsonify({"ok": False, "error": str(ex)}), 500

        recipients = [r for r in recipients if r["email"] != email]
        _save_user_recipients(cur, conn, username, recipients)
        cur.close()
        conn.close()
        return jsonify({"ok": True, "message": f"Removed '{email}' from notification recipients list"})
    except Exception as ex:
        try: cur.close()
        except Exception: pass
        try: conn.close()
        except Exception: pass
        print(f"[API Delete Recipient Error] {ex}", flush=True)
        return jsonify({"ok": False, "error": str(ex)}), 500


@profiles_bp.route("/api/email-settings", methods=["GET", "POST"])
def api_email_settings():
    """GET/POST dedicated email settings for automated alerts and best-fit notifications."""
    username = session.get("username") or "admin"

    conn = _get_db()
    cur = conn.cursor()

    if request.method == "GET":
        try:
            cur.execute(
                "SELECT pref_key, pref_value FROM user_prefs WHERE username=%s",
                (username,),
            )
            rows = cur.fetchall()
            prefs = {r[0]: r[1] for r in rows}

            recipients = _get_user_recipients(cur, username)
            clean_recipients = [
                {
                    "email": r["email"],
                    "verified": bool(r.get("verified")),
                    "primary": bool(r.get("primary"))
                }
                for r in recipients
            ]

            # Get user's primary email as default fallback if notification_email is unset
            if not prefs.get("notification_email"):
                u_q = "SELECT email FROM users WHERE username = %s OR firebase_uid = %s"
                cur.execute(u_q, (username, username))
                u_row = cur.fetchone()
                if u_row and u_row[0]:
                    prefs["notification_email"] = u_row[0]

            cur.close()
            conn.close()

            min_score_val = 70
            try:
                raw_fit = prefs.get("min_fit_score") or prefs.get("fit_score_threshold") or "70"
                min_score_val = int(str(raw_fit).replace("%", "").replace("+", "").strip())
            except Exception:
                min_score_val = 70

            settings = {
                "automated_emails_enabled": prefs.get("automated_emails_enabled", "true") == "true",
                "email_alerts_enabled": prefs.get("automated_emails_enabled", "true") == "true",
                "best_fit_emails_enabled": prefs.get("best_fit_emails_enabled", "true") == "true",
                "new_match_alerts_enabled": prefs.get("best_fit_emails_enabled", "true") == "true",
                "notification_email": prefs.get("notification_email", ""),
                "recipients": clean_recipients,
                "min_fit_score": min_score_val,
                "fit_score_threshold": f"{min_score_val}%",
                "email_frequency": prefs.get("email_frequency", "Immediately"),
                "new_match_frequency": prefs.get("new_match_frequency", prefs.get("email_frequency", "Immediately")),
                "selected_profile_id": prefs.get("selected_profile_id", ""),
                "alert_keywords": prefs.get("alert_keywords", ""),
                "deadline_reminders_enabled": prefs.get("deadline_reminders_enabled", "true") == "true",
                "deadline_reminders_threshold": prefs.get("deadline_reminders_threshold", "7 days before"),
                "low_credit_warning_enabled": prefs.get("low_credit_warning_enabled", "false") == "true",
                "low_credit_threshold": prefs.get("low_credit_threshold", "Below 50 credits"),
            }
            return jsonify({"ok": True, "settings": settings, **settings})
        except Exception as e:
            cur.close()
            conn.close()
            return jsonify({"ok": False, "error": str(e)}), 500

    # POST update settings
    body = request.get_json(silent=True) or {}
    auto_emails = "true" if (body.get("automated_emails_enabled") or body.get("email_alerts_enabled")) else "false"
    best_fit_emails = "true" if (body.get("best_fit_emails_enabled") or body.get("new_match_alerts_enabled")) else "false"
    deadline_reminders = "true" if body.get("deadline_reminders_enabled") else "false"
    low_credit_warning = "true" if body.get("low_credit_warning_enabled") else "false"

    raw_min_fit = body.get("min_fit_score") or body.get("fit_score_threshold") or "70"
    try:
        min_fit_val = int(str(raw_min_fit).replace("%", "").replace("+", "").strip())
    except Exception:
        min_fit_val = 70

    settings_to_save = {
        "automated_emails_enabled": auto_emails,
        "email_alerts_enabled": auto_emails,
        "best_fit_emails_enabled": best_fit_emails,
        "new_match_alerts_enabled": best_fit_emails,
        "notification_email": (body.get("notification_email") or "").strip(),
        "min_fit_score": str(min_fit_val),
        "fit_score_threshold": f"{min_fit_val}%",
        "email_frequency": str(body.get("email_frequency") or body.get("new_match_frequency") or "Immediately"),
        "new_match_frequency": str(body.get("new_match_frequency") or body.get("email_frequency") or "Immediately"),
        "selected_profile_id": str(body.get("selected_profile_id") or ""),
        "alert_keywords": str(body.get("alert_keywords") or ""),
        "deadline_reminders_enabled": deadline_reminders,
        "deadline_reminders_threshold": str(body.get("deadline_reminders_threshold") or "7 days before"),
        "low_credit_warning_enabled": low_credit_warning,
        "low_credit_threshold": str(body.get("low_credit_threshold") or "Below 50 credits"),
    }

    try:
        for k, v in settings_to_save.items():
            cur.execute(
                """INSERT INTO user_prefs (username, pref_key, pref_value, updated_at)
                   VALUES (%s, %s, %s, NOW())
                   ON CONFLICT (username, pref_key)
                   DO UPDATE SET pref_value = EXCLUDED.pref_value, updated_at = NOW()""",
                (username, k, v),
            )

        notif_email = settings_to_save["notification_email"]
        if notif_email:
            try:
                cur.execute("UPDATE users SET email = %s WHERE username = %s OR firebase_uid = %s", (notif_email, username, username))
            except Exception:
                pass

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"ok": True, "message": "Email settings saved successfully", "settings": settings_to_save})
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.post("/api/email-settings/test")
def api_email_settings_test():
    """Trigger an immediate test run of the profile-based best fit email notification."""
    username = session.get("username") or "admin"

    try:
        from tender_app.email_notifier import run_automated_email_job
        result = run_automated_email_job(_get_db, username, is_test=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.route("/api/pipeline", methods=["GET", "POST"])
def api_pipeline():
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    if request.method == "GET":
        conn = _get_db()
        cur = conn.cursor()
        cur.execute(
            """SELECT tender_key, title, source, contracting_authority, submission_deadline,
               estimated_value, stage, notes, fit_score, fit_band, updated_at
               FROM pipeline WHERE username=%s ORDER BY updated_at DESC""",
            (username,),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        items = [
            {
                "tender_key": r[0], "title": r[1], "source": r[2],
                "contracting_authority": r[3], "submission_deadline": r[4],
                "estimated_value": r[5], "stage": r[6], "notes": r[7] or "",
                "fit_score": r[8], "fit_band": r[9], "updated_at": str(r[10]),
            }
            for r in rows
        ]
        return jsonify({"items": items})
    body = request.get_json(silent=True) or {}
    tender_key = (body.get("tender_key") or "").strip()
    if not tender_key:
        return jsonify({"error": "tender_key required"}), 400
    stage = (body.get("stage") or "watching").strip().lower()
    if stage not in _PIPELINE_STAGES:
        return jsonify({"error": f"invalid stage; must be one of {_PIPELINE_STAGES}"}), 400
    conn = _get_db()
    cur = conn.cursor()
    fields = (
        body.get("title", ""), body.get("source", ""),
        body.get("contracting_authority", ""), body.get("submission_deadline", ""),
        body.get("estimated_value", ""), stage, body.get("notes", ""),
        body.get("fit_score"), body.get("fit_band", ""),
    )
    cur.execute(
        """INSERT INTO pipeline (username, tender_key, title, source, contracting_authority,
           submission_deadline, estimated_value, stage, notes, fit_score, fit_band, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
           ON CONFLICT(username, tender_key) DO UPDATE SET
           title=EXCLUDED.title, source=EXCLUDED.source,
           contracting_authority=EXCLUDED.contracting_authority,
           submission_deadline=EXCLUDED.submission_deadline,
           estimated_value=EXCLUDED.estimated_value, stage=EXCLUDED.stage,
           notes=EXCLUDED.notes, fit_score=EXCLUDED.fit_score,
           fit_band=EXCLUDED.fit_band, updated_at=CURRENT_TIMESTAMP""",
        (username, tender_key, *fields),
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})


@profiles_bp.route("/api/pipeline/<path:tender_key>", methods=["DELETE", "PATCH"])
def api_pipeline_item(tender_key):
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    conn = _get_db()
    cur = conn.cursor()
    if request.method == "DELETE":
        cur.execute("DELETE FROM pipeline WHERE username=%s AND tender_key=%s", (username, tender_key))
    else:
        body = request.get_json(silent=True) or {}
        stage = body.get("stage")
        notes = body.get("notes")
        if stage and stage not in _PIPELINE_STAGES:
            return jsonify({"error": "invalid stage"}), 400
        if stage is not None:
            cur.execute(
                "UPDATE pipeline SET stage=%s, updated_at=CURRENT_TIMESTAMP WHERE username=%s AND tender_key=%s",
                (stage, username, tender_key),
            )
        if notes is not None:
            cur.execute(
                "UPDATE pipeline SET notes=%s, updated_at=CURRENT_TIMESTAMP WHERE username=%s AND tender_key=%s",
                (notes, username, tender_key),
            )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})


@profiles_bp.route("/api/answer-bank", methods=["GET", "POST"])
def api_answer_bank():
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    if request.method == "GET":
        profile_id_str = request.args.get("profile_id")
        profile_id = None
        if profile_id_str and profile_id_str.strip() not in ("", "null", "undefined"):
            try:
                profile_id = int(profile_id_str)
            except ValueError:
                pass

        conn = _get_db()
        cur = conn.cursor()
        if profile_id is not None:
            cur.execute(
                "SELECT id, category, question, answer, updated_at, profile_id, file_name, file_content FROM answer_bank WHERE username=%s AND (profile_id=%s OR profile_id IS NULL) ORDER BY category, question",
                (username, profile_id),
            )
        else:
            cur.execute(
                "SELECT id, category, question, answer, updated_at, profile_id, file_name, file_content FROM answer_bank WHERE username=%s ORDER BY category, question",
                (username,),
            )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({
            "items": [
                {
                    "id": r[0],
                    "category": r[1],
                    "question": r[2],
                    "answer": r[3],
                    "updated_at": str(r[4]),
                    "profile_id": r[5],
                    "file_name": r[6] if len(r) > 6 else None,
                    "file_content": r[7] if len(r) > 7 else None,
                }
                for r in rows
            ]
        })
    body = request.get_json(silent=True) or {}
    category = (body.get("category") or "General").strip()
    question = (body.get("question") or "").strip()
    answer = (body.get("answer") or "").strip()
    file_name = (body.get("file_name") or "").strip() or None
    file_content = (body.get("file_content") or "").strip() or None
    profile_id_val = body.get("profile_id")
    profile_id = None
    if profile_id_val is not None and str(profile_id_val).strip() not in ("", "null", "undefined"):
        try:
            profile_id = int(profile_id_val)
        except ValueError:
            pass

    if not question or not answer:
        return jsonify({"error": "question and answer required"}), 400
    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO answer_bank (username, category, question, answer, profile_id, file_name, file_content) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (username, category, question, answer, profile_id, file_name, file_content),
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True, "id": new_id})


@profiles_bp.post("/api/answer-bank/upload")
def api_answer_bank_upload():
    """Upload document (PDF, DOCX, TXT, CSV) to extract text for Answer Bank."""
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    uploaded_file = request.files["file"]
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "Empty filename"}), 400

    filename = secure_filename(uploaded_file.filename) or uploaded_file.filename
    upload_dir = os.path.join(os.getcwd(), "uploads", "answer_bank_docs")
    os.makedirs(upload_dir, exist_ok=True)
    temp_path = os.path.join(upload_dir, f"{int(time.time())}_{filename}")
    uploaded_file.save(temp_path)

    extracted_text = ""
    try:
        from tender_app.doc_date_extractor import extract_text_from_file
        extracted_text = extract_text_from_file(temp_path, max_chars=50000)
    except Exception as e:
        print("Failed to extract document text:", e)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

    if not extracted_text or not extracted_text.strip():
        return jsonify({"error": "Could not extract text from document or file is empty"}), 400

    clean_name = os.path.splitext(uploaded_file.filename)[0].replace("_", " ").replace("-", " ").title()

    return jsonify({
        "ok": True,
        "file_name": uploaded_file.filename,
        "extracted_text": extracted_text.strip(),
        "suggested_question": f"Document: {clean_name}"
    })


@profiles_bp.route("/api/answer-bank/<int:item_id>", methods=["PUT", "DELETE"])
def api_answer_bank_item(item_id):
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    conn = _get_db()
    cur = conn.cursor()
    if request.method == "DELETE":
        cur.execute("DELETE FROM answer_bank WHERE id=%s AND username=%s", (item_id, username))
    else:
        body = request.get_json(silent=True) or {}
        category = (body.get("category") or "General").strip()
        question = (body.get("question") or "").strip()
        answer = (body.get("answer") or "").strip()
        file_name = (body.get("file_name") or "").strip() or None
        file_content = (body.get("file_content") or "").strip() or None
        profile_id_val = body.get("profile_id")
        profile_id = None
        if profile_id_val is not None and str(profile_id_val).strip() not in ("", "null", "undefined"):
            try:
                profile_id = int(profile_id_val)
            except ValueError:
                pass

        cur.execute(
            "UPDATE answer_bank SET category=%s, question=%s, answer=%s, profile_id=%s, file_name=%s, file_content=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s AND username=%s",
            (category, question, answer, profile_id, file_name, file_content, item_id, username),
        )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})


# ── Previous winning bids (reference material for proposal generation) ──────────

_WINNING_BID_EXTS = (".pdf", ".docx", ".txt", ".md")
_WINNING_BID_MAX_BYTES = 50 * 1024 * 1024


@profiles_bp.get("/api/winning-bids")
def api_winning_bids_list():
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401

    profile_id = None
    raw_pid = (request.args.get("profile_id") or "").strip()
    if raw_pid.isdigit():
        profile_id = int(raw_pid)

    conn = _get_db()
    cur = conn.cursor()
    sql = (
        "SELECT id, profile_id, title, buyer, contract_year, file_name, "
        "LENGTH(extracted_text), uploaded_at FROM winning_bids WHERE username=%s"
    )
    params = [username]
    if profile_id is not None:
        sql += " AND (profile_id=%s OR profile_id IS NULL)"
        params.append(profile_id)
    sql += " ORDER BY uploaded_at DESC, id DESC"
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify({
        "ok": True,
        "items": [
            {
                "id": r[0],
                "profile_id": r[1],
                "title": r[2],
                "buyer": r[3],
                "contract_year": r[4],
                "file_name": r[5],
                "char_count": r[6],
                "uploaded_at": str(r[7]),
            }
            for r in rows
        ],
    })


@profiles_bp.post("/api/winning-bids")
def api_winning_bids_upload():
    """Upload a previous winning bid (PDF/DOCX/TXT); its text is stored for use as proposal reference."""
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401

    uploaded_file = request.files.get("file")
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "No file uploaded"}), 400

    original_name = uploaded_file.filename
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in _WINNING_BID_EXTS:
        return jsonify({"error": "Unsupported file type. Please upload a PDF, DOCX or TXT file (convert .doc files to DOCX first)."}), 400

    uploaded_file.seek(0, os.SEEK_END)
    size = uploaded_file.tell()
    uploaded_file.seek(0)
    if size > _WINNING_BID_MAX_BYTES:
        return jsonify({"error": "File exceeds the 50MB limit"}), 400

    profile_id = None
    raw_pid = (request.form.get("profile_id") or "").strip()
    if raw_pid.isdigit():
        profile_id = int(raw_pid)

    title = (request.form.get("title") or "").strip()
    if not title:
        title = os.path.splitext(original_name)[0].replace("_", " ").replace("-", " ").strip().title()
    buyer = (request.form.get("buyer") or "").strip() or None
    contract_year = (request.form.get("contract_year") or "").strip()[:10] or None

    upload_dir = os.path.join(os.getcwd(), "uploads", "winning_bids_tmp")
    os.makedirs(upload_dir, exist_ok=True)
    temp_path = os.path.join(upload_dir, f"{int(time.time())}_{secure_filename(original_name) or 'bid' + ext}")
    uploaded_file.save(temp_path)

    extracted_text = ""
    try:
        from tender_app.doc_date_extractor import extract_text_from_file
        extracted_text = extract_text_from_file(temp_path, max_chars=50000)
    except Exception as e:
        print("Failed to extract winning bid text:", e)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    extracted_text = (extracted_text or "").strip()
    if not extracted_text:
        return jsonify({"error": "Could not extract text from the document (it may be a scanned image or empty)"}), 400

    conn = _get_db()
    cur = conn.cursor()
    if profile_id is not None:
        cur.execute("SELECT 1 FROM company_profiles WHERE id=%s AND username=%s", (profile_id, username))
        if not cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"error": "Profile not found or access denied"}), 404
    cur.execute(
        "INSERT INTO winning_bids (username, profile_id, title, buyer, contract_year, file_name, extracted_text) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id, uploaded_at",
        (username, profile_id, title[:255], buyer, contract_year, original_name[:255], extracted_text),
    )
    new_id, uploaded_at = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({
        "ok": True,
        "item": {
            "id": new_id,
            "profile_id": profile_id,
            "title": title[:255],
            "buyer": buyer,
            "contract_year": contract_year,
            "file_name": original_name,
            "char_count": len(extracted_text),
            "uploaded_at": str(uploaded_at),
        },
    })


@profiles_bp.delete("/api/winning-bids/<int:bid_id>")
def api_winning_bids_delete(bid_id: int):
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM winning_bids WHERE id=%s AND username=%s", (bid_id, username))
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    if not deleted:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


@profiles_bp.post("/api/answer-bank/generate")
def api_answer_bank_generate():
    """Generate (or enrich) an answer-bank answer using the LLM and attached reference documents."""
    username = session.get("username", "")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    existing_answer = (body.get("existing_answer") or "").strip()
    category = (body.get("category") or "General").strip()
    company_name = (body.get("company_name") or "").strip()
    document_context = (body.get("document_context") or body.get("attached_text") or body.get("file_content") or "").strip()
    profile_id_val = body.get("profile_id")

    if not question:
        return jsonify({"ok": False, "error": "question is required"}), 400

    profile_text = ""
    user_docs_context = ""

    # Look up company profile & user documents
    try:
        conn = _get_db()
        cur = conn.cursor()
        if profile_id_val:
            try:
                pid = int(profile_id_val)
                cur.execute(
                    "SELECT name, profile_text FROM company_profiles WHERE id=%s AND username=%s",
                    (pid, username),
                )
                p_row = cur.fetchone()
                if p_row:
                    if not company_name:
                        company_name = p_row[0] or ""
                    profile_text = p_row[1] or ""
            except Exception:
                pass
        else:
            try:
                cur.execute(
                    "SELECT name, profile_text FROM company_profiles WHERE username=%s ORDER BY is_default DESC, id DESC LIMIT 1",
                    (username,),
                )
                p_row = cur.fetchone()
                if p_row:
                    if not company_name:
                        company_name = p_row[0] or ""
                    profile_text = p_row[1] or ""
            except Exception:
                pass

        # Fetch recent user business documents for context
        try:
            cur.execute(
                "SELECT filename, file_path FROM user_documents WHERE username=%s ORDER BY id DESC LIMIT 5",
                (username,),
            )
            u_rows = cur.fetchall()
            from tender_app.doc_date_extractor import extract_text_from_file
            extracted_u_docs = []
            for r_fn, r_fp in u_rows:
                if r_fp and os.path.exists(r_fp):
                    try:
                        txt = extract_text_from_file(r_fp, max_chars=4000)
                        if txt and txt.strip():
                            extracted_u_docs.append(f"--- Document: {r_fn} ---\n{txt.strip()[:3000]}")
                    except Exception:
                        pass
            if extracted_u_docs:
                user_docs_context = "\n\n".join(extracted_u_docs)
        except Exception as e_ud:
            print("Notice: User documents lookup in generate:", e_ud)

        cur.close()
        conn.close()
    except Exception as e_db:
        print("Notice: DB lookup in answer_bank_generate:", e_db)

    try:
        from server import call_chat_api

        doc_blocks = []
        if company_name:
            doc_blocks.append(f"Bidding Company Name: {company_name}")
        if profile_text:
            doc_blocks.append(f"Company Capabilities & Profile Details:\n{profile_text[:2500]}")
        if user_docs_context:
            doc_blocks.append(f"Company Supporting Business Documents (My Documents):\n{user_docs_context[:4000]}")
        if document_context:
            doc_blocks.append(f"Attached Answer Reference Documents Content:\n{document_context[:8000]}")

        combined_doc_context = "\n\n".join(doc_blocks)

        if existing_answer:
            # Enrich existing answer
            system = (
                "You are an expert bid-writing consultant specializing in public-sector and enterprise tender responses. "
                "Your task is to enrich, enhance, and polish the provided pre-written answer to make it more persuasive, "
                "professional, factual, and compliant with procurement best-practices. "
                "MANDATORY: Carefully review all provided company profile information and attached reference documents. "
                "Weave in specific facts, figures, methodology, qualifications, and evidence from the documents into the answer. "
                "Keep the tone professional and written in the first-person plural (we/our). "
                "Return ONLY the final enriched answer text — no explanations, no headers, no markdown fencing."
            )
            user = (
                f"{combined_doc_context}\n\n"
                f"Category: {category}\n"
                f"Question / Topic: {question}\n\n"
                f"Existing answer to enrich:\n{existing_answer}"
            )
        else:
            # Generate from scratch
            system = (
                "You are an expert bid-writing consultant specializing in public-sector and enterprise tender responses. "
                "Your task is to write a highly professional, factual, concise, and persuasive pre-written answer that can be "
                "reused in tender and procurement bid responses. "
                "MANDATORY: You MUST thoroughly review and incorporate facts, capabilities, methodologies, certifications, and details "
                "from the provided company profile and attached reference documents into the answer. "
                "The answer should be factual, credible, and written in the first-person plural (we/our). "
                "Return ONLY the answer text — no explanations, no headers, no markdown fencing."
            )
            user = (
                f"{combined_doc_context}\n\n"
                f"Category: {category}\n"
                f"Write a professional answer for the following tender question / topic:\n\n"
                f"{question}"
            )

        generated = call_chat_api(
            system=system,
            user=user,
            max_tokens=1000,
            temperature=0.3,
        )
        return jsonify({"ok": True, "answer": generated.strip()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.get("/api/company-profiles/<int:profile_id>/documents")
def api_get_profile_documents(profile_id: int):
    username = session.get("username", "admin")
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, filename, file_size, uploaded_at, issue_date, expiry_date FROM company_profile_documents WHERE profile_id = %s AND username = %s ORDER BY uploaded_at DESC",
            (profile_id, username),
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        seen = set()
        documents = []
        for r in rows:
            key = (r[1], r[2])  # (filename, file_size)
            if key not in seen:
                seen.add(key)
                documents.append({
                    "id": r[0],
                    "filename": r[1],
                    "file_size": r[2],
                    "uploaded_at": str(r[3]),
                    "issue_date": r[4] or "",
                    "expiry_date": r[5] or ""
                })

        return jsonify({"ok": True, "documents": documents})
    except Exception as e:
        print("Failed to fetch profile documents:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.post("/api/company-profiles/<int:profile_id>/documents")
def api_upload_profile_document(profile_id: int):
    username = session.get("username", "admin")
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file part in the request"}), 400
    
    file = request.files["file"]
    if not file or file.filename == "":
        return jsonify({"ok": False, "error": "No selected file"}), 400

    # Validate file size: limit to 500MB (524288000 bytes)
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    
    if file_size > 500 * 1024 * 1024:
        return jsonify({"ok": False, "error": "File size exceeds the 500MB limit"}), 400

    filename = secure_filename(file.filename)
    if not filename:
        return jsonify({"ok": False, "error": "Invalid filename"}), 400

    # Ensure profile belongs to user
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM company_profiles WHERE id = %s AND username = %s", (profile_id, username))
        profile_row = cursor.fetchone()
        if not profile_row:
            cursor.close()
            conn.close()
            return jsonify({"ok": False, "error": "Profile not found or access denied"}), 404

        issue_date = (request.form.get("issue_date") or request.args.get("issue_date") or "").strip()
        expiry_date = (request.form.get("expiry_date") or request.args.get("expiry_date") or "").strip()

        # Save to disk
        upload_dir = os.path.join("uploads", "profile_docs")
        os.makedirs(upload_dir, exist_ok=True)
        
        # Prevent collisions by prefixing with profile_id
        safe_filename = f"{profile_id}_{filename}"
        file_path = os.path.join(upload_dir, safe_filename)
        file.save(file_path)

        # Automatic Date Extraction if user didn't specify dates
        if not issue_date or not expiry_date:
            try:
                from tender_app.doc_date_extractor import auto_extract_document_dates
                auto_issue, auto_exp = auto_extract_document_dates(file_path)
                if not issue_date and auto_issue:
                    issue_date = auto_issue
                if not expiry_date and auto_exp:
                    expiry_date = auto_exp
            except Exception as err:
                print("Auto-date extraction error:", err)

        # Save to database
        cursor.execute(
            "INSERT INTO company_profile_documents (profile_id, username, filename, file_path, file_size, issue_date, expiry_date) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (profile_id, username, filename, file_path, file_size, issue_date, expiry_date),
        )
        doc_id = cursor.fetchone()[0]
            
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            "ok": True, 
            "document": {
                "id": doc_id, 
                "filename": filename, 
                "file_size": file_size, 
                "uploaded_at": "Just now",
                "issue_date": issue_date,
                "expiry_date": expiry_date
            }
        })
    except Exception as e:
        print("Failed to upload profile document:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.delete("/api/company-profiles/<int:profile_id>/documents/<int:doc_id>")
def api_delete_profile_document(profile_id: int, doc_id: int):
    username = session.get("username", "admin")
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT file_path FROM company_profile_documents WHERE id = %s AND profile_id = %s AND username = %s",
            (doc_id, profile_id, username),
        )
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return jsonify({"ok": False, "error": "Document not found or access denied"}), 404
            
        file_path = row[0]
        
        # Delete from database
        cursor.execute("DELETE FROM company_profile_documents WHERE id = %s", (doc_id,))
            
        conn.commit()
        cursor.close()
        conn.close()
        
        # Delete from disk
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as disk_err:
                print(f"Warning: failed to delete file {file_path} from disk: {disk_err}")
                
        return jsonify({"ok": True})
    except Exception as e:
        print("Failed to delete profile document:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.get("/api/company-profiles/<int:profile_id>/documents/<int:doc_id>/download")
def api_download_profile_document(profile_id: int, doc_id: int):
    username = session.get("username", "admin")
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT filename, file_path FROM company_profile_documents WHERE id = %s AND profile_id = %s AND username = %s",
            (doc_id, profile_id, username),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return jsonify({"error": "Document not found or access denied"}), 404
            
        filename, file_path = row
        if not os.path.exists(file_path):
            return jsonify({"error": "File not found on disk"}), 404
            
        inline = request.args.get('inline') == 'true'
        return send_file(file_path, as_attachment=not inline, download_name=filename)
    except Exception as e:
        print("Failed to download profile document:", e)
        return jsonify({"error": str(e)}), 500


@profiles_bp.get("/api/user-documents")
def api_get_user_documents():
    username = session.get("username", "admin")
    try:
        conn = _get_db()
        cursor = conn.cursor()
        
        # 1. Fetch global user documents
        cursor.execute(
            "SELECT id, filename, file_size, uploaded_at, issue_date, expiry_date FROM user_documents WHERE username = %s ORDER BY uploaded_at DESC",
            (username,),
        )
        user_rows = cursor.fetchall()
        
        # 2. Fetch company profile documents
        cursor.execute(
            """
            SELECT d.id, d.filename, d.file_size, d.uploaded_at, d.profile_id, p.name, d.issue_date, d.expiry_date 
            FROM company_profile_documents d
            JOIN company_profiles p ON d.profile_id = p.id
            WHERE d.username = %s
            ORDER BY d.uploaded_at DESC
            """,
            (username,),
        )
        profile_rows = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        documents = []
        for r in user_rows:
            documents.append({
                "id": r[0],
                "filename": r[1],
                "file_size": r[2],
                "uploaded_at": str(r[3]),
                "issue_date": r[4] or "",
                "expiry_date": r[5] or "",
                "type": "user",
                "profile_id": None,
                "company_name": None
            })
            
        for r in profile_rows:
            documents.append({
                "id": r[0],
                "filename": r[1],
                "file_size": r[2],
                "uploaded_at": str(r[3]),
                "issue_date": r[6] or "",
                "expiry_date": r[7] or "",
                "type": "profile",
                "profile_id": r[4],
                "company_name": r[5]
            })
            
        # Sort combined documents by uploaded_at descending
        documents.sort(key=lambda x: x["uploaded_at"], reverse=True)
        
        return jsonify({"ok": True, "documents": documents})
    except Exception as e:
        print("Failed to fetch user documents:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.get("/api/user-documents/expiry-alerts")
def api_get_user_documents_expiry_alerts():
    username = session.get("username", "admin")
    try:
        conn = _get_db()
        cursor = conn.cursor()
        
        # 1. Fetch user_documents with expiry_date
        cursor.execute(
            "SELECT id, filename, expiry_date, 'user' as type, NULL as profile_id FROM user_documents WHERE username = %s AND expiry_date IS NOT NULL AND expiry_date != ''",
            (username,),
        )
        user_rows = cursor.fetchall()
        
        # 2. Fetch company_profile_documents with expiry_date
        cursor.execute(
            """
            SELECT d.id, d.filename, d.expiry_date, 'profile' as type, d.profile_id 
            FROM company_profile_documents d
            WHERE d.username = %s AND d.expiry_date IS NOT NULL AND d.expiry_date != ''
            """,
            (username,),
        )
        profile_rows = cursor.fetchall()
        cursor.close()
        conn.close()

        from datetime import datetime, date
        today = date.today()

        alerts = []
        for r in (user_rows + profile_rows):
            doc_id, filename, exp_str, doc_type, p_id = r[0], r[1], r[2], r[3], r[4]
            if not exp_str:
                continue
            try:
                exp_date = datetime.strptime(str(exp_str).split("T")[0], "%Y-%m-%d").date()
                diff_days = (exp_date - today).days

                if diff_days <= 30:
                    level = "critical" if diff_days <= 3 else ("urgent" if diff_days <= 7 else ("warning" if diff_days <= 15 else "notice"))
                    
                    if diff_days < 0:
                        desc = f"Document '{filename}' expired {abs(diff_days)} days ago ({exp_date.strftime('%d %b %Y')})."
                        threshold = "expired"
                    elif diff_days == 0:
                        desc = f"Document '{filename}' expires TODAY ({exp_date.strftime('%d %b %Y')})!"
                        threshold = "today"
                    elif diff_days == 1:
                        desc = f"Document '{filename}' expires tomorrow (1 day remaining)."
                        threshold = "1_day"
                    elif diff_days in (2, 3):
                        desc = f"Document '{filename}' expires in {diff_days} days."
                        threshold = f"{diff_days}_days"
                    elif diff_days <= 7:
                        desc = f"Document '{filename}' expires in {diff_days} days."
                        threshold = "7_days"
                    elif diff_days <= 15:
                        desc = f"Document '{filename}' expires in {diff_days} days."
                        threshold = "15_days"
                    else:
                        desc = f"Document '{filename}' expires in {diff_days} days."
                        threshold = "30_days"

                    alerts.append({
                        "id": doc_id,
                        "filename": filename,
                        "expiry_date": str(exp_date),
                        "days_remaining": diff_days,
                        "threshold": threshold,
                        "level": level,
                        "type": doc_type,
                        "profile_id": p_id,
                        "message": desc
                    })
            except Exception as parse_err:
                print(f"Error parsing date {exp_str}:", parse_err)

        alerts.sort(key=lambda x: x["days_remaining"])
        return jsonify({"ok": True, "alerts": alerts, "total_expiring": len(alerts)})

    except Exception as e:
        print("Failed to fetch expiry alerts:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.post("/api/user-documents")
def api_upload_user_document():
    username = session.get("username", "admin")
    profile_id = request.form.get("profile_id") or request.args.get("profile_id")
    if profile_id and str(profile_id).strip().lower() not in ("all", "global", "none", "null", ""):
        try:
            p_id = int(profile_id)
            return api_upload_profile_document(p_id)
        except (ValueError, TypeError):
            pass

    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file part in the request"}), 400
    
    file = request.files["file"]
    if not file or file.filename == "":
        return jsonify({"ok": False, "error": "No selected file"}), 400

    # Validate file size: limit to 500MB (524288000 bytes)
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    
    if file_size > 500 * 1024 * 1024:
        return jsonify({"ok": False, "error": "File size exceeds the 500MB limit"}), 400

    filename = secure_filename(file.filename)
    if not filename:
        return jsonify({"ok": False, "error": "Invalid filename"}), 400

    issue_date = (request.form.get("issue_date") or request.args.get("issue_date") or "").strip()
    expiry_date = (request.form.get("expiry_date") or request.args.get("expiry_date") or "").strip()

    try:
        import uuid
        unique_prefix = uuid.uuid4().hex
        
        # Save to disk
        upload_dir = os.path.join("uploads", "user_docs")
        os.makedirs(upload_dir, exist_ok=True)
        
        safe_filename = f"{unique_prefix}_{filename}"
        file_path = os.path.join(upload_dir, safe_filename)
        file.save(file_path)

        # Automatic Date Extraction if user didn't specify dates
        if not issue_date or not expiry_date:
            try:
                from tender_app.doc_date_extractor import auto_extract_document_dates
                auto_issue, auto_exp = auto_extract_document_dates(file_path)
                if not issue_date and auto_issue:
                    issue_date = auto_issue
                if not expiry_date and auto_exp:
                    expiry_date = auto_exp
            except Exception as err:
                print("Auto-date extraction error:", err)

        # Save to database
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO user_documents (username, filename, file_path, file_size, issue_date, expiry_date) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (username, filename, file_path, file_size, issue_date, expiry_date),
        )
        doc_id = cursor.fetchone()[0]
            
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            "ok": True, 
            "document": {
                "id": doc_id, 
                "filename": filename, 
                "file_size": file_size, 
                "uploaded_at": "Just now",
                "issue_date": issue_date,
                "expiry_date": expiry_date
            }
        })
    except Exception as e:
        print("Failed to upload user document:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.delete("/api/user-documents/<int:doc_id>")
def api_delete_user_document(doc_id: int):
    username = session.get("username", "admin")
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT file_path FROM user_documents WHERE id = %s AND username = %s",
            (doc_id, username),
        )
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return jsonify({"ok": False, "error": "Document not found or access denied"}), 404
            
        file_path = row[0]
        
        # Delete from database
        cursor.execute("DELETE FROM user_documents WHERE id = %s", (doc_id,))
            
        conn.commit()
        cursor.close()
        conn.close()
        
        # Delete from disk
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as disk_err:
                print(f"Warning: failed to delete file {file_path} from disk: {disk_err}")
                
        return jsonify({"ok": True})
    except Exception as e:
        print("Failed to delete user document:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.get("/api/user-documents/<int:doc_id>/download")
def api_download_user_document(doc_id: int):
    username = session.get("username", "admin")
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT filename, file_path FROM user_documents WHERE id = %s AND username = %s",
            (doc_id, username),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return jsonify({"error": "Document not found or access denied"}), 404
            
        filename, file_path = row
        if not os.path.exists(file_path):
            return jsonify({"error": "File not found on disk"}), 404
            
        inline = request.args.get('inline') == 'true'
        return send_file(file_path, as_attachment=not inline, download_name=filename)
    except Exception as e:
        print("Failed to download user document:", e)
        return jsonify({"error": str(e)}), 500


@profiles_bp.get("/api/user-documents/<int:doc_id>/preview")
def api_preview_user_document(doc_id: int):
    username = session.get("username", "admin")
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT filename, file_path FROM user_documents WHERE id = %s AND username = %s",
            (doc_id, username),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return jsonify({"ok": False, "error": "Document not found or access denied"}), 404
            
        filename, file_path = row
        if not os.path.exists(file_path):
            return jsonify({"ok": False, "error": "File not found on disk"}), 404
            
        from etenders_scraper.cft_documents import _extract_bytes
        
        with open(file_path, "rb") as f:
            data = f.read()
            
        preview_text = _extract_bytes(data, filename, max_chars=50000)
        
        return jsonify({
            "ok": True,
            "filename": filename,
            "preview_text": preview_text
        })
    except Exception as e:
        print("Failed to preview user document:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.get("/api/company-profiles/<int:profile_id>/documents/<int:doc_id>/preview")
def api_preview_profile_document(profile_id: int, doc_id: int):
    username = session.get("username", "admin")
    try:
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT filename, file_path FROM company_profile_documents WHERE id = %s AND profile_id = %s AND username = %s",
            (doc_id, profile_id, username),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return jsonify({"ok": False, "error": "Document not found or access denied"}), 404
            
        filename, file_path = row
        if not os.path.exists(file_path):
            return jsonify({"ok": False, "error": "File not found on disk"}), 404
            
        from etenders_scraper.cft_documents import _extract_bytes
        
        with open(file_path, "rb") as f:
            data = f.read()
            
        preview_text = _extract_bytes(data, filename, max_chars=50000)
        
        return jsonify({
            "ok": True,
            "filename": filename,
            "preview_text": preview_text
        })
    except Exception as e:
        print("Failed to preview profile document:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


def search_yahoo(query: str, max_results: int = 5) -> list[dict]:
    import urllib.request
    import urllib.parse
    from bs4 import BeautifulSoup
    results = []
    try:
        url = "https://search.yahoo.com/search?p=" + urllib.parse.quote_plus(query)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read()
            soup = BeautifulSoup(html, "html.parser")
            for item in soup.find_all("div", class_="algo"):
                a = item.find("a")
                snippet_div = item.find("div", class_="compText") or item.find("span", class_="fc-t")
                if a:
                    title = a.get_text(strip=True)
                    link = a.get("href", "")
                    if "/RU=" in link:
                        parts = link.split("/RU=")
                        if len(parts) > 1:
                            target = parts[1].split("/")[0]
                            link = urllib.parse.unquote(target)
                    desc = snippet_div.get_text(strip=True) if snippet_div else ""
                    results.append({"title": title, "link": link, "snippet": desc})
                    if len(results) >= max_results:
                        break
    except Exception as e:
        print(f"Yahoo search failed for query '{query}':", e)
    return results


def search_duckduckgo(query: str, max_results: int = 5) -> list[dict]:
    import urllib.request
    import urllib.parse
    from bs4 import BeautifulSoup
    results = []
    try:
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read()
            soup = BeautifulSoup(html, "html.parser")
            for body in soup.find_all("div", class_="result__body"):
                a = body.find("a", class_="result__a")
                snippet = body.find("a", class_="result__snippet")
                if a:
                    title = a.get_text(strip=True)
                    link = a.get("href", "")
                    if "uddg=" in link:
                        parsed_link = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
                        link = parsed_link.get("uddg", [link])[0]
                    desc = snippet.get_text(strip=True) if snippet else ""
                    results.append({"title": title, "link": link, "snippet": desc})
                    if len(results) >= max_results:
                        break
    except Exception as e:
        print(f"DuckDuckGo search failed for query '{query}':", e)

    if not results:
        print(f"DuckDuckGo returned 0 results for '{query}'. Falling back to Yahoo search...")
        return search_yahoo(query, max_results)
    return results

def crawl_official_website(website_url: str) -> str:
    import urllib.request
    import urllib.parse
    from bs4 import BeautifulSoup
    import json
    
    if not website_url:
        return ""
        
    print(f"[Crawl] Crawling official website: {website_url}")
    results_text = []
    
    # Ensure scheme
    url = website_url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
        
    parsed_root = urllib.parse.urlparse(url)
    base_url = f"{parsed_root.scheme}://{parsed_root.netloc}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    
    homepage_html = None
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as response:
            homepage_html = response.read()
    except Exception as e:
        print(f"[Crawl] Failed to fetch homepage {url}: {e}")
        # Try base_url if different
        if base_url != url:
            try:
                req = urllib.request.Request(base_url, headers=headers)
                with urllib.request.urlopen(req, timeout=8) as response:
                    homepage_html = response.read()
            except Exception:
                pass
                
    if not homepage_html:
        return ""
        
    soup = BeautifulSoup(homepage_html, "html.parser")
    
    # 1. Parse JSON-LD
    json_ld_data = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            js_content = script.get_text().strip()
            if js_content:
                parsed_json = json.loads(js_content)
                json_ld_data.append(parsed_json)
        except Exception:
            pass
            
    if json_ld_data:
        results_text.append("--- Structured JSON-LD Data from Homepage ---")
        results_text.append(json.dumps(json_ld_data, indent=2))
        
    # Extract meta description
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        results_text.append(f"Homepage Meta Description: {meta_desc.get('content')}")
        
    # Extract homepage visible text (first 1500 chars)
    text_content = soup.get_text(" ", strip=True)
    text_content = " ".join(text_content.split())
    results_text.append(f"Homepage Snippet:\n{text_content[:1500]}")
    
    # 2. Find contact/about/legal links
    candidate_links = []
    seen_urls = {url, base_url, base_url + "/"}
    
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        text = a.get_text(strip=True).lower()
        
        full_href = urllib.parse.urljoin(base_url, href)
        parsed_link = urllib.parse.urlparse(full_href)
        
        if parsed_link.netloc != parsed_root.netloc:
            continue
            
        keywords = ["contact", "about", "legal", "terms", "privacy", "address", "imprint", "headquarter"]
        is_candidate = False
        for kw in keywords:
            if kw in text or kw in href.lower():
                is_candidate = True
                break
                
        if is_candidate and full_href not in seen_urls:
            candidate_links.append(full_href)
            seen_urls.add(full_href)
            if len(candidate_links) >= 3:
                break
                
    # 3. Crawl candidates
    for link in candidate_links:
        try:
            print(f"[Crawl] Fetching subpage: {link}")
            sub_req = urllib.request.Request(link, headers=headers)
            with urllib.request.urlopen(sub_req, timeout=6) as response:
                sub_html = response.read()
                sub_soup = BeautifulSoup(sub_html, "html.parser")
                
                sub_json_ld = []
                for script in sub_soup.find_all("script", type="application/ld+json"):
                    try:
                        js_content = script.get_text().strip()
                        if js_content:
                            sub_json_ld.append(json.loads(js_content))
                    except Exception:
                        pass
                if sub_json_ld:
                    results_text.append(f"--- Structured JSON-LD Data from {link} ---")
                    results_text.append(json.dumps(sub_json_ld, indent=2))
                    
                sub_text = sub_soup.get_text(" ", strip=True)
                sub_text = " ".join(sub_text.split())
                results_text.append(f"Snippet from page ({link}):\n{sub_text[:2000]}")
        except Exception as e:
            print(f"[Crawl] Failed to fetch subpage {link}: {e}")
            
    return "\n\n".join(results_text)


@profiles_bp.post("/api/company-profiles/generate-info")
def api_generate_company_profile_info():
    body = request.get_json(silent=True) or {}
    company_name = body.get("company_name", "").strip()
    linkedin_url = body.get("linkedin_url", "").strip()
    website_url = body.get("website_url", "").strip()
    
    existing_data = body.get("existing_data")
    
    if not company_name:
        return jsonify({"ok": False, "error": "Company Name / Search Keyword is required"}), 400
        
    try:
        from server import call_chat_api
        import json
        import re
        
        # 0. Crawl official website directly if provided to extract raw text & JSON-LD
        website_crawled_text = ""
        if website_url:
            try:
                website_crawled_text = crawl_official_website(website_url)
            except Exception as e:
                print(f"[Crawl Error] Failed to crawl website {website_url}: {e}")
        
        # Build search queries and perform search to gather accurate real-world data
        search_results = []
        
        # 1. Primary search for general info/website
        q_general = f"{company_name} company profile"
        if website_url:
            q_general += f" {website_url}"
        search_results.extend(search_duckduckgo(q_general, max_results=6))

        # 1b. Dedicated official-website search — without this, a company whose site
        # wasn't already provided has no query aimed specifically at finding it, and
        # "website" tends to come back empty even though the site is easy to search for.
        if not website_url:
            search_results.extend(search_duckduckgo(f"{company_name} official website", max_results=5))

        # 2. Targeted search for official contact details & headquarters address
        if website_url:
            domain = website_url.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
            q_contact = f"site:{domain} contact OR address OR phone OR email OR headquarters"
            search_results.extend(search_duckduckgo(q_contact, max_results=5))
        else:
            q_contact = f"{company_name} contact details address email phone number"
            search_results.extend(search_duckduckgo(q_contact, max_results=5))
            
        # 3. Registry details search (to get correct registration/vat numbers and addresses)
        q_registry = f"{company_name} company registration office registered address contact"
        search_results.extend(search_duckduckgo(q_registry, max_results=5))
        
        # 3b. Targeted UK Companies House query
        q_companies_house = f'site:company-information.service.gov.uk "{company_name}"'
        search_results.extend(search_duckduckgo(q_companies_house, max_results=3))
        
        # 3c. DUNS & VAT targeted search
        q_duns_vat = f'"{company_name}" duns OR "duns number" OR "vat number" OR "vat registration" OR "gb"'
        search_results.extend(search_duckduckgo(q_duns_vat, max_results=4))
        
        # 4. LinkedIn search if not provided
        if not linkedin_url:
            q_linkedin = f"{company_name} linkedin company"
            search_results.extend(search_duckduckgo(q_linkedin, max_results=4))
            
        search_text = ""
        if website_crawled_text:
            search_text += f"\n--- High-Fidelity Data Crawled Directly From the Official Company Website ({website_url}) ---\n{website_crawled_text}\n"
            
        if search_results:
            search_text += "\nHere are some web search results for the company to help you get accurate details:\n"
            # Deduplicate by link
            seen_links = set()
            for idx, r in enumerate(search_results, 1):
                link = r["link"]
                if link in seen_links:
                    continue
                seen_links.add(link)
                search_text += f"{len(seen_links)}. Title: {r['title']}\n   URL: {r['link']}\n   Snippet: {r['snippet']}\n\n"

        existing_text = ""
        if existing_data:
            existing_text = f"\nHere is the existing company profile data we have. Your goal is to ENRICH, IMPROVE, and FILL IN MISSING DETAILS for this profile, preserving correct information while correcting errors or filling in blanks:\n{json.dumps(existing_data, indent=2)}\n"

        system_prompt = (
            "You are an expert AI business analyst and researcher. Your task is to research, enrich, or generate detailed corporate profile information "
            "for a company based on its name, website, LinkedIn URL, existing data, and provided search results. This profile will be used to automatically score the company's fit "
            "against public tenders (RFPs) and write proposals. You must output a JSON object matching the requested schema exactly. "
            "Take your time and perform a deep deductive analysis of all search snippets to locate the official office address, postal code, registration number, "
            "email address, and contact numbers. Search thoroughly for any string patterns resembling email addresses (containing '@' and matching the company's domain if possible) "
            "and contact phone numbers (including landlines, mobile numbers, and support numbers). "
            "When extracting corporate attributes (contact name, email, phone, registered office address, and registration numbers), you must resolve conflicts "
            "and prioritize information using the following strict hierarchy of source reliability:\n"
            "1. (Highest Reliability) Official company website (Contact, About Us, Legal, or Terms pages) - e.g., domains matching website_url.\n"
            "2. (High Reliability) LinkedIn Company Page.\n"
            "3. (High Reliability) Companies House or official government registry details (e.g. gov.uk, search.cro.ie).\n"
            "4. (High Reliability) Google Business Profile listings.\n"
            "5. (Medium Reliability) Crunchbase / ZoomInfo corporate directory profiles.\n"
            "6. (Low Reliability) News articles or third-party press releases.\n"
            "7. (Very Low Reliability) Generic snippets or unrelated domain text.\n"
            "Analyze the URLs of the search results to map each snippet to its reliability tier, and strictly prioritize higher-tier sources to ensure maximum accuracy. "
            "For the website field specifically: if no website was provided, look at every result URL's domain — the correct one is usually the domain that most "
            "closely matches the company name and appears repeatedly across results (not social media, directory, or news-site domains like linkedin.com, "
            "facebook.com, bloomberg.com, crunchbase.com, or gov.uk registries). Prefer the shortest, most canonical form (e.g. https://example.com over a deep "
            "subpage URL). Only leave website empty if no plausible company domain appears anywhere in the results. "
            "Do not output default placeholders or generic placeholders unless the information cannot be found anywhere in the provided search results."
        )
        
        user_prompt = f"""Generate profile information for:
Company Name: {company_name}
LinkedIn: {linkedin_url or 'Not provided'}
Website: {website_url or 'Not provided'}

{search_text}
{existing_text}

You must return a JSON object with the following fields:
1. "org_name": Legal or trade name of the company (string).
2. "company_type": Must be exactly one of: "private", "public", "sole_trader", "partnership".
3. "enterprise_type": Employee size classification. Must be exactly one of:
   - "micro" (1-10 employees)
   - "small" (11-50 employees)
   - "medium" (51-250 employees)
   - "large" (250+ employees)
4. "business_type": The primary sector NACE code letter. Must be exactly one of:
   - "A" (Agriculture, Forestry, Fishing)
   - "B" (Mining)
   - "C" (Manufacturing)
   - "D" (Electricity, Gas, Steam)
   - "E" (Water Supply, Waste Management)
   - "F" (Construction)
   - "G" (Wholesale/Retail Trade)
   - "H" (Transportation/Storage)
   - "I" (Accommodation/Food Services)
   - "J" (Information and Communication - e.g., Software, IT, Telecoms)
   - "K" (Financial/Insurance)
   - "L" (Real Estate)
   - "M" (Professional, Scientific and Technical - e.g., Consulting, Legal, Engineering)
   - "N" (Administrative and Support Services)
   - "O" (Public Administration)
   - "P" (Education)
   - "Q" (Human Health/Social Work)
   - "R" (Arts, Entertainment, Recreation)
   - "S" (Other Services)
   - "T" (Households)
   - "U" (Extraterritorial Organisations)
5. "turnover": An estimated annual revenue or turnover range (string, e.g. "€2.5M", "£500k", or "Unknown").
6. "website": The company's main website URL (string). Use empty string "" if not found or not provided.
7. "linkedin": The company's linkedin URL (string). Use empty string "" if not found or not provided.
8. "addr1": HQ street address if known (string).
9. "city": HQ City (string).
10. "country": 2-letter ISO country code of HQ, e.g., "IE" (Ireland), "GB" (United Kingdom), "US" (United States) (string).
11. "postcode": Postal code / ZIP code of HQ (string).
12. "county": State, county, or region of HQ (string).
13. "profile_text": A comprehensive, rich company profile description of 400-800 words. This text is crucial as it will be compared against tender specifications to compute Match Scores. Include the company's history, core expertise, key products/services, industry vertical solutions, technologies, typical clients/case studies, certifications (e.g. ISO 27001), qualifications, and competitive differentiators. Be descriptive and keyword-rich.
14. "cov_country": 2-letter ISO country code of their primary market coverage/target audience, e.g. "GB" or "IE" (string). Use empty string "" if not found.
15. "cov_region": Specific target region/state within their market coverage, e.g. "England" or "" (string).
16. "cov_city": Specific target city within their market coverage, e.g. "London" or "" (string).
17. "reg_number": Company Registration Number (CRN) if known, e.g. "12345678" or "" (string).
18. "vat_number": VAT registration number if known, e.g. "GB123456789" or "" (string).
19. "duns_number": DUNS number if known, e.g. "987654321" or "" (string).
20. "nuts": NUTS code of the company region if known, e.g. "UKI" or "" (string).
21. "contact_name": A primary contact person or executive name if known, or "" (string).
22. "email": A general contact or sales email address if known, e.g. "info@example.com" or "" (string).
23. "phone": General phone number of the company, or "" (string).
24. "fax": General fax number of the company, or "" (string).
25. "contact_name_2": A secondary contact person or secondary bidding contact name if known, or "" (string).
26. "email_2": A secondary contact email address if known, or "" (string).
27. "phone_2": A secondary phone number of the company/contact, or "" (string).

Ensure your response is valid JSON. Return ONLY the JSON object. Do not include markdown code block syntax (like ```json).
"""

        raw_res = call_chat_api(
            system=system_prompt,
            user=user_prompt,
            max_tokens=2500,
            temperature=0.2,
            response_format_json=True
        )
        
        cleaned = raw_res.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            cleaned = cleaned.rsplit("```", 1)[0].strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
                
        data = json.loads(cleaned)
        return jsonify({"ok": True, "data": data})
        
    except Exception as e:
        print("Failed to auto-generate company profile:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


_CH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# UK nation/country names that show up as the last segment of a Companies House
# registered-office address before the postcode — stripped out so what remains
# splits cleanly into (address line, city).
_CH_ADDRESS_NATION_WORDS = {
    "england", "scotland", "wales", "northern ireland", "united kingdom", "uk",
}


def _parse_ch_registered_address(address_text: str) -> dict:
    """Splits a Companies House "Registered office address" string (e.g.
    "220 C, Blythe Road, London, England, W14 0HH") into address_line1/city/postcode.
    Heuristic — CH addresses are free-text, comma-separated with no field markers."""
    parts = [p.strip() for p in address_text.split(",") if p.strip()]
    if not parts:
        return {"address_line1": "", "city": "", "postcode": ""}

    postcode = parts.pop() if len(parts) > 1 else ""
    if parts and parts[-1].lower() in _CH_ADDRESS_NATION_WORDS:
        parts.pop()
    city = parts.pop() if parts else ""
    address_line1 = ", ".join(parts)
    return {"address_line1": address_line1, "city": city, "postcode": postcode}


_LEGAL_SUFFIX_RE = re.compile(r"\b(ltd|limited|plc|llp|dac|cic|lp|inc|corp|co)\.?\s*$", re.IGNORECASE)

# Directory/social/registry domains that are never a company's own site — a search
# hit on one of these should never be used as the "website" guess.
_WEBSITE_DOMAIN_BLOCKLIST = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "bloomberg.com", "crunchbase.com", "opencorporates.com", "wikipedia.org",
    "indeed.com", "glassdoor.com", "zoominfo.com", "yell.com", "gov.uk",
    "company-information.service.gov.uk", "youtube.com", "endole.co.uk",
    "duedil.com", "creditsafe.com", "checkcompany.co.uk", "companieshouse.gov.uk",
}


def _strip_legal_suffix(name: str) -> str:
    """Companies House names always carry a legal suffix ("Softcat plc"), but
    company-lookup APIs/search index the trading name ("Softcat") — stripping it
    is what makes those lookups actually match."""
    s = re.sub(r"\s+", " ", name or "").strip()
    stripped = _LEGAL_SUFFIX_RE.sub("", s).strip().rstrip(",")
    return stripped or s


def _fetch_company_website(company_name: str) -> str:
    """Deterministic (non-AI) company-name -> official website lookup, used to
    pre-fill the website field right after a Companies House search selection —
    a head start before the slower AI enrichment step runs its own web search."""
    import requests
    from tender_app.ch_matcher import calculate_name_similarity

    query_name = _strip_legal_suffix(company_name)

    # 1) Clearbit's free company autocomplete — fast, structured, no key required.
    try:
        resp = requests.get(
            "https://autocomplete.clearbit.com/v1/companies/suggest",
            params={"query": query_name}, timeout=6,
        )
        if resp.ok:
            best_domain, best_score = "", 0.0
            for c in (resp.json() or []):
                score = calculate_name_similarity(query_name, c.get("name", ""))
                if score > best_score:
                    best_score, best_domain = score, c.get("domain", "")
            if best_domain and best_score >= 0.6:
                return f"https://{best_domain}"
    except Exception as e:
        print(f"[Clearbit lookup failed] {e}")

    # 2) Fallback: a targeted web search, filtered to plausible company domains —
    # never guess a directory/social-media/registry site, and require the domain's
    # own name to resemble the company name (no-fabrication: blank beats wrong).
    try:
        for r in search_duckduckgo(f"{query_name} official website", max_results=6):
            domain = r["link"].replace("https://", "").replace("http://", "").split("/")[0].replace("www.", "")
            if not domain or any(domain == b or domain.endswith("." + b) for b in _WEBSITE_DOMAIN_BLOCKLIST):
                continue
            domain_root = domain.split(".")[0]
            if calculate_name_similarity(query_name, domain_root) >= 0.5:
                return f"https://{domain}"
    except Exception as e:
        print(f"[Website search fallback failed] {e}")

    return ""


def _fetch_ch_registered_address(company_number: str) -> dict:
    """Fetches the real registered office address from a company's own Companies
    House page — the search-results listing never includes it (it only shows the
    registration/incorporation status), so this needs a second request."""
    import requests
    from bs4 import BeautifulSoup

    url = f"https://find-and-update.company-information.service.gov.uk/company/{company_number}"
    resp = requests.get(url, headers=_CH_HEADERS, timeout=8)
    if not resp.ok:
        return {"address_line1": "", "city": "", "postcode": ""}

    soup = BeautifulSoup(resp.text, "html.parser")
    for dt in soup.select("dt"):
        if dt.get_text(strip=True).lower() == "registered office address":
            dd = dt.find_next_sibling("dd")
            if dd:
                return _parse_ch_registered_address(dd.get_text(" ", strip=True))
    return {"address_line1": "", "city": "", "postcode": ""}


@profiles_bp.get("/api/company-profiles/ch-search")
def api_search_companies_house():
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify({"ok": True, "companies": []})

    import requests
    import urllib.parse
    from bs4 import BeautifulSoup

    url = f"https://find-and-update.company-information.service.gov.uk/search/companies?q={urllib.parse.quote(q)}"

    try:
        resp = requests.get(url, headers=_CH_HEADERS, timeout=8)
        if not resp.ok:
            return jsonify({"ok": True, "companies": []})

        soup = BeautifulSoup(resp.text, 'html.parser')
        items = soup.select('li.type-company')
        results = []

        for item in items[:6]:
            title_a = item.select_one('a')
            if not title_a:
                continue

            comp_title = title_a.text.strip()
            link = title_a.get('href', '')
            comp_num = link.replace('/company/', '').strip()

            # The search listing only ever shows registration status (e.g. "Incorporated
            # on 7 January 2016"), never the address — label it honestly as status, not
            # address. The real address is fetched separately once a company is picked
            # (see /api/company-profiles/ch-address), since that's a per-company page.
            snippet_p = item.select_one('p')
            status_text = snippet_p.text.strip() if snippet_p else ""

            results.append({
                "title": comp_title,
                "company_number": comp_num,
                "status_text": status_text,
                "link": f"https://find-and-update.company-information.service.gov.uk{link}"
            })

        return jsonify({"ok": True, "companies": results})
    except Exception as e:
        print(f"[Companies House Search Error]: {e}")
        return jsonify({"ok": True, "companies": []})


@profiles_bp.get("/api/company-profiles/ch-address")
def api_get_ch_registered_address():
    """Fetches the real registered office address for one company, split into
    address_line1/city/postcode, plus a best-effort official website — called
    after a Companies House search result is selected (the search listing itself
    never includes either of those)."""
    company_number = request.args.get("company_number", "").strip()
    company_name = request.args.get("company_name", "").strip()
    if not company_number:
        return jsonify({"ok": False, "error": "company_number is required"}), 400
    try:
        addr = _fetch_ch_registered_address(company_number)
    except Exception as e:
        print(f"[Companies House Address Fetch Error]: {e}")
        addr = {"address_line1": "", "city": "", "postcode": ""}
    website = ""
    if company_name:
        try:
            website = _fetch_company_website(company_name)
        except Exception as e:
            print(f"[Company Website Fetch Error]: {e}")
    return jsonify({"ok": True, "website": website, **addr})


# CRO's own open-data CKAN API (opendata.cro.ie) -- free, no auth, updated daily, confirmed
# live 2026-10-01. Unlike the Companies House scrape above, a single search call already
# returns the full registered address, so no second per-company request is needed. The CRO's
# live company-filings portal (core.cro.ie) sits behind a Cloudflare JS challenge and its
# official REST API (services.cro.ie) requires paid credentials, so this is the only free,
# programmatic route in -- it has no confirmed public per-company detail-page URL to link to,
# unlike Companies House, so results carry the full address instead of a "view record" link.
_CRO_DATASTORE_URL = "https://opendata.cro.ie/api/3/action/datastore_search"
_CRO_COMPANIES_RESOURCE_ID = "3fef41bc-b8f4-4b10-8434-ce51c29b1bba"


def _cro_address_parts(rec: dict) -> dict:
    lines = [str(rec.get(f"company_address_{i}") or "").strip() for i in range(1, 5)]
    lines = [ln for ln in lines if ln]
    # The profile form has a single street-address field, not one per CRO address line --
    # city/county is its own field, so keep that off address_line1 rather than bundling in
    # everything. address_3/4 overlap in practice (e.g. "SWORDS CO. DUBLIN" / "SWORDS,DUBLIN"),
    # so the last distinct line is the best approximation of "city" available here.
    city = lines[-1] if len(lines) > 1 else ""
    street_lines = lines[:-1] if len(lines) > 1 else lines
    return {
        "address_line1": ", ".join(street_lines),
        "city": city,
        "postcode": str(rec.get("eircode") or "").strip(),
    }


@profiles_bp.get("/api/company-profiles/cro-search")
def api_search_cro():
    """Irish company search via the CRO's open-data API -- see _CRO_DATASTORE_URL comment."""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify({"ok": True, "companies": []})

    import requests

    try:
        resp = requests.get(_CRO_DATASTORE_URL, params={
            "resource_id": _CRO_COMPANIES_RESOURCE_ID,
            "q": q,
            "limit": 8,
        }, timeout=8)
        if not resp.ok:
            return jsonify({"ok": True, "companies": []})

        data = resp.json()
        if not data.get("success"):
            return jsonify({"ok": True, "companies": []})

        results = []
        for rec in data.get("result", {}).get("records", []):
            comp_num = str(rec.get("company_num") or "").strip()
            status = str(rec.get("company_status") or "").strip()
            comp_type = str(rec.get("company_type") or "").strip()
            results.append({
                "title": str(rec.get("company_name") or "").strip(),
                "company_number": comp_num,
                "status_text": " · ".join(p for p in (status, comp_type) if p),
                **_cro_address_parts(rec),
            })
        return jsonify({"ok": True, "companies": results})
    except Exception as e:
        print(f"[CRO Search Error]: {e}")
        return jsonify({"ok": True, "companies": []})


@profiles_bp.post("/api/company-profiles/enrich-duns")
def api_enrich_company_duns():
    body = request.get_json(silent=True) or {}
    company_name = body.get("company_name", "").strip()
    website_url = body.get("website_url", "").strip()
    
    if not company_name:
        return jsonify({"ok": False, "error": "Company Name / Search Keyword is required"}), 400
        
    try:
        from server import call_chat_api
        import json
        
        # 1. Do a targeted search for the DUNS number
        search_results = []
        if website_url:
            domain = website_url.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
            search_results.extend(search_duckduckgo(f'site:{domain} duns OR "duns number" OR "d-u-n-s"', max_results=3))
            search_results.extend(search_duckduckgo(f'"{company_name}" duns OR "duns number" OR "d-u-n-s" OR "d&b"', max_results=3))
        else:
            search_results.extend(search_duckduckgo(f'"{company_name}" duns OR "duns number" OR "d-u-n-s" OR "d&b"', max_results=5))
        
        search_text = ""
        if search_results:
            search_text += "Here are some search results related to the company's DUNS number:\n"
            for idx, r in enumerate(search_results, 1):
                search_text += f"Title: {r['title']}\nURL: {r['link']}\nSnippet: {r['snippet']}\n\n"
        
        system_prompt = (
            "You are an expert AI business researcher. Your task is to find the DUNS (Data Universal Numbering System) number "
            "for a company based on search snippets. A DUNS number is a unique nine-digit numeric identifier (e.g. 123456789 or 12-345-6789). "
            "Examine the search snippets carefully. If you find the nine-digit DUNS number for this specific company, extract it. "
            "If you cannot find it, return null. Do not guess or make up a number."
        )
        
        user_prompt = f"""Company Name: {company_name}
Website: {website_url or 'Not provided'}

{search_text}

Output a JSON object with a single key "duns_number" (string of 9 digits, e.g. "123456789" without dashes, or null if not found). 
Ensure your response is valid JSON. Return ONLY the JSON object. Do not include markdown code block syntax."""

        raw_res = call_chat_api(
            system=system_prompt,
            user=user_prompt,
            max_tokens=150,
            temperature=0.1,
            response_format_json=True
        )
        
        cleaned = raw_res.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            cleaned = cleaned.rsplit("```", 1)[0].strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
                
        data = json.loads(cleaned)
        return jsonify({"ok": True, "data": data})
        
    except Exception as e:
        print("Failed to enrich DUNS number:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.post("/api/company-profiles/enrich-experience")
def api_enrich_company_experience():
    body = request.get_json(silent=True) or {}
    company_name = body.get("company_name", "").strip()
    website_url = body.get("website_url", "").strip()
    existing_profile_text = body.get("existing_profile_text", "").strip()
    instructions = body.get("instructions", "").strip()
    
    if not company_name:
        return jsonify({"ok": False, "error": "Company Name / Search Keyword is required"}), 400
        
    try:
        from server import call_chat_api
        import json
        
        # 1. Gather web info only if website is provided and we want to augment details
        search_text = ""
        if website_url or company_name:
            q_search = f"{company_name} company profile overview key expertise"
            if website_url:
                domain = website_url.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
                q_search += f" site:{domain} OR {domain}"
            search_results = search_duckduckgo(q_search, max_results=4)
            if search_results:
                search_text += "Here are some search results with background information:\n"
                for idx, r in enumerate(search_results, 1):
                    search_text += f"Snippet: {r['snippet']}\n\n"
                    
        system_prompt = (
            "You are an expert AI copywriter and bidding consultant. Your task is to rewrite and enrich a company's profile description / context. "
            "This description is used for matching public tenders and writing bids. Make it highly professional, comprehensive (400-800 words), and rich in keywords. "
            "Ensure you describe the company's domain, history, capabilities, key services, certifications (if any), and competitive edge. "
            "Incorporate any specific instructions the user provided."
        )
        
        user_prompt = f"""Company Name: {company_name}
Website: {website_url or 'Not provided'}

Existing Profile Text:
{existing_profile_text or 'None provided'}

User's Enrichment Instructions:
{instructions or 'Expand and refine the details based on search results to make it highly competitive.'}

{search_text}

Output a JSON object with a single key "profile_text" (string) containing the enriched company details and experience description.
Ensure your response is valid JSON. Return ONLY the JSON object. Do not include markdown code block syntax."""

        raw_res = call_chat_api(
            system=system_prompt,
            user=user_prompt,
            max_tokens=1500,
            temperature=0.3,
            response_format_json=True
        )
        
        cleaned = raw_res.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            cleaned = cleaned.rsplit("```", 1)[0].strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
                
        data = json.loads(cleaned)
        return jsonify({"ok": True, "data": data})
        
    except Exception as e:
        print("Failed to enrich company details/experience:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@profiles_bp.post("/api/company-profiles/enrich-contacts")
def api_enrich_company_contacts():
    body = request.get_json(silent=True) or {}
    company_name = body.get("company_name", "").strip()
    website_url = body.get("website_url", "").strip()
    reg_number = body.get("reg_number", "").strip()
    
    if not company_name:
        return jsonify({"ok": False, "error": "Company Name / Search Keyword is required"}), 400
        
    try:
        from server import call_chat_api
        import json
        
        # Try to fetch official registry data from UK Companies House if possible
        registry_text = ""
        target_reg = reg_number
        
        # If no reg_number but we have company_name, search for registry page first
        if not target_reg and company_name:
            try:
                ch_results = search_duckduckgo(f'site:company-information.service.gov.uk "{company_name}"', max_results=3)
                for r in ch_results:
                    link = r.get("link", "")
                    import re
                    m = re.search(r'/company/([a-zA-Z0-9]{8})', link)
                    if m:
                        target_reg = m.group(1)
                        break
            except Exception as e:
                print("Failed to locate registry number via search:", e)

        if target_reg:
            import re
            # Clean and format registry number (must be 8 chars for UK)
            clean_reg = re.sub(r'[^a-zA-Z0-9]', '', target_reg)
            if clean_reg.isdigit() and len(clean_reg) < 8:
                clean_reg = clean_reg.zfill(8)
                
            if len(clean_reg) == 8:
                try:
                    import urllib.request
                    from bs4 import BeautifulSoup
                    url = f"https://find-and-update.company-information.service.gov.uk/company/{clean_reg}/officers"
                    print(f"[Registry Crawler] Fetching officers from: {url}")
                    req = urllib.request.Request(
                        url,
                        headers={
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                            'Accept-Language': 'en-US,en;q=0.5'
                        }
                    )
                    with urllib.request.urlopen(req, timeout=8) as response:
                        html_content = response.read()
                        soup = BeautifulSoup(html_content, 'html.parser')
                        raw_text = soup.get_text(" ", strip=True)
                        registry_text = f"\n--- High-Fidelity Companies House Registry Officers Data (Company Registration Number: {clean_reg}) ---\n{raw_text[:12000]}\n"
                except Exception as e:
                    print(f"[Registry Crawler] Failed to fetch officers from Companies House: {e}")
        
        # 1. Search for contact details and team/executives page
        search_results = []
        if website_url:
            domain = website_url.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
            search_results.extend(search_duckduckgo(f'site:{domain} contact OR team OR people OR management OR officers', max_results=3))
            search_results.extend(search_duckduckgo(f'"{company_name}" contact details email phone', max_results=3))
        else:
            search_results.extend(search_duckduckgo(f'"{company_name}" contact details email phone', max_results=4))
            search_results.extend(search_duckduckgo(f'"{company_name}" team OR management OR founders OR directors', max_results=3))
        
        search_text = ""
        if search_results:
            search_text += "Here are some search results related to the company contact persons and details:\n"
            for idx, r in enumerate(search_results, 1):
                search_text += f"Title: {r['title']}\nSnippet: {r['snippet']}\n\n"
        
        system_prompt = (
            "You are an expert AI business researcher. Your task is to find contact details for two key contact persons (First/Primary contact and Second/Secondary contact) "
            "for a company based on search snippets and official Companies House registry data. Companies House lists directors, secretaries, and officers. "
            "Prefer active officers/directors/secretaries for the key contacts. Locate names, emails, and phone numbers if available. "
            "Output a JSON object matching the schema exactly. If a detail cannot be found, output empty string."
        )
        
        user_prompt = f"""Company Name: {company_name}
Website: {website_url or 'Not provided'}

{registry_text}
{search_text}

Output a JSON object with the following fields:
1. "contact_name": Primary contact person name (string).
2. "email": Primary contact email (string).
3. "phone": Primary contact phone (string).
4. "fax": Primary general fax number (string).
5. "contact_name_2": Secondary contact person name (string).
6. "email_2": Secondary contact email (string).
7. "phone_2": Secondary contact phone (string).

Ensure your response is valid JSON. Return ONLY the JSON object. Do not include markdown code block syntax."""

        raw_res = call_chat_api(
            system=system_prompt,
            user=user_prompt,
            max_tokens=300,
            temperature=0.2,
            response_format_json=True
        )
        
        cleaned = raw_res.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            cleaned = cleaned.rsplit("```", 1)[0].strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
                
        data = json.loads(cleaned)
        return jsonify({"ok": True, "data": data})
        
    except Exception as e:
        print("Failed to enrich contact details:", e)
        return jsonify({"ok": False, "error": str(e)}), 500





