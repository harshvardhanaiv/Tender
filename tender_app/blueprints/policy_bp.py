"""Policy templates (generated into the company's Documents) and Answer Bank starter packs."""
from __future__ import annotations

import os
import uuid
from datetime import date

from flask import Blueprint, jsonify, request, session

from tender_app import answer_starters as starters
from tender_app import certifications as certs
from tender_app import policy_templates as pt
from tender_app.config import CREDIT_COST_POLICY
from tender_app.metering import require_credits

policy_bp = Blueprint("policy", __name__)

_get_db = None


def init_policy_blueprint(get_db_connection):
    global _get_db
    _get_db = get_db_connection
    return policy_bp


def _connect():
    return _get_db()


def _error(message: str, status: int):
    return jsonify({"ok": False, "error": message}), status


def _username() -> str:
    return (session.get("username") or "").strip()


class _Failed(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message, self.status = message, status


@require_credits(CREDIT_COST_POLICY, "policy_template", _connect)
def _charged_write(template: dict, company: str, profile_text: str, facts: dict) -> dict:
    """The only place a policy costs credits. Raising makes require_credits refund them, so a failed run is free."""
    from server import AIServiceError, call_chat_json

    system, user = pt.build_prompt(template, company, profile_text, facts)
    try:
        raw = call_chat_json(system, user, max_tokens=4000, temperature=0.3, expect=dict)
    except AIServiceError as exc:
        status = exc.status_code if isinstance(exc.status_code, int) and 400 <= exc.status_code < 600 else 502
        raise _Failed(str(exc), status) from exc
    except ValueError as exc:  # no API key configured
        raise _Failed(str(exc), 502) from exc
    content = pt.clean(raw, template)
    if not content["sections"]:
        raise _Failed("The AI gave no usable policy.", 502)
    return content


@policy_bp.get("/api/policy-templates")
def policy_list():
    if not _username():
        return _error("Unauthorized", 401)
    return jsonify({"ok": True, "templates": pt.listing(), "cost": CREDIT_COST_POLICY, "review_months": list(pt.REVIEW_MONTHS), "default_review_months": pt.DEFAULT_REVIEW_MONTHS})


def _profile(cur, username: str, raw_id):
    try:
        wanted = int(raw_id) if raw_id not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        wanted = None
    if wanted:
        cur.execute("SELECT id, name, profile_text FROM company_profiles WHERE id = %s AND username = %s", (wanted, username))
    else:
        cur.execute("SELECT id, name, profile_text FROM company_profiles WHERE username = %s ORDER BY COALESCE(is_default, FALSE) DESC, id ASC LIMIT 1", (username,))
    return cur.fetchone()


@policy_bp.post("/api/policy-templates/<key>/generate")
def policy_generate(key: str):
    """Write the policy from the company profile and the facts given, save it as a Word file in the company's Documents with
    a review date, and return it. Costs credits; a failed attempt is refunded."""
    username = _username()
    if not username:
        return _error("Unauthorized", 401)
    template = pt.TEMPLATES.get(key)
    if not template:
        return _error("Unknown policy.", 404)
    body = request.get_json(silent=True) or {}
    conn = _get_db()
    try:
        cur = conn.cursor()
        profile = _profile(cur, username, body.get("profile_id"))
        cur.close()
    finally:
        conn.close()
    if not profile:
        return _error("Add a company profile first: the policy is written for your company.", 400)
    profile_id, company, profile_text = profile[0], profile[1] or "", (profile[2] or "")[:6000]
    facts = pt.clean_facts(template, body.get("facts"))
    months = pt.review_months(body.get("review_months"))
    approved_by = " ".join(str(body.get("approved_by") or "").split())[:120]

    try:
        content = _charged_write(template, company, profile_text, facts)
    except _Failed as exc:
        return _error(f"{exc.message} You have not been charged.", exc.status)
    if isinstance(content, tuple):  # require_credits answered itself: not enough credits, or a viewer account
        return content

    issued = date.today()
    review = pt.add_months(issued, months)
    data = pt.build_docx(template, company, content, issued, review, approved_by)
    filename = pt.file_name(company, template["title"])
    upload_dir = os.path.join("uploads", "profile_docs")
    os.makedirs(upload_dir, exist_ok=True)
    path = os.path.join(upload_dir, f"{uuid.uuid4().hex}_{filename}")
    with open(path, "wb") as fh:
        fh.write(data)
    conn = _get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO company_profile_documents (profile_id, username, filename, file_path, file_size, issue_date, expiry_date) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (profile_id, username, filename, path, len(data), issued.isoformat(), review.isoformat()),
        )
        doc_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return jsonify({
        "ok": True, "cost": CREDIT_COST_POLICY, "content": content,
        "document": {"id": doc_id, "filename": filename, "profile_id": profile_id, "company_name": company, "issue_date": issued.isoformat(), "expiry_date": review.isoformat(),
                     "download_url": f"/api/company-profiles/{profile_id}/documents/{doc_id}/download"},
    })


@policy_bp.get("/api/certifications")
def certifications_list():
    """Step-by-step guides to ISO 9001/14001/45001/27001, Cyber Essentials, SSIP and insurance, with the user's matching files."""
    username = _username()
    if not username:
        return _error("Unauthorized", 401)
    conn = _get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT filename FROM user_documents WHERE username = %s UNION SELECT filename FROM company_profile_documents WHERE username = %s", (username, username))
        names = [r[0] for r in cur.fetchall()]
        cur.close()
    finally:
        conn.close()
    return jsonify({"ok": True, "guides": certs.listing(certs.match_documents(names)), "note": certs.NOTE})


@policy_bp.get("/api/answer-starters")
def starters_list():
    if not _username():
        return _error("Unauthorized", 401)
    return jsonify({"ok": True, "packs": starters.listing()})


@policy_bp.post("/api/answer-starters/<key>/add")
def starters_add(key: str):
    """Add a starter pack to the user's Answer Bank (for the chosen company). Questions already there are left alone. Free."""
    username = _username()
    if not username:
        return _error("Unauthorized", 401)
    pack = starters.PACKS.get(key)
    if not pack:
        return _error("Unknown pack.", 404)
    body = request.get_json(silent=True) or {}
    raw = body.get("profile_id")
    try:
        profile_id = int(raw) if raw not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        profile_id = None
    conn = _get_db()
    try:
        cur = conn.cursor()
        if profile_id is not None:
            cur.execute("SELECT 1 FROM company_profiles WHERE id = %s AND username = %s", (profile_id, username))
            if not cur.fetchone():
                return _error("That company profile is not yours.", 400)
        cur.execute("SELECT lower(question) FROM answer_bank WHERE username = %s AND profile_id IS NOT DISTINCT FROM %s", (username, profile_id))
        have = {r[0] for r in cur.fetchall()}
        added = skipped = 0
        for item in pack["items"]:
            if item["question"].lower() in have:
                skipped += 1
                continue
            cur.execute(
                "INSERT INTO answer_bank (username, category, question, answer, profile_id) VALUES (%s, %s, %s, %s, %s)",
                (username, item["category"], item["question"], item["answer"], profile_id),
            )
            added += 1
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return jsonify({"ok": True, "added": added, "skipped": skipped})
