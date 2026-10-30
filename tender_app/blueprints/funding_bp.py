"""Funding: open government grants from Find a Grant, with a plain word match to the user's company profile."""
from __future__ import annotations

import json

from flask import Blueprint, Response, jsonify, request, session

from tender_app import funding
from tender_app import funding_export as fx
from tender_app import funding_fit as ff
from tender_app import funding_track as ft
from tender_app import funding_ai as fai
from tender_app import growth_studio as gs
from tender_app.config import (
    CREDIT_COST_FUNDING_ANALYSIS,
    CREDIT_COST_FUNDING_DRAFT,
    CREDIT_COST_FUNDING_ELIGIBILITY,
    CREDIT_COST_FUNDING_BUDGET,
    CREDIT_COST_FUNDING_EVIDENCE,
    CREDIT_COST_FUNDING_QUESTIONS,
    CREDIT_COST_FUNDING_SUMMARY,
)
from tender_app.metering import require_credits

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


def _profile_context(cursor, username: str, raw_id) -> dict:
    """The chosen company profile (else the user's default) as {id, name, keywords, applicant}; applicant is the
    funding_fit view of it. Everything is empty when the user has no profile."""
    empty = {"id": None, "name": "", "keywords": frozenset(), "applicant": ff.applicant_from_profile({}), "meta": {}}
    try:
        profile_id = int(raw_id) if raw_id not in (None, "", "0") else None
    except ValueError:
        profile_id = None
    if not username:
        return empty
    cols = "SELECT id, name, profile_text, meta_json FROM company_profiles WHERE "
    if profile_id:
        cursor.execute(cols + "id = %s AND username = %s", (profile_id, username))
    else:
        cursor.execute(cols + "username = %s ORDER BY COALESCE(is_default, FALSE) DESC, id ASC LIMIT 1", (username,))
    row = cursor.fetchone()
    if not row:
        return empty
    try:
        meta = json.loads(row[3] or "{}")
    except ValueError:
        meta = {}
    meta = meta if isinstance(meta, dict) else {}
    text = row[2] or ""
    return {"id": row[0], "name": row[1] or meta.get("org_name") or "", "keywords": gs.profile_keywords(text, meta), "meta": meta,
            "applicant": ff.applicant_from_profile(meta, text, str(meta.get("org_name") or ""))}


def _ensure_saved_table(cur) -> None:
    ft.ensure_schema(cur)


def _saved_labels(cur, username: str) -> frozenset[str]:
    if not username:
        return frozenset()
    _ensure_saved_table(cur)
    cur.execute("SELECT label FROM funding_saved WHERE username = %s", (username,))
    return frozenset(r[0] for r in cur.fetchall())


def _bad_params(args):
    """The first query parameter that is not valid, as a message; None when all are fine."""
    for key in ("page", "min_award", "need_min", "need_max"):
        raw = args.get(key)
        if raw not in (None, "") and _number(raw) is None:
            return f"{key} must be a number."
    if args.get("sort") not in (None, "") and args.get("sort") not in funding.SORTS:
        return "sort must be one of: " + ", ".join(funding.SORTS) + "."
    if args.get("applicant") not in (None, "") and args.get("applicant") not in funding.APPLICANT_TYPES:
        return "applicant is not a known applicant type."
    return None


@funding_bp.route("/api/funding", methods=["GET"])
def list_funding():
    args = request.args
    problem = _bad_params(args)
    if problem:
        return jsonify({"error": problem}), 422
    page = int(_number(args.get("page")) or 1)
    username = (session.get("username") or "").strip()
    conn = _get_db()
    try:
        cur = conn.cursor()
        ctx = _profile_context(cur, username, args.get("profile_id"))
        applicant = args.get("applicant") if "applicant" in args else ctx["applicant"]["kind"]  # absent: the profile's own type
        include_uk_wide = args.get("include_uk_wide", "1") in ("1", "true")
        result = funding.search(
            cur,
            q=(args.get("q") or "")[:100],
            applicant=applicant or "",
            location="",   # the older ?location= is an alias of ?where=, so both give the same grants
            min_award=_number(args.get("min_award")),
            need_min=_number(args.get("need_min")),
            need_max=_number(args.get("need_max")),
            closing_soon=args.get("closing_soon") in ("1", "true"),
            sort=args.get("sort") or "relevance",
            page=page,
            keywords=ctx["keywords"],
            where=args.get("where") or args.get("location") or "",
            include_uk_wide=include_uk_wide,
            fit_applicant=ctx["applicant"],
            hide_unlikely=args.get("hide_unlikely") in ("1", "true"),
            saved=_saved_labels(cur, username),
            saved_only=args.get("saved_only") in ("1", "true"),
        )
        conn.commit()
        result["applied"] = {"applicant": applicant or ""}
        result["profile"] = {"id": ctx["id"], "name": ctx["name"], "region": ctx["applicant"]["region"], "size": ctx["applicant"]["size"]}
        cur.close()
        return jsonify(result)
    finally:
        conn.close()


@funding_bp.post("/api/funding/event")
def funding_event():
    """One usage event from the Funding page (filter used, search made, grant opened, apply clicked, saved, AI run)."""
    username = (session.get("username") or "").strip()
    if not username:
        return _error("Unauthorized", 401)
    body = request.get_json(silent=True) or {}
    conn = _get_db()
    try:
        cur = conn.cursor()
        problem = ft.record_event(cur, username, str(body.get("event") or ""), body.get("detail"))
        if problem:
            conn.rollback()
            return _error(problem, 422)
        conn.commit()
        cur.close()
        return jsonify({"ok": True})
    finally:
        conn.close()


@funding_bp.get("/api/funding/tracked")
def funding_tracked():
    """The user's saved grants with stage, notes and reminder switch, for the Pipeline page."""
    username = (session.get("username") or "").strip()
    if not username:
        return _error("Unauthorized", 401)
    conn = _get_db()
    try:
        cur = conn.cursor()
        items = ft.tracked(cur, username)
        conn.commit()
        cur.close()
        return jsonify({"items": items, "stages": [[s, ft.STAGE_LABEL[s]] for s in ft.STAGES]})
    finally:
        conn.close()


@funding_bp.patch("/api/funding/tracked/<label>")
def funding_tracked_update(label: str):
    username = (session.get("username") or "").strip()
    if not username:
        return _error("Unauthorized", 401)
    if not fai.valid_label(label):
        return _error("Say which grant.", 400)
    body = request.get_json(silent=True) or {}
    conn = _get_db()
    try:
        cur = conn.cursor()
        problem = ft.update(cur, username, label, stage=body.get("stage"), notes=body.get("notes"), remind=body.get("remind"))
        if problem:
            conn.rollback()
            return _error(problem, 404 if "not in your list" in problem else 422)
        conn.commit()
        cur.close()
        return jsonify({"ok": True})
    finally:
        conn.close()


@funding_bp.route("/api/funding/alerts", methods=["GET", "POST"])
def funding_alerts():
    """The weekly email of new grants that fit the user's profile. Off until the user switches it on."""
    username = (session.get("username") or "").strip()
    if not username:
        return _error("Unauthorized", 401)
    conn = _get_db()
    try:
        cur = conn.cursor()
        if request.method == "POST":
            enabled = (request.get_json(silent=True) or {}).get("enabled")
            if not isinstance(enabled, bool):
                return _error("enabled must be true or false.", 422)
            ft.set_digest_pref(cur, username, enabled)
            conn.commit()
        pref = ft.get_digest_pref(cur, username)
        conn.commit()
        cur.close()
        return jsonify({"ok": True, **pref})
    finally:
        conn.close()


@funding_bp.get("/api/funding/alerts/unsubscribe")
def funding_alerts_unsubscribe():
    """Linked from the weekly email and opened without a session: needs the signed token for that user."""
    import hmac

    from tender_app.email_svc import alert_pref_token

    user = (request.args.get("user") or "").strip()
    expected = alert_pref_token(user) if user else ""
    if not expected or not hmac.compare_digest(expected, request.args.get("token") or ""):
        return ("This link is not valid.", 400)
    conn = _get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT username FROM users WHERE LOWER(username) = LOWER(%s) OR LOWER(email) = LOWER(%s) LIMIT 1", (user, user))
        found = cur.fetchone()
        ft.set_digest_pref(cur, found[0] if found else user, False)
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return ("<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>Unsubscribed</title></head><body style=\"font-family:sans-serif;text-align:center;padding:48px\">"
            "<h2>You will not get the weekly grants email any more.</h2><p>You can switch it back on any time on the Funding page.</p></body></html>")


@funding_bp.get("/api/funding/item/<label>")
def funding_item(label: str):
    """One grant with its eligibility guide, for a shared link to a grant that is not on the visible page."""
    if not fai.valid_label(label):
        return _error("Say which grant.", 400)
    username = (session.get("username") or "").strip()
    conn = _get_db()
    try:
        cur = conn.cursor()
        grant = funding.get_one(cur, label)
        if not grant:
            return _error("That grant is not listed any more.", 404)
        ctx = _profile_context(cur, username, request.args.get("profile_id"))
        grant["matches"] = funding.relevance(ctx["keywords"], grant)
        grant["band"] = "high" if len(grant["matches"]) >= 4 else "mid" if len(grant["matches"]) >= 2 else "low" if grant["matches"] else None
        grant["saved"] = label in _saved_labels(cur, username)
        grant["fit"] = ff.assess(grant, ctx["applicant"], has_profile=bool(ctx["keywords"]))
        conn.commit()
        cur.close()
        return jsonify({"item": grant})
    finally:
        conn.close()


@funding_bp.post("/api/funding/save")
def funding_save():
    """Save or unsave a grant for the signed-in user. Free."""
    username = (session.get("username") or "").strip()
    if not username:
        return _error("Unauthorized", 401)
    body = request.get_json(silent=True) or {}
    label = body.get("label")
    if not fai.valid_label(label):
        return _error("Say which grant.", 400)
    conn = _get_db()
    try:
        cur = conn.cursor()
        _ensure_saved_table(cur)
        if body.get("saved", True):
            cur.execute("INSERT INTO funding_saved (username, label) VALUES (%s, %s) ON CONFLICT DO NOTHING", (username, label))
        else:
            cur.execute("DELETE FROM funding_saved WHERE username = %s AND label = %s", (username, label))
        conn.commit()
        cur.close()
        return jsonify({"ok": True, "saved": bool(body.get("saved", True))})
    finally:
        conn.close()


# ── AI tools for one grant ────────────────────────────────────────────────────────────────────────
COSTS = {
    "eligibility": CREDIT_COST_FUNDING_ELIGIBILITY,
    "summary": CREDIT_COST_FUNDING_SUMMARY,
    "draft": CREDIT_COST_FUNDING_DRAFT,
    "analysis": CREDIT_COST_FUNDING_ANALYSIS,
    "questions": CREDIT_COST_FUNDING_QUESTIONS,
    "budget": CREDIT_COST_FUNDING_BUDGET,
    "evidence": CREDIT_COST_FUNDING_EVIDENCE,
}


def _connect():
    return _get_db()


def _ensure_results_table(cur) -> None:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS funding_ai_results (
               username   TEXT NOT NULL,
               label      VARCHAR(300) NOT NULL,
               kind       VARCHAR(20) NOT NULL,
               profile_id INT NOT NULL DEFAULT 0,
               result     TEXT NOT NULL,
               created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
               PRIMARY KEY (username, label, kind, profile_id)
           )"""
    )


def _profile(cur, username: str, raw_id):
    """(id, name, text) of the user's chosen company profile, else their default; None when they have none."""
    try:
        wanted = int(raw_id) if raw_id not in (None, "", "0", 0) else None
    except (TypeError, ValueError):
        wanted = None
    if wanted:
        cur.execute("SELECT id, name, profile_text, meta_json FROM company_profiles WHERE id = %s AND username = %s", (wanted, username))
    else:
        cur.execute(
            "SELECT id, name, profile_text, meta_json FROM company_profiles WHERE username = %s ORDER BY COALESCE(is_default, FALSE) DESC, id ASC LIMIT 1",
            (username,),
        )
    row = cur.fetchone()
    if not row:
        return None
    text = (row[2] or "")[:6000]
    if row[3] and row[3] not in ("{}", ""):
        text += "\n\nCOMPANY DETAILS (JSON):\n" + row[3][:3000]
    return row[0], row[1] or "", text


def _answers(cur, username: str, profile_id) -> str:
    cur.execute(
        "SELECT category, question, answer FROM answer_bank WHERE username = %s AND (profile_id = %s OR profile_id IS NULL) ORDER BY category, id LIMIT 60",
        (username, profile_id),
    )
    return "\n\n".join(f"[{r[0]}] Q: {r[1]}\nA: {r[2]}" for r in cur.fetchall())[:8000]


_STATE_MAX = 20000


def _ensure_state_table(cur) -> None:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS funding_ai_state (
               username   TEXT NOT NULL,
               label      VARCHAR(300) NOT NULL,
               kind       VARCHAR(20) NOT NULL,
               state      TEXT NOT NULL,
               updated_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
               PRIMARY KEY (username, label, kind)
           )"""
    )


def _state(cur, username: str, label: str, kind: str) -> dict:
    """What the user ticked or typed against a result (question status, evidence ticks, project cost)."""
    _ensure_state_table(cur)
    cur.execute("SELECT state FROM funding_ai_state WHERE username = %s AND label = %s AND kind = %s", (username, label, kind))
    row = cur.fetchone()
    try:
        data = json.loads(row[0]) if row else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _documents(cur, username: str) -> list[dict]:
    cur.execute("SELECT filename, expiry_date FROM user_documents WHERE username = %s ORDER BY id DESC LIMIT 300", (username,))
    return [{"filename": r[0], "expiry_date": r[1]} for r in cur.fetchall()]


def _decorate(cur, username: str, label: str, kind: str, result: dict) -> dict:
    """The saved result plus what depends on today's data: the user's documents (evidence) and their own ticks."""
    extra: dict = {"state": _state(cur, username, label, kind)}
    if kind == "evidence":
        result = {**result, "items": fai.match_documents(result.get("items") or [], _documents(cur, username))}
    return {"result": result, **extra}


def _saved(cur, username: str, label: str, kind: str, profile_id: int):
    cur.execute(
        "SELECT result, created_at FROM funding_ai_results WHERE username = %s AND label = %s AND kind = %s AND profile_id = %s",
        (username, label, kind, profile_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    try:
        return {"result": json.loads(row[0]), "saved_at": row[1].isoformat() if row[1] else None}
    except ValueError:
        return None


def _error(message: str, status: int):
    return jsonify({"ok": False, "error": message}), status


class _Failed(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message, self.status = message, status


def _run_ai(kind: str, system: str, user: str) -> dict:
    """Ask the AI and clean its answer. Raising makes require_credits refund the credits, so a failed run is free."""
    from server import AIServiceError, call_chat_json

    try:
        raw = call_chat_json(system, user, max_tokens=4000 if kind == "draft" else 2500, temperature=0.2, expect=dict)
    except AIServiceError as exc:
        status = exc.status_code if isinstance(exc.status_code, int) and 400 <= exc.status_code < 600 else 502
        raise _Failed(str(exc), status) from exc
    except ValueError as exc:  # no API key configured
        raise _Failed(str(exc), 502) from exc
    result = fai.clean(kind, raw)
    if fai.is_empty(kind, result):
        raise _Failed("The AI gave no usable answer. You have not been charged. Please try again.", 502)
    return result


def _charged(kind: str):
    @require_credits(COSTS[kind], f"funding_{kind}", _connect)
    def run(system: str, user: str) -> dict:
        return _run_ai(kind, system, user)

    return run


_CHARGED = {kind: _charged(kind) for kind in fai.KINDS}


@funding_bp.get("/api/funding/ai/costs")
def funding_ai_costs():
    return jsonify({"costs": COSTS})


@funding_bp.post("/api/funding/ai/<kind>")
def funding_ai(kind: str):
    """One AI tool for one grant. A saved result is returned free; refresh=true runs it again (charged)."""
    if kind not in fai.KINDS:
        return _error("Unknown tool.", 404)
    username = (session.get("username") or "").strip()
    if not username:
        return _error("Unauthorized", 401)
    body = request.get_json(silent=True) or {}
    label = body.get("label")
    if not fai.valid_label(label):
        return _error("Say which grant.", 400)
    conn = _get_db()
    try:
        cur = conn.cursor()
        grant = funding.get_one(cur, label)
        if not grant:
            return _error("That grant is not listed any more.", 404)
        profile = _profile(cur, username, body.get("profile_id"))
        if fai.NEEDS_PROFILE[kind] and not profile:
            return _error("Add a company profile first: this tool compares the grant with your business.", 400)
        profile_id = profile[0] if profile and fai.NEEDS_PROFILE[kind] else 0
        used = {"id": profile[0], "name": profile[1]} if profile and fai.NEEDS_PROFILE[kind] else None  # shown with the result
        _ensure_results_table(cur)
        conn.commit()
        if not body.get("refresh"):
            saved = _saved(cur, username, label, kind, profile_id)
            if saved:
                return jsonify({"ok": True, "kind": kind, "cached": True, "cost": 0, "saved_at": saved["saved_at"], "profile": used, **_decorate(cur, username, label, kind, saved["result"])})
        if body.get("saved_only"):  # opening a tab: show what is kept, never run (and charge for) the AI
            return jsonify({"ok": True, "kind": kind, "none": True, "cost": COSTS[kind]})
        answers = _answers(cur, username, profile[0]) if kind == "draft" and profile else ""
        fit = None
        if profile and kind in ("eligibility", "draft", "analysis"):  # the free check goes into the prompt, so the AI states what is already known
            ctx = _profile_context(cur, username, profile[0])
            fit = ff.assess(grant, ctx["applicant"], has_profile=bool(ctx["keywords"]))
        cur.close()
    finally:
        conn.close()

    page_text, page_read = fai.full_text(label)
    system, user = fai.build_prompt(kind, fai.grant_block(grant, page_text), profile[1] if profile else "", profile[2] if profile else "", answers, fit)
    try:
        result = _CHARGED[kind](system, user)
    except _Failed as exc:
        return _error(exc.message, exc.status)
    if isinstance(result, tuple):  # require_credits answered itself: not enough credits, or a viewer account
        return result

    conn = _get_db()
    decorated = {"result": result, "state": {}}
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO funding_ai_results (username, label, kind, profile_id, result, created_at)
               VALUES (%s, %s, %s, %s, %s, NOW() AT TIME ZONE 'utc')
               ON CONFLICT (username, label, kind, profile_id)
               DO UPDATE SET result = EXCLUDED.result, created_at = EXCLUDED.created_at""",
            (username, label, kind, profile_id, json.dumps(result)),
        )
        conn.commit()
        decorated = _decorate(cur, username, label, kind, result)
        cur.close()
    except Exception as exc:  # noqa: BLE001 - the answer is still returned; it just is not kept
        print("funding ai result not saved:", exc)
    finally:
        conn.close()
    return jsonify({"ok": True, "kind": kind, "cached": False, "cost": COSTS[kind], "page_read": page_read, "profile": used, **decorated})


@funding_bp.post("/api/funding/export/docx")
def funding_export_docx():
    """A Word file of the text on screen (the user may have edited it). Free."""
    if not (session.get("username") or "").strip():
        return _error("Unauthorized", 401)
    body = request.get_json(silent=True) or {}
    title, text = body.get("title"), body.get("text")
    if not isinstance(text, str) or not text.strip():
        return _error("There is nothing to export.", 400)
    if len(text) > fx.MAX_CHARS:
        return _error("That is too long to export.", 400)
    data = fx.build_docx(title if isinstance(title, str) else "", text)
    name = fx.file_name(title if isinstance(title, str) else "")
    return Response(data, mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@funding_bp.post("/api/funding/ai/<kind>/state")
def funding_ai_state(kind: str):
    """Keep what the user ticked or typed against a tool's result. Free."""
    if kind not in fai.KINDS:
        return _error("Unknown tool.", 404)
    username = (session.get("username") or "").strip()
    if not username:
        return _error("Unauthorized", 401)
    body = request.get_json(silent=True) or {}
    label, state = body.get("label"), body.get("state")
    if not fai.valid_label(label) or not isinstance(state, dict):
        return _error("Say which grant and what to keep.", 400)
    raw = json.dumps(state)
    if len(raw) > _STATE_MAX:
        return _error("That is too much to keep.", 400)
    conn = _get_db()
    try:
        cur = conn.cursor()
        _ensure_state_table(cur)
        cur.execute(
            """INSERT INTO funding_ai_state (username, label, kind, state, updated_at)
               VALUES (%s, %s, %s, %s, NOW() AT TIME ZONE 'utc')
               ON CONFLICT (username, label, kind) DO UPDATE SET state = EXCLUDED.state, updated_at = EXCLUDED.updated_at""",
            (username, label, kind, raw),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return jsonify({"ok": True})


@funding_bp.get("/api/funding/best")
def funding_best():
    """The open grants that share the most words with the user's company profile, closing soonest first within a band."""
    username = (session.get("username") or "").strip()
    conn = _get_db()
    try:
        cur = conn.cursor()
        keywords = _profile_keywords(cur, username, request.args.get("profile_id"))
        items = []
        if keywords:
            found = funding.search(cur, sort="relevance", page=1, keywords=keywords)
            items = funding.best_of(found["items"])
        cur.close()
        return jsonify({"has_profile": bool(keywords), "items": items})
    finally:
        conn.close()
