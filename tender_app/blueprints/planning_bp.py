"""Planning Leads Flask Blueprint for TenderFlow.

Serves UK planning applications harvested nightly from PlanIt into
planning_applications (see etenders_scraper/planning). Unlike /api/search, nothing here
calls an external service: every request is a query against our own table, so filtering,
sorting and paging are done in SQL rather than in the browser.

Planning applications are pre-tender leads, not tenders. There is deliberately no value
field or filter: planning registers do not publish contract values and none is
estimated.
"""
from __future__ import annotations

import math
import threading
from datetime import date
from typing import Any

from flask import Blueprint, jsonify, request, session

from etenders_scraper.planning import harvester
from etenders_scraper.planning.scoring import explain_lead_score
from tender_app.config import (
    ADMIN_EMAILS,
    CREDIT_COST_PLANNING_SEARCH,
    PLANNING_MIN_PLAN,
    RATE_LIMIT_SEARCH,
)
from tender_app.geo import geo_radius_clause
from tender_app.metering import require_credits
from tender_app.security import csrf_required, rate_limit

planning_bp = Blueprint("planning_bp", __name__)

_get_db = None

MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 25
NEARBY_RADIUS_KM = 2.0
MAX_RADIUS_KM = 200.0

# Plan tiers, low to high. professional/business are aliases of standard/advance, as in
# config.PLAN_CREDITS.
PLAN_RANK = {"free": 0, "starter": 1, "standard": 2, "professional": 2, "advance": 3, "business": 3}
# Subscription statuses under which the paid plan is actually in force.
ENTITLED_STATUSES = ("active", "trialing")

VALID_SIZES = ("Large", "Medium", "Small")
VALID_STATES = ("Undecided", "Permitted", "Conditions", "Rejected", "Withdrawn", "Referred", "Unresolved", "Other")
VALID_TYPES = ("Full", "Outline", "Amendment", "Conditions", "Heritage", "Trees", "Advertising", "Telecoms", "Other")

# Must match idx_planning_fts in tender_app/db_ext.py exactly, or Postgres cannot use the
# index and falls back to a sequential scan.
FTS_DOCUMENT = "to_tsvector('english', coalesce(description, '') || ' ' || coalesce(address, ''))"

# The default view: new, tender-scale opportunities. low_value_reason is set at harvest
# time (and by `scripts/planning_backfill.py --rescore-all`) for follow-up paperwork and
# householder-scale minor works — see etenders_scraper/planning/scoring.py.
OPPORTUNITIES_SQL = "low_value_reason IS NULL"

LIST_COLUMNS = (
    "id", "uid", "planning_portal_id", "authority", "country", "region",
    "description", "address", "postcode", "latitude", "longitude",
    "app_size", "app_state", "app_type", "n_dwellings", "low_value_reason",
    "applicant_name", "applicant_company", "agent_name", "agent_company",
    "start_date", "decided_date", "target_decision_date", "decision",
    "docs_url", "detail_url", "planit_url", "lead_score",
)
DETAIL_COLUMNS = LIST_COLUMNS + (
    "agent_address", "case_officer", "ward_name", "consultation_end_date", "updated_at",
)

SORTS = {
    "lead_score": "lead_score DESC NULLS LAST, n_dwellings DESC NULLS LAST, start_date DESC NULLS LAST",
    "newest": "start_date DESC NULLS LAST, lead_score DESC NULLS LAST",
    "decided": "decided_date DESC NULLS LAST, lead_score DESC NULLS LAST",
    "dwellings": "n_dwellings DESC NULLS LAST, lead_score DESC NULLS LAST",
}


def init_planning_blueprint(get_db_connection):
    global _get_db
    _get_db = get_db_connection
    return planning_bp


def _get_connection():
    if _get_db is None:
        raise RuntimeError("Planning blueprint not initialized with database connection")
    return _get_db()


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        out[k] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


def _with_breakdown(row: dict[str, Any]) -> dict[str, Any]:
    serialized = _serialize(row)
    serialized["lead_score_breakdown"] = explain_lead_score(row)
    return serialized


def _fetch_dicts(cursor) -> list[dict[str, Any]]:
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) for r in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def _is_admin(conn) -> bool:
    email = (session.get("email") or session.get("username") or "").lower()
    if email and email in ADMIN_EMAILS:
        return True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT role FROM users WHERE LOWER(username) = %s OR LOWER(email) = %s",
            (email, email),
        )
        row = cur.fetchone()
    return bool(row and row[0] == "admin")


def _effective_plan(conn, username: str) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT plan, status FROM subscriptions WHERE username = %s", (username,))
        row = cur.fetchone()
    if not row or (row[1] or "").lower() not in ENTITLED_STATUSES:
        return "free"
    return (row[0] or "free").lower()


def _plan_gate():
    """Return a 403 response if PLANNING_MIN_PLAN is set and the user's plan is below
    it, else None. Admins always pass so they can support customers."""
    if not PLANNING_MIN_PLAN:
        return None
    required = PLAN_RANK.get(PLANNING_MIN_PLAN)
    if required is None:
        # A typo in the env var must not silently open or close the module to everyone.
        return jsonify({"error": f"Planning Leads misconfigured: unknown PLANNING_MIN_PLAN {PLANNING_MIN_PLAN!r}"}), 500
    conn = _get_connection()
    try:
        if _is_admin(conn):
            return None
        plan = _effective_plan(conn, session.get("username", ""))
    finally:
        conn.close()
    if PLAN_RANK.get(plan, 0) >= required:
        return None
    return jsonify({
        "error": "Planning Leads is not included in your plan",
        "code": "PLAN_REQUIRED",
        "required_plan": PLANNING_MIN_PLAN,
        "current_plan": plan,
    }), 403


def _require_admin():
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    conn = _get_connection()
    try:
        if not _is_admin(conn):
            return jsonify({"error": "Forbidden"}), 403
    finally:
        conn.close()
    return None


# ---------------------------------------------------------------------------
# Query parsing
# ---------------------------------------------------------------------------

class BadRequest(ValueError):
    pass


def _multi(name: str, allowed: tuple[str, ...] | None = None) -> list[str]:
    """Comma-separated or repeated query param. Values are matched case-insensitively
    against `allowed` and returned in canonical case."""
    raw: list[str] = []
    for v in request.args.getlist(name):
        raw.extend(p.strip() for p in v.split(","))
    values = [v for v in raw if v]
    if allowed is None:
        return values
    canon = {a.lower(): a for a in allowed}
    bad = [v for v in values if v.lower() not in canon]
    if bad:
        raise BadRequest(f"Invalid {name}: {', '.join(bad)}. Allowed: {', '.join(allowed)}")
    return [canon[v.lower()] for v in values]


def _int_arg(name: str, default: int | None = None, lo: int | None = None, hi: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        n = int(raw)
    except ValueError:
        raise BadRequest(f"{name} must be an integer")
    if lo is not None and n < lo:
        raise BadRequest(f"{name} must be >= {lo}")
    if hi is not None and n > hi:
        raise BadRequest(f"{name} must be <= {hi}")
    return n


def _float_arg(name: str) -> float | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except ValueError:
        raise BadRequest(f"{name} must be a number")


def _date_arg(name: str) -> date | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise BadRequest(f"{name} must be a date in YYYY-MM-DD format")



def _build_search() -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []

    q = (request.args.get("q") or "").strip()
    if q:
        where.append(f"{FTS_DOCUMENT} @@ websearch_to_tsquery('english', %s)")
        params.append(q)

    developer = (request.args.get("developer") or "").strip()
    if developer:
        where.append("(agent_company ILIKE %s OR applicant_company ILIKE %s OR agent_name ILIKE %s OR applicant_name ILIKE %s)")
        like = "%" + developer.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params.extend([like] * 4)

    for column, values in (
        ("country", _multi("country")),
        ("authority", _multi("authority")),
        ("app_size", _multi("size", VALID_SIZES)),
        ("app_state", _multi("state", VALID_STATES)),
    ):
        if values:
            where.append(f"{column} = ANY(%s)")
            params.append(values)

    types = _multi("type", VALID_TYPES)
    include_low_value = request.args.get("include_low_value") in ("1", "true")
    if types:
        where.append("app_type = ANY(%s)")
        params.append(types)
        if not include_low_value:
            where.append(OPPORTUNITIES_SQL)
    elif not include_low_value:
        where.append(OPPORTUNITIES_SQL)

    min_dwellings = _int_arg("min_dwellings", lo=0)
    if min_dwellings:
        where.append("n_dwellings >= %s")
        params.append(min_dwellings)

    min_score = _int_arg("min_score", lo=0, hi=100)
    if min_score:
        where.append("lead_score >= %s")
        params.append(min_score)

    for arg, column, op in (
        ("submitted_from", "start_date", ">="),
        ("submitted_to", "start_date", "<="),
        ("decided_from", "decided_date", ">="),
        ("decided_to", "decided_date", "<="),
    ):
        d = _date_arg(arg)
        if d:
            where.append(f"{column} {op} %s")
            params.append(d)

    sort_key = request.args.get("sort") or "lead_score"
    distance_select = "NULL::double precision"
    distance_params: list[Any] = []
    lat, lng = _float_arg("lat"), _float_arg("lng")
    if (lat is None) != (lng is None):
        raise BadRequest("lat and lng must be given together")
    if lat is not None:
        radius = _float_arg("radius_km")
        radius = 10.0 if radius is None else radius
        if not (0 < radius <= MAX_RADIUS_KM):
            raise BadRequest(f"radius_km must be greater than 0 and at most {MAX_RADIUS_KM:g}")
        geo = geo_radius_clause(lat, lng, radius)
        where.append(geo["where_sql"])
        params.extend(geo["where_params"])
        distance_select = geo["distance_sql"]
        distance_params = geo["distance_params"]
    elif sort_key == "distance":
        raise BadRequest("sort=distance requires lat and lng")

    if sort_key == "distance":
        order_by = "distance_km ASC, lead_score DESC NULLS LAST"
    elif sort_key in SORTS:
        order_by = SORTS[sort_key]
    else:
        raise BadRequest(f"Invalid sort. Allowed: {', '.join([*SORTS, 'distance'])}")

    return {
        "where": " AND ".join(where) if where else "TRUE",
        "params": params,
        "order_by": order_by,
        "distance_select": distance_select,
        "distance_params": distance_params,
    }


# Params that page or order a result set without narrowing it.
_NON_FILTER_ARGS = {"page", "per_page", "sort"}


def _is_free_request() -> bool:
    """Free: paging through a query already paid for on page 1, and the unfiltered
    default list, which is what opening Planning Leads shows. Only a keyword or filter
    search costs credits, so looking at the module never spends one by surprise."""
    try:
        if int(request.args.get("page") or 1) > 1:
            return True
    except ValueError:
        return False
    return not any(v.strip() for k, v in request.args.items(multi=True) if k not in _NON_FILTER_ARGS)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@planning_bp.route("/api/planning/search", methods=["GET"])
@rate_limit(RATE_LIMIT_SEARCH)
def planning_search():
    gate = _plan_gate()
    if gate:
        return gate
    try:
        built = _build_search()
        page = _int_arg("page", 1, lo=1)
        per_page = _int_arg("per_page", DEFAULT_PER_PAGE, lo=1, hi=MAX_PER_PAGE)
    except BadRequest as exc:
        return jsonify({"error": str(exc)}), 400
    return _charged_search(built, page, per_page)


# Charging is applied only after validation above, so a malformed request never costs a
# credit. The lambda resolves the connection factory at call time because
# init_planning_blueprint() runs after this module is imported.
@require_credits(CREDIT_COST_PLANNING_SEARCH, "planning_search", lambda: _get_connection(),
                 skip_if=_is_free_request)
def _charged_search(built: dict[str, Any], page: int, per_page: int):
    sql = f"""
        SELECT {", ".join(LIST_COLUMNS)},
               {built["distance_select"]} AS distance_km,
               COUNT(*) OVER () AS _total
        FROM planning_applications
        WHERE {built["where"]}
        ORDER BY {built["order_by"]}
        LIMIT %s OFFSET %s
    """
    params = built["distance_params"] + built["params"] + [per_page, (page - 1) * per_page]
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = _fetch_dicts(cur)
    finally:
        conn.close()

    total = rows[0].pop("_total") if rows else 0
    results = []
    for r in rows:
        r.pop("_total", None)
        if r.get("distance_km") is not None:
            r["distance_km"] = round(r["distance_km"], 2)
        else:
            r.pop("distance_km", None)
        results.append(_with_breakdown(r))

    return jsonify({
        "results": results,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": math.ceil(total / per_page) if total else 0,
        "source": "UK PlanIt (planit.org.uk), from local planning authority public registers",
    })


# Enough pins to see a region's pattern, few enough to render instantly and to keep this
# endpoint from being a bulk export. The client narrows with the viewport box to see more.
MAP_MAX_POINTS = 500


@planning_bp.route("/api/planning/map", methods=["GET"])
@rate_limit(RATE_LIMIT_SEARCH)
def planning_map():
    """Compact map points for the current filters, optionally limited to the visible map box.

    Takes every filter /api/planning/search takes, plus south/west/north/east (all four or none).
    Returns the highest-scoring MAP_MAX_POINTS applications that have coordinates. Not charged:
    it is another view of a query the list already ran (which the list itself charges for), and
    it carries no description text beyond a short excerpt.
    """
    gate = _plan_gate()
    if gate:
        return gate
    try:
        built = _build_search()
        box = {k: _float_arg(k) for k in ("south", "west", "north", "east")}
        given = [v is not None for v in box.values()]
        if any(given) and not all(given):
            raise BadRequest("south, west, north and east must be given together")
        if all(given) and not (-90 <= box["south"] < box["north"] <= 90 and -180 <= box["west"] < box["east"] <= 180):
            raise BadRequest("Invalid map bounds")
    except BadRequest as exc:
        return jsonify({"error": str(exc)}), 400

    where = f"({built['where']}) AND latitude IS NOT NULL AND longitude IS NOT NULL"
    params: list[Any] = list(built["params"])
    if all(given):
        where += " AND latitude BETWEEN %s AND %s AND longitude BETWEEN %s AND %s"
        params += [box["south"], box["north"], box["west"], box["east"]]

    sql = f"""
        SELECT id, latitude, longitude, lead_score, app_size, app_state, app_type, authority, address,
               LEFT(description, 160) AS description, agent_company,
               COUNT(*) OVER () AS _total
        FROM planning_applications
        WHERE {where}
        ORDER BY {SORTS["lead_score"]}
        LIMIT %s
    """
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params + [MAP_MAX_POINTS])
            rows = _fetch_dicts(cur)
    finally:
        conn.close()

    total = rows[0]["_total"] if rows else 0
    points = []
    for r in rows:
        r.pop("_total", None)
        r["latitude"], r["longitude"] = float(r["latitude"]), float(r["longitude"])
        points.append(r)
    return jsonify({
        "points": points,
        "total_with_location": total,
        "shown": len(points),
        "capped": total > len(points),
        "max_points": MAP_MAX_POINTS,
    })


@planning_bp.route("/api/planning/application/<path:app_id>", methods=["GET"])
def planning_application(app_id: str):
    gate = _plan_gate()
    if gate:
        return gate
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(DETAIL_COLUMNS)} FROM planning_applications WHERE id = %s",
                (app_id,),
            )
            rows = _fetch_dicts(cur)
            if not rows:
                return jsonify({"error": "Not found"}), 404
            app = rows[0]

            nearby: list[dict[str, Any]] = []
            if app.get("latitude") is not None and app.get("longitude") is not None:
                geo = geo_radius_clause(app["latitude"], app["longitude"], NEARBY_RADIUS_KM)
                cur.execute(
                    f"""
                    SELECT id, description, address, app_size, app_state, app_type,
                           n_dwellings, start_date, lead_score, {geo["distance_sql"]} AS distance_km
                    FROM planning_applications
                    WHERE id <> %s AND {geo["where_sql"]} AND {OPPORTUNITIES_SQL}
                    ORDER BY lead_score DESC NULLS LAST
                    LIMIT 5
                    """,
                    [*geo["distance_params"], app_id, *geo["where_params"]],
                )
                for n in _fetch_dicts(cur):
                    n["distance_km"] = round(n["distance_km"], 2)
                    nearby.append(_serialize(n))
    finally:
        conn.close()

    result = _with_breakdown(app)
    result["nearby"] = nearby
    return jsonify(result)


@planning_bp.route("/api/planning/authorities", methods=["GET"])
def planning_authorities():
    gate = _plan_gate()
    if gate:
        return gate
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT authority, country, COUNT(*) AS applications
                FROM planning_applications
                WHERE authority IS NOT NULL
                GROUP BY authority, country
                ORDER BY authority
            """)
            rows = _fetch_dicts(cur)
    finally:
        conn.close()
    return jsonify({"authorities": rows})


@planning_bp.route("/api/planning/stats", methods=["GET"])
def planning_stats():
    gate = _plan_gate()
    if gate:
        return gate
    # Takes every filter /api/planning/search takes (nation, county/authority, keyword, dates,
    # etc.) so the summary banner can reflect the active filters instead of always showing the
    # unfiltered UK-wide total -- previously the frontend just hid the banner under any filter
    # rather than show a stale, contradicting figure.
    try:
        built = _build_search()
    except BadRequest as exc:
        return jsonify({"error": str(exc)}), 400
    where, params = built["where"], built["params"]

    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE start_date >= CURRENT_DATE - 7),
                       COUNT(*) FILTER (WHERE decided_date >= CURRENT_DATE - 7
                                        AND app_state IN ('Permitted', 'Conditions')),
                       MAX(updated_at)
                FROM planning_applications
                WHERE {where}
            """, params)
            total, new_7d, approved_7d, last_updated = cur.fetchone()

            breakdowns = {}
            for key, column in (("country", "country"), ("size", "app_size"),
                                ("state", "app_state"), ("type", "app_type")):
                cur.execute(
                    f"SELECT COALESCE({column}, 'Unknown'), COUNT(*) FROM planning_applications "
                    f"WHERE {where} GROUP BY 1 ORDER BY 2 DESC",
                    params
                )
                breakdowns[key] = {k: n for k, n in cur.fetchall()}
    finally:
        conn.close()
    return jsonify({
        "total": total,
        "new_last_7_days": new_7d,
        "approved_last_7_days": approved_7d,
        "last_updated": last_updated.isoformat() if last_updated else None,
        "by": breakdowns,
        "excludes": "paperwork and householder-scale minor works (see low_value_reason on each application)",
        # Shown in the UI before searching, so a credit is never spent by surprise.
        "credit_cost_per_search": CREDIT_COST_PLANNING_SEARCH,
    })


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@planning_bp.route("/api/admin/planning/scheduler-status", methods=["GET"])
def planning_scheduler_status():
    denied = _require_admin()
    if denied:
        return denied
    conn = _get_connection()
    try:
        state = harvester.get_harvest_state(conn)
        with conn.cursor() as cur:
            # Authorities with no submission or decision in 60 days: usually a stale
            # PlanIt scraper for that council rather than a quiet council. Both dates
            # count, because the nightly harvest pulls decisions on old applications —
            # judging by submissions alone flags healthy authorities as stale.
            cur.execute("""
                SELECT authority, country,
                       GREATEST(MAX(start_date), MAX(decided_date)) AS latest_activity,
                       COUNT(*) AS applications
                FROM planning_applications
                GROUP BY authority, country
                HAVING GREATEST(MAX(start_date), MAX(decided_date)) < CURRENT_DATE - 60
                ORDER BY latest_activity
                LIMIT 50
            """)
            stale = [_serialize(r) for r in _fetch_dicts(cur)]
    finally:
        conn.close()
    return jsonify({
        "scheduler": harvester.get_planning_scheduler_status(),
        "harvest_state": _serialize(state),
        "possibly_stale_authorities": stale,
    })


@planning_bp.route("/api/admin/planning/harvest", methods=["POST"])
@csrf_required
def planning_trigger_harvest():
    denied = _require_admin()
    if denied:
        return denied
    if harvester.get_planning_scheduler_status().get("running"):
        return jsonify({"status": "already_running"}), 409
    # A harvest takes several minutes at PlanIt's one-request-a-minute pace, so it runs
    # in the background; poll scheduler-status for the result.
    threading.Thread(
        target=harvester.run_planning_harvest, args=(_get_db,), daemon=True, name="planning-harvest-manual",
    ).start()
    return jsonify({"status": "started"}), 202


@planning_bp.route("/api/admin/planning/suppress", methods=["POST"])
@csrf_required
def planning_suppress():
    """Remove an application and stop future harvests re-adding it (e.g. UK GDPR erasure)."""
    denied = _require_admin()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    app_id = (body.get("id") or "").strip()
    reason = (body.get("reason") or "").strip() or None
    if not app_id:
        return jsonify({"error": "id is required"}), 400
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO planning_suppressions (id, reason, created_by) VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET reason = EXCLUDED.reason, created_by = EXCLUDED.created_by
                """,
                (app_id, reason, session.get("email") or session.get("username")),
            )
            cur.execute("DELETE FROM planning_applications WHERE id = %s", (app_id,))
            deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "suppressed", "id": app_id, "deleted": deleted})
