"""Funding: open government grants from Find a Grant, with a plain word match to the user's company profile."""
from __future__ import annotations

import json

from flask import Blueprint, jsonify, request, session

from tender_app import funding
from tender_app import growth_studio as gs

funding_bp = Blueprint("funding", __name__)

_get_db = None


def init_funding_blueprint(get_db_connection):
    global _get_db
    _get_db = get_db_connection
    return funding_bp


def _profile_keywords(cursor, username: str, raw_id) -> frozenset[str]:
    """Words from the user's own company profile (the one asked for, else their default); empty when none."""
    try:
        profile_id = int(raw_id) if raw_id not in (None, "", "0") else None
    except ValueError:
        profile_id = None
    if not username:
        return frozenset()
    if profile_id:
        cursor.execute("SELECT profile_text, meta_json FROM company_profiles WHERE id = %s AND username = %s", (profile_id, username))
    else:
        cursor.execute(
            "SELECT profile_text, meta_json FROM company_profiles WHERE username = %s ORDER BY COALESCE(is_default, FALSE) DESC, id ASC LIMIT 1",
            (username,),
        )
    row = cursor.fetchone()
    if not row:
        return frozenset()
    try:
        meta = json.loads(row[1] or "{}")
    except ValueError:
        meta = {}
    return gs.profile_keywords(row[0] or "", meta if isinstance(meta, dict) else {})


def _number(raw):
    try:
        return float(raw) if raw not in (None, "") else None
    except ValueError:
        return None


@funding_bp.route("/api/funding", methods=["GET"])
def list_funding():
    args = request.args
    try:
        page = int(args.get("page") or 1)
    except ValueError:
        page = 1
    conn = _get_db()
    try:
        cur = conn.cursor()
        keywords = _profile_keywords(cur, (session.get("username") or "").strip(), args.get("profile_id"))
        result = funding.search(
            cur,
            q=(args.get("q") or "")[:100],
            applicant=args.get("applicant") or "",
            location=args.get("location") or "",
            min_award=_number(args.get("min_award")),
            closing_soon=args.get("closing_soon") in ("1", "true"),
            sort=args.get("sort") or "relevance",
            page=page,
            keywords=keywords,
        )
        cur.close()
        return jsonify(result)
    finally:
        conn.close()
