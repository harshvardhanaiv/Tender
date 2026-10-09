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
    /api/market-radar/peers/profile?buyer=     Insights drawer: the buyer's Buyer Intelligence profile + its awards here
    /api/market-radar/peers/insight (POST)     Insights drawer: DeepSeek summary of the buyer's pattern (may be unavailable)
    /api/market-radar/company-profile?supplier_id=  Companies House filings/owners + Google rating
    /api/market-radar/similar-suppliers?supplier=  suppliers with awards under the same CPV classes (plain data)
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

from flask import Blueprint, abort, jsonify, request, session

from tender_app import buyer_insights as bi
from tender_app import market_radar as mr
from tender_app.config import (
    BUYER_INSIGHT_MIN_AWARDS,
    BUYER_INSIGHT_TIMEOUT_SECONDS,
    ENABLE_BUYER_INSIGHTS,
    RATE_LIMIT_AI,
    RATE_LIMIT_SEARCH,
)
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


# ── Insights drawer and similar suppliers ────────────────────────────────────────────────────

# A buyer profile costs several queries and the narrative costs a model call, so both are kept for a while.
_PROFILE_CACHE = mr.AnalysisCache(ttl=15 * 60, max_entries=200)
_INSIGHT_CACHE = mr.AnalysisCache(ttl=6 * 3600, max_entries=500)


def _insights_on() -> None:
    if not ENABLE_BUYER_INSIGHTS:
        abort(404)


def _peer_in_view(args) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """(category, analysis, peer) for ?buyer=<peer key> under the same filters as the table."""
    buyer = (args.get("buyer") or "").strip()
    if not buyer:
        raise mr.CategoryError("buyer is required")
    cat = _category_from(args)
    authority, window, scope = _filters_from(args)
    analysis = mr.get_analysis(_connect, cat, authority, window, scope)
    peer = next((p for p in analysis["peers"] if p["key"] == buyer), None)
    if not peer:
        raise LookupError("buyer not found in this view")
    return cat, analysis, peer


def _buyer_profile(name: str) -> dict[str, Any] | None:
    """The Buyer Intelligence profile (GET /api/buyers/<name>), reused as it is rather than re-queried here.

    None when it cannot be built or the buyer has no awards on record; the drawer then shows the Market
    Radar figures alone."""
    from tender_app.blueprints.buyers_bp import get_buyer_detail

    def compute() -> dict[str, Any]:
        result = get_buyer_detail(name)
        response = result[0] if isinstance(result, tuple) else result
        data = response.get_json(silent=True) or {}
        if response.status_code != 200 or not data.get("ok") or not (data.get("stats") or {}).get("total_contracts"):
            raise LookupError("no profile")
        return data

    try:
        return _PROFILE_CACHE.get_or_compute(name.strip().lower(), compute)
    except Exception:
        return None


def _profile_public(profile: dict[str, Any]) -> dict[str, Any]:
    st = profile["stats"]
    return {
        "buyer_type": profile.get("buyer_type"),
        "stats": {k: st.get(k) for k in (
            "total_contracts", "total_spend", "avg_contract_value", "framework_appointments", "earliest_award",
            "latest_award", "direct_awards", "competitive_awards", "unique_suppliers", "repeat_supplier_pct")},
        "sample_size_floor_met": bool(profile.get("sample_size_floor_met")),
        "top_suppliers": [{"supplier": s.get("supplier_name"), "contracts": s.get("contracts_won"), "value": s.get("total_value"),
                           "framework_appointments": s.get("framework_appointments"), "last_award": s.get("last_award_date")}
                          for s in (profile.get("top_suppliers") or [])[:5]],
        "sectors": [{"label": c.get("cpv_description"), "cpv": c.get("cpv_code"), "awards": c.get("count")}
                    for c in (profile.get("cpv_breakdown") or []) if c.get("cpv_code") != "uncategorized"][:5],
        "recent_awards": [{"title": h.get("tender_title"), "supplier": h.get("supplier_name"), "value": h.get("contract_value"),
                           "value_is_ceiling": bool(h.get("is_framework")), "signed": h.get("date_signed"),
                           "competitive": h.get("is_competitive"), "url": h.get("notice_url")}
                          for h in (profile.get("contract_history") or [])[:8]],
    }


@market_radar_bp.get("/api/market-radar/peers/profile")
@rate_limit(RATE_LIMIT_SEARCH)
def radar_peer_profile():
    _insights_on()
    try:
        cat, _, peer = _peer_in_view(request.args)
    except mr.CategoryError as exc:
        return _error(str(exc))
    except LookupError as exc:
        return _error(str(exc), 404)
    profile = _buyer_profile(peer["buyer"])
    return jsonify({
        "buyer": peer["buyer"],
        "type_label": peer["type_label"],
        "category": cat.to_public(),
        "in_category": {
            "awards": peer["awards"], "frameworks": peer["frameworks"], "suppliers": peer["suppliers"],
            "total_value": peer["total_value"], "shown": len(peer["_awards"]), "recent_awards": peer["_awards"][:8],
        },
        "profile": _profile_public(profile) if profile else None,
        "min_awards_for_insight": BUYER_INSIGHT_MIN_AWARDS,
    })


@market_radar_bp.post("/api/market-radar/peers/insight")
@rate_limit(RATE_LIMIT_AI)
def radar_peer_insight():
    """The AI summary. Always 200 with {"available": bool}: a failure here must never block the drawer."""
    _insights_on()
    try:
        cat, _, peer = _peer_in_view(request.args)
    except mr.CategoryError as exc:
        return _error(str(exc))
    except LookupError as exc:
        return _error(str(exc), 404)
    profile = _buyer_profile(peer["buyer"])
    awards_on_record = int(((profile or {}).get("stats") or {}).get("total_contracts") or peer["awards"])
    if awards_on_record < BUYER_INSIGHT_MIN_AWARDS:
        return jsonify({"available": False, "reason": "thin", "message": (
            f"{peer['buyer']} has only {awards_on_record} award{'s' if awards_on_record != 1 else ''} on record, "
            f"which is too few to describe a procurement pattern. Insights need at least {BUYER_INSIGHT_MIN_AWARDS}.")})
    facts = bi.build_facts(peer["buyer"], profile, cat.label, peer["_awards"], peer["awards"])
    try:
        from server import call_deepseek_insight

        def compute() -> str:
            text = call_deepseek_insight(bi.SYSTEM_PROMPT, bi.build_prompt(facts), max_tokens=450, temperature=0.2,
                                         timeout=BUYER_INSIGHT_TIMEOUT_SECONDS).strip()
            if not text or not bi.numbers_are_grounded(text, facts):
                raise ValueError("insight rejected: empty, or it quotes a figure that is not in the data")
            return text

        narrative = _INSIGHT_CACHE.get_or_compute(f"{peer['key']}#{cat.key}#{hash(tuple(facts))}", compute)
    except Exception:
        traceback.print_exc()
        return jsonify({"available": False, "reason": "unavailable", "message": "Insight unavailable right now."})
    return jsonify({"available": True, "narrative": narrative, "based_on": facts, "provider": "DeepSeek"})


@market_radar_bp.get("/api/market-radar/similar-suppliers")
@rate_limit(RATE_LIMIT_SEARCH)
def radar_similar_suppliers():
    supplier = (request.args.get("supplier") or "").strip()
    if not supplier:
        return _error("supplier is required")
    try:
        cat = _category_from(request.args)
        authority, window, scope = _filters_from(request.args)
        analysis = mr.get_analysis(_connect, cat, authority, window, scope)
    except mr.CategoryError as exc:
        return _error(str(exc))
    result = mr.similar_suppliers(analysis["_supplier_cpv"], supplier)
    if result is None:
        return _error("supplier not found in this view", 404)
    labels, _ = mr.load_cpv_labels(_connect)
    result["cpv_labels"] = {c: next((labels[c[:n]] for n in (5, 4, 3, 2) if c[:n] in labels), None) for c in result["cpv"][:5]}
    result["category"] = cat.to_public()
    result["unawarded"] = _unawarded_suppliers()
    return jsonify(result)


@market_radar_bp.get("/api/market-radar/company-profile")
@rate_limit(RATE_LIMIT_SEARCH)
def radar_company_profile():
    try:
        supplier_id = int(request.args.get("supplier_id", ""))
    except ValueError:
        return _error("supplier_id is required")
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, company_number, region, website FROM suppliers WHERE id = %s", (supplier_id,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row:
        return _error("supplier not found", 404)
    from tender_app import company_profile as cp
    return jsonify(cp.build_profile({"id": row[0], "name": row[1], "company_number": row[2], "region": row[3], "website": row[4]}))


def _unawarded_suppliers(limit: int = 15) -> dict:
    """Registered suppliers with no contract award anywhere. The register carries no CPV or category for
    them, so they cannot be ranked by similarity; they are listed separately, newest first."""
    where = "FROM suppliers s WHERE COALESCE(s.name, '') <> '' AND NOT EXISTS (SELECT 1 FROM contract_awards a WHERE a.supplier_id = s.id)"
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) {where}")
        total = int(cur.fetchone()[0])
        cur.execute(f"SELECT s.id, s.name, s.region, s.sme_status {where} ORDER BY s.created_at DESC, s.id DESC LIMIT %s", (limit,))
        rows = [{"supplier_id": r[0], "supplier": r[1], "region": r[2], "sme": (r[3] or "") == "SME"} for r in cur.fetchall()]
        cur.close()
    except Exception:
        traceback.print_exc()
        return {"total": 0, "rows": []}
    finally:
        conn.close()
    return {"total": total, "rows": rows}


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
        "insights": ENABLE_BUYER_INSIGHTS,
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
