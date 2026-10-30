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
    /api/growth/outreach/status                 is Outreach connected, test or live key, credits
    /api/growth/outreach/connect   (POST, DELETE) connect the user's own Outreach key (or go back to the organisation's), or disconnect
    /api/growth/outreach/signin, /signup, /resend, /forgot   partner programme: sign in to or create the user's own Outreach account here
    /outreach/connected                         where Outreach sends a user after they confirm their email
    /api/growth/campaigns/<id>/outreach         (GET, POST) its Outreach campaigns; send through Outreach
    /api/growth/social/company                  the user's companies and what a post would say about the chosen one
    /api/growth/social/posts       (GET, POST)  Outreach social posts: write one (the AI), list, refresh
    /api/growth/social/posts/<id>   (PATCH)      change its publish time, have the AI write the text / picture again, or set the user's own caption / picture
    /api/growth/social/posts/<id>/decision (POST)  approve (publishes, optionally at a new time), reject or cancel a post
    /api/growth/voice/calls        (GET, POST)  Outreach AI phone calls: one number per request, list, refresh
    /api/growth/voice/calls/<id>   (GET, DELETE) one call as Outreach holds it; hang up a live call
    /api/growth/voice/scripts, /voice/lookup    saved scripts; the phone number a buyer published on a notice
    /api/growth/inbox, /inbox/<message id>       the user's emails sent through Outreach and the replies to them
                                                 (DELETE hides one for this user, POST .../restore brings it back)
    /api/growth/performance                     tiles, funnel and campaign -> Pipeline outcomes

Every campaign, target and state row belongs to the user who made it and is always looked up by
username, so one user can never read or change another's (a miss is a 404, never a 403). Nothing is
sent from here by TenderFlow itself: the Outreach send route makes an email campaign in Outreach for each
chosen buyer and approves it, and Outreach does the sending from the user's own connected mailbox. Mutating
requests are CSRF-protected by the app-wide hook.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote

from flask import Blueprint, Response, g, jsonify, request, session

from tender_app import email_rich as er
from tender_app import growth_outreach as go
from tender_app import growth_services as gsv
from tender_app import growth_studio as gs
from tender_app import mail_account as ma
from tender_app import market_radar as mr
from tender_app import outreach_channels as och
from tender_app import outreach_client as oc
from tender_app import outreach_connection as occ
from tender_app import user_mailer as um
from tender_app.config import (
    CREDIT_COST_GROWTH_DRAFT,
    MAIL_CRED_KEY,
    OUTREACH_ALLOW_LIVE,
    OUTREACH_API_BASE,
    OUTREACH_API_KEY,
    OUTREACH_CAMPAIGN_MAILBOX_FIELD,
    OUTREACH_CLIENT_ID,
    OUTREACH_CONSENT_VERSION,
    OUTREACH_DAILY_CALL_CAP,
    OUTREACH_DAILY_POST_CAP,
    OUTREACH_MAILBOX_IDS,
    OUTREACH_ORIGIN,
    OUTREACH_PARTNER_SECRET,
    OUTREACH_PUBLISHABLE_KEY,
    OUTREACH_RETURN_URL,
    OUTREACH_SEND_MAILBOX_FIELD,
    OUTREACH_TIMEOUT_SECONDS,
    RATE_LIMIT_AI,
    RATE_LIMIT_SEARCH,
)
from tender_app.metering import require_credits
from tender_app.security import rate_limit

growth_bp = Blueprint("growth", __name__)

_get_db = None
_SMTP_FACTORY = None  # tests put a fake SMTP connection here; None means a real one
_SMTP_PAUSE = um.SEND_PAUSE_SECONDS  # between the emails of one batch

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

    def __init__(self, message: str, status: int = 400, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def _error(message: str, status: int = 400, code: str | None = None):
    return jsonify({"error": message, **({"code": code} if code else {})}), status


@growth_bp.errorhandler(_Http)
def _http_error(exc: _Http):
    return _error(exc.message, exc.status, exc.code)


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


def _services_for(source) -> dict[str, dict[str, Any]] | None:
    """The custom services a request names (own ones, or ones an administrator shared), keyed "service:<id>"."""
    ids = gsv.ids_in(source.get("category") or source.get("preset"))
    if not ids:
        return None
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, cpv, keywords FROM growth_services WHERE id = ANY(%s) AND (owner = %s OR visibility = 'shared')",
                        (ids, _username()))
            return {gsv.service_id(r[0]): gsv.spec_from_row(*r) for r in cur.fetchall()}
    finally:
        conn.close()


def _filters(source) -> "gs.Filters":
    return gs.filters_from(source, _cpv_labels(source), _services_for(source))


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
    built = gs.build_signals(_connect, f, _keywords(profile), username, ceiling=gs.bid_ceiling(profile["meta"] if profile else None))
    pid = profile["id"] if profile else 0
    signals = gs.apply_user_state(
        built["signals"], _dismissed(conn, username, pid), _opted_out(conn, username), _campaigns_by_buyer(conn, username, pid)
    )
    return signals, built["notes"]


# ── options, context, targeting ─────────────────────────────────────────────────────────────────

@growth_bp.get("/api/growth/options")
def growth_options():
    return jsonify({
        "presets": [{"preset": p["id"], "label": p["label"], "hint": p["hint"], "group": mr.PRESET_GROUP_OF.get(p["id"], "Other")} for p in mr.CATEGORY_PRESETS],
        "max_services": mr.MAX_SERVICES,
        "fit_choices": [0, 50, 60, 75, 90],
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
                saved = _filters(json.loads(raw))
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
    f = _filters(raw_filters)
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
    "funding": "Recently funded companies come from Innovate UK grants on UKRI Gateway to Research. A grant is not a "
               "budget the company will spend with you, and the match is on the words in the grant description.",
}


@growth_bp.get("/api/growth/signals")
@rate_limit(RATE_LIMIT_SEARCH)
def growth_signals():
    username = _username()
    f = _filters(request.args)
    show_dismissed = _flag(request.args.get("show_dismissed"))
    conn = _connect()
    try:
        profile = _profile_for(conn, username, request.args.get("profile_id"))
        signals, notes = _signals_for(conn, username, profile, f)
    finally:
        conn.close()
    below_fit = [s for s in signals if s["fit"] < f.min_fit]
    signals = [s for s in signals if s["fit"] >= f.min_fit]
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
        "below_min_fit": len(below_fit),
        "shown": len(shown),
        "shown_by_type": {t: shown_by_type.get(t, 0) for t in gs.SIGNAL_TYPES},
        "total": len(visible),
        "notes": {k: v for k, v in SIGNAL_NOTES.items()
                  if (k != "renewal" or "renewal" in f.types) and (k != "funding" or "funding" in f.types)} | {
            "development": notes.get("development"),
            "funding": notes.get("funding"),
            "truncated": "More contracts matched than could be read, so the soonest-ending are shown. Narrow the filters to see the rest."
            if notes.get("truncated") else None,
        },
        "computed_at": _now().isoformat(timespec="seconds") + "Z",
    })


@growth_bp.get("/api/growth/services")
def growth_services_list():
    """The user's own services and the ones an administrator shared with everyone."""
    me = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, owner, name, cpv, keywords, visibility FROM growth_services "
                        "WHERE owner = %s OR visibility = 'shared' ORDER BY (owner = %s) DESC, LOWER(name)", (me, me))
            rows = cur.fetchall()
    finally:
        conn.close()
    return jsonify({"services": [gsv.public(r, me) for r in rows], "limit": gsv.MAX_PER_USER})


@growth_bp.get("/api/growth/services/search")
@rate_limit(RATE_LIMIT_SEARCH)
def growth_services_search():
    """Find any CPV branch in the awards data by its description, to use as a service."""
    labels, counts = mr.load_cpv_labels(_connect)
    return jsonify({"results": gsv.search_cpv(labels, counts, request.args.get("q") or "")})


@growth_bp.post("/api/growth/services")
@rate_limit(30, 60)
def growth_services_create():
    clean = gsv.clean_service(_json_body())
    me = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM growth_services WHERE owner = %s", (me,))
            if cur.fetchone()[0] >= gsv.MAX_PER_USER:
                raise _Http(f"You can keep {gsv.MAX_PER_USER} services. Remove one first.", 400)
            cur.execute("SELECT 1 FROM growth_services WHERE owner = %s AND LOWER(name) = LOWER(%s)", (me, clean["name"]))
            if cur.fetchone():
                raise _Http("You already have a service with that name", 409)
            cur.execute(
                """INSERT INTO growth_services (owner, name, cpv, keywords, visibility) VALUES (%s, %s, %s, %s, %s)
                   RETURNING id, owner, name, cpv, keywords, visibility""",
                (me, clean["name"], ",".join(clean["cpv"]), "|".join(clean["keywords"]), "suggested" if clean["share"] else "private"))
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return jsonify({"service": gsv.public(row, me)}), 201


@growth_bp.delete("/api/growth/services/<int:service_row_id>")
def growth_services_delete(service_row_id: int):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM growth_services WHERE id = %s AND owner = %s", (service_row_id, _username()))
            gone = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    if not gone:
        raise _Http("That service was not found", 404)
    return jsonify({"ok": True})


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
    "id, username, profile_id, profile_name, name, filters_json, category_label, channel, status, subject, body, body_html, "
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
        "subject": c["subject"], "body": c["body"], "body_html": c.get("body_html") or "",
        "created_at": _iso(c["created_at"]), "updated_at": _iso(c["updated_at"]), "last_activity_at": _iso(c["last_activity_at"]),
    }


def _public_target(t: dict[str, Any], opted_out: set[str]) -> dict[str, Any]:
    snap = gs.refresh_snapshot_days(t["snapshot"], date.today())
    email = t["contact_email"]
    return {
        "id": t["id"], "buyer_key": t["buyer_key"], "buyer_name": t["buyer_name"], "signal_key": t["signal_key"],
        "signal_type": t["signal_type"], "signal_label": gs.SIGNAL_LABELS.get(t["signal_type"], t["signal_type"]),
        "headline": snap.get("headline"), "detail": snap.get("detail"), "date": snap.get("date"), "fit": snap.get("fit"),
        "links": snap.get("links") or [], "included": t["included"], "contact_email": email,
        "personal_email": bool(email) and gs.looks_personal_email(email),
        "contact_found": snap.get("contact_found"),
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


def _files_for(conn, username: str, cid: int, with_data: bool = False) -> list[dict[str, Any]]:
    """The files attached to a campaign, oldest first. The bytes are only read when they are about to be sent."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, filename, content_type, size_bytes{', data' if with_data else ''} FROM growth_campaign_files "
            "WHERE campaign_id = %s AND username = %s ORDER BY id",
            (cid, username),
        )
        rows = cur.fetchall()
    keys = ("id", "filename", "content_type", "size_bytes", "data")
    return [dict(zip(keys, r)) for r in rows]


def _detail(conn, username: str, cid: int, **extra: Any) -> dict[str, Any]:
    campaign, targets = _load_campaign(conn, username, cid)
    opted = _opted_out(conn, username)
    return {
        "campaign": _public_campaign(campaign),
        "targets": [_public_target(t, opted) for t in targets],
        "attachments": [{"id": f["id"], "name": f["filename"], "type": f["content_type"], "size": f["size_bytes"]}
                        for f in _files_for(conn, username, cid)],
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
        del c["subject"], c["body"], c["body_html"]
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
    f = _filters(raw_filters)
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


def _identical_empty_draft(conn, username: str, category_label: str, channel: str, signal_keys: set[str],
                           profile_id: int | None, message: dict[str, Any]) -> int | None:
    """The id of this user's draft for the same service, channel, profile and signals that nothing has been done to: the message
    is still the default text, no target is sent, replied to or switched off, and no outreach draft was made. None when there is none."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT c.id, c.profile_id, c.subject, c.body, COALESCE(c.body_html, ''),
                      COALESCE(ARRAY_AGG(t.signal_key) FILTER (WHERE t.id IS NOT NULL), '{}'),
                      BOOL_OR(t.id IS NOT NULL AND (t.status <> 'not_sent' OR NOT t.included OR t.sent_at IS NOT NULL))
                 FROM growth_campaigns c LEFT JOIN growth_campaign_targets t ON t.campaign_id = c.id
                WHERE c.username = %s AND c.status = 'draft' AND c.category_label = %s AND c.channel = %s
                  AND NOT EXISTS (SELECT 1 FROM growth_outreach_drafts d WHERE d.campaign_id = c.id)
                GROUP BY c.id ORDER BY c.id""",
            (username, category_label, channel),
        )
        for cid, pid, subject, text, html, keys, touched in cur.fetchall():
            if (not touched and set(keys) == signal_keys and pid == profile_id and not html
                    and subject == message["subject"] and text == message["body"]):
                return cid
    return None


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
        name = body.get("name")
        if not name:
            # Starting the same campaign again opens the untouched draft that already exists instead of making "... (2)" (re-test 3 GS6)
            same = _identical_empty_draft(conn, username, f.category.label, body.get("channel") or ("portal" if set(types) == {"engagement"} else "email"),
                                          {s["key"] for s in picked}, profile["id"] if profile else None, message)
            if same:
                return jsonify({**_detail(conn, username, same), "skipped": skipped, "existing": True}), 200
            # an automatic name is made unique ("... (2)") so campaigns made the same day can be told apart
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM growth_campaigns WHERE username = %s", (username,))
                name = gs.unique_campaign_name(fallback[:120], [r[0] for r in cur.fetchall()])
        fields = gs.validate_campaign({
            "name": name,
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
    raw = _json_body()
    fields = gs.validate_campaign(raw, partial=True)
    wants_profile = "profile_id" in raw
    if not fields and not wants_profile:
        raise _Http("Nothing to change")
    conn = _connect()
    try:
        _load_campaign(conn, _username(), cid)
        if wants_profile:
            # the company this campaign speaks for: its name and details fill {{company_name}}, {{sender_name}} and the signature
            profile = _profile_for(conn, _username(), raw["profile_id"]) if raw["profile_id"] not in (None, "", 0, "0") else None
            fields["profile_id"] = profile["id"] if profile else None
            fields["profile_name"] = profile["name"] if profile else None
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


@growth_bp.post("/api/growth/campaigns/<int:cid>/attachments")
def growth_add_attachments(cid: int):
    """Attach one or more files (form field "files") to every email of the campaign. Each is checked on its own, so one bad
    file does not stop the others; the answer is the campaign again, with `rejected` naming what was refused and why."""
    uploads = [f for f in request.files.getlist("files") if f and f.filename]
    if not uploads:
        raise _Http("Choose a file to attach.")
    username = _username()
    conn = _connect()
    try:
        _load_campaign(conn, username, cid)
        existing = _files_for(conn, username, cid)
        rejected: list[str] = []
        with conn.cursor() as cur:
            for up in uploads:
                name = er.safe_filename(up.filename)
                data = up.read(er.MAX_FILE_BYTES + 1)
                problem = er.attachment_problem(name, len(data), existing)
                if problem:
                    rejected.append(problem)
                    continue
                ctype = er.content_type_for(name, up.mimetype)
                cur.execute(
                    "INSERT INTO growth_campaign_files (campaign_id, username, filename, content_type, size_bytes, data) "
                    "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (cid, username, name, ctype, len(data), data),
                )
                existing.append({"id": cur.fetchone()[0], "filename": name, "content_type": ctype, "size_bytes": len(data)})
            _touch(cur, cid)
        conn.commit()
        return jsonify({**_detail(conn, username, cid), "rejected": rejected})
    finally:
        conn.close()


@growth_bp.delete("/api/growth/campaigns/<int:cid>/attachments/<int:fid>")
def growth_remove_attachment(cid: int, fid: int):
    username = _username()
    conn = _connect()
    try:
        _load_campaign(conn, username, cid)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM growth_campaign_files WHERE id = %s AND campaign_id = %s AND username = %s", (fid, cid, username))
            if not cur.rowcount:
                raise _Http("That file is not attached to this campaign.", 404)
            _touch(cur, cid)
        conn.commit()
        return jsonify(_detail(conn, username, cid))
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


# ── find a buyer's contact inbox instead of sending the user to the notice ───────────────────────
_CONTACT_FRESH_DAYS = {"found": 30, "not_found": 7}
_CONTACT_LOOKUP_VERSION = 2  # 2: notice read with a browser agent, notice website and council domains tried (1 missed most councils)


def _contact_search(query: str) -> list[dict]:
    from tender_app.blueprints.profiles_bp import search_duckduckgo
    return search_duckduckgo(query, max_results=6)


def _cached_buyer_contact(cur, buyer_key: str) -> dict[str, Any] | None:
    cur.execute("SELECT status, email, candidates, source, source_url, website, checked_at, lookup_version FROM buyer_contact_cache WHERE buyer_key = %s", (buyer_key,))
    row = cur.fetchone()
    if not row:
        return None
    status, email, candidates, source, source_url, website, checked_at, version = row
    try:
        cands = json.loads(candidates) if candidates else []
    except ValueError:
        cands = []
    from datetime import datetime, timedelta
    fresh = bool(checked_at) and (version or 1) >= _CONTACT_LOOKUP_VERSION and datetime.utcnow() - checked_at < timedelta(days=_CONTACT_FRESH_DAYS.get(status, 7))
    return {"fresh": fresh, "status": status, "email": email, "candidates": cands, "source": source,
            "source_url": source_url, "website": website}


@growth_bp.post("/api/growth/campaigns/<int:cid>/targets/<int:tid>/find-contact")
@rate_limit(30, 60)
def growth_find_contact(cid: int, tid: int):
    """Look the buyer's published procurement inbox up (the notice first, then the buyer's website) and put it in the
    contact box if that is still empty. A named person's address is never filled in automatically."""
    from tender_app import buyer_contacts as bcn
    from tender_app.db_ext import create_buyer_contact_cache
    username = _username()
    conn = _connect()
    try:
        campaign, targets = _load_campaign(conn, username, cid)
        target = next((t for t in targets if t["id"] == tid), None)
        if target is None:
            raise _Http("Target not found", 404)
        opted = _opted_out(conn, username)
        if target["status"] == "opted_out" or target["buyer_key"] in opted:
            return jsonify(_detail(conn, username, cid, find_contact={"target_id": tid, "status": "skipped"}))
        if target["contact_email"] and request.args.get("force") != "1":
            return jsonify(_detail(conn, username, cid, find_contact={"target_id": tid, "status": "already"}))
        with conn.cursor() as cur:
            create_buyer_contact_cache(cur)
            conn.commit()
            result = _cached_buyer_contact(cur, target["buyer_key"])
            if not result or not result["fresh"] or request.args.get("force") == "1":
                urls = [l.get("url") for l in (target["snapshot"].get("links") or []) if isinstance(l, dict)]
                found = bcn.find_buyer_contact(target["buyer_name"], urls, search=_contact_search)
                cur.execute(
                    """INSERT INTO buyer_contact_cache (buyer_key, status, email, candidates, source, source_url, website, checked_at, lookup_version)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, (NOW() AT TIME ZONE 'utc'), %s)
                       ON CONFLICT (buyer_key) DO UPDATE SET status = EXCLUDED.status, email = EXCLUDED.email,
                           candidates = EXCLUDED.candidates, source = EXCLUDED.source, source_url = EXCLUDED.source_url,
                           website = EXCLUDED.website, checked_at = EXCLUDED.checked_at, lookup_version = EXCLUDED.lookup_version""",
                    (target["buyer_key"], found["status"], found.get("email"), json.dumps(found.get("candidates") or []),
                     found.get("source"), found.get("source_url"), found.get("website"), _CONTACT_LOOKUP_VERSION),
                )
                conn.commit()
                result = {"status": found["status"], "email": found.get("email"), "candidates": found.get("candidates") or [],
                          "source": found.get("source"), "source_url": found.get("source_url")}
            best = (result["candidates"] or [None])[0]
            status = "not_found"
            if result["status"] == "found" and best:
                if best.get("role") and gs.valid_email(best["email"]):
                    snapshot = dict(target["snapshot"])
                    snapshot["contact_found"] = {"source": best.get("source"), "url": best.get("source_url")}
                    cur.execute(
                        "UPDATE growth_campaign_targets SET contact_email = %s, snapshot_json = %s "
                        "WHERE id = %s AND campaign_id = %s AND (contact_email IS NULL OR contact_email = '' OR %s)",
                        (best["email"], json.dumps(snapshot), tid, cid, request.args.get("force") == "1"),
                    )
                    _touch(cur, cid)
                    conn.commit()
                    status = "found"
                else:
                    status = "personal_only"
        fc: dict[str, Any] = {"target_id": tid, "status": status}
        if status == "personal_only" and best and gs.valid_email(best.get("email")):
            # a named person's address is never filled in by itself; it is offered, and the user decides
            fc["suggestion"] = {"email": best["email"], "source": best.get("source"), "url": best.get("source_url")}
        return jsonify(_detail(conn, username, cid, find_contact=fc))
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
    fields = gs.validate_campaign({k: body[k] for k in ("subject", "body", "body_html") if k in body}, partial=True)
    username = _username()
    conn = _connect()
    try:
        campaign, targets = _load_campaign(conn, username, cid)
        sender = _sender_for(conn, username, campaign)
        files = _files_for(conn, username, cid)
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
    raw_html = fields["body_html"] if "body_html" in fields else (None if "body" in fields else campaign.get("body_html"))
    return jsonify({
        "target_id": target["id"], "buyer": target["buyer_name"], "to": target["contact_email"],
        "subject": subject, "body": text,
        "html": er.render_html(raw_html, values) if raw_html else None,
        "attachments": [{"name": f["filename"], "size": f["size_bytes"]} for f in files],
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


# ── Outreach: email drafts only ─────────────────────────────────────────────────────────────────

def _outreach_connection() -> dict[str, Any]:
    """Which Outreach key this user works with (their own, the organisation's, or none because they disconnected), worked
    out once per request. See tender_app/outreach_connection.py."""
    username = _username()
    cached = getattr(g, "_outreach_conn", None)
    if cached is not None and cached[0] == username and cached[1] == (OUTREACH_API_KEY or ""):
        return cached[2]
    conn = _connect()
    try:
        got = occ.resolve(conn, username, OUTREACH_API_KEY, MAIL_CRED_KEY)
    finally:
        conn.close()
    g._outreach_conn = (username, OUTREACH_API_KEY or "", got)
    return got


def _outreach_problem() -> str | None:
    """Why Outreach cannot be used right now, in the user's words; None when the user's key is usable."""
    link = _outreach_connection()
    if link["error"]:
        return link["error"]
    if link["source"] == occ.DISCONNECTED:
        return "Outreach is disconnected. Connect it again from Connected apps in the sidebar."
    key = (link["key"] or "").strip()
    if not key:
        return "Outreach is not connected. Connect it from Connected apps in the sidebar."
    mode = oc.key_mode(key)
    if mode is None:
        return "The configured Outreach key is not an sk_test_ or sk_live_ key."
    if mode == "live" and not OUTREACH_ALLOW_LIVE:
        return "A live Outreach key is configured but live sending is switched off. Use an sk_test_ key."
    return None


def _outreach_client() -> "oc.OutreachClient":
    problem = _outreach_problem()
    if problem:
        raise _Http(problem, 409)
    return oc.OutreachClient(_outreach_connection()["key"], OUTREACH_API_BASE, timeout=OUTREACH_TIMEOUT_SECONDS)


def _outreach_status_payload() -> dict[str, Any]:
    link = _outreach_connection()
    key = (link["key"] or "").strip()
    mode = oc.key_mode(key)
    out: dict[str, Any] = {
        "configured": bool(key), "mode": mode, "live_allowed": bool(OUTREACH_ALLOW_LIVE),
        "key_hint": oc.mask_key(key) if mode else link.get("hint"), "ready": False, "wallet": None,
        "problem": _outreach_problem(), "max_per_push": go.MAX_PER_PUSH, "call_cap": OUTREACH_DAILY_CALL_CAP,
        # how this user is connected, and what they can do about it from the Connections window
        "source": link["source"], "shared_available": bool((OUTREACH_API_KEY or "").strip()),
        "can_store_key": bool((MAIL_CRED_KEY or "").strip()), "via": link.get("via"),
        "partner_enabled": bool(OUTREACH_CLIENT_ID), "partner_signup": bool(OUTREACH_CLIENT_ID and OUTREACH_CONSENT_VERSION),
        "partner_consent_version": bool(OUTREACH_CONSENT_VERSION),  # Outreach wants it with every sign-in and sign-up
        # the user's own email account (set up in Connections), the other way to send from Growth Studio
        # (mail_account is the default one; mail_accounts is all of them, the default first)
        "mail_available": _mail_available(), "mail_accounts": _mail_accounts_summary(),
    }
    out["mail_account"] = out["mail_accounts"][0] if out["mail_accounts"] else None
    if out["problem"]:
        return out
    try:
        out["wallet"] = oc.wallet_summary(_outreach_client().wallets())
        oc.apply_mailbox_ids(out["wallet"]["mailboxes"], oc.parse_mailbox_ids(OUTREACH_MAILBOX_IDS))
        # a mailbox with no id can only be chosen when Outreach's sending fields take an address
        out["wallet"]["select_by_address"] = go.selects_by_address(OUTREACH_SEND_MAILBOX_FIELD, OUTREACH_CAMPAIGN_MAILBOX_FIELD)
        out["ready"] = True
    except oc.OutreachError as exc:
        out["problem"] = exc.message
    return out


@growth_bp.get("/api/growth/outreach/status")
def growth_outreach_status():
    """Whether Growth Studio can hand drafts to Outreach. Never returns the key, only its first characters."""
    return jsonify(_outreach_status_payload())


@growth_bp.post("/api/growth/outreach/connect")
@rate_limit(10, 60)
def growth_outreach_connect():
    """Connect Outreach for this user. Body {"api_key": "sk_test_..."}: their own key, checked with Outreach before it is
    kept (encrypted) and never returned; or {"use_shared": true}: the organisation's connection from the server settings.
    Answers with the new status."""
    body = _json_body()
    username = _username()
    if body.get("use_shared") is True:
        if not (OUTREACH_API_KEY or "").strip():
            raise _Http("The server has no Outreach connection of its own. Paste your own key.", 409)
        conn = _connect()
        try:
            occ.use_shared(conn, username)
        finally:
            conn.close()
        g._outreach_conn = None
        return jsonify(_outreach_status_payload())
    key = str(body.get("api_key") or "").strip()
    bad = occ.key_problem(key, bool(OUTREACH_ALLOW_LIVE))
    if bad:
        raise _Http(bad, 400)
    if not (MAIL_CRED_KEY or "").strip():
        raise _Http("Saving your own key is not enabled on this server. Ask your administrator to set MAIL_CRED_KEY, "
                    "or use the organisation's connection.", 409)
    try:
        oc.wallet_summary(oc.OutreachClient(key, OUTREACH_API_BASE, timeout=OUTREACH_TIMEOUT_SECONDS).wallets())
    except oc.OutreachError as exc:
        raise _Http(exc.message if exc.status != 401 else "Outreach did not accept that key. Check you copied all of it.",
                    502 if exc.status >= 500 or exc.code == "unreachable" else 400)
    conn = _connect()
    try:
        occ.store_own(conn, username, key, MAIL_CRED_KEY)
    except um.MailError as exc:
        raise _Http(exc.message, 409)
    finally:
        conn.close()
    g._outreach_conn = None
    return jsonify(_outreach_status_payload())


_PARTNER_TRANSPORT = None  # tests put a fake transport here; None means the real network


def _partner_origin() -> str:
    """The web address Outreach has on its list for this app (sent as Origin): OUTREACH_ORIGIN, else this app's own."""
    return OUTREACH_ORIGIN or request.host_url.rstrip("/")


def _partner_return_url() -> str:
    return OUTREACH_RETURN_URL or _partner_origin() + "/outreach/connected"


_PARTNER_MESSAGES = {
    "bad_credentials": "That email and password do not match an Outreach account.",
    "email_not_verified": "Confirm your email first: Outreach sent you a link. Check your inbox and spam folder, then sign in.",
    "account_exists": "An Outreach account with that email already exists. Sign in instead.",
    "consent_required": "Tick the box to agree, then try again.",
    "partner_suspended": "Outreach has paused sign-in from TenderFlow. Ask your administrator.",
    "invalid_client": "TenderFlow's Outreach client ID is not valid. Ask your administrator to check OUTREACH_CLIENT_ID.",
}


def _partner_call(action: str, data: dict[str, Any]) -> dict[str, Any]:
    """Call one of Outreach's partner sign-in actions and turn a refusal into a message for the user. The password in `data`
    is only ever sent to Outreach; it is not stored, logged or put in an error."""
    if not OUTREACH_CLIENT_ID:
        raise _Http("Signing in to Outreach from here is not switched on for this server. Paste your Outreach key instead, "
                    "or ask your administrator to set OUTREACH_CLIENT_ID.", 409, "partner_disabled")
    try:
        return oc.partner_auth(action, {"client_id": OUTREACH_CLIENT_ID, **data}, base_url=OUTREACH_API_BASE, origin=_partner_origin(),
                               timeout=OUTREACH_TIMEOUT_SECONDS, transport=_PARTNER_TRANSPORT)
    except oc.OutreachError as exc:
        code = exc.code or ""
        if code in ("origin_not_allowed", "return_url_not_allowed"):
            what = "web address" if code == "origin_not_allowed" else "return address"
            value = _partner_origin() if code == "origin_not_allowed" else _partner_return_url()
            raise _Http(f"Outreach has not been told this TenderFlow {what} ({value}). Ask your administrator to add it in Outreach, "
                        "under Developer API, Partner programme.", 409, code)
        if code in _PARTNER_MESSAGES:
            raise _Http(_PARTNER_MESSAGES[code], 409 if code in ("partner_suspended", "invalid_client") else 400, code)
        raise _Http(exc.message, 502 if exc.status >= 500 or code == "unreachable" else (exc.status if exc.status in (400, 403, 409, 422, 429) else 400), code or None)


# ── Outreach screens inside Connections (embed.js) ──────────────────────────────────────────────

_EMBED_TRANSPORT = None  # tests put a fake transport here; None means the real network
EMBED_SCREENS = ("mail", "social", "voice")   # the screens Connections shows; Outreach also has leads and campaigns


def _embed_problem() -> str | None:
    """Why Outreach screens cannot be shown here, in the administrator's words; None when they can."""
    mode = oc.secret_mode(OUTREACH_PARTNER_SECRET)
    if not OUTREACH_PUBLISHABLE_KEY or not OUTREACH_PARTNER_SECRET:
        return "Outreach screens are not switched on for this server (OUTREACH_PUBLISHABLE_KEY and OUTREACH_PARTNER_SECRET)."
    if mode is None:
        return "OUTREACH_PARTNER_SECRET is not a psk_test_ or psk_live_ secret."
    if mode == "live" and not OUTREACH_ALLOW_LIVE:
        return "A live Outreach partner secret is configured but live use is switched off (OUTREACH_ALLOW_LIVE=0). Use a psk_test_ secret."
    return None


@growth_bp.get("/api/growth/embed/config")
def growth_embed_config():
    """Whether Connections can show Outreach's own screens, and what the page needs to load them. The partner secret is never
    returned: only the publishable key, which is not secret."""
    problem = _embed_problem()
    if problem:
        return jsonify({"enabled": False, "reason": problem})
    return jsonify({"enabled": True, "host": OUTREACH_API_BASE.rstrip("/"), "key": OUTREACH_PUBLISHABLE_KEY,
                    "mode": oc.secret_mode(OUTREACH_PARTNER_SECRET), "screens": list(EMBED_SCREENS)})


def _embed_refusal(exc: "oc.OutreachError") -> _Http:
    if exc.status in (401, 403) and exc.code != "embed_scope":
        return _Http("Outreach did not accept TenderFlow's partner secret. Ask your administrator to check OUTREACH_PARTNER_SECRET "
                     "(a test secret on a live integration, or the other way round, is refused).", 409, "partner_secret_rejected")
    if exc.status == 409:
        return _Http("This Outreach account is already connected to a different TenderFlow user. Disconnect it there first.", 409, "already_linked")
    if exc.status == 429:
        return _Http("Too many requests to Outreach. Wait a moment and try again.", 429, "rate_limited")
    return _Http(exc.message, 502 if exc.status >= 500 or exc.code == "unreachable" else 400, exc.code or None)


@growth_bp.post("/api/growth/embed/session")
@rate_limit(30, 60)
def growth_embed_session():
    """Body {"component": "social"|"voice"}. Asks Outreach (with the partner secret, from here only) whether this TenderFlow user is
    linked to an Outreach account. Linked: {"linked": true, "token", "expires_at"}, a 15-minute token for that one screen.
    Not linked: {"linked": false, "connect_token", "expires_at"}, for Outreach's own sign-in / create-account screen, where
    the person proves they own the account and agrees. TenderFlow never sees an Outreach password."""
    component = str(_json_body().get("component") or "").strip()
    if component not in EMBED_SCREENS:
        raise _Http("That Outreach screen is not available here.", 400)
    problem = _embed_problem()
    if problem:
        raise _Http(problem, 409, "embed_disabled")
    external_id = _username()
    common = {"base_url": OUTREACH_API_BASE, "timeout": OUTREACH_TIMEOUT_SECONDS, "transport": _EMBED_TRANSPORT}
    try:
        link = oc.embed_call("/api/embed/connect-sessions", {"external_id": external_id, "component": component},
                             secret=OUTREACH_PARTNER_SECRET, **common)
        if link.get("linked") is not True:
            token = str(link.get("connect_token") or "")
            if not token:
                raise _Http("Outreach did not return a sign-in token. Try again.", 502, "bad_response")
            return jsonify({"linked": False, "connect_token": token, "expires_at": link.get("expires_at")})
        session = oc.embed_call("/api/embed/sessions", {"external_id": external_id, "component": component, "access": "full", "ttl_seconds": 900},
                                secret=OUTREACH_PARTNER_SECRET, **common)
    except oc.OutreachError as exc:
        raise _embed_refusal(exc)
    token = str(session.get("token") or "")
    if not token:
        raise _Http("Outreach did not return a screen token. Try again.", 502, "bad_response")
    return jsonify({"linked": True, "token": token, "expires_at": session.get("expires_at"), "component": component})


@growth_bp.post("/api/growth/embed/key")
@rate_limit(10, 60)
def growth_embed_key():
    """After the user has linked their Outreach account in Outreach's own screen, fetch the API key Growth Studio sends with (server to
    server, with the partner secret) and keep it encrypted, exactly as a key from the sign-in form would be. Outreach replaces the old
    key whenever it issues a new one, so a user who already has a working key is left alone. The key is never returned to the page."""
    problem = _embed_problem()
    if problem:
        raise _Http(problem, 409, "embed_disabled")
    if _outreach_problem() is None and _outreach_connection()["source"] in ("own", "shared"):
        return jsonify(_outreach_status_payload())
    if not (MAIL_CRED_KEY or "").strip():
        raise _Http("Saving your key is not enabled on this server. Ask your administrator to set MAIL_CRED_KEY.", 409)
    username = _username()
    try:
        answer = oc.embed_call(f"/api/embed/links/{quote(username, safe='')}/key", {}, secret=OUTREACH_PARTNER_SECRET,
                               base_url=OUTREACH_API_BASE, timeout=OUTREACH_TIMEOUT_SECONDS, transport=_EMBED_TRANSPORT)
    except oc.OutreachError as exc:
        if exc.status == 404:
            raise _Http("Your Outreach account is not linked yet. Sign in with Outreach first.", 409, "not_linked")
        raise _embed_refusal(exc)
    return jsonify(_keep_partner_key(username, answer))


def _clean_field(value: Any, label: str, limit: int, required: bool = True) -> str:
    text = " ".join(str(value or "").split())[:limit]
    if required and not text:
        raise _Http(f"Enter {label}.")
    return text


def _credentials(body: dict[str, Any]) -> tuple[str, str]:
    email = _clean_field(body.get("email"), "your Outreach email", 254).lower()
    if not gs.valid_email(email):
        raise _Http("That does not look like an email address.")
    password = body.get("password")
    if not isinstance(password, str) or not password or len(password) > 200:
        raise _Http("Enter your password.")
    return email, password


def _keep_partner_key(username: str, answer: dict[str, Any]) -> dict[str, Any]:
    """Store the key a signup / login answered with (encrypted) and return the new status. The key never leaves this function."""
    key = oc.partner_key(answer)
    if not key:
        raise _Http("Outreach did not give a key for this account. Try again, or paste your key instead.", 502)
    bad = occ.key_problem(key, bool(OUTREACH_ALLOW_LIVE))
    if bad:
        raise _Http(bad, 409)
    if not (MAIL_CRED_KEY or "").strip():
        raise _Http("Saving your key is not enabled on this server. Ask your administrator to set MAIL_CRED_KEY.", 409)
    conn = _connect()
    try:
        occ.store_own(conn, username, key, MAIL_CRED_KEY, via="partner")
    except um.MailError as exc:
        raise _Http(exc.message, 409)
    finally:
        conn.close()
    g._outreach_conn = None
    return _outreach_status_payload()


@growth_bp.post("/api/growth/outreach/signin")
@rate_limit(10, 60)
def growth_outreach_signin():
    """Sign in to the user's existing Outreach account (partner programme) and keep the key it answers with. Linking an
    existing account to TenderFlow is a permission the user gives, so the sign-in carries their consent (Outreach asks for it
    with consent_required)."""
    body = _json_body()
    email, password = _credentials(body)
    if body.get("consent") is not True:
        raise _Http(_PARTNER_MESSAGES["consent_required"], 400, "consent_required")
    if OUTREACH_CLIENT_ID and not OUTREACH_CONSENT_VERSION:
        raise _Http("Outreach needs the version of the consent text with every sign-in. Ask your administrator to set "
                    "OUTREACH_CONSENT_VERSION (shown in the example call on Outreach's Partner programme tab).", 409, "consent_version_missing")
    answer = _partner_call("login", {"email": email, "password": password, "consent": True, "consent_version": OUTREACH_CONSENT_VERSION})
    return jsonify(_keep_partner_key(_username(), answer))


@growth_bp.post("/api/growth/outreach/signup")
@rate_limit(5, 60)
def growth_outreach_signup():
    """Create an Outreach account for the user (partner programme). With email confirmation on, Outreach answers
    `verify_email` and no key: the user confirms, then signs in."""
    body = _json_body()
    if not (OUTREACH_CLIENT_ID and OUTREACH_CONSENT_VERSION):
        raise _Http("Creating an Outreach account from here is not switched on for this server. Ask your administrator to set "
                    "OUTREACH_CLIENT_ID and OUTREACH_CONSENT_VERSION.", 409, "partner_disabled")
    if body.get("consent") is not True:
        raise _Http(_PARTNER_MESSAGES["consent_required"], 400, "consent_required")
    company = _clean_field(body.get("company"), "your company name", 120)
    name = _clean_field(body.get("name"), "your name", 120)
    email, password = _credentials(body)
    if len(password) < 8:
        raise _Http("Choose a password of at least 8 characters.")
    answer = _partner_call("signup", {"company": company, "name": name, "email": email, "password": password, "consent": True,
                                      "consent_version": OUTREACH_CONSENT_VERSION, "return_url": _partner_return_url()})
    if oc.partner_needs_verification(answer) and not oc.partner_key(answer):
        return jsonify({**_outreach_status_payload(), "verify_email": True, "email": email})
    return jsonify(_keep_partner_key(_username(), answer))


@growth_bp.post("/api/growth/outreach/resend")
@rate_limit(5, 60)
def growth_outreach_resend():
    """Ask Outreach to send the confirmation email again (it decides whether there is one to send)."""
    email = _clean_field(_json_body().get("email"), "your Outreach email", 254).lower()
    if not gs.valid_email(email):
        raise _Http("That does not look like an email address.")
    # return_url: the link in the email sends the user back here once they confirm. Outreach answers ok whether or not it sent
    # anything (so nobody can learn who has an account); it only sends for an account made through TenderFlow's sign-up that is
    # not confirmed yet, at most 5 an hour per address.
    _partner_call("resend_verification", {"email": email, "return_url": _partner_return_url()})
    return jsonify({"sent": True, "email": email})


@growth_bp.post("/api/growth/outreach/forgot")
@rate_limit(5, 60)
def growth_outreach_forgot():
    """Ask Outreach to email a password reset link."""
    email = _clean_field(_json_body().get("email"), "your Outreach email", 254).lower()
    if not gs.valid_email(email):
        raise _Http("That does not look like an email address.")
    _partner_call("forgot_password", {"email": email})
    return jsonify({"sent": True, "email": email})


@growth_bp.get("/outreach/connected")
def growth_outreach_connected():
    """Where Outreach sends a user once they have confirmed their email: back into TenderFlow with the Connections window open.
    It is also where the small window of a social sign-in ends (it arrives with ?provider=&status=): that window tells the page
    that opened it and closes itself, so the user is back on Connections without anything else opening."""
    from flask import redirect
    provider = re.sub(r"[^a-z0-9_]", "", (request.args.get("provider") or "").lower())[:30]
    if provider:
        status = "connected" if request.args.get("status") == "connected" else "error"
        what = "Your phone number request" if provider == "number" else provider.title()
        text = f"{what} was received." if provider == "number" and status == "connected" else (
            f"{provider.title()} is connected." if status == "connected" else (f"{what} could not be completed." if provider == "number" else f"{what} could not be connected."))
        # told to the page that opened this window, and broadcast to every tab of this site (that still works when the browser has cut
        # the window's link back); a browser that will not close the window gets a Close button instead of a dead end
        page = (
            '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Connections</title>'
            f'<div style="font:16px system-ui,sans-serif;padding:24px"><p>{text} You can close this window.</p>'
            '<p><button id="c" hidden onclick="window.close()" style="font:inherit;padding:8px 16px">Close this window</button></p></div>'
            '<script>var d={type:"tf.outreach.channel",status:"__STATUS__",provider:"__PROVIDER__"};'
            'try{window.opener&&window.opener.postMessage(d,window.location.origin)}catch(e){}'
            'try{var b=new BroadcastChannel("tf-outreach-channel");b.postMessage(d);b.close()}catch(e){}'
            'window.close();setTimeout(function(){document.getElementById("c").hidden=false},400);</script>'
        ).replace("__STATUS__", status).replace("__PROVIDER__", provider)
        return Response(page, mimetype="text/html", headers={"Cache-Control": "no-store"})
    return redirect("/?outreach=connected", code=302)


# ── connecting social accounts from the Connections window (Outreach's `social` plugin, channel.* actions) ──
# The user signs in on the network's own page in a small window, so TenderFlow never sees a password or token. Until Outreach
# has shipped these actions its answer is a plain "unknown" (400/404/422), and the page falls back to its older instructions.

_CHANNEL_NOT_THERE = (400, 404, 405, 422)


def _channel_refusal(exc: "oc.OutreachError") -> _Http:
    code = exc.code or ""
    if code in ("origin_not_allowed", "return_url_not_allowed"):
        what = "web address" if code == "origin_not_allowed" else "return address"
        value = _partner_origin() if code == "origin_not_allowed" else _partner_return_url()
        return _Http(f"Outreach has not been told this TenderFlow {what} ({value}). Ask your administrator to add it in Outreach, "
                     "under Developer API, Partner programme.", 409, code)
    return _Http(exc.message, exc.status if exc.status in (400, 403, 404) else 502, code or None)


def _connected_networks(client: "oc.OutreachClient") -> tuple[list[str] | None, bool]:
    """(networks, simulated): the networks the user has a working connection to, from Outreach's channel list. The list is None
    when Outreach cannot say (it does not have the channel actions yet, or did not answer), so the caller falls back to what the
    wallet reported. `simulated` is True for a test key, whose sample accounts are not the user's."""
    try:
        raw = client.channel_list()
    except oc.OutreachError:
        return None, False
    if isinstance(raw, dict) and raw.get("simulated"):
        return [], True
    out: list[str] = []
    for c in oc.channel_list_summary(raw):
        if c["status"] == "connected" and c["provider"] not in out:
            out.append(c["provider"])
    return out, False


@growth_bp.get("/api/growth/social/channels")
def growth_social_channels():
    """The networks that can be connected and the accounts the user has connected. {"supported": false} when Outreach has not
    shipped the connect actions yet."""
    client = _outreach_client()
    base = (OUTREACH_API_BASE or "").rstrip("/")
    out: dict[str, Any] = {"supported": True, "providers": [], "channels": [], "return_url": _partner_return_url(),
                           "outreach_origin": "/".join(base.split("/")[:3]) if base.startswith("http") else ""}
    try:
        out["providers"] = oc.channel_providers_summary(client.channel_providers())
        listed = client.channel_list(refresh=_flag(request.args.get("refresh")))
        out["channels"] = oc.channel_list_summary(listed)
        # a test key lists sample accounts (simulated: true) that are always there; the page says so instead of letting them pass for real ones
        out["simulated"] = bool(listed.get("simulated")) if isinstance(listed, dict) else False
        if out["simulated"]:
            out["channels"] = []  # they are not the user's accounts, so none is shown as connected
    except oc.OutreachError as exc:
        if exc.status in _CHANNEL_NOT_THERE:
            return jsonify({**out, "supported": False})
        raise _channel_refusal(exc)
    if not out["providers"]:
        out["providers"] = [{"provider": p, "label": oc.provider_label(p), "available": True} for p in oc.DEFAULT_PROVIDERS]
    return jsonify(out)


@growth_bp.post("/api/growth/social/channels/connect")
def growth_social_channel_connect():
    """Start connecting one network: the answer is the link to open in a small window."""
    provider = str(_json_body().get("provider") or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", provider):
        raise _Http("Choose a social network to connect.")
    client = _outreach_client()
    try:
        answer = oc.connect_summary(client.channel_connect(provider, _partner_return_url()))
    except oc.OutreachError as exc:
        if exc.status in _CHANNEL_NOT_THERE and exc.code not in ("origin_not_allowed", "return_url_not_allowed"):
            raise _Http(f"{oc.provider_label(provider)} cannot be connected from here yet. {exc.message}", 409, "not_available")
        raise _channel_refusal(exc)
    if not answer["auth_url"]:
        raise _Http("Outreach did not give a sign-in link. Try again, or ask your administrator.", 502, "bad_response")
    return jsonify({"auth_url": answer["auth_url"], "connected": answer["connected"], "provider": provider})


@growth_bp.delete("/api/growth/social/channels/<channel_id>")
def growth_social_channel_disconnect(channel_id: str):
    client = _outreach_client()
    try:
        client.channel_disconnect(channel_id)
    except oc.OutreachError as exc:
        raise _channel_refusal(exc)
    return jsonify({"disconnected": channel_id})


# ── phone numbers for calls (Outreach's `numbers` plugin): bought and verified in Outreach's own popup ──

@growth_bp.get("/api/growth/numbers")
def growth_numbers():
    """The user's phone numbers and their status. {"supported": false} when Outreach does not know the numbers actions."""
    client = _outreach_client()
    out: dict[str, Any] = {"supported": True, "numbers": [], "return_url": _partner_return_url()}
    try:
        listed = client.number_list()
        out["numbers"] = oc.number_list_summary(listed)
        out["simulated"] = bool(listed.get("simulated")) if isinstance(listed, dict) else False
    except oc.OutreachError as exc:
        if exc.status in _CHANNEL_NOT_THERE:
            return jsonify({**out, "supported": False})
        raise _channel_refusal(exc)
    return jsonify(out)


@growth_bp.post("/api/growth/numbers/connect")
def growth_number_connect():
    """Start getting a number: the answer is the link to open in a small window."""
    client = _outreach_client()
    try:
        answer = oc.connect_summary(client.number_connect(_partner_return_url()))
    except oc.OutreachError as exc:
        if exc.status in _CHANNEL_NOT_THERE and exc.code not in ("origin_not_allowed", "return_url_not_allowed"):
            raise _Http(f"A phone number cannot be added from here yet. {exc.message}", 409, "not_available")
        raise _channel_refusal(exc)
    if not answer["auth_url"]:
        raise _Http("Outreach did not give a link. Try again, or ask your administrator.", 502, "bad_response")
    return jsonify({"auth_url": answer["auth_url"], "connected": answer["connected"]})


@growth_bp.delete("/api/growth/numbers/<number_id>")
def growth_number_release(number_id: str):
    client = _outreach_client()
    try:
        client.number_release(number_id)
    except oc.OutreachError as exc:
        raise _channel_refusal(exc)
    return jsonify({"released": number_id})


@growth_bp.delete("/api/growth/outreach/connect")
def growth_outreach_disconnect():
    """Disconnect Outreach for this user: their own key is forgotten, and the organisation's key is not used for them
    until they connect again. A key that came from signing in through the partner programme is also revoked in Outreach
    (best effort: the user is disconnected here whatever Outreach answers). Nothing already sent or posted is affected."""
    username = _username()
    link = _outreach_connection()
    revoked = False
    if link["source"] == occ.OWN and link.get("via") == "partner" and link["key"]:
        try:
            oc.OutreachClient(link["key"], OUTREACH_API_BASE, timeout=OUTREACH_TIMEOUT_SECONDS).disconnect_partner()
            revoked = True
        except oc.OutreachError:
            revoked = False  # already revoked in Outreach, or unreachable: either way the user is disconnected here
    conn = _connect()
    try:
        occ.disconnect(conn, username, OUTREACH_API_KEY)
    finally:
        conn.close()
    g._outreach_conn = None
    return jsonify({**_outreach_status_payload(), "revoked": revoked})


# ── Email account: the user's own mailbox (SMTP) ────────────────────────────────────────────────
# Outreach's API cannot add a mailbox to an Outreach account, so a user can set up their own here (Gmail, Outlook, Zoho or any
# SMTP login) and Growth Studio sends from it. The login is checked before it is kept; the password is kept encrypted and is
# never returned. See tender_app/mail_account.py and user_mailer.py.

SMTP_REF = "smtp"  # the "Send from" choice that means the user's own email account; "smtp:<id>" names one of several


def _mail_available() -> bool:
    return bool((MAIL_CRED_KEY or "").strip())


def _mail_error(exc: "um.MailError", status: int = 400) -> _Http:
    return _Http(exc.message, status, exc.code)


def _smtp_choice(ref: Any) -> tuple[bool, int | None]:
    """Whether a "Send from" value means one of the user's own email accounts, and which: "smtp" is the default account,
    "smtp:<id>" a particular one. A malformed id is refused, not turned into the default."""
    text = str(ref or "").strip().lower()
    if text == SMTP_REF:
        return True, None
    if text.startswith(SMTP_REF + ":"):
        tail = text[len(SMTP_REF) + 1:]
        if not (tail.isascii() and tail.isdigit() and len(tail) <= 9):
            raise _Http("That email account is not valid. Choose one from the list.", 400)
        return True, int(tail)
    return False, None


def _account_id(value: Any) -> int | None:
    """An account id from a request (body or query string): None when absent, an error when it is not a whole number."""
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not (isinstance(value, int) or (isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 9)):
        raise _Http("id must be a whole number")
    return int(value)


def _mail_accounts_summary() -> list[dict[str, Any]]:
    """Every email account of the user, the default first. Never fails the page that asks."""
    conn = _connect()
    try:
        return ma.public_all(conn, _username())
    except Exception:  # noqa: BLE001 - a status check must still answer
        return []
    finally:
        conn.close()


def _mail_state(conn, username: str) -> dict[str, Any]:
    accounts = ma.public_all(conn, username)
    return {
        "available": _mail_available(),
        "account": accounts[0] if accounts else None,  # the default one
        "accounts": accounts, "max_accounts": ma.MAX_ACCOUNTS,
        "presets": [{"id": k, "label": v["label"], "host": v["host"], "port": v["port"], "security": v["security"], "help": v["help"]}
                    for k, v in um.PRESETS.items()],
    }


@growth_bp.get("/api/growth/email-account")
def growth_email_account():
    """The user's own email accounts (no passwords), whether the server can keep any, and the provider presets."""
    conn = _connect()
    try:
        return jsonify(_mail_state(conn, _username()))
    finally:
        conn.close()


@growth_bp.put("/api/growth/email-account")
@rate_limit(10, 60)
def growth_email_account_save():
    """Add an account, or change one. With "id" that account is changed; without it a new account is added (or the one already
    sending from that address is replaced). The login is tried before anything is kept. A blank password keeps the saved one,
    but only while the login and server are unchanged."""
    if not _mail_available():
        raise _Http("Connecting an email account is not switched on for this server. Ask your administrator to set MAIL_CRED_KEY.", 409, "no_key")
    body = _json_body()
    for name in ("provider", "host", "security", "username", "from_email", "from_name", "reply_to", "password"):
        if body.get(name) is not None and not isinstance(body[name], str):
            raise _Http(f"{name} must be text")
    for name in ("port", "daily_cap"):
        if isinstance(body.get(name), bool):
            raise _Http(f"{name} must be a whole number")
    account_id = _account_id(body.get("id"))
    try:
        cleaned = um.clean({**body, "login": body.get("username")})
    except um.MailError as exc:
        raise _mail_error(exc)
    if len(cleaned["password"]) > 200:
        raise _Http("That password is too long.", 400, "bad_password")
    username = _username()
    conn = _connect()
    try:
        password = cleaned["password"]
        if not password:
            old = ma.get(conn, username, account_id) if account_id is not None else ma.find_by_address(conn, username, cleaned["from_email"])
            if not (old and old["login"] == cleaned["login"] and old["host"] == cleaned["host"]):
                raise _Http("Enter the mailbox password (an app password, where your provider asks for one).", 400, "password_required")
            try:
                password = ma.password_for(old, MAIL_CRED_KEY)
            except um.MailError as exc:
                raise _mail_error(exc, 409)
        try:
            um.test_connection(cleaned, password, _SMTP_FACTORY)
        except um.MailError as exc:
            raise _mail_error(exc)
        try:
            ma.save(conn, username, cleaned, password, MAIL_CRED_KEY, account_id)
        except um.MailError as exc:
            raise _mail_error(exc, 404 if exc.code == "no_mail_account" else 409)
        return jsonify(_mail_state(conn, username))
    finally:
        conn.close()


@growth_bp.post("/api/growth/email-account/test")
@rate_limit(5, 60)
def growth_email_account_test():
    """Send a short test email from one account (body {"id"}; default: the default account) to its own address, so the user
    sees that it lands."""
    username = _username()
    account_id = _account_id(_json_body().get("id"))
    conn = _connect()
    try:
        row = ma.get(conn, username, account_id)
        if not row:
            raise _Http("Set up your email account first.", 409, "no_mail_account")
        try:
            password = ma.password_for(row, MAIL_CRED_KEY)
        except um.MailError as exc:
            raise _mail_error(exc, 409)
        item = {"key": 0, "to": row["from_email"], "subject": "TenderFlow test email",
                "body": "This is a test from Growth Studio. Your email account is set up and TenderFlow can send from it."}
        res = um.send_batch(ma.smtp_settings(row), password, [item], factory=_SMTP_FACTORY, pause=0)[0]
        if not res["ok"]:
            ma.note_error(conn, username, res["error"], row["id"])
            raise _Http(res["error"], 400)
        ma.note_error(conn, username, None, row["id"])
        return jsonify({"sent_to": row["from_email"]})
    finally:
        conn.close()


@growth_bp.post("/api/growth/email-account/default")
@rate_limit(20, 60)
def growth_email_account_default():
    """Make one of the user's accounts the default: the one Send from picks when nothing else is chosen."""
    account_id = _account_id(_json_body().get("id"))
    if account_id is None:
        raise _Http("id is required")
    username = _username()
    conn = _connect()
    try:
        if not ma.set_default(conn, username, account_id):
            raise _Http("That email account is not there. Reload and try again.", 404, "no_mail_account")
        return jsonify(_mail_state(conn, username))
    finally:
        conn.close()


@growth_bp.delete("/api/growth/email-account")
def growth_email_account_delete():
    """Forget one account (?id=; default: the default account). Emails already sent are not affected. When the default goes,
    the oldest remaining account becomes the default."""
    username = _username()
    account_id = _account_id(request.args.get("id"))
    conn = _connect()
    try:
        ma.delete(conn, username, account_id)
        return jsonify(_mail_state(conn, username))
    finally:
        conn.close()


def _draft_rows(conn, username: str, cid: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT d.target_id, t.buyer_name, d.outreach_id, d.mode, d.status, d.approval, d.error, d.created_at, d.checked_at, d.body_hash
               FROM growth_outreach_drafts d JOIN growth_campaign_targets t ON t.id = d.target_id
               WHERE d.username = %s AND d.campaign_id = %s ORDER BY d.id""",
            (username, cid),
        )
        rows = cur.fetchall()
    return [
        {"target_id": r[0], "buyer": r[1], "outreach_id": r[2], "mode": r[3], "status": r[4], "approval": r[5],
         "error": r[6], "created_at": _iso(r[7]), "checked_at": _iso(r[8]), "body_hash": r[9]}
        for r in rows
    ]


def _save_draft(cur, username: str, cid: int, target_id: int, *, outreach_id: str | None, mode: str,
                status: str | None, approval: str | None, error: str | None, body_hash: str | None = None,
                mail_account_id: int | None = None) -> None:
    cur.execute(
        """INSERT INTO growth_outreach_drafts (username, campaign_id, target_id, outreach_id, mode, status, approval, error, body_hash,
                                               mail_account_id, checked_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
           ON CONFLICT (target_id) DO UPDATE SET outreach_id = EXCLUDED.outreach_id, mode = EXCLUDED.mode,
               status = EXCLUDED.status, approval = EXCLUDED.approval, error = EXCLUDED.error,
               body_hash = EXCLUDED.body_hash, mail_account_id = EXCLUDED.mail_account_id, checked_at = NOW()""",
        (username, cid, target_id, outreach_id, mode, status, approval, error, body_hash, mail_account_id),
    )


def _is_sent_through_outreach(d: dict[str, Any]) -> bool:
    """Approved in Outreach (by this route, or reported so by Outreach): it must never be approved again."""
    return bool(d.get("outreach_id")) and str(d.get("approval") or "").lower() in ("approved", "auto_approved", go.SENT_NOW)


@growth_bp.get("/api/growth/campaigns/<int:cid>/outreach")
def growth_outreach_drafts(cid: int):
    """The campaign's Outreach campaigns. With ?refresh=1 each one's state is read from Outreach first."""
    username = _username()
    refresh = _flag(request.args.get("refresh"))
    client = _outreach_client() if refresh else None
    conn = _connect()
    try:
        _load_campaign(conn, username, cid)  # a campaign that is not theirs is a 404
        drafts = _draft_rows(conn, username, cid)
        details: dict[int, dict[str, Any]] = {}
        seen: list[dict[str, Any]] = []
        stopped = None
        if client is not None:
            with conn.cursor() as cur:
                for d in [d for d in drafts if d["outreach_id"] and d["mode"] != "smtp"][: go.MAX_PER_PUSH]:
                    try:
                        if d["approval"] == go.SENT_NOW:
                            # sent with /send: its id is a message, not a campaign; it can become replied or bounced
                            msg = oc.message_summary(client.get_email_message(d["outreach_id"]))
                            d.update(status=msg["status"] or d["status"], error=None)
                            seen.append({"buyer": d["buyer"], "mailbox": msg["mailbox"], "mailbox_id": msg["mailbox_id"],
                                         "fields": msg["fields"]})
                            _save_draft(cur, username, cid, d["target_id"], outreach_id=d["outreach_id"], mode=d["mode"],
                                        status=d["status"], approval=d["approval"], error=None, body_hash=d["body_hash"])
                            continue
                        summary = oc.campaign_summary(client.get_email_campaign(d["outreach_id"]))
                    except oc.OutreachError as exc:
                        d["error"] = exc.message[:300]
                        if exc.fatal:
                            stopped = exc.message
                            break
                        continue
                    # an answer with no status or approval never wipes what is known: "approved" is what stops a second send
                    d.update(status=summary["status"] or d["status"], approval=summary["approval"] or d["approval"], error=None)
                    details[d["target_id"]] = summary
                    _save_draft(cur, username, cid, d["target_id"], outreach_id=d["outreach_id"], mode=d["mode"],
                                status=d["status"], approval=d["approval"], error=None, body_hash=d["body_hash"])
            conn.commit()
        for d in drafts:
            extra = details.get(d["target_id"]) or {}
            d["sent"], d["replies"] = extra.get("sent"), extra.get("replies")
            d.pop("body_hash", None)
        return jsonify({"drafts": drafts, "refreshed": client is not None, "stopped": stopped, "messages": seen})
    finally:
        conn.close()


@growth_bp.post("/api/growth/campaigns/<int:cid>/outreach")
def growth_outreach_send(cid: int):
    """Send the campaign's message through Outreach, one email campaign per buyer that can be contacted.

    Each buyer is made as an Outreach campaign (filled in for them) and then approved, which is what makes
    Outreach send it; the buyer is then recorded as sent here. A buyer already sent is skipped, so sending
    again only does the ones that were left out or failed. If a campaign was made but not approved, a retry
    approves that same campaign, but only while the message text is unchanged: edited text makes a new one."""
    body = _json_body()
    ids = body.get("target_ids")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
        raise _Http("target_ids must be a list of whole numbers")
    allow_personal = _flag(body.get("allow_personal"))
    mailbox_ref = body.get("mailbox")
    if mailbox_ref is not None and (not isinstance(mailbox_ref, str) or len(mailbox_ref) > 254):
        raise _Http("mailbox must be text")
    own, own_id = _smtp_choice(mailbox_ref)
    if own:
        return _send_from_own_mailbox(cid, ids, allow_personal, own_id)  # one of the user's own email accounts, not Outreach
    client = _outreach_client()
    chosen = None
    if (mailbox_ref or "").strip():
        # only a mailbox Outreach itself reports as connected can be chosen, so nothing arbitrary is ever forwarded
        try:
            boxes = oc.apply_mailbox_ids(oc.wallet_summary(client.wallets())["mailboxes"],
                                         oc.parse_mailbox_ids(OUTREACH_MAILBOX_IDS))
        except oc.OutreachError as exc:
            raise _Http(exc.message, 502)
        chosen = go.find_mailbox(boxes, mailbox_ref)
        if not chosen:
            raise _Http("That mailbox is not connected in Outreach. Choose one from the list.", 400)
        if chosen.get("can_send") is False:
            raise _Http(f"{chosen['email']} cannot send yet. Finish its setup in Outreach (warm-up or connection test).", 400)
        if not chosen.get("id") and not go.selects_by_address(OUTREACH_SEND_MAILBOX_FIELD, OUTREACH_CAMPAIGN_MAILBOX_FIELD):
            # Outreach looks the mailbox up by id; an address matches nothing and it answers "no mailbox can send"
            raise _Http(f"Outreach cannot choose {chosen['email']} yet: it gives no mailbox id for it. Pick \"Let Outreach choose\", "
                        "or ask Outreach for the mailbox id and set it in OUTREACH_MAILBOX_IDS on the server.", 400)
    username = _username()
    conn = _connect()
    try:
        campaign, targets = _load_campaign(conn, username, cid)
        sender = _sender_for(conn, username, campaign)
        opted = _opted_out(conn, username)
        records = {d["target_id"]: d for d in _draft_rows(conn, username, cid)}
        already_sent = {tid for tid, d in records.items() if _is_sent_through_outreach(d)}
        if _files_for(conn, username, cid):
            raise _Http("Outreach cannot carry attached files. Send from your own email account (choose it under Send from), "
                        "or remove the files from the message.", 400, "attachments_need_own_account")
        plan = go.plan_push(campaign, targets, opted, sender, already_sent, only_ids=ids, allow_personal=allow_personal)
        if plan["blockers"]:
            raise _Http(" ".join(plan["blockers"]), 400)
        if not plan["items"]:
            reasons = sorted({s["reason"] for s in plan["skipped"]})
            raise _Http("There is nobody to send: " + (" ".join(reasons) or "no buyers are in this campaign."), 400)
        if chosen:
            # the choice is part of what was sent: a campaign made earlier from the same text but under another
            # mailbox is not reused, and a retry under a different mailbox is never answered from the first request
            for it in plan["items"]:
                it["hash"] = go.message_hash(it["subject"], it["text"] + "\n[mailbox:" + chosen["ref"] + "]")
                it["payload"][OUTREACH_CAMPAIGN_MAILBOX_FIELD] = [chosen["ref"]]
        warnings: list[str] = []
        if er.has_formatting(campaign.get("body_html")):
            warnings.append("Outreach sends plain text, so bold, lists and links went out as plain text. "
                            "Send from your own email account to keep the formatting.")

        def choice_note(exc: "oc.OutreachError", field: str) -> str:
            """When a refusal names the mailbox, say which value was sent for the chosen one, so a value Outreach
            does not recognise (an address where it wants an id, or the other way round) is visible and not guessed."""
            if not chosen or not (exc.setup_required or exc.status in (400, 404, 422)):
                return ""
            return (f" (You chose {chosen['email']}; it was sent to Outreach as {field} = {chosen['ref']}. "
                    f"If \"Let Outreach choose\" sends, Outreach did not accept that value.)")
        by_id = {t["id"]: t for t in targets}
        sent: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        stopped = None
        attempted = 0
        single = go.use_single_send(plan["items"], records)
        with conn.cursor() as cur:
            for item in plan["items"]:
                attempted += 1
                tid = item["target_id"]
                rec = records.get(tid) or {}
                short = item["hash"][:8]
                retry = go.retry_tag(rec)
                oid = None
                status = None
                step = "create"
                if single:
                    # one email: send it now (no campaign, so no warm-up wait and no sending hours)
                    step = "send"
                    try:
                        send_body = go.build_send_payload(item)
                        if chosen:
                            send_body[OUTREACH_SEND_MAILBOX_FIELD] = chosen["ref"]
                        answer = oc.send_summary(client.send_email(
                            send_body, go.idempotency_key(username, cid, tid, f"send-{short}{retry}")))
                    except oc.OutreachError as exc:
                        reason = exc.message + choice_note(exc, OUTREACH_SEND_MAILBOX_FIELD)
                        if exc.daily_limit:
                            reason = (exc.message + " Single emails are limited per day; try again tomorrow, or select "
                                      "several buyers to send them as a campaign.")
                        _save_draft(cur, username, cid, tid, outreach_id=None, mode=client.mode,
                                    status="pending" if exc.code == "unreachable" else "failed",
                                    approval=None, error=reason[:300], body_hash=item["hash"])
                        failed.append({"target_id": tid, "buyer": item["buyer"], "step": step, "reason": reason})
                        if exc.fatal:
                            stopped = exc.message
                        continue
                    if answer["status"] != "sent":
                        note = f"Outreach answered \"{answer['status'] or 'no status'}\", not \"sent\"."
                        _save_draft(cur, username, cid, tid, outreach_id=None, mode=client.mode, status="not_sent",
                                    approval=None, error=note, body_hash=item["hash"])
                        failed.append({"target_id": tid, "buyer": item["buyer"], "step": step, "reason": note})
                        continue
                    _save_draft(cur, username, cid, tid, outreach_id=answer["id"], mode=client.mode, status="sent",
                                approval=go.SENT_NOW, error=None, body_hash=item["hash"])
                    _apply_status(cur, username, campaign, by_id[tid], "sent")
                    sent.append({"target_id": tid, "buyer": item["buyer"], "outreach_id": answer["id"],
                                 "status": "sent", "immediate": True, "from": answer.get("from")})
                    if chosen and answer.get("from") and answer["from"].strip().lower() != chosen["email"].lower():
                        # Outreach does not document choosing the mailbox; the answer names the one it really used
                        warnings.append(f"You chose {chosen['email']}, but Outreach sent to {item['buyer']} from "
                                        f"{answer['from']}. Outreach may not support choosing the mailbox from the API yet.")
                    continue
                try:
                    if rec.get("outreach_id") and rec.get("body_hash") == item["hash"]:
                        oid, status = rec["outreach_id"], rec.get("status")  # made earlier from this same text
                    else:
                        made = oc.campaign_summary(
                            client.create_email_campaign(item["payload"], go.idempotency_key(username, cid, tid, f"{short}{retry}"))
                        )
                        if not made["id"]:
                            raise oc.OutreachError("Outreach did not return a campaign id.", 502, "bad_response")
                        oid, status = made["id"], made["status"] or "created"
                        _save_draft(cur, username, cid, tid, outreach_id=oid, mode=client.mode, status=status,
                                    approval=None, error=None, body_hash=item["hash"])
                    step = "approve"
                    decided = oc.campaign_summary(
                        client.decide_email_campaign(oid, "approve", go.idempotency_key(username, cid, tid, f"approve-{short}{retry}"))
                    )
                except oc.OutreachError as exc:
                    if oid:  # made, not approved: kept so a retry approves this one
                        _save_draft(cur, username, cid, tid, outreach_id=oid, mode=client.mode, status=status or "created",
                                    approval=None, error=exc.message[:300], body_hash=item["hash"])
                    else:
                        _save_draft(cur, username, cid, tid, outreach_id=None, mode=client.mode, status="failed",
                                    approval=None, error=exc.message[:300], body_hash=item["hash"])
                    reason = exc.message + choice_note(exc, OUTREACH_CAMPAIGN_MAILBOX_FIELD)
                    failed.append({"target_id": tid, "buyer": item["buyer"], "step": step, "reason": reason})
                    if exc.fatal:
                        stopped = exc.message
                        break
                    continue
                _save_draft(cur, username, cid, tid, outreach_id=oid, mode=client.mode, status=decided["status"] or "approved",
                            approval="approved", error=None, body_hash=item["hash"])
                _apply_status(cur, username, campaign, by_id[tid], "sent")
                sent.append({"target_id": tid, "buyer": item["buyer"], "outreach_id": oid, "status": decided["status"] or "approved"})
            if sent:
                _touch(cur, cid)
        conn.commit()
        drafts = _draft_rows(conn, username, cid)
        for d in drafts:
            d.pop("body_hash", None)
        return jsonify({
            "sent": sent, "failed": failed, "skipped": plan["skipped"], "stopped": stopped, "mode": client.mode,
            "route": "single" if single else "campaign",
            "mailbox": chosen["email"] if chosen else None, "warnings": warnings,
            "deferred": plan["deferred"] + (len(plan["items"]) - attempted), "drafts": drafts,
        })
    finally:
        conn.close()


def _send_from_own_mailbox(cid: int, ids: list[int] | None, allow_personal: bool, account_id: int | None = None):
    """Send the campaign's message from one of the user's own email accounts (SMTP; the default account unless an id is given),
    one email per buyer that can be contacted. Each result is recorded as soon as it is known, so a stopped batch never sends the
    same buyer twice. The daily limit counts the emails sent from that account in the last 24 hours."""
    username = _username()
    conn = _connect()
    try:
        row = ma.get(conn, username, account_id)
        if not row or not row["verified_at"]:
            raise _Http("Set up your email account in Connections first, then send again.", 409, "no_mail_account")
        try:
            password = ma.password_for(row, MAIL_CRED_KEY)
        except um.MailError as exc:
            raise _mail_error(exc, 409)
        room = int(row["daily_cap"]) - ma.sent_last_day(conn, username, row["id"])
        if room <= 0:
            raise _Http(f"Your limit of {row['daily_cap']} emails a day from {row['from_email']} is used up. Try again tomorrow, "
                        "or raise the limit in Connections.", 400, "daily_limit")
        campaign, targets = _load_campaign(conn, username, cid)
        sender = _sender_for(conn, username, campaign)
        opted = _opted_out(conn, username)
        records = {d["target_id"]: d for d in _draft_rows(conn, username, cid)}
        already_sent = {tid for tid, d in records.items() if _is_sent_through_outreach(d)}
        files = _files_for(conn, username, cid, with_data=True)
        plan = go.plan_push(campaign, targets, opted, sender, already_sent, only_ids=ids, allow_personal=allow_personal, files=files)
        if plan["blockers"]:
            raise _Http(" ".join(plan["blockers"]), 400)
        if not plan["items"]:
            reasons = sorted({s["reason"] for s in plan["skipped"]})
            raise _Http("There is nobody to send: " + (" ".join(reasons) or "no buyers are in this campaign."), 400)
        items = plan["items"][:room]
        attached = [{"filename": f["filename"], "content_type": f["content_type"], "data": bytes(f["data"])} for f in files]
        warnings: list[str] = []
        if len(items) < len(plan["items"]):
            warnings.append(f"Only {len(items)} could be sent today: your limit is {row['daily_cap']} a day from {row['from_email']}. "
                            "Send again tomorrow for the rest.")
        by_id = {t["id"]: t for t in targets}
        sent: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        stopped = None
        not_attempted = 0
        cur = conn.cursor()

        def record(entry: dict[str, Any], res: dict[str, Any]) -> None:
            nonlocal stopped, not_attempted
            item = entry["item"]
            tid = item["target_id"]
            if res["ok"]:
                _save_draft(cur, username, cid, tid, outreach_id=res["message_id"], mode=SMTP_REF, status="sent",
                            approval=go.SENT_NOW, error=None, body_hash=item["hash"], mail_account_id=row["id"])
                _apply_status(cur, username, campaign, by_id[tid], "sent")
                _touch(cur, cid)
                sent.append({"target_id": tid, "buyer": item["buyer"], "outreach_id": res["message_id"], "status": "sent",
                             "immediate": True, "from": row["from_email"]})
            elif res["error"] == "not attempted":
                not_attempted += 1  # an earlier failure stopped the batch: nothing is recorded, so a retry sends it
                return
            else:
                _save_draft(cur, username, cid, tid, outreach_id=None, mode=SMTP_REF, status="failed", approval=None,
                            error=res["error"][:300], body_hash=item["hash"], mail_account_id=row["id"])
                failed.append({"target_id": tid, "buyer": item["buyer"], "step": "send", "reason": res["error"]})
                if res.get("fatal") and not stopped:
                    stopped = res["error"]
            conn.commit()

        batch = [{"key": it["target_id"], "to": it["email"], "subject": it["subject"], "body": it["text"], "html": it.get("html"),
                  "attachments": attached, "item": it} for it in items]
        um.send_batch(ma.smtp_settings(row), password, batch, on_result=record, factory=_SMTP_FACTORY, pause=_SMTP_PAUSE)
        conn.commit()
        if stopped:
            ma.note_error(conn, username, stopped, row["id"])
        elif sent:
            ma.note_error(conn, username, None, row["id"])
        drafts = _draft_rows(conn, username, cid)
        for d in drafts:
            d.pop("body_hash", None)
        return jsonify({
            "sent": sent, "failed": failed, "skipped": plan["skipped"], "stopped": stopped, "mode": SMTP_REF, "route": SMTP_REF,
            "mailbox": row["from_email"], "warnings": warnings,
            "deferred": plan["deferred"] + (len(plan["items"]) - len(items)) + not_attempted, "drafts": drafts,
        })
    finally:
        conn.close()


# ── Outreach: social posts and phone calls ──────────────────────────────────────────────────────

POST_COLUMNS = ("id, outreach_id, prompt, channels, publish_at, mode, status, caption, preview_url, published_url, error, created_at, "
                "checked_at, decided_at, image_url, variants_json, profile_id, company_name")
CALL_COLUMNS = "id, outreach_id, buyer_name, to_number, goal, mode, status, outcome, duration_seconds, meeting_booked, error, created_at, checked_at"
MAX_REFRESH = 10  # Outreach allows 120 requests a minute; a refresh reads at most this many items


def _post_public(r) -> dict[str, Any]:
    d = dict(zip(POST_COLUMNS.split(", "), r))
    d["channels"] = [c for c in (d["channels"] or "").split(",") if c]
    try:
        variants = json.loads(d.pop("variants_json") or "[]")
    except ValueError:
        variants = []
    d["variants"] = variants if isinstance(variants, list) else []
    for k in ("created_at", "checked_at", "decided_at"):
        d[k] = _iso(d[k])
    return d


def _call_public(r) -> dict[str, Any]:
    d = dict(zip(CALL_COLUMNS.split(", "), r))
    d["number_masked"] = och.mask_number(d["to_number"])
    d["can_end"] = och.call_is_live(d["status"])
    for k in ("created_at", "checked_at"):
        d[k] = _iso(d[k])
    return d


def _attempt(body: dict[str, Any]) -> str:
    token = och.attempt_token(body.get("attempt"))
    if not token:
        raise _Http("The request needs an attempt token. Reload the page and try again.")
    return token


def _wallet_for_channels(client: "oc.OutreachClient") -> dict[str, Any]:
    try:
        return oc.wallet_summary(client.wallets())
    except oc.OutreachError as exc:
        raise _Http(exc.message, 502)


@growth_bp.get("/api/growth/social/posts")
def growth_social_posts():
    """The user's social posts, newest first. With ?refresh=1 each unfinished one is read from Outreach first."""
    username = _username()
    client = _outreach_client() if _flag(request.args.get("refresh")) else None
    conn = _connect()
    try:
        stopped = None
        with conn.cursor() as cur:
            cur.execute(f"SELECT {POST_COLUMNS} FROM growth_social_posts WHERE username = %s ORDER BY id DESC LIMIT 50", (username,))
            rows = [_post_public(r) for r in cur.fetchall()]
            if client is not None:
                todo = [p for p in rows if p["outreach_id"] and (p["status"] or "").lower() not in och.FINAL_POST_STATES][:MAX_REFRESH]
                for p in todo:
                    try:
                        s = oc.post_summary(client.get_social_post(p["outreach_id"]))
                    except oc.OutreachError as exc:
                        p["error"] = exc.message[:300]
                        if exc.fatal:
                            stopped = exc.message
                            break
                        continue
                    p.update(status=s["status"] or p["status"], caption=s["caption"] or p["caption"],
                             preview_url=s["preview_url"] or p["preview_url"], published_url=s["published_url"] or p["published_url"],
                             image_url=s["image_url"] or p["image_url"], variants=s["variants"] or p["variants"],
                             publish_at=s["publish_at"] or p["publish_at"], error=s["reason"], checked_at=_iso(_now()))
                    cur.execute(
                        """UPDATE growth_social_posts SET status = %s, caption = %s, preview_url = %s, published_url = %s,
                               image_url = %s, variants_json = %s, publish_at = %s, error = %s, checked_at = NOW()
                           WHERE id = %s AND username = %s""",
                        (p["status"], p["caption"], p["preview_url"], p["published_url"], p["image_url"], json.dumps(p["variants"]),
                         p["publish_at"], p["error"], p["id"], username),
                    )
        conn.commit()
        return jsonify({"posts": rows, "refreshed": client is not None, "stopped": stopped})
    finally:
        conn.close()


@growth_bp.post("/api/growth/social/posts")
@rate_limit(RATE_LIMIT_AI)
def growth_social_create():
    """Ask Outreach's AI to write a post. Nothing is published: the post waits for the user to read the preview
    and approve it (POST .../decision)."""
    body = _json_body()
    attempt = _attempt(body)
    client = _outreach_client()
    wallet = _wallet_for_channels(client)
    allowed = wallet["social_channels"]
    if not allowed:
        # the wallet does not always list them (a test key's sample accounts are only in the channel list), so ask that too
        networks, simulated = _connected_networks(client)
        if simulated:
            pass  # a test key: posts are pretend too, so any of the usual networks may be chosen
        elif networks:
            allowed = networks
        elif networks == [] or (wallet.get("connected") or {}).get("social_channels") == 0:
            raise _Http("No social channel is connected. Connect one under Connections, Social, then try again.", 409)
    # "write it myself": the user's own caption. It is the prompt, either to be polished by the AI (polish: true) or to be
    # used as it is (the browser then sets it on the post by hand once Outreach has finished writing). The user's own
    # picture is likewise set by hand afterwards (PATCH), so the AI is never asked to draw one.
    own_caption = body.get("caption") not in (None, "")
    polish = body.get("polish") is True
    if own_caption:
        bad_caption, caption = och.caption_problem(body.get("caption"), och.MAX_DRAFT_CAPTION)
        problems, channels = och.channel_problems(body.get("channels"), allowed)
        if bad_caption:
            problems.insert(0, bad_caption)
        prompt = caption
    else:
        problems, channels, prompt = och.post_problems(body.get("prompt"), body.get("channels"), allowed)
    publish_at, bad_time = och.parse_publish_at(body.get("publish_at"))
    if bad_time:
        problems.append(bad_time)
    picture = body.get("picture") if isinstance(body.get("picture"), dict) else None
    if problems:
        raise _Http(" ".join(problems), 400)
    username = _username()
    conn = _connect()
    try:
        # posting as one of the user's own companies: only a profile of theirs is used (a miss is a 404), and the AI is
        # told only what the profile says (och.company_brief), so it never writes about a company it was not given
        profile = _profile_for(conn, username, body.get("profile_id")) if body.get("profile_id") not in (None, "", 0, "0") else None
        brief = och.company_brief(profile)
        if own_caption and not polish:
            sent_prompt = och.exact_prompt(prompt)  # the caption goes out as written, so no company facts are added to it
        elif own_caption:
            sent_prompt = och.compose_post_prompt(och.polish_prompt(prompt, channels), brief)
        else:
            sent_prompt = och.compose_post_prompt(prompt, brief)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM growth_social_posts WHERE username = %s AND created_at > NOW() - INTERVAL '24 hours'", (username,))
            if cur.fetchone()[0] >= OUTREACH_DAILY_POST_CAP:
                raise _Http(f"You have made {OUTREACH_DAILY_POST_CAP} posts in the last 24 hours. Try again later.", 429)
        try:
            made = oc.post_summary(client.create_social_post(
                och.build_post_payload(sent_prompt, channels, publish_at, picture), och.attempt_key(username, "post", attempt)))
        except oc.OutreachError as exc:
            raise _Http(exc.message, 502 if exc.status >= 500 or exc.code == "unreachable" else (exc.status if exc.status in (402, 403, 409, 422, 429) else 400))
        if not made["id"]:
            raise _Http("Outreach did not return a post id.", 502)
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO growth_social_posts (username, outreach_id, prompt, channels, publish_at, mode, status, caption,
                        preview_url, published_url, image_url, variants_json, profile_id, company_name, error, checked_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NOW()) RETURNING {POST_COLUMNS}""",
                (username, made["id"], prompt, ",".join(made["channels"] or channels), publish_at, client.mode,
                 made["status"] or "writing", made["caption"], made["preview_url"], made["published_url"], made["image_url"],
                 json.dumps(made["variants"]), profile["id"] if profile else None, brief["name"] if brief else None),
            )
            post = _post_public(cur.fetchone())
        conn.commit()
        return jsonify({"post": post, "mode": client.mode})
    finally:
        conn.close()


@growth_bp.post("/api/growth/social/posts/<int:pid>/decision")
def growth_social_decide(pid: int):
    """Approve (which publishes it, at its publish time), reject or cancel a post. Approving needs `confirm: true`
    and is checked against Outreach's own state first, so a post still being written or already finished is never approved."""
    body = _json_body()
    decision = body.get("decision")
    if decision not in ("approve", "reject", "cancel"):
        raise _Http("decision must be approve, reject or cancel")
    if decision == "approve" and body.get("confirm") is not True:
        raise _Http("Confirm that you have read the preview and want this published.")
    new_time = None
    if decision == "approve" and body.get("publish_at"):
        new_time, bad_time = och.parse_publish_at(body.get("publish_at"))
        if bad_time:
            raise _Http(bad_time)
    attempt = _attempt(body)
    client = _outreach_client()
    username = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {POST_COLUMNS} FROM growth_social_posts WHERE id = %s AND username = %s", (pid, username))
            row = cur.fetchone()
        if not row:
            raise _Http("Post not found", 404)
        post = _post_public(row)
        if not post["outreach_id"]:
            raise _Http("This post was never created in Outreach.", 409)
        try:
            live = oc.post_summary(client.get_social_post(post["outreach_id"]))
            why = och.can_decide(live["status"] or post["status"], decision)
            if why:
                _store_post(conn, pid, username, live)
                conn.commit()
                raise _Http(why, 409)
            done = oc.post_summary(client.decide_social_post(post["outreach_id"], decision, och.attempt_key(username, "post-decide", attempt), new_time))
        except oc.OutreachError as exc:
            raise _Http(exc.message, 403 if exc.status == 403 else (502 if exc.status >= 500 or exc.code == "unreachable" else 400))
        final = done["status"] or {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[decision]
        merged = {**live, "status": final, "caption": done["caption"] or live["caption"],
                  "published_url": done["published_url"] or live["published_url"], "reason": None,
                  "publish_at": done["publish_at"] or new_time or live["publish_at"]}
        _store_post(conn, pid, username, merged, decided=True)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(f"SELECT {POST_COLUMNS} FROM growth_social_posts WHERE id = %s AND username = %s", (pid, username))
            return jsonify({"post": _post_public(cur.fetchone())})
    finally:
        conn.close()


def _store_post(conn, pid: int, username: str, s: dict[str, Any], decided: bool = False) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE growth_social_posts SET status = COALESCE(%s, status), caption = COALESCE(%s, caption),
                    preview_url = COALESCE(%s, preview_url), published_url = COALESCE(%s, published_url),
                    image_url = COALESCE(%s, image_url), variants_json = COALESCE(%s, variants_json),
                    publish_at = COALESCE(%s, publish_at), error = %s, checked_at = NOW(){", decided_at = NOW()" if decided else ""}
                WHERE id = %s AND username = %s""",
            (s["status"], s["caption"], s["preview_url"], s["published_url"], s.get("image_url"),
             json.dumps(s["variants"]) if s.get("variants") else None, s.get("publish_at"), s.get("reason"), pid, username),
        )


@growth_bp.patch("/api/growth/social/posts/<int:pid>")
def growth_social_change(pid: int):
    """Change a post in Outreach: a new publish time, or ask the AI to write the text, the picture or both again (with an
    optional note). Outreach sends a changed post back for approval, so this never publishes: the user reads the new
    preview and approves again."""
    body = _json_body()
    attempt = _attempt(body)
    problems, payload = och.change_payload(body.get("publish_at"), body.get("regenerate"), body.get("instruction"),
                                           caption=body.get("caption"), picture=body.get("picture_data"))
    hand = och.HAND_CAPTION_FIELD in payload or och.HAND_PICTURE_FIELD in payload
    if problems:
        raise _Http(" ".join(problems))
    client = _outreach_client()
    username = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {POST_COLUMNS} FROM growth_social_posts WHERE id = %s AND username = %s", (pid, username))
            row = cur.fetchone()
        if not row:
            raise _Http("Post not found", 404)
        post = _post_public(row)
        if not post["outreach_id"]:
            raise _Http("This post was never created in Outreach.", 409)
        try:
            live = oc.post_summary(client.get_social_post(post["outreach_id"]))
            why = och.can_decide(live["status"] or post["status"], "edit" if hand else "change")
            if why:
                _store_post(conn, pid, username, live)
                conn.commit()
                raise _Http(why, 409)
            done = oc.post_summary(client.update_social_post(post["outreach_id"], payload, och.attempt_key(username, "post-edit", attempt)))
        except oc.OutreachError as exc:
            raise _Http(exc.message, 403 if exc.status == 403 else (502 if exc.status >= 500 or exc.code == "unreachable" else 400))
        merged = {**live, **{k: v for k, v in done.items() if v}, "reason": None}
        merged["status"] = done["status"] or ("writing" if "regenerate" in payload else live["status"])
        if "publish_at" in payload and not done["publish_at"]:
            merged["publish_at"] = payload["publish_at"]
        if och.HAND_CAPTION_FIELD in payload:
            merged["caption"] = payload[och.HAND_CAPTION_FIELD]  # the user's words, whatever Outreach echoes back
        _store_post(conn, pid, username, merged)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(f"SELECT {POST_COLUMNS} FROM growth_social_posts WHERE id = %s AND username = %s", (pid, username))
            return jsonify({"post": _post_public(cur.fetchone())})
    finally:
        conn.close()


@growth_bp.get("/api/growth/social/company")
def growth_social_company():
    """The user's companies (their company profiles) and what a post would say about the chosen one. With no ?profile_id
    the company is the one Growth Studio last used, else their default. The brief is exactly what the AI is told."""
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
    finally:
        conn.close()
    brief = och.company_brief(chosen)
    return jsonify({
        "profiles": [{"id": p["id"], "name": p["name"], "is_default": p["is_default"], "has_description": len(p["text"].strip()) >= 20} for p in profiles],
        "profile_id": chosen["id"] if chosen else None,
        "brief": brief,
        "hints": och.brief_hints(profiles, brief),
    })


@growth_bp.get("/api/growth/voice/scripts")
def growth_voice_scripts():
    """The saved call scripts in the user's Outreach account, for the "saved script" choice."""
    client = _outreach_client()
    try:
        return jsonify({"scripts": oc.script_list(client.voice_scripts())})
    except oc.OutreachError as exc:
        raise _Http(exc.message, 502)


@growth_bp.get("/api/growth/voice/lookup")
@rate_limit(RATE_LIMIT_SEARCH)
def growth_voice_lookup():
    """The phone number a buyer published on a notice we already hold (never invented), checked as a number we may call."""
    name = " ".join((request.args.get("buyer") or "").split())[:200]
    if len(name) < 3:
        raise _Http("Type the buyer's name first.")
    from tender_app.blueprints import buyers_bp as bb

    conn = _connect()
    try:
        with conn.cursor() as cur:
            info = bb._fetch_buyer_contact_info(cur, conn, name)
    finally:
        conn.close()
    phone = info.get("phone")
    if not phone or phone == "Not available":
        return jsonify({"found": False, "phone": None, "number": None, "problem": None})
    number, problem = och.normalize_uk_landline(phone)
    return jsonify({"found": True, "phone": str(phone)[:40], "number": number, "problem": problem})


@growth_bp.get("/api/growth/voice/calls")
def growth_voice_calls():
    """The user's calls, newest first. With ?refresh=1 each call that is not finished is read from Outreach first."""
    username = _username()
    client = _outreach_client() if _flag(request.args.get("refresh")) else None
    conn = _connect()
    try:
        stopped = None
        with conn.cursor() as cur:
            cur.execute(f"SELECT {CALL_COLUMNS} FROM growth_voice_calls WHERE username = %s ORDER BY id DESC LIMIT 50", (username,))
            rows = [_call_public(r) for r in cur.fetchall()]
            if client is not None:
                todo = [c for c in rows if c["outreach_id"] and (c["status"] or "").lower() not in och.CALL_DONE][:MAX_REFRESH]
                for c in todo:
                    try:
                        s = oc.call_summary(client.get_call(c["outreach_id"]))
                    except oc.OutreachError as exc:
                        c["error"] = exc.message[:300]
                        if exc.fatal:
                            stopped = exc.message
                            break
                        continue
                    secs = int(s["duration_seconds"]) if s["duration_seconds"] is not None else c["duration_seconds"]
                    c.update(status=s["status"] or c["status"], outcome=s["outcome"] or c["outcome"], duration_seconds=secs,
                             meeting_booked=s["meeting_booked"] if s["meeting_booked"] is not None else c["meeting_booked"],
                             error=s["reason"], checked_at=_iso(_now()))
                    c["can_end"] = och.call_is_live(c["status"])
                    cur.execute(
                        """UPDATE growth_voice_calls SET status = %s, outcome = %s, duration_seconds = %s, meeting_booked = %s,
                               error = %s, checked_at = NOW() WHERE id = %s AND username = %s""",
                        (c["status"], c["outcome"], c["duration_seconds"], c["meeting_booked"], c["error"], c["id"], username),
                    )
        conn.commit()
        return jsonify({"calls": rows, "refreshed": client is not None, "stopped": stopped})
    finally:
        conn.close()


@growth_bp.get("/api/growth/voice/calls/<int:cid>")
def growth_voice_call(cid: int):
    """One call as Outreach holds it: what was said and the answers to the questions. Read live, not stored here."""
    client = _outreach_client()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {CALL_COLUMNS} FROM growth_voice_calls WHERE id = %s AND username = %s", (cid, _username()))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row[1]:
        raise _Http("Call not found", 404)
    try:
        s = oc.call_summary(client.get_call(row[1]))
    except oc.OutreachError as exc:
        raise _Http(exc.message, 502)
    return jsonify({"call": s})


@growth_bp.post("/api/growth/voice/calls")
@rate_limit(RATE_LIMIT_AI)
def growth_voice_call_make():
    """Phone ONE number now, with Outreach's AI. Refused unless the user confirmed this call (an organisation's line,
    checked against the TPS / CTPS), the number is a UK landline and the buyer is not on the do-not-contact list."""
    body = _json_body()
    attempt = _attempt(body)
    username = _username()
    conn = _connect()
    try:
        suppressed = mr.canonical_buyer_key(" ".join(str(body.get("buyer") or "").split())) in _opted_out(conn, username)
        problems, fields = och.call_problems(
            number=body.get("number"), buyer=body.get("buyer"), goal=body.get("goal"), script_id=body.get("script_id"),
            instructions=body.get("instructions"), questions=body.get("questions"),
            business_line=body.get("business_line"), tps_checked=body.get("tps_checked"), suppressed=suppressed)
        if problems:
            raise _Http(" ".join(problems), 400)
        if body.get("confirm") is not True:
            raise _Http("Confirm that you want the AI to phone this number now.")
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM growth_voice_calls WHERE username = %s AND created_at > NOW() - INTERVAL '24 hours'", (username,))
            if cur.fetchone()[0] >= OUTREACH_DAILY_CALL_CAP:
                raise _Http(f"You have made {OUTREACH_DAILY_CALL_CAP} calls in the last 24 hours. Try again later.", 429)
            cur.execute(
                """SELECT 1 FROM growth_voice_calls WHERE username = %s AND to_number = %s AND created_at > NOW() - INTERVAL '24 hours'
                   AND COALESCE(status, '') NOT IN ('failed', 'no_answer', 'busy', 'voicemail') LIMIT 1""",
                (username, fields["number"]))
            if cur.fetchone():
                raise _Http("This number was already called in the last 24 hours. Wait before calling it again.", 409)
        client = _outreach_client()
        wallet = _wallet_for_channels(client)
        status = wallet.get("phone_status")
        if wallet.get("phone_numbers") == 0 or (isinstance(status, dict) and "active" in status and status["active"] == 0):
            waiting = isinstance(status, dict) and (status.get("pending", 0) or status.get("verification_pending", 0) or status.get("held", 0))
            raise _Http("Your phone number is still being checked by Outreach (usually up to 3 days), so there is no line to call from yet."
                        if waiting else
                        "No phone number is connected in Outreach, so the AI has no line to call from. Add one under Connections, Calls.", 409)
        try:
            made = oc.call_summary(client.make_call(och.build_call_payload(fields), och.attempt_key(username, "call", attempt)))
        except oc.OutreachError as exc:
            raise _Http(exc.message, 502 if exc.status >= 500 or exc.code == "unreachable" else (exc.status if exc.status in (402, 403, 409, 422, 429) else 400))
        if not made["id"]:
            raise _Http("Outreach did not return a call id.", 502)
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO growth_voice_calls (username, buyer_key, buyer_name, to_number, goal, outreach_id, mode, status, checked_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW()) RETURNING {CALL_COLUMNS}""",
                (username, mr.canonical_buyer_key(fields["buyer"]), fields["buyer"], fields["number"], fields["goal"] or None,
                 made["id"], client.mode, made["status"] or "ringing"),
            )
            call = _call_public(cur.fetchone())
        conn.commit()
        return jsonify({"call": call, "mode": client.mode, "live_url": made["live_url"]})
    finally:
        conn.close()


@growth_bp.delete("/api/growth/voice/calls/<int:cid>")
def growth_voice_call_end(cid: int):
    """Hang up a call that is ringing or in progress."""
    client = _outreach_client()
    username = _username()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT outreach_id, status FROM growth_voice_calls WHERE id = %s AND username = %s", (cid, username))
            row = cur.fetchone()
        if not row or not row[0]:
            raise _Http("Call not found", 404)
        if not och.call_is_live(row[1]):
            raise _Http("That call is not in progress.", 409)
        try:
            client.end_call(row[0])
        except oc.OutreachError as exc:
            raise _Http(exc.message, 502 if exc.status >= 500 or exc.code == "unreachable" else 400)
        with conn.cursor() as cur:
            cur.execute("UPDATE growth_voice_calls SET status = 'ended', checked_at = NOW() WHERE id = %s AND username = %s", (cid, username))
            cur.execute(f"SELECT {CALL_COLUMNS} FROM growth_voice_calls WHERE id = %s AND username = %s", (cid, username))
            call = _call_public(cur.fetchone())
        conn.commit()
        return jsonify({"call": call})
    finally:
        conn.close()


# ── Outreach: the inbox (replies to the emails sent through Outreach) ──────────────────────────

INBOX_PAGES = 5        # pages of Outreach's list read for one request (the key is shared, so most rows belong to others)
INBOX_WANTED = 40      # stop reading pages once this many of the user's own emails are found
_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{3,64}$")


def _inbox_owner(conn, username: str) -> dict[str, Any]:
    """What identifies this user's emails in Outreach's shared list: the ids of their one-to-one emails, the
    addresses they have sent to (with the Growth Studio buyer for each), and the addresses that another TenderFlow
    user has also emailed (so a campaign email to one of those cannot be told apart and is not shown)."""
    message_ids: set[str] = set()
    by_message: dict[str, dict[str, Any]] = {}
    by_email: dict[str, dict[str, Any]] = {}
    emails: set[str] = set()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT d.outreach_id, d.approval, LOWER(t.contact_email), t.id, t.campaign_id, t.buyer_name, c.name, t.status
               FROM growth_outreach_drafts d
               JOIN growth_campaign_targets t ON t.id = d.target_id JOIN growth_campaigns c ON c.id = t.campaign_id
               WHERE d.username = %s AND d.mode <> 'smtp' AND d.outreach_id IS NOT NULL ORDER BY d.id""",
            (username,),
        )
        for oid, approval, email, tid, camp_id, buyer, camp_name, status in cur.fetchall():
            info = {"target_id": tid, "campaign_id": camp_id, "campaign_name": camp_name, "buyer": buyer, "status": status}
            if approval == go.SENT_NOW:
                message_ids.add(oid)
                by_message[oid] = info
            if email:
                emails.add(email)
                by_email[email] = info
        cur.execute("SELECT outreach_id, LOWER(recipient_email) FROM email_sends WHERE username = %s AND outreach_id IS NOT NULL", (username,))
        for oid, email in cur.fetchall():
            message_ids.add(oid)  # a one-to-one email's id; a campaign id here can never equal a message id
            emails.add(email)
    ambiguous: set[str] = set()
    if emails:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT LOWER(t.contact_email) FROM growth_outreach_drafts d JOIN growth_campaign_targets t ON t.id = d.target_id
                   WHERE d.username <> %s AND d.mode <> 'smtp' AND d.outreach_id IS NOT NULL AND LOWER(t.contact_email) = ANY(%s)
                   UNION SELECT LOWER(recipient_email) FROM email_sends WHERE username <> %s AND LOWER(recipient_email) = ANY(%s)""",
                (username, list(emails), username, list(emails)),
            )
            ambiguous = {r[0] for r in cur.fetchall()}
    return {"message_ids": message_ids, "by_message": by_message, "by_email": by_email, "emails": emails, "ambiguous": ambiguous}


def _target_info(owner: dict[str, Any], msg: dict[str, Any]) -> dict[str, Any] | None:
    return owner["by_message"].get(msg.get("id")) or owner["by_email"].get((msg.get("to") or "").lower())


def _mark_replied(conn, username: str, info: dict[str, Any] | None) -> str | None:
    """A reply arrived for a buyer recorded as sent: record it as replied (which the Performance tab counts). A buyer
    already replied, in a meeting or opted out is left alone. Returns the buyer's name when it changed."""
    if not info or info.get("status") != "sent":
        return None
    campaign, targets = _load_campaign(conn, username, info["campaign_id"])
    target = next((t for t in targets if t["id"] == info["target_id"]), None)
    if target is None or target["status"] != "sent":
        return None
    with conn.cursor() as cur:
        _apply_status(cur, username, campaign, target, "replied")
        _touch(cur, campaign["id"])
    conn.commit()
    info["status"] = "replied"
    return target["buyer_name"]


@growth_bp.get("/api/growth/inbox")
def growth_inbox():
    """The user's emails sent through Outreach, newest first: ?filter=replies (default) | sent | bounced | all, ?q= for an
    address or subject, ?cursor= for the next page. Outreach's key is shared, so only emails TenderFlow can show to
    this user are returned. A reply to a buyer recorded as sent marks that buyer replied."""
    which = request.args.get("filter") or "replies"
    if which not in go.INBOX_FILTERS:
        raise _Http("filter must be replies, sent, bounced or all")
    q = " ".join((request.args.get("q") or "").split())[:100] or None
    cursor = (request.args.get("cursor") or "").strip() or None
    client = _outreach_client()
    username = _username()
    conn = _connect()
    try:
        owner = _inbox_owner(conn, username)
        hidden = _hidden_messages(conn, username)
        items: list[dict[str, Any]] = []
        pages = 0
        more = cursor
        first = True
        while pages < INBOX_PAGES and len(items) < INBOX_WANTED and (first or more):
            first = False
            try:
                page = oc.message_page(client.list_email_messages(status=go.INBOX_FILTERS[which], q=q, cursor=more))
            except oc.OutreachError as exc:
                raise _Http(exc.message, 403 if exc.status == 403 else 502)
            pages += 1
            more = page["next_cursor"]
            items += [m for m in page["items"] if m["id"] and m["id"] not in hidden and go.owned_message(m, owner["message_ids"], owner["emails"], owner["ambiguous"])]
            if not more:
                break
        seen: set[str] = set()
        if items:
            with conn.cursor() as cur:
                cur.execute("SELECT message_id FROM growth_inbox_seen WHERE username = %s AND message_id = ANY(%s)", (username, [m["id"] for m in items]))
                seen = {r[0] for r in cur.fetchall()}
        marked: list[str] = []
        for m in items:
            info = _target_info(owner, m)
            m["buyer"] = info["buyer"] if info else None
            m["campaign_name"] = info["campaign_name"] if info else None
            m["unread"] = m["status"] == "replied" and m["id"] not in seen
            if m["status"] == "replied":
                name = _mark_replied(conn, username, info)
                if name:
                    marked.append(name)
        return jsonify({"items": items, "next_cursor": more, "filter": which, "marked": marked,
                        "unread": sum(1 for m in items if m["unread"])})
    finally:
        conn.close()


def _hidden_messages(conn, username: str) -> set[str]:
    """The ids of the emails this user has deleted from their inbox."""
    from tender_app.db_ext import create_growth_inbox_hidden
    with conn.cursor() as cur:
        create_growth_inbox_hidden(cur)
        cur.execute("SELECT message_id FROM growth_inbox_hidden WHERE username = %s", (username,))
        return {r[0] for r in cur.fetchall()}


@growth_bp.delete("/api/growth/inbox/<message_id>")
def growth_inbox_delete(message_id: str):
    """Delete an email (a sent email, or one with a reply) from the user's inbox. Outreach's API has no call that erases a
    sent email, so this hides it for this user only: Outreach keeps its record, and the email is not unsent. Only an email
    that is the user's own can be hidden. Undone by POST .../restore."""
    if not _MESSAGE_ID_RE.match(message_id):
        raise _Http("That is not an Outreach message id.")
    client = _outreach_client()
    username = _username()
    conn = _connect()
    try:
        try:
            raw = client.get_email_message(message_id)
        except oc.OutreachError as exc:
            raise _Http(exc.message, 404 if exc.status == 404 else 403 if exc.status == 403 else 502)
        detail = oc.reply_detail(raw)
        detail["id"] = detail["id"] or message_id
        owner = _inbox_owner(conn, username)
        if detail["id"] != message_id or not go.owned_message(detail, owner["message_ids"], owner["emails"], owner["ambiguous"]):
            raise _Http("Message not found", 404)
        _hidden_messages(conn, username)  # makes sure the table exists
        with conn.cursor() as cur:
            cur.execute("INSERT INTO growth_inbox_hidden (username, message_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (username, message_id))
        conn.commit()
        return jsonify({"deleted": message_id})
    finally:
        conn.close()


@growth_bp.post("/api/growth/inbox/<message_id>/restore")
def growth_inbox_restore(message_id: str):
    """Undo a delete: the email shows in the user's inbox again."""
    if not _MESSAGE_ID_RE.match(message_id):
        raise _Http("That is not an Outreach message id.")
    username = _username()
    conn = _connect()
    try:
        _hidden_messages(conn, username)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM growth_inbox_hidden WHERE username = %s AND message_id = %s", (username, message_id))
        conn.commit()
        return jsonify({"restored": message_id})
    finally:
        conn.close()


@growth_bp.get("/api/growth/inbox/<message_id>")
def growth_inbox_message(message_id: str):
    """One sent email with the reply (or the bounce reason), if it is the user's. Opening it marks it read."""
    if not _MESSAGE_ID_RE.match(message_id):
        raise _Http("That is not an Outreach message id.")
    client = _outreach_client()
    username = _username()
    conn = _connect()
    try:
        try:
            raw = client.get_email_message(message_id)
        except oc.OutreachError as exc:
            raise _Http(exc.message, 404 if exc.status == 404 else 403 if exc.status == 403 else 502)
        detail = oc.reply_detail(raw)
        detail["id"] = detail["id"] or message_id
        owner = _inbox_owner(conn, username)
        if not go.owned_message(detail, owner["message_ids"], owner["emails"], owner["ambiguous"]):
            raise _Http("Message not found", 404)
        info = _target_info(owner, detail)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO growth_inbox_seen (username, message_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (username, detail["id"]))
        conn.commit()
        if detail["status"] == "replied":
            _mark_replied(conn, username, info)
        sender = next((r["from"] for r in detail["replies"] if r.get("from") and gs.valid_email(r["from"])), None)
        reply_to = sender or (detail["to"] if detail["to"] and gs.valid_email(detail["to"]) else None)
        return jsonify({
            "message": detail,
            "buyer": info["buyer"] if info else None,
            "campaign": {"id": info["campaign_id"], "name": info["campaign_name"]} if info else None,
            "target_id": info["target_id"] if info else None,
            "target_status": info["status"] if info else None,
            "reply_to": reply_to,
            "opt_out_hint": any(go.looks_like_opt_out(r.get("text")) for r in detail["replies"]),
        })
    finally:
        conn.close()


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
