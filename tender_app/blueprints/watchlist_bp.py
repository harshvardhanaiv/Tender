"""Global watchlist of buyers and suppliers (tender_app/entity_watchlist.py).

    /api/watchlist                      (GET)           the user's watched buyers and suppliers
    /api/watchlist                      (POST)          add one (idempotent: adding again keeps the first source)
    /api/watchlist/<kind>/<key>         (DELETE, PATCH) remove it / change its note
Every row belongs to the user and is always looked up by username.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from tender_app import entity_watchlist as ew

watchlist_bp = Blueprint("watchlist", __name__)
_get_db = None


def init_watchlist_blueprint(get_db_connection):
    global _get_db
    _get_db = get_db_connection
    return watchlist_bp


def _username() -> str:
    return session.get("username", "")


@watchlist_bp.get("/api/watchlist")
def list_watchlist():
    if not _username():
        return jsonify({"error": "Unauthorized"}), 401
    conn = _get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT kind, entity_key, name, added_from, note, created_at FROM entity_watchlist "
                    "WHERE username=%s ORDER BY created_at DESC LIMIT %s", (_username(), ew.MAX_ENTRIES))
        return jsonify({"items": [ew.public(r) for r in cur.fetchall()]})
    finally:
        conn.close()


@watchlist_bp.post("/api/watchlist")
def add_watchlist():
    if not _username():
        return jsonify({"error": "Unauthorized"}), 401
    entry = ew.clean_entry(request.get_json(silent=True))
    if not entry:
        return jsonify({"error": "kind (buyer or supplier), entity_key and name are required"}), 400
    conn = _get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM entity_watchlist WHERE username=%s", (_username(),))
        if cur.fetchone()[0] >= ew.MAX_ENTRIES:
            return jsonify({"error": f"The watchlist is full ({ew.MAX_ENTRIES}). Remove some first."}), 400
        cur.execute(
            """INSERT INTO entity_watchlist (username, kind, entity_key, name, added_from, note)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (username, kind, entity_key) DO UPDATE SET name=EXCLUDED.name
               RETURNING kind, entity_key, name, added_from, note, created_at""",
            (_username(), entry["kind"], entry["entity_key"], entry["name"], entry["added_from"], entry["note"]))
        row = cur.fetchone()
        conn.commit()
        return jsonify({"ok": True, "item": ew.public(row)})
    finally:
        conn.close()


@watchlist_bp.route("/api/watchlist/<kind>/<path:entity_key>", methods=["DELETE", "PATCH"])
def change_watchlist(kind, entity_key):
    if not _username():
        return jsonify({"error": "Unauthorized"}), 401
    if kind not in ew.KINDS:
        return jsonify({"error": "invalid kind"}), 400
    conn = _get_db()
    try:
        cur = conn.cursor()
        if request.method == "DELETE":
            cur.execute("DELETE FROM entity_watchlist WHERE username=%s AND kind=%s AND entity_key=%s", (_username(), kind, entity_key))
        else:
            note = str((request.get_json(silent=True) or {}).get("note") or "").strip()[:1000]
            cur.execute("UPDATE entity_watchlist SET note=%s WHERE username=%s AND kind=%s AND entity_key=%s", (note, _username(), kind, entity_key))
        changed = cur.rowcount
        conn.commit()
        return jsonify({"ok": True, "changed": changed})
    finally:
        conn.close()
