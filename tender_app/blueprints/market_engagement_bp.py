"""Market Engagement API: plan, run and record a preliminary market engagement (PME).

The pure logic (validation, notice drafting, step state, supplier shortlist, hand-over pack) is in
tender_app/market_engagement.py; this module is the routes and SQL. A plan, its suppliers and its
log belong to the user who created them, and every route checks that.

    GET    /api/market-engagement                        the user's plans
    POST   /api/market-engagement                        start a plan
    GET    /api/market-engagement/<id>                   one plan with suppliers, log and step state
    PATCH  /api/market-engagement/<id>                   edit scope, notice text, contact ...
    DELETE /api/market-engagement/<id>
    POST   /api/market-engagement/<id>/suppliers/match   shortlist suppliers from the award history
    POST   /api/market-engagement/<id>/suppliers         add a supplier by name
    PATCH  /api/market-engagement/<id>/suppliers/<sid>   include / status / note
    DELETE /api/market-engagement/<id>/suppliers/<sid>
    GET    /api/market-engagement/<id>/notice            draft notice text from the plan (not saved)
    POST   /api/market-engagement/<id>/publish           record that the notice has been published
    POST   /api/market-engagement/<id>/close             engagement closed
    POST   /api/market-engagement/<id>/convert           handed over to the tender
    POST   /api/market-engagement/<id>/log               add a note to the record
    GET    /api/market-engagement/<id>/handoff           hand-over pack (markdown)

Nothing here publishes to Find a Tender or emails a supplier: see the module note in
market_engagement.py. Mutating requests are CSRF-protected by the app-wide hook.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from flask import Blueprint, jsonify, request, session

from tender_app import market_engagement as me
from tender_app import market_radar as mr

market_engagement_bp = Blueprint("market_engagement", __name__)

_get_db = None

PLAN_COLUMNS = (
    "id", "username", "organisation", "title", "category_json", "category_label", "est_value", "term_years",
    "engagement_type", "objectives", "supplier_day_at", "supplier_day_place", "response_deadline",
    "contact_name", "contact_email", "status", "notice_text", "published_url", "published_at", "closed_at",
    "converted_at", "created_at", "updated_at",
)
SUPPLIER_COLUMNS = ("id", "supplier_key", "supplier_name", "supplier_id", "source", "included", "status", "note", "stats_json")
MAX_PLANS_PER_USER = 200
MAX_SUPPLIERS_PER_PLAN = 300


def init_market_engagement_blueprint(get_db_connection):
    global _get_db
    _get_db = get_db_connection
    return market_engagement_bp


def _connect():
    if _get_db is None:
        raise RuntimeError("Market Engagement blueprint not initialised with a database connection")
    return _get_db()


def _username() -> str:
    return (session.get("username") or "").strip()


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _fetch_dicts(cur) -> list[dict[str, Any]]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _load_plan(cur, plan_id: int, username: str) -> dict[str, Any] | None:
    cur.execute(f"SELECT {', '.join(PLAN_COLUMNS)} FROM market_engagements WHERE id = %s AND username = %s", (plan_id, username))
    rows = _fetch_dicts(cur)
    return rows[0] if rows else None


def _load_suppliers(cur, plan_id: int) -> list[dict[str, Any]]:
    cur.execute(
        f"""SELECT {', '.join(SUPPLIER_COLUMNS)} FROM market_engagement_suppliers
            WHERE engagement_id = %s ORDER BY included DESC, supplier_name""",
        (plan_id,),
    )
    out = []
    for r in _fetch_dicts(cur):
        r["stats"] = json.loads(r.pop("stats_json")) if r.get("stats_json") else None
        r.pop("stats_json", None)
        out.append(r)
    return out


def _load_log(cur, plan_id: int) -> list[dict[str, Any]]:
    cur.execute("SELECT id, at, kind, message FROM market_engagement_log WHERE engagement_id = %s ORDER BY at, id", (plan_id,))
    return _fetch_dicts(cur)


def _log(cur, plan_id: int, kind: str, message: str) -> None:
    cur.execute(
        "INSERT INTO market_engagement_log (engagement_id, kind, message) VALUES (%s, %s, %s)",
        (plan_id, kind, message[:1000]),
    )


def _touch(cur, plan_id: int) -> None:
    cur.execute("UPDATE market_engagements SET updated_at = NOW() WHERE id = %s", (plan_id,))


def _public_plan(plan: dict[str, Any], suppliers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out = {k: _jsonable(v) for k, v in plan.items() if k != "username"}
    out["category"] = json.loads(plan["category_json"]) if plan.get("category_json") else None
    out.pop("category_json", None)
    included = [s for s in (suppliers or []) if s["included"]]
    out["steps"] = me.step_states(plan, len(included))
    out["supplier_counts"] = {
        "included": len(included),
        "responded": sum(1 for s in included if s["status"] == "responded"),
        "contacted": sum(1 for s in included if s["status"] != "not_contacted"),
    }
    out["engagement_type_label"] = me.ENGAGEMENT_TYPES.get(plan.get("engagement_type"), plan.get("engagement_type"))
    return out


def _full_plan(cur, plan_id: int, username: str) -> dict[str, Any] | None:
    plan = _load_plan(cur, plan_id, username)
    if not plan:
        return None
    suppliers = _load_suppliers(cur, plan_id)
    out = _public_plan(plan, suppliers)
    out["suppliers"] = [{k: _jsonable(v) for k, v in s.items()} for s in suppliers]
    out["log"] = [{k: _jsonable(v) for k, v in e.items()} for e in _load_log(cur, plan_id)]
    out["allowed_transitions"] = sorted(me.TRANSITIONS.get(plan["status"], ()))
    return out


def _category_from_body(body: dict[str, Any]) -> mr.Category:
    spec = body.get("category")
    if not isinstance(spec, dict):
        raise mr.CategoryError("category is required: choose a preset, a CPV code or search text")
    cpv = str(spec["cpv"]).strip() if spec.get("cpv") not in (None, "") else None
    text = spec.get("q") if isinstance(spec.get("q"), str) else None
    labels = mr.load_cpv_labels(_connect)[0] if cpv else None
    return mr.resolve_category(spec.get("preset") if isinstance(spec.get("preset"), str) else None, cpv, text, labels)


def _plan_id(raw: str) -> int | None:
    return int(raw) if raw.isdigit() and len(raw) < 10 else None


def _body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


# ── plans ────────────────────────────────────────────────────────────────────────────────────

@market_engagement_bp.get("/api/market-engagement")
def list_plans():
    username = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT {', '.join('e.' + c for c in PLAN_COLUMNS)},
                           (SELECT COUNT(*) FROM market_engagement_suppliers s WHERE s.engagement_id = e.id AND s.included) AS n_included,
                           (SELECT COUNT(*) FROM market_engagement_suppliers s WHERE s.engagement_id = e.id AND s.included AND s.status = 'responded') AS n_responded
                    FROM market_engagements e WHERE e.username = %s
                    ORDER BY e.updated_at DESC LIMIT 100""",
                (username,),
            )
            rows = _fetch_dicts(cur)
    finally:
        conn.close()
    plans = []
    for r in rows:
        included, responded = r.pop("n_included"), r.pop("n_responded")
        public = _public_plan(r)
        public["steps"] = me.step_states(r, included)
        public["supplier_counts"] = {"included": included, "responded": responded, "contacted": None}
        for heavy in ("notice_text", "objectives"):
            public.pop(heavy, None)
        plans.append(public)
    return jsonify({"plans": plans})


@market_engagement_bp.post("/api/market-engagement")
def create_plan():
    body = _body()
    username = _username()
    try:
        fields = me.validate_plan(body)
        cat = _category_from_body(body)
    except (me.ValidationError, mr.CategoryError) as exc:
        return _error(str(exc))
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM market_engagements WHERE username = %s", (username,))
            if cur.fetchone()[0] >= MAX_PLANS_PER_USER:
                return _error("You have reached the limit of saved engagements. Delete one to start another.", 409)
            if not fields.get("organisation"):
                cur.execute("SELECT pref_value FROM user_prefs WHERE username = %s AND pref_key = 'buyer_org_name'", (username,))
                row = cur.fetchone()
                fields["organisation"] = row[0] if row else None
            cur.execute(
                """INSERT INTO market_engagements
                   (username, organisation, title, category_json, category_label, est_value, term_years,
                    engagement_type, objectives, supplier_day_at, supplier_day_place, response_deadline,
                    contact_name, contact_email)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    username, fields.get("organisation"), fields["title"], json.dumps(cat.to_public()), cat.label,
                    fields.get("est_value"), fields.get("term_years"), fields["engagement_type"],
                    fields.get("objectives"), fields.get("supplier_day_at"), fields.get("supplier_day_place"),
                    fields.get("response_deadline"), fields.get("contact_name"), fields.get("contact_email"),
                ),
            )
            plan_id = cur.fetchone()[0]
            _log(cur, plan_id, "created", f"Plan created for {cat.label}")
            conn.commit()
            return jsonify(_full_plan(cur, plan_id, username)), 201
    finally:
        conn.close()


@market_engagement_bp.get("/api/market-engagement/<plan_id>")
def get_plan(plan_id: str):
    pid = _plan_id(plan_id)
    if pid is None:
        return _error("not found", 404)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            plan = _full_plan(cur, pid, _username())
    finally:
        conn.close()
    return jsonify(plan) if plan else _error("not found", 404)


_SCOPE_FIELDS = {"title", "organisation", "est_value", "term_years", "engagement_type", "objectives",
                 "supplier_day_at", "supplier_day_place", "response_deadline", "contact_name", "contact_email"}


@market_engagement_bp.patch("/api/market-engagement/<plan_id>")
def update_plan(plan_id: str):
    pid = _plan_id(plan_id)
    body = _body()
    if pid is None:
        return _error("not found", 404)
    try:
        fields = me.validate_plan(body, partial=True)
        cat = _category_from_body(body) if "category" in body else None
    except (me.ValidationError, mr.CategoryError) as exc:
        return _error(str(exc))
    if cat is not None:
        fields["category_json"] = json.dumps(cat.to_public())
        fields["category_label"] = cat.label
    if not fields:
        return _error("nothing to update")
    username = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            plan = _load_plan(cur, pid, username)
            if not plan:
                return _error("not found", 404)
            if plan["status"] == "converted":
                return _error("This engagement has been handed over to the tender and can no longer be edited.", 409)
            assignments = ", ".join(f"{column} = %s" for column in fields)  # columns come from the validated dict above
            cur.execute(
                f"UPDATE market_engagements SET {assignments}, updated_at = NOW() WHERE id = %s AND username = %s",
                list(fields.values()) + [pid, username],
            )
            changed = sorted(set(fields) & (_SCOPE_FIELDS | {"category_json"}))
            if changed:
                names = ["category" if c == "category_json" else c.replace("_", " ") for c in changed]
                _log(cur, pid, "scope", "Scope updated: " + ", ".join(names))
            if "notice_text" in fields:
                _log(cur, pid, "notice", "Notice text edited")
            conn.commit()
            return jsonify(_full_plan(cur, pid, username))
    finally:
        conn.close()


@market_engagement_bp.delete("/api/market-engagement/<plan_id>")
def delete_plan(plan_id: str):
    pid = _plan_id(plan_id)
    if pid is None:
        return _error("not found", 404)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM market_engagements WHERE id = %s AND username = %s", (pid, _username()))
            deleted = cur.rowcount
            conn.commit()
    finally:
        conn.close()
    return jsonify({"deleted": True}) if deleted else _error("not found", 404)


# ── suppliers ────────────────────────────────────────────────────────────────────────────────

@market_engagement_bp.post("/api/market-engagement/<plan_id>/suppliers/match")
def match_suppliers(plan_id: str):
    pid = _plan_id(plan_id)
    body = _body()
    if pid is None:
        return _error("not found", 404)
    window = body.get("window") or "5y"
    if window not in mr.WINDOWS:
        return _error("unknown window")
    username = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            plan = _load_plan(cur, pid, username)
            if not plan:
                return _error("not found", 404)
            if plan["status"] == "converted":
                return _error("This engagement has been handed over and can no longer be edited.", 409)
            spec = json.loads(plan["category_json"]) if plan.get("category_json") else {}
        try:
            cat = mr.resolve_category(spec.get("preset"), spec.get("cpv"), spec.get("q"))
            analysis = mr.get_analysis(_connect, cat, "all", window, "all")
        except mr.CategoryError as exc:
            return _error(str(exc))
        candidates = me.shortlist_suppliers(analysis["suppliers"], limit=40, smaller_first=bool(body.get("smaller_first")))
        added = 0
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM market_engagement_suppliers WHERE engagement_id = %s", (pid,))
            room = MAX_SUPPLIERS_PER_PLAN - cur.fetchone()[0]
            for c in candidates[:max(room, 0)]:
                cur.execute(
                    """INSERT INTO market_engagement_suppliers
                       (engagement_id, supplier_key, supplier_name, supplier_id, source, stats_json, included)
                       VALUES (%s, %s, %s, %s, 'matched', %s, FALSE)
                       ON CONFLICT (engagement_id, supplier_key) DO NOTHING""",
                    (pid, c["supplier_key"], c["supplier_name"], c["supplier_id"], json.dumps(c["stats"])),
                )
                added += cur.rowcount
            if added:
                _log(cur, pid, "suppliers", f"Added {added} supplier{'' if added == 1 else 's'} from the award history ({mr.WINDOW_LABELS[window].lower()})")
            _touch(cur, pid)
            conn.commit()
            suppliers = _load_suppliers(cur, pid)
        return jsonify({
            "added": added,
            "candidates": len(candidates),
            "basis": mr.WINDOW_LABELS[window],
            "smaller_first_is_a_proxy": bool(body.get("smaller_first")),
            "suppliers": [{k: _jsonable(v) for k, v in s.items()} for s in suppliers],
        })
    finally:
        conn.close()


@market_engagement_bp.post("/api/market-engagement/<plan_id>/suppliers")
def add_supplier(plan_id: str):
    pid = _plan_id(plan_id)
    body = _body()
    if pid is None:
        return _error("not found", 404)
    name = body.get("name")
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 200:
        return _error("name must be 2 to 200 characters")
    name = re.sub(r"\s+", " ", name.strip())
    key = mr.canonical_supplier_key(name)
    username = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            plan = _load_plan(cur, pid, username)
            if not plan:
                return _error("not found", 404)
            if plan["status"] == "converted":
                return _error("This engagement has been handed over and can no longer be edited.", 409)
            cur.execute("SELECT COUNT(*) FROM market_engagement_suppliers WHERE engagement_id = %s", (pid,))
            if cur.fetchone()[0] >= MAX_SUPPLIERS_PER_PLAN:
                return _error("This engagement already has the maximum number of suppliers.", 409)
            cur.execute(
                """INSERT INTO market_engagement_suppliers (engagement_id, supplier_key, supplier_name, source)
                   VALUES (%s, %s, %s, 'manual') ON CONFLICT (engagement_id, supplier_key) DO NOTHING""",
                (pid, key, name),
            )
            created = cur.rowcount
            if created:
                _log(cur, pid, "suppliers", f"Added {name} manually")
                _touch(cur, pid)
            conn.commit()
            suppliers = _load_suppliers(cur, pid)
        return jsonify({
            "added": bool(created),
            "suppliers": [{k: _jsonable(v) for k, v in s.items()} for s in suppliers],
        }), (201 if created else 200)
    finally:
        conn.close()


@market_engagement_bp.patch("/api/market-engagement/<plan_id>/suppliers/<supplier_row>")
def update_supplier(plan_id: str, supplier_row: str):
    pid, sid = _plan_id(plan_id), _plan_id(supplier_row)
    if pid is None or sid is None:
        return _error("not found", 404)
    try:
        changes = me.validate_supplier_update(_body())
    except me.ValidationError as exc:
        return _error(str(exc))
    username = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            plan = _load_plan(cur, pid, username)
            if not plan:
                return _error("not found", 404)
            if plan["status"] == "converted":
                return _error("This engagement has been handed over and can no longer be edited.", 409)
            cur.execute("SELECT supplier_name, status FROM market_engagement_suppliers WHERE id = %s AND engagement_id = %s", (sid, pid))
            row = cur.fetchone()
            if not row:
                return _error("not found", 404)
            assignments = ", ".join(f"{column} = %s" for column in changes)  # columns come from the validated dict
            cur.execute(f"UPDATE market_engagement_suppliers SET {assignments} WHERE id = %s", list(changes.values()) + [sid])
            if "status" in changes and changes["status"] != row[1]:
                _log(cur, pid, "supplier", f"{row[0]}: {me.SUPPLIER_STATUSES[row[1]].lower()} → {me.SUPPLIER_STATUSES[changes['status']].lower()}")
            _touch(cur, pid)
            conn.commit()
            return jsonify({"suppliers": [{k: _jsonable(v) for k, v in s.items()} for s in _load_suppliers(cur, pid)]})
    finally:
        conn.close()


@market_engagement_bp.delete("/api/market-engagement/<plan_id>/suppliers/<supplier_row>")
def delete_supplier(plan_id: str, supplier_row: str):
    pid, sid = _plan_id(plan_id), _plan_id(supplier_row)
    if pid is None or sid is None:
        return _error("not found", 404)
    username = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            plan = _load_plan(cur, pid, username)
            if not plan:
                return _error("not found", 404)
            if plan["status"] == "converted":
                return _error("This engagement has been handed over and can no longer be edited.", 409)
            cur.execute("DELETE FROM market_engagement_suppliers WHERE id = %s AND engagement_id = %s RETURNING supplier_name", (sid, pid))
            row = cur.fetchone()
            if not row:
                return _error("not found", 404)
            _log(cur, pid, "suppliers", f"Removed {row[0]}")
            _touch(cur, pid)
            conn.commit()
            return jsonify({"suppliers": [{k: _jsonable(v) for k, v in s.items()} for s in _load_suppliers(cur, pid)]})
    finally:
        conn.close()


# ── notice, status, record, hand-over ────────────────────────────────────────────────────────

@market_engagement_bp.get("/api/market-engagement/<plan_id>/notice")
def draft_notice(plan_id: str):
    pid = _plan_id(plan_id)
    if pid is None:
        return _error("not found", 404)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            plan = _load_plan(cur, pid, _username())
    finally:
        conn.close()
    if not plan:
        return _error("not found", 404)
    text, missing = me.build_notice_text(plan)
    return jsonify({"text": text, "missing": missing, "saved": bool(plan.get("notice_text"))})


def _transition(plan_id: str, target: str, note: str):
    pid = _plan_id(plan_id)
    if pid is None:
        return _error("not found", 404)
    body = _body()
    username = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            plan = _load_plan(cur, pid, username)
            if not plan:
                return _error("not found", 404)
            if target not in me.TRANSITIONS.get(plan["status"], set()):
                return _error(f"This engagement is {plan['status']}, so it cannot be moved to {target}.", 409)
            sets, params = ["status = %s", "updated_at = NOW()"], [target]
            message = note
            if target == "published":
                if not (plan.get("notice_text") or "").strip():
                    return _error("Save the notice text before you mark it as published.", 409)
                try:
                    fields = me.validate_plan({"published_url": body.get("url")}, partial=True)
                except me.ValidationError as exc:
                    return _error(str(exc))
                url = fields.get("published_url")
                sets.append("published_url = %s")
                params.append(url)
                sets.append("published_at = COALESCE(published_at, NOW())")
                if url:
                    message += f" ({url})"
            elif target == "closed":
                sets.append("closed_at = NOW()")
            elif target == "converted":
                sets.append("converted_at = NOW()")
            cur.execute(f"UPDATE market_engagements SET {', '.join(sets)} WHERE id = %s AND username = %s", params + [pid, username])
            _log(cur, pid, target, message)
            conn.commit()
            return jsonify(_full_plan(cur, pid, username))
    finally:
        conn.close()


@market_engagement_bp.post("/api/market-engagement/<plan_id>/publish")
def publish_plan(plan_id: str):
    return _transition(plan_id, "published", "Marked as published")


@market_engagement_bp.post("/api/market-engagement/<plan_id>/close")
def close_plan(plan_id: str):
    return _transition(plan_id, "closed", "Engagement closed")


@market_engagement_bp.post("/api/market-engagement/<plan_id>/convert")
def convert_plan(plan_id: str):
    return _transition(plan_id, "converted", "Handed over to the tender")


@market_engagement_bp.post("/api/market-engagement/<plan_id>/log")
def add_log(plan_id: str):
    pid = _plan_id(plan_id)
    if pid is None:
        return _error("not found", 404)
    message = _body().get("message")
    if not isinstance(message, str) or not message.strip() or len(message.strip()) > 500:
        return _error("message must be 1 to 500 characters")
    username = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if not _load_plan(cur, pid, username):
                return _error("not found", 404)
            _log(cur, pid, "note", message.strip())
            _touch(cur, pid)
            conn.commit()
            return jsonify({"log": [{k: _jsonable(v) for k, v in e.items()} for e in _load_log(cur, pid)]}), 201
    finally:
        conn.close()


@market_engagement_bp.get("/api/market-engagement/<plan_id>/handoff")
def handoff(plan_id: str):
    pid = _plan_id(plan_id)
    if pid is None:
        return _error("not found", 404)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            plan = _load_plan(cur, pid, _username())
            if not plan:
                return _error("not found", 404)
            suppliers = _load_suppliers(cur, pid)
            log = _load_log(cur, pid)
    finally:
        conn.close()
    slug = re.sub(r"[^a-z0-9]+", "-", (plan["title"] or "engagement").lower()).strip("-")[:60] or "engagement"
    return jsonify({
        "filename": f"market-engagement-{slug}.md",
        "markdown": me.build_handoff_markdown(plan, suppliers, log),
    })
