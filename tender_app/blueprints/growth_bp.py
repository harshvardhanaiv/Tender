"""Growth Studio API for TenderFlow: signals, campaigns and their results for a supplier.

The logic lives in tender_app/growth_studio.py (read its docstring for what a signal is, how fit is
scored and what is deliberately not built); this module is the HTTP layer and the SQL for the user's
own rows:

    /api/growth/options                         categories, authority types, windows, merge fields
    /api/growth/context                         company profiles, the chosen one, its saved filters, suggestions
    /api/growth/targeting          (PUT)        remember the chosen profile and its filters
    /api/growth/signals                         ranked signals for a profile and filters
    /api/growth/signals/state      (PUT)        dismiss or restore a signal
    /api/growth/suppressions       (GET, DELETE) the do-not-contact list
    /api/growth/campaigns          (GET, POST)  list; create from signals
    /api/growth/campaigns/<id>     (GET, PATCH, DELETE)
    /api/growth/campaigns/<id>/targets          (POST) add signals;  .../<tid> (PATCH, DELETE)
    /api/growth/campaigns/<id>/mark-sent (POST) record that the campaign was sent
    /api/growth/campaigns/<id>/preview   (POST) the message for one target; unsaved text allowed
    /api/growth/campaigns/<id>/draft     (POST) a first draft: template (free) or AI (credits)
    /api/growth/campaigns/<id>/export           CSV, Mailchimp audience, Mailchimp message
    /api/growth/performance                     tiles, funnel and campaign -> Pipeline outcomes

Every campaign, target and state row belongs to the user who made it and is always looked up by
username, so one user can never read or change another's (a miss is a 404, never a 403). Nothing is
sent from here. Mutating requests are CSRF-protected by the app-wide hook.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

from flask import Blueprint, Response, jsonify, request, session

from tender_app import growth_studio as gs
from tender_app import market_radar as mr
from tender_app.config import CREDIT_COST_GROWTH_DRAFT, RATE_LIMIT_AI, RATE_LIMIT_SEARCH
from tender_app.metering import require_credits
from tender_app.security import rate_limit

growth_bp = Blueprint("growth", __name__)

_get_db = None

PREF_PROFILE = "growth_profile"
PREF_TARGETING = "growth_targeting_"  # + the company profile id (0 when the user has none)
MAX_CAMPAIGNS = 200
MAX_TARGETS = 500
MAX_KEYS = 200
PERFORMANCE_WINDOWS = {"30": 30, "90": 90, "365": 365, "all": None}
SIGNAL_KEY_PREFIXES = tuple(f"{t}:" for t in gs.SIGNAL_TYPES)


def init_growth_blueprint(get_db_connection):
    global _get_db
    _get_db = get_db_connection
    return growth_bp


def _connect():
    if _get_db is None:
        raise RuntimeError("Growth Studio blueprint not initialised with a database connection")
    return _get_db()


class _Http(Exception):
    """An early exit with a message and a status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


@growth_bp.errorhandler(_Http)
def _http_error(exc: _Http):
    return _error(exc.message, exc.status)


@growth_bp.errorhandler(gs.ValidationError)
@growth_bp.errorhandler(mr.CategoryError)
def _bad_request(exc: Exception):
    return _error(str(exc))


def _username() -> str:
    return (session.get("username") or "").strip()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _json_body() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise _Http("The request body must be a JSON object")
    return body


def _optional_id(raw: Any, field: str = "profile_id") -> int | None:
    if raw in (None, "", 0, "0"):
        return None
    if isinstance(raw, bool):
        raise gs.ValidationError(f"{field} must be a whole number")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise gs.ValidationError(f"{field} must be a whole number") from None
    if value <= 0:
        raise gs.ValidationError(f"{field} must be a whole number")
    return value


def _flag(raw: Any) -> bool:
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


# ── per-user preferences (user_prefs), as the buyer workspace keeps its own ─────────────────────

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


# ── company profiles ────────────────────────────────────────────────────────────────────────────

def _profile_dict(row) -> dict[str, Any]:
    try:
        meta = json.loads(row[3] or "{}")
    except ValueError:
        meta = {}
    return {"id": row[0], "name": row[1] or "", "text": row[2] or "", "meta": meta if isinstance(meta, dict) else {}, "is_default": bool(row[4])}


def _load_profile(conn, username: str, profile_id: int | None) -> dict[str, Any] | None:
    """The user's own profile with this id, or their default one when no id is given. None if there is none."""
    with conn.cursor() as cur:
        if profile_id:
            cur.execute(
                "SELECT id, name, profile_text, meta_json, COALESCE(is_default, FALSE) FROM company_profiles WHERE id = %s AND username = %s",
                (profile_id, username),
            )
        else:
            cur.execute(
                """SELECT id, name, profile_text, meta_json, COALESCE(is_default, FALSE) FROM company_profiles
                   WHERE username = %s ORDER BY is_default DESC, id ASC LIMIT 1""",
                (username,),
            )
        row = cur.fetchone()
    return _profile_dict(row) if row else None


def _profile_for(conn, username: str, raw_id: Any) -> dict[str, Any] | None:
    profile_id = _optional_id(raw_id)
    profile = _load_profile(conn, username, profile_id)
    if profile_id and not profile:
        raise _Http("That company profile was not found", 404)
    return profile


def _keywords(profile: dict[str, Any] | None) -> frozenset[str]:
    return gs.profile_keywords(profile["text"], profile["meta"]) if profile else frozenset()


def _cpv_labels(source) -> dict[str, str] | None:
    if (source.get("cpv") or "").strip():
        labels, _ = mr.load_cpv_labels(_connect)
        return labels
    return None


# ── what a user has done with signals: dismissed, opted out, already in a campaign ──────────────

def _dismissed(conn, username: str, pid: int) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT signal_key FROM growth_signal_state WHERE username = %s AND profile_id = %s AND state = 'dismissed'",
            (username, pid),
        )
        return {r[0] for r in cur.fetchall()}


def _opted_out(conn, username: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT buyer_key FROM growth_suppressions WHERE username = %s", (username,))
        return {r[0] for r in cur.fetchall()}


def _campaigns_by_buyer(conn, username: str, pid: int) -> dict[str, list[dict[str, Any]]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT t.buyer_key, c.id, c.name
               FROM growth_campaign_targets t JOIN growth_campaigns c ON c.id = t.campaign_id
               WHERE c.username = %s AND COALESCE(c.profile_id, 0) = %s AND c.status <> 'completed'
               ORDER BY c.id""",
            (username, pid),
        )
        out: dict[str, list[dict[str, Any]]] = {}
        for key, cid, name in cur.fetchall():
            out.setdefault(key, []).append({"id": cid, "name": name})
        return out


def _signals_for(conn, username: str, profile: dict[str, Any] | None, f: gs.Filters) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    built = gs.build_signals(_connect, f, _keywords(profile), username)
    pid = profile["id"] if profile else 0
    signals = gs.apply_user_state(
        built["signals"], _dismissed(conn, username, pid), _opted_out(conn, username), _campaigns_by_buyer(conn, username, pid)
    )
    return signals, built["notes"]


# ── options, context, targeting ─────────────────────────────────────────────────────────────────

@growth_bp.get("/api/growth/options")
def growth_options():
    return jsonify({
        "presets": [{"preset": p["id"], "label": p["label"], "hint": p["hint"]} for p in mr.CATEGORY_PRESETS],
        "authority_types": [{"id": i, "label": label} for i, label in mr.AUTHORITY_TYPES],
        "signal_types": [{"id": t, "label": gs.SIGNAL_LABELS[t]} for t in gs.SIGNAL_TYPES],
        "day_choices": [90, 120, 180, 270, 365],
        "default_days": gs.DEFAULT_DAYS,
        "channels": [{"id": k, "label": v} for k, v in gs.CHANNELS.items()],
        "campaign_statuses": list(gs.CAMPAIGN_STATUSES),
        "target_statuses": list(gs.TARGET_STATUSES),
        "merge_fields": [{"name": m["name"], "label": m["label"], "types": list(m["types"])} for m in gs.MERGE_FIELDS],
        "opt_out_line": gs.OPT_OUT_LINE,
        "credit_cost_draft": CREDIT_COST_GROWTH_DRAFT,
        "max_per_type": gs.MAX_PER_TYPE,
        "limits": {"campaigns": MAX_CAMPAIGNS, "targets": MAX_TARGETS},
    })


@growth_bp.get("/api/growth/context")
def growth_context():
    """The user's profiles and the filters saved for one of them: the one asked for (?profile_id=, which
    stores nothing), else the one used last, else the default."""
    username = _username()
    wanted = _optional_id(request.args.get("profile_id"))
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, profile_text, meta_json, COALESCE(is_default, FALSE) FROM company_profiles
                   WHERE username = %s ORDER BY is_default DESC, name ASC""",
                (username,),
            )
            profiles = [_profile_dict(r) for r in cur.fetchall()]
        if wanted and not any(p["id"] == wanted for p in profiles):
            raise _Http("That company profile was not found", 404)
        chosen_raw = str(wanted) if wanted else _get_pref(conn, username, PREF_PROFILE)
        chosen = next((p for p in profiles if str(p["id"]) == chosen_raw), None) or (profiles[0] if profiles else None)
        saved = None
        raw = _get_pref(conn, username, f"{PREF_TARGETING}{chosen['id'] if chosen else 0}")
        if raw:
            try:
                saved = gs.filters_from(json.loads(raw), _cpv_labels(json.loads(raw)))
            except (ValueError, TypeError, AttributeError, mr.CategoryError):
                saved = None
    finally:
        conn.close()
    return jsonify({
        "profiles": [
            {"id": p["id"], "name": p["name"], "is_default": p["is_default"], "has_description": len(p["text"].strip()) >= 20}
            for p in profiles
        ],
        "profile_id": chosen["id"] if chosen else None,
        "filters": saved.to_request() if saved else None,
        "category": saved.category.to_public() if saved else None,
        "suggestions": gs.suggest_presets(_keywords(chosen)),
    })


@growth_bp.put("/api/growth/targeting")
def growth_save_targeting():
    body = _json_body()
    raw_filters = body.get("filters")
    if not isinstance(raw_filters, dict):
        raise _Http("filters must be an object")
    f = gs.filters_from(raw_filters, _cpv_labels(raw_filters))
    username = _username()
    conn = _connect()
    try:
        profile = _profile_for(conn, username, body.get("profile_id"))  # no id means the default profile, as everywhere else
        profile_id = profile["id"] if profile else None
        _set_pref(conn, username, PREF_PROFILE, str(profile_id) if profile_id else None)
        _set_pref(conn, username, f"{PREF_TARGETING}{profile_id or 0}", json.dumps(f.to_request()))
    finally:
        conn.close()
    return jsonify({"filters": f.to_request(), "category": f.category.to_public()})


# ── signals ─────────────────────────────────────────────────────────────────────────────────────

SIGNAL_NOTES = {
    "fit": "Fit is a rules-based score out of 100 (timing, category, your profile, evidence). It is not AI and not "
           "a chance of winning: open a signal's score to see where every point came from.",
    "renewal": "Renewal dates come from award notices and may not include extension options, so an ending contract "
               "is a reason to get in touch, not a promise that it will be re-tendered.",
    "values": "Values are the figures published in award notices. A framework's value is shared by every appointed "
              "supplier, so it is never shown, and planning approvals publish no value at all.",
    "where": "The place filter matches the buyer's name (or, for planning approvals, the authority, region and "
             "address): award notices carry no reliable buyer location.",
}


@growth_bp.get("/api/growth/signals")
@rate_limit(RATE_LIMIT_SEARCH)
def growth_signals():
    username = _username()
    f = gs.filters_from(request.args, _cpv_labels(request.args))
    show_dismissed = _flag(request.args.get("show_dismissed"))
    conn = _connect()
    try:
        profile = _profile_for(conn, username, request.args.get("profile_id"))
        signals, notes = _signals_for(conn, username, profile, f)
    finally:
        conn.close()
    dismissed = [s for s in signals if s["state"]["dismissed"]]
    live = [s for s in signals if not s["state"]["dismissed"]]
    visible = signals if show_dismissed else live
    counts = Counter(s["type"] for s in live)
    shown: list[dict[str, Any]] = []
    shown_by_type: Counter = Counter()
    for s in visible:  # best fit first; each type keeps its own best few so a big one cannot crowd out the rest
        if shown_by_type[s["type"]] < gs.MAX_PER_TYPE:
            shown_by_type[s["type"]] += 1
            shown.append(s)
    return jsonify({
        "filters": f.to_public(),
        "profile": {"id": profile["id"], "name": profile["name"], "has_description": len(profile["text"].strip()) >= 20} if profile else None,
        "counts": {t: counts.get(t, 0) for t in gs.SIGNAL_TYPES} | {"total": len(live), "dismissed": len(dismissed)},
        "signals": shown,
        "shown": len(shown),
        "shown_by_type": {t: shown_by_type.get(t, 0) for t in gs.SIGNAL_TYPES},
        "total": len(visible),
        "notes": {k: v for k, v in SIGNAL_NOTES.items() if k != "renewal" or "renewal" in f.types} | {
            "development": notes.get("development"),
            "truncated": "More contracts matched than could be read, so the soonest-ending are shown. Narrow the filters to see the rest."
            if notes.get("truncated") else None,
        },
        "computed_at": _now().isoformat(timespec="seconds") + "Z",
    })


@growth_bp.put("/api/growth/signals/state")
def growth_signal_state():
    body = _json_body()
    key = body.get("key")
    if not isinstance(key, str) or not key.startswith(SIGNAL_KEY_PREFIXES) or len(key) > 300:
        raise _Http("key must be the key of a signal")
    dismissed = body.get("dismissed")
    if not isinstance(dismissed, bool):
        raise _Http("dismissed must be true or false")
    username = _username()
    conn = _connect()
    try:
        profile = _profile_for(conn, username, body.get("profile_id"))  # no id means the default profile, as in the list
        profile_id = profile["id"] if profile else None
        with conn.cursor() as cur:
            if dismissed:
                cur.execute(
                    """INSERT INTO growth_signal_state (username, profile_id, signal_key, state, updated_at)
                       VALUES (%s, %s, %s, 'dismissed', NOW())
                       ON CONFLICT (username, profile_id, signal_key) DO UPDATE SET state = 'dismissed', updated_at = NOW()""",
                    (username, profile_id or 0, key),
                )
            else:
                cur.execute(
                    "DELETE FROM growth_signal_state WHERE username = %s AND profile_id = %s AND signal_key = %s",
                    (username, profile_id or 0, key),
                )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"key": key, "dismissed": dismissed})


@growth_bp.get("/api/growth/suppressions")
def growth_suppressions():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT buyer_key, buyer_name, reason, created_at FROM growth_suppressions WHERE username = %s ORDER BY created_at DESC",
                (_username(),),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return jsonify({"suppressions": [{"key": k, "name": n, "reason": r, "created_at": _iso(c)} for k, n, r, c in rows]})


@growth_bp.delete("/api/growth/suppressions")
def growth_remove_suppression():
    key = (request.args.get("key") or "").strip()
    if not key:
        raise _Http("key is required")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM growth_suppressions WHERE username = %s AND buyer_key = %s", (_username(), key))
            removed = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    if not removed:
        raise _Http("That buyer is not on your do-not-contact list", 404)
    return jsonify({"removed": key})


# ── campaigns: reading ──────────────────────────────────────────────────────────────────────────

CAMPAIGN_COLUMNS = (
    "id, username, profile_id, profile_name, name, filters_json, category_label, channel, status, subject, body, "
    "created_at, updated_at, last_activity_at"
)
TARGET_COLUMNS = (
    "id, campaign_id, buyer_key, buyer_name, signal_key, signal_type, snapshot_json, included, contact_email, status, "
    "sent_at, replied_at, meeting_at, note, created_at"
)


def _campaign_dict(row) -> dict[str, Any]:
    keys = CAMPAIGN_COLUMNS.replace("\n", " ").split(", ")
    return dict(zip(keys, row))


def _target_dict(row) -> dict[str, Any]:
    t = dict(zip(TARGET_COLUMNS.split(", "), row))
    try:
        snapshot = json.loads(t.pop("snapshot_json") or "{}")
    except ValueError:
        snapshot = {}
    t["snapshot"] = snapshot if isinstance(snapshot, dict) else {}
    return t


def _public_campaign(c: dict[str, Any]) -> dict[str, Any]:
    try:
        filters = json.loads(c["filters_json"]) if c.get("filters_json") else None
    except ValueError:
        filters = None
    return {
        "id": c["id"], "name": c["name"], "profile_id": c["profile_id"], "profile_name": c["profile_name"],
        "filters": filters, "category_label": c["category_label"],
        "channel": c["channel"], "channel_label": gs.CHANNELS.get(c["channel"], c["channel"]), "status": c["status"],
        "subject": c["subject"], "body": c["body"],
        "created_at": _iso(c["created_at"]), "updated_at": _iso(c["updated_at"]), "last_activity_at": _iso(c["last_activity_at"]),
    }


def _public_target(t: dict[str, Any], opted_out: set[str]) -> dict[str, Any]:
    snap = t["snapshot"]
    email = t["contact_email"]
    return {
        "id": t["id"], "buyer_key": t["buyer_key"], "buyer_name": t["buyer_name"], "signal_key": t["signal_key"],
        "signal_type": t["signal_type"], "signal_label": gs.SIGNAL_LABELS.get(t["signal_type"], t["signal_type"]),
        "headline": snap.get("headline"), "detail": snap.get("detail"), "date": snap.get("date"), "fit": snap.get("fit"),
        "links": snap.get("links") or [], "included": t["included"], "contact_email": email,
        "personal_email": bool(email) and gs.looks_personal_email(email),
        "status": t["status"], "opted_out": t["status"] == "opted_out" or t["buyer_key"] in opted_out,
        "sent_at": _iso(t["sent_at"]), "replied_at": _iso(t["replied_at"]), "meeting_at": _iso(t["meeting_at"]), "note": t["note"],
    }


def _load_campaign(conn, username: str, cid: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {CAMPAIGN_COLUMNS} FROM growth_campaigns WHERE id = %s AND username = %s", (cid, username))
        row = cur.fetchone()
        if not row:
            raise _Http("Campaign not found", 404)
        cur.execute(f"SELECT {TARGET_COLUMNS} FROM growth_campaign_targets WHERE campaign_id = %s ORDER BY id", (cid,))
        targets = [_target_dict(r) for r in cur.fetchall()]
    return _campaign_dict(row), targets


def _sender_for(conn, username: str, campaign: dict[str, Any]) -> dict[str, str]:
    profile = _load_profile(conn, username, campaign["profile_id"]) if campaign["profile_id"] else None
    return gs.sender_fields(
        profile["name"] if profile else campaign["profile_name"],
        profile["text"] if profile else None,
        profile["meta"] if profile else None,
        campaign["category_label"] or "",
    )


def _detail(conn, username: str, cid: int, **extra: Any) -> dict[str, Any]:
    campaign, targets = _load_campaign(conn, username, cid)
    opted = _opted_out(conn, username)
    return {
        "campaign": _public_campaign(campaign),
        "targets": [_public_target(t, opted) for t in targets],
        "sender": _sender_for(conn, username, campaign),
        "warnings": gs.message_warnings(campaign["subject"], campaign["body"]),
        "eligible": len(gs.eligible_targets(targets, opted)),
        **extra,
    }


def _touch(cur, cid: int) -> None:
    cur.execute("UPDATE growth_campaigns SET updated_at = NOW(), last_activity_at = NOW() WHERE id = %s", (cid,))


@growth_bp.get("/api/growth/campaigns")
def growth_campaigns():
    profile_id = _optional_id(request.args.get("profile_id"))
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT {", ".join("c." + c for c in CAMPAIGN_COLUMNS.split(", "))},
                           COUNT(t.id) FILTER (WHERE t.included), COUNT(t.sent_at), COUNT(t.replied_at), COUNT(t.meeting_at)
                    FROM growth_campaigns c LEFT JOIN growth_campaign_targets t ON t.campaign_id = c.id
                    WHERE c.username = %s {"AND c.profile_id = %s" if profile_id else ""}
                    GROUP BY c.id ORDER BY c.updated_at DESC, c.id DESC""",
                (_username(), profile_id) if profile_id else (_username(),),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    n = len(CAMPAIGN_COLUMNS.split(", "))
    out = []
    for row in rows:
        c = _public_campaign(_campaign_dict(row[:n]))
        c["counts"] = {"targets": row[n], "sent": row[n + 1], "replied": row[n + 2], "meetings": row[n + 3]}
        del c["subject"], c["body"]
        out.append(c)
    return jsonify({"campaigns": out})


@growth_bp.get("/api/growth/campaigns/<int:cid>")
def growth_campaign(cid: int):
    conn = _connect()
    try:
        return jsonify(_detail(conn, _username(), cid))
    finally:
        conn.close()


# ── campaigns: creating and changing ────────────────────────────────────────────────────────────

def _resolve_picks(conn, username: str, body: dict[str, Any], existing: set[str] | frozenset = frozenset()):
    """The signals the user picked, re-read from the data (never trusted from the browser), split into
    those that can become targets and those that cannot, with the reason."""
    keys = body.get("keys")
    if (not isinstance(keys, list) or not keys or len(keys) > MAX_KEYS
            or not all(isinstance(k, str) and 0 < len(k) <= 300 for k in keys)):
        raise _Http("Choose at least one signal")
    raw_filters = body.get("filters")
    if not isinstance(raw_filters, dict):
        raise _Http("filters must be an object")
    f = gs.filters_from(raw_filters, _cpv_labels(raw_filters))
    profile = _profile_for(conn, username, body.get("profile_id"))
    signals, _ = _signals_for(conn, username, profile, f)
    by_key = {s["key"]: s for s in signals}
    picked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen = set(existing)
    for key in dict.fromkeys(keys):
        s = by_key.get(key)
        if s is None:
            skipped.append({"key": key, "reason": "This signal is no longer in the list. Refresh and try again."})
        elif not s["target"]["targetable"]:
            skipped.append({"key": key, "buyer": s["buyer"], "reason": s["target"]["reason"]})
        elif s["target"]["key"] in seen:
            skipped.append({"key": key, "buyer": s["buyer"], "reason": "This buyer is already in the campaign."})
        else:
            seen.add(s["target"]["key"])
            picked.append(s)
    picked.sort(key=lambda s: -s["fit"])
    return f, profile, picked, skipped


def _snapshot(signal: dict[str, Any]) -> str:
    snap = {k: v for k, v in signal.items() if k != "state"}
    snap["contracts"] = (snap.get("contracts") or [])[:5]
    return json.dumps(snap)


def _insert_targets(cur, cid: int, picked: list[dict[str, Any]]) -> None:
    for s in picked:
        cur.execute(
            """INSERT INTO growth_campaign_targets (campaign_id, buyer_key, buyer_name, signal_key, signal_type, snapshot_json)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (campaign_id, buyer_key) DO NOTHING""",
            (cid, s["target"]["key"], s["target"]["name"], s["key"], s["type"], _snapshot(s)),
        )


def _dominant_type(types: list[str]) -> str:
    if not types:
        return "renewal"
    counts = Counter(types)
    return max(gs.SIGNAL_TYPES, key=lambda t: (counts.get(t, 0), -gs.SIGNAL_TYPES.index(t)))


@growth_bp.post("/api/growth/campaigns")
def growth_create_campaign():
    body = _json_body()
    username = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM growth_campaigns WHERE username = %s", (username,))
            if cur.fetchone()[0] >= MAX_CAMPAIGNS:
                raise _Http(f"You have reached the limit of {MAX_CAMPAIGNS} campaigns. Delete some you no longer need.", 409)
        f, profile, picked, skipped = _resolve_picks(conn, username, body)
        if not picked:
            raise _Http("None of the chosen signals can be added: " + "; ".join(sorted({s["reason"] for s in skipped})), 400)
        types = [s["type"] for s in picked]
        kind = _dominant_type(types)
        sender = gs.sender_fields(profile["name"] if profile else None, profile["text"] if profile else None,
                                  profile["meta"] if profile else None, f.category.label)
        message = gs.default_message(kind, sender)
        fallback = f"{f.category.label}: {gs.SIGNAL_PLURALS[kind]}, {date.today().strftime('%d %b %Y').lstrip('0')}"
        fields = gs.validate_campaign({
            "name": body.get("name") or fallback[:120],
            "channel": body.get("channel") or ("portal" if set(types) == {"engagement"} else "email"),
            **message,
        })
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO growth_campaigns
                       (username, profile_id, profile_name, name, filters_json, category_label, channel, status, subject, body, last_activity_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s, NOW()) RETURNING id""",
                (username, profile["id"] if profile else None, profile["name"] if profile else None, fields["name"],
                 json.dumps(f.to_request()), f.category.label, fields["channel"], fields["subject"], fields["body"]),
            )
            cid = cur.fetchone()[0]
            _insert_targets(cur, cid, picked)
        conn.commit()
        return jsonify({**_detail(conn, username, cid), "skipped": skipped}), 201
    finally:
        conn.close()


@growth_bp.patch("/api/growth/campaigns/<int:cid>")
def growth_update_campaign(cid: int):
    fields = gs.validate_campaign(_json_body(), partial=True)
    if not fields:
        raise _Http("Nothing to change")
    conn = _connect()
    try:
        _load_campaign(conn, _username(), cid)
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE growth_campaigns SET {', '.join(k + ' = %s' for k in fields)}, updated_at = NOW(), last_activity_at = NOW() "
                "WHERE id = %s AND username = %s",
                [*fields.values(), cid, _username()],
            )
        conn.commit()
        return jsonify(_detail(conn, _username(), cid))
    finally:
        conn.close()


@growth_bp.delete("/api/growth/campaigns/<int:cid>")
def growth_delete_campaign(cid: int):
    conn = _connect()
    try:
        _load_campaign(conn, _username(), cid)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM growth_campaigns WHERE id = %s AND username = %s", (cid, _username()))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"deleted": cid})


@growth_bp.post("/api/growth/campaigns/<int:cid>/targets")
def growth_add_targets(cid: int):
    body = _json_body()
    username = _username()
    conn = _connect()
    try:
        _, existing = _load_campaign(conn, username, cid)
        f, profile, picked, skipped = _resolve_picks(conn, username, body, {t["buyer_key"] for t in existing})
        if len(existing) + len(picked) > MAX_TARGETS:
            raise _Http(f"A campaign can have at most {MAX_TARGETS} buyers", 409)
        with conn.cursor() as cur:
            _insert_targets(cur, cid, picked)
            _touch(cur, cid)
        conn.commit()
        return jsonify({**_detail(conn, username, cid), "skipped": skipped, "added": len(picked)})
    finally:
        conn.close()


def _record_opt_out(cur, username: str, target: dict[str, Any]) -> None:
    cur.execute(
        """INSERT INTO growth_suppressions (username, buyer_key, buyer_name, reason)
           VALUES (%s, %s, %s, 'opted_out') ON CONFLICT (username, buyer_key) DO NOTHING""",
        (username, target["buyer_key"], target["buyer_name"]),
    )


def _apply_status(cur, username: str, campaign: dict[str, Any], target: dict[str, Any], status: str) -> None:
    stamps = gs.status_timestamps(status, target, _now())
    cur.execute(
        "UPDATE growth_campaign_targets SET status = %s, sent_at = %s, replied_at = %s, meeting_at = %s WHERE id = %s",
        (status, stamps["sent_at"], stamps["replied_at"], stamps["meeting_at"], target["id"]),
    )
    if status == "opted_out":
        _record_opt_out(cur, username, target)
    if status in ("sent", "replied", "meeting") and campaign["status"] == "draft":
        cur.execute("UPDATE growth_campaigns SET status = 'active' WHERE id = %s", (campaign["id"],))
        campaign["status"] = "active"


@growth_bp.patch("/api/growth/campaigns/<int:cid>/targets/<int:tid>")
def growth_update_target(cid: int, tid: int):
    patch = gs.validate_target_patch(_json_body())
    if not patch:
        raise _Http("Nothing to change")
    username = _username()
    conn = _connect()
    try:
        campaign, targets = _load_campaign(conn, username, cid)
        target = next((t for t in targets if t["id"] == tid), None)
        if target is None:
            raise _Http("Target not found", 404)
        with conn.cursor() as cur:
            plain = {k: v for k, v in patch.items() if k in ("included", "contact_email", "note")}
            if plain:
                cur.execute(f"UPDATE growth_campaign_targets SET {', '.join(k + ' = %s' for k in plain)} WHERE id = %s", [*plain.values(), tid])
            if "status" in patch:
                _apply_status(cur, username, campaign, target, patch["status"])
            _touch(cur, cid)
        conn.commit()
        return jsonify(_detail(conn, username, cid))
    finally:
        conn.close()


@growth_bp.delete("/api/growth/campaigns/<int:cid>/targets/<int:tid>")
def growth_delete_target(cid: int, tid: int):
    username = _username()
    conn = _connect()
    try:
        _load_campaign(conn, username, cid)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM growth_campaign_targets WHERE id = %s AND campaign_id = %s", (tid, cid))
            if not cur.rowcount:
                raise _Http("Target not found", 404)
            _touch(cur, cid)
        conn.commit()
        return jsonify(_detail(conn, username, cid))
    finally:
        conn.close()


@growth_bp.post("/api/growth/campaigns/<int:cid>/mark-sent")
def growth_mark_sent(cid: int):
    """Record that the campaign went out (sent from the user's own mail or Mailchimp): every included buyer
    that has not been contacted, or just the ones listed. Buyers who opted out are never marked."""
    body = _json_body()
    ids = body.get("target_ids")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
        raise _Http("target_ids must be a list of whole numbers")
    username = _username()
    conn = _connect()
    try:
        campaign, targets = _load_campaign(conn, username, cid)
        opted = _opted_out(conn, username)
        pool = gs.eligible_targets(targets, opted)
        chosen = [t for t in pool if t["status"] == "not_sent"] if ids is None else [t for t in pool if t["id"] in set(ids)]
        marked = 0
        with conn.cursor() as cur:
            for t in chosen:
                if t["status"] == "not_sent":
                    _apply_status(cur, username, campaign, t, "sent")
                    marked += 1
            if marked:
                _touch(cur, cid)
        conn.commit()
        return jsonify({**_detail(conn, username, cid), "marked": marked})
    finally:
        conn.close()


# ── the message: preview, draft, export ─────────────────────────────────────────────────────────

@growth_bp.post("/api/growth/campaigns/<int:cid>/preview")
def growth_preview(cid: int):
    body = _json_body()
    fields = gs.validate_campaign({k: body[k] for k in ("subject", "body") if k in body}, partial=True)
    username = _username()
    conn = _connect()
    try:
        campaign, targets = _load_campaign(conn, username, cid)
        sender = _sender_for(conn, username, campaign)
    finally:
        conn.close()
    if not targets:
        raise _Http("Add a buyer to preview the message for")
    wanted = body.get("target_id")
    target = next((t for t in targets if t["id"] == wanted), None) if wanted is not None else next((t for t in targets if t["included"]), targets[0])
    if target is None:
        raise _Http("Target not found", 404)
    values = gs.fields_for_target(target, sender)
    raw_subject = fields.get("subject", campaign["subject"])
    raw_body = fields.get("body", campaign["body"])
    subject, missing_s, unknown_s = gs.render_template(raw_subject, values)
    text, missing_b, unknown_b = gs.render_template(raw_body, values)
    return jsonify({
        "target_id": target["id"], "buyer": target["buyer_name"], "to": target["contact_email"],
        "subject": subject, "body": text,
        "missing": sorted(set(missing_s + missing_b)), "unknown": sorted(set(unknown_s + unknown_b)),
        "warnings": gs.message_warnings(raw_subject, raw_body),
    })


_ANSWER_TOPICS = re.compile(r"accredit|certif|case stud|experience|insurance|safety|compliance|social value|sustainab", re.IGNORECASE)


def _answers_for(conn, username: str, profile_id: int | None) -> list[dict[str, str]]:
    """The few Answer Bank entries that say who the company is: accreditations, case studies, insurance."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT category, question, answer FROM answer_bank
               WHERE username = %s AND (profile_id IS NULL OR profile_id = %s)
               ORDER BY updated_at DESC LIMIT 80""",
            (username, profile_id or 0),
        )
        rows = cur.fetchall()
    return [{"category": c or "", "question": q or "", "answer": a or ""} for c, q, a in rows if _ANSWER_TOPICS.search(f"{c} {q}")][:6]


class _DraftFailed(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


@require_credits(CREDIT_COST_GROWTH_DRAFT, "growth_draft", _connect)
def _charged_ai_draft(system: str, user: str) -> dict[str, Any]:
    """The only place a draft costs credits. Raising makes require_credits refund them, so a failed
    attempt is free."""
    from server import AIServiceError, call_chat_json

    try:
        return gs.clean_ai_message(call_chat_json(system, user, max_tokens=900, temperature=0.4))
    except AIServiceError as exc:
        status = exc.status_code if isinstance(exc.status_code, int) and 400 <= exc.status_code < 600 else 502
        raise _DraftFailed(str(exc), status) from exc
    except ValueError as exc:  # no API key configured, or a reply with nothing usable
        raise _DraftFailed(str(exc), 502) from exc


@growth_bp.post("/api/growth/campaigns/<int:cid>/draft")
@rate_limit(RATE_LIMIT_AI)
def growth_draft(cid: int):
    """A first draft of the message. It is returned, not saved: the user reads it, edits it and saves it."""
    body = _json_body()
    mode = body.get("mode") or "template"
    if mode not in ("template", "ai"):
        raise _Http("mode must be template or ai")
    extra = body.get("instructions")
    if extra is not None and (not isinstance(extra, str) or len(extra) > 500):
        raise _Http("instructions must be text of at most 500 characters")
    username = _username()
    conn = _connect()
    try:
        campaign, targets = _load_campaign(conn, username, cid)
        profile = _load_profile(conn, username, campaign["profile_id"]) if campaign["profile_id"] else None
        sender = _sender_for(conn, username, campaign)
        live = [t for t in targets if t["included"]] or targets
        types = [t["signal_type"] for t in live]
        answers = _answers_for(conn, username, campaign["profile_id"]) if mode == "ai" else []
    finally:
        conn.close()
    if mode == "template":
        message = gs.default_message(_dominant_type(types), sender)
        return jsonify({**message, "mode": "template", "warnings": gs.message_warnings(message["subject"], message["body"])})
    system, user = gs.ai_prompt(sender, types, campaign["category_label"] or "", answers, profile["text"] if profile else "", extra or "")
    try:
        message = _charged_ai_draft(system, user)
    except _DraftFailed as exc:
        return _error(f"The AI draft failed and you have not been charged: {exc.message}", exc.status)
    if isinstance(message, tuple):  # require_credits answered itself: no credits, or a viewer account
        return message
    return jsonify({**message, "mode": "ai", "cost": CREDIT_COST_GROWTH_DRAFT})


def _file_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "campaign"


@growth_bp.get("/api/growth/campaigns/<int:cid>/export")
def growth_export(cid: int):
    fmt = request.args.get("format") or "csv"
    if fmt not in gs.EXPORT_FORMATS:
        raise _Http(f"format must be one of: {', '.join(gs.EXPORT_FORMATS)}")
    username = _username()
    conn = _connect()
    try:
        campaign, targets = _load_campaign(conn, username, cid)
        sender = _sender_for(conn, username, campaign)
        opted = _opted_out(conn, username)
        if not campaign["subject"].strip() or not campaign["body"].strip():
            raise _Http("Write the subject and the message before you export.")
        if fmt != "mailchimp-message" and not gs.eligible_targets(targets, opted):
            raise _Http("There is nobody to export: every buyer is switched off, has opted out or is on your do-not-contact list.")
        text = gs.build_export(targets, campaign["subject"], campaign["body"], sender, fmt, opted)
        with conn.cursor() as cur:
            _touch(cur, cid)
        conn.commit()
    finally:
        conn.close()
    is_csv = fmt != "mailchimp-message"
    filename = f"{_file_slug(campaign['name'])}-{fmt}-{date.today().isoformat()}.{'csv' if is_csv else 'txt'}"
    return Response(
        text,
        mimetype="text/csv" if is_csv else "text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


# ── performance ─────────────────────────────────────────────────────────────────────────────────

@growth_bp.get("/api/growth/performance")
def growth_performance():
    window = request.args.get("days") or "365"
    if window not in PERFORMANCE_WINDOWS:
        raise _Http(f"days must be one of: {', '.join(PERFORMANCE_WINDOWS)}")
    profile_id = _optional_id(request.args.get("profile_id"))
    username = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            scope = "AND profile_id = %s" if profile_id else ""
            args = (username, profile_id) if profile_id else (username,)
            cur.execute(f"SELECT id, name, status, last_activity_at FROM growth_campaigns WHERE username = %s {scope} ORDER BY updated_at DESC", args)
            campaigns = [{"id": r[0], "name": r[1], "status": r[2], "last_activity_at": r[3]} for r in cur.fetchall()]
            cur.execute(
                f"""SELECT t.campaign_id, t.buyer_key, t.sent_at, t.replied_at, t.meeting_at, t.included
                    FROM growth_campaign_targets t JOIN growth_campaigns c ON c.id = t.campaign_id
                    WHERE c.username = %s {"AND c.profile_id = %s" if profile_id else ""}""",
                args,
            )
            targets = [dict(zip(("campaign_id", "buyer_key", "sent_at", "replied_at", "meeting_at", "included"), r)) for r in cur.fetchall()]
            cur.execute(
                """SELECT id, tender_key, title, contracting_authority, estimated_value, stage, created_at
                   FROM pipeline WHERE username = %s AND COALESCE(contracting_authority, '') <> ''""",
                (username,),
            )
            pipeline = [dict(zip(("id", "tender_key", "title", "contracting_authority", "estimated_value", "stage", "created_at"), r)) for r in cur.fetchall()]
    finally:
        conn.close()
    matches = gs.pipeline_matches(targets, pipeline)
    summary = gs.performance_summary(campaigns, targets, matches, PERFORMANCE_WINDOWS[window])
    summary["window"] = window
    summary["notes"] = {
        "opened": "Opens are not tracked: nothing is sent from TenderFlow, so the funnel starts at the messages you "
                  "record as sent.",
        "pipeline": "A Pipeline tender counts once, for the first campaign that reached its buyer, and only when you "
                    "began tracking it after that message was sent. Value is the estimate on the Pipeline entry, "
                    "where it has one.",
    }
    return jsonify(summary)
