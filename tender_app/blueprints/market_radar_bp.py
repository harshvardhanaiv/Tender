"""Market Radar and buyer-workspace API for TenderFlow.

Market Radar answers a public sector buyer's first question before it writes a specification: who
else has bought this, from whom, how, and for roughly what. The analysis itself lives in
tender_app/market_radar.py (read its docstring for what the numbers mean and do not mean); this
module is the HTTP layer plus the small per-user state the buyer workspace needs:

    /api/market-radar/options                  presets, authority types, windows
    /api/market-radar/categories?q=            picker suggestions
    /api/market-radar/analysis                 summary, peers (page 1), supplier comparison, cost benchmark
    /api/market-radar/peers                    further pages of peers (search, sort)
    /api/market-radar/peers/awards?buyer=      one peer's awards in the category
    /api/buyer-workspace/me                    the user's organisation and watchlist
    /api/buyer-workspace/organisations?q=      organisation search
    /api/buyer-workspace/organisation  (PUT)   choose or clear "my organisation"
    /api/buyer-workspace/watchlist     (PUT)   categories the user follows
    /api/buyer-workspace/dashboard             renewals, engagements, watched categories, peer activity

Everything is read-only over award data that is already public; the only writes are the user's two
preferences, stored in user_prefs. Mutating requests are CSRF-protected by the app-wide hook.
"""
from __future__ import annotations

import json
import traceback
from typing import Any

from flask import Blueprint, jsonify, request, session

from tender_app import market_radar as mr
from tender_app.config import RATE_LIMIT_SEARCH
from tender_app.security import rate_limit

market_radar_bp = Blueprint("market_radar", __name__)

_get_db = None

PREF_ORG = "buyer_org_name"
PREF_WATCHLIST = "buyer_watchlist"
MAX_WATCHLIST = 12
DEFAULT_PER_PAGE = 25
MAX_PER_PAGE = 100
RENEWAL_DAYS = 183  # "renewing in the next six months"

PEER_SORTS = {
    "recent": lambda p: (p["latest_signed"] or "", p["awards"]),
    "value": lambda p: (p["total_value"] or 0, p["awards"]),
    "awards": lambda p: (p["awards"], p["latest_signed"] or ""),
    "name": lambda p: p["buyer"].lower(),
}


def init_market_radar_blueprint(get_db_connection):
    global _get_db
    _get_db = get_db_connection
    return market_radar_bp


def _connect():
    if _get_db is None:
        raise RuntimeError("Market Radar blueprint not initialised with a database connection")
    return _get_db()


def _username() -> str:
    return (session.get("username") or "").strip()


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


# ── per-user preferences (user_prefs) ────────────────────────────────────────────────────────

def _get_pref(conn, username: str, key: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT pref_value FROM user_prefs WHERE username = %s AND pref_key = %s", (username, key))
        row = cur.fetchone()
    return row[0] if row else None


def _set_pref(conn, username: str, key: str, value: str | None) -> None:
    with conn.cursor() as cur:
        if value is None:
            cur.execute("DELETE FROM user_prefs WHERE username = %s AND pref_key = %s", (username, key))
        else:
            cur.execute(
                """INSERT INTO user_prefs (username, pref_key, pref_value, updated_at)
                   VALUES (%s, %s, %s, NOW())
                   ON CONFLICT (username, pref_key)
                   DO UPDATE SET pref_value = EXCLUDED.pref_value, updated_at = NOW()""",
                (username, key, value),
            )
    conn.commit()


def _my_org(conn, username: str) -> dict[str, Any] | None:
    name = _get_pref(conn, username, PREF_ORG)
    if not name:
        return None
    with conn.cursor() as cur:
        return mr.lookup_organisation(cur, name) or mr.organisation_profile(name)


def _watchlist(conn, username: str) -> list[mr.Category]:
    raw = _get_pref(conn, username, PREF_WATCHLIST)
    try:
        items = json.loads(raw) if raw else []
    except ValueError:
        items = []
    cats: list[mr.Category] = []
    labels: dict[str, str] | None = None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            if item.get("cpv") and labels is None:
                labels, _ = mr.load_cpv_labels(_connect)
            cats.append(mr.resolve_category(item.get("preset"), item.get("cpv"), item.get("q"), labels))
        except mr.CategoryError:
            continue
    return cats


# ── request parsing ──────────────────────────────────────────────────────────────────────────

def _category_from(args) -> mr.Category:
    labels = None
    if (args.get("cpv") or "").strip():
        labels, _ = mr.load_cpv_labels(_connect)
    return mr.resolve_category(args.get("category") or args.get("preset"), args.get("cpv"), args.get("q"), labels)


def _filters_from(args) -> tuple[str, str, str]:
    authority = (args.get("authority") or "all").strip()
    window = (args.get("window") or mr.DEFAULT_WINDOW).strip()
    scope = (args.get("scope") or "all").strip()
    if authority not in mr.AUTHORITY_LABELS or authority == "local-other":
        raise mr.CategoryError(f"unknown authority type: {authority}")
    if window not in mr.WINDOWS:
        raise mr.CategoryError(f"unknown window: {window}")
    if scope not in mr.SCOPES:
        raise mr.CategoryError(f"unknown scope: {scope}")
    return authority, window, scope


def _int_arg(name: str, default: int, lo: int, hi: int) -> int:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise mr.CategoryError(f"{name} must be a whole number")
    if not lo <= value <= hi:
        raise mr.CategoryError(f"{name} must be between {lo} and {hi}")
    return value


def _public_peer(peer: dict[str, Any], org_key: str | None) -> dict[str, Any]:
    out = {k: v for k, v in peer.items() if not k.startswith("_")}
    out["is_me"] = bool(org_key) and peer["key"] == org_key
    return out


def _page_of_peers(analysis: dict[str, Any], org_key: str | None) -> dict[str, Any]:
    peers = list(analysis["peers"])
    search = (request.args.get("search") or "").strip().lower()
    if search:
        peers = [p for p in peers if search in p["buyer"].lower()]
    sort = request.args.get("sort") or "recent"
    if sort not in PEER_SORTS:
        raise mr.CategoryError(f"sort must be one of: {', '.join(PEER_SORTS)}")
    peers.sort(key=PEER_SORTS[sort], reverse=(sort != "name"))
    per_page = _int_arg("per_page", DEFAULT_PER_PAGE, 1, MAX_PER_PAGE)
    pages = max(1, -(-len(peers) // per_page))
    page = _int_arg("page", 1, 1, pages)
    window = peers[(page - 1) * per_page: page * per_page]
    return {
        "total": len(peers),
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "sort": sort,
        "rows": [_public_peer(p, org_key) for p in window],
    }


def _org_key_for(username: str) -> str | None:
    conn = _connect()
    try:
        org = _my_org(conn, username)
    finally:
        conn.close()
    return org["key"] if org else None


# ── Market Radar ─────────────────────────────────────────────────────────────────────────────

@market_radar_bp.get("/api/market-radar/options")
def radar_options():
    return jsonify({
        "presets": [{"preset": p["id"], "label": p["label"], "hint": p["hint"]} for p in mr.CATEGORY_PRESETS],
        "authority_types": [{"id": i, "label": label} for i, label in mr.AUTHORITY_TYPES],
        "windows": [{"id": w, "label": mr.WINDOW_LABELS[w]} for w in mr.WINDOWS],
        "scopes": [
            {"id": "all", "label": "All awards"},
            {"id": "direct", "label": "Direct contracts only (no frameworks)"},
        ],
        "default_window": mr.DEFAULT_WINDOW,
    })


@market_radar_bp.get("/api/market-radar/categories")
@rate_limit(RATE_LIMIT_SEARCH)
def radar_categories():
    return jsonify(mr.suggest_categories(_connect, request.args.get("q", "")))


@market_radar_bp.get("/api/market-radar/analysis")
@rate_limit(RATE_LIMIT_SEARCH)
def radar_analysis():
    try:
        cat = _category_from(request.args)
        authority, window, scope = _filters_from(request.args)
        analysis = mr.get_analysis(_connect, cat, authority, window, scope)
        org_key = _org_key_for(_username())
        peers = _page_of_peers(analysis, org_key)
    except mr.CategoryError as exc:
        return _error(str(exc))
    suppliers = analysis["suppliers"]
    return jsonify({
        "category": cat.to_public(),
        "filters": {
            "authority": authority,
            "authority_label": mr.AUTHORITY_LABELS[authority],
            "window": window,
            "window_label": mr.WINDOW_LABELS[window],
            "scope": scope,
        },
        "summary": analysis["summary"],
        "peers": peers,
        "suppliers": {"total": len(suppliers), "rows": suppliers[:25]},
        "cost": analysis["cost"],
        "truncated": analysis["truncated"],
        "computed_at": analysis["computed_at"],
        "notes": {
            "values": "Contract values are the values published in award notices, not invoiced spend. "
                      "Framework and call-off values are shared ceilings, so they are never counted as spend.",
            "size": "Authorities are grouped by type (London borough, county, district ...). Population is not "
                    "available in the award data.",
            "cost": "Award notices publish one total value. Annual value divides it by the stated term where "
                    "both start and end dates are published; support, maintenance and implementation costs "
                    "are not separated.",
        },
    })


@market_radar_bp.get("/api/market-radar/peers")
@rate_limit(RATE_LIMIT_SEARCH)
def radar_peers():
    try:
        cat = _category_from(request.args)
        authority, window, scope = _filters_from(request.args)
        analysis = mr.get_analysis(_connect, cat, authority, window, scope)
        page = _page_of_peers(analysis, _org_key_for(_username()))
    except mr.CategoryError as exc:
        return _error(str(exc))
    return jsonify(page)


@market_radar_bp.get("/api/market-radar/peers/awards")
@rate_limit(RATE_LIMIT_SEARCH)
def radar_peer_awards():
    buyer = (request.args.get("buyer") or "").strip()
    if not buyer:
        return _error("buyer is required")
    try:
        cat = _category_from(request.args)
        authority, window, scope = _filters_from(request.args)
        analysis = mr.get_analysis(_connect, cat, authority, window, scope)
    except mr.CategoryError as exc:
        return _error(str(exc))
    peer = next((p for p in analysis["peers"] if p["key"] == buyer), None)
    if not peer:
        return _error("buyer not found in this view", 404)
    return jsonify({
        "buyer": peer["buyer"],
        "total_awards": peer["awards"],
        "shown": len(peer["_awards"]),
        "awards": peer["_awards"],
    })


# ── buyer workspace: organisation, watchlist, dashboard ──────────────────────────────────────

@market_radar_bp.get("/api/buyer-workspace/me")
def workspace_me():
    username = _username()
    conn = _connect()
    try:
        org = _my_org(conn, username)
        watch = _watchlist(conn, username)
    finally:
        conn.close()
    return jsonify({
        "username": username,
        "organisation": org,
        "watchlist": [c.to_public() for c in watch],
    })


@market_radar_bp.get("/api/buyer-workspace/organisations")
@rate_limit(RATE_LIMIT_SEARCH)
def workspace_organisations():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            results = mr.search_organisations(cur, request.args.get("q", ""))
    finally:
        conn.close()
    return jsonify({"results": results})


@market_radar_bp.put("/api/buyer-workspace/organisation")
def workspace_set_organisation():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    username = _username()
    conn = _connect()
    try:
        if not name:
            _set_pref(conn, username, PREF_ORG, None)
            return jsonify({"organisation": None})
        if not isinstance(name, str) or len(name) > 300:
            return _error("name must be the name of a buyer from the search results")
        with conn.cursor() as cur:
            org = mr.lookup_organisation(cur, name.strip())
        if not org:
            return _error("That organisation was not found. Choose one from the search results.", 404)
        _set_pref(conn, username, PREF_ORG, org["name"])
        return jsonify({"organisation": org})
    finally:
        conn.close()


@market_radar_bp.put("/api/buyer-workspace/watchlist")
def workspace_set_watchlist():
    body = request.get_json(silent=True) or {}
    items = body.get("categories")
    if not isinstance(items, list):
        return _error("categories must be a list")
    cats: list[mr.Category] = []
    seen: set[str] = set()
    labels = None
    try:
        for item in items:
            if not isinstance(item, dict):
                return _error("each category must be an object")
            if item.get("cpv") and labels is None:
                labels, _ = mr.load_cpv_labels(_connect)
            cat = mr.resolve_category(item.get("preset"), item.get("cpv"), item.get("q"), labels)
            if cat.key not in seen:
                seen.add(cat.key)
                cats.append(cat)
    except mr.CategoryError as exc:
        return _error(str(exc))
    if len(cats) > MAX_WATCHLIST:
        return _error(f"You can follow at most {MAX_WATCHLIST} categories")
    stored = [
        {"preset": c.preset_id} if c.preset_id else {k: v for k, v in c.to_public().items() if k in ("cpv", "q")}
        for c in cats
    ]
    conn = _connect()
    try:
        _set_pref(conn, _username(), PREF_WATCHLIST, json.dumps(stored))
    finally:
        conn.close()
    return jsonify({"watchlist": [c.to_public() for c in cats]})


def _engagement_summary(conn, username: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT status, COUNT(*) FROM market_engagements WHERE username = %s GROUP BY status", (username,))
        by_status = {status: n for status, n in cur.fetchall()}
        cur.execute(
            """SELECT id, title, status, updated_at FROM market_engagements
               WHERE username = %s ORDER BY updated_at DESC LIMIT 3""",
            (username,),
        )
        recent = [
            {"id": i, "title": t, "status": s, "updated_at": u.isoformat() if u else None}
            for i, t, s, u in cur.fetchall()
        ]
    return {
        "active": by_status.get("draft", 0) + by_status.get("published", 0),
        "total": sum(by_status.values()),
        "by_status": by_status,
        "recent": recent,
    }


@market_radar_bp.get("/api/buyer-workspace/dashboard")
@rate_limit(RATE_LIMIT_SEARCH)
def workspace_dashboard():
    username = _username()
    conn = _connect()
    try:
        org = _my_org(conn, username)
        watch = _watchlist(conn, username)
        engagements = _engagement_summary(conn, username)
        renewals: dict[str, Any] | None = None
        activity: list[dict[str, Any]] = []
        unavailable: list[str] = []
        if org:
            try:
                with conn.cursor() as cur:
                    items = mr.fetch_org_renewals(cur, org, days=RENEWAL_DAYS)
                renewals = {"count": len(items), "days": RENEWAL_DAYS, "items": items[:8]}
            except Exception:
                traceback.print_exc()
                conn.rollback()
                unavailable.append("renewals")
            if watch:
                try:
                    with conn.cursor() as cur:
                        activity = mr.fetch_peer_activity(cur, watch, org)
                except Exception:
                    traceback.print_exc()
                    conn.rollback()
                    unavailable.append("peer_activity")
    finally:
        conn.close()
    return jsonify({
        "organisation": org,
        "renewals": renewals,
        "engagements": engagements,
        "watchlist": {"count": len(watch), "categories": [c.to_public() for c in watch]},
        "peer_activity": activity,
        "unavailable": unavailable,
    })
