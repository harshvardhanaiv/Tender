"""Who may plan a market engagement in a public body's name (Buyer workspace QA W1).

Any signed-in user can pick "London Borough of Camden" as their organisation, so choosing it proves nothing. Planning an engagement
(drafting a notice, inviting suppliers, handing over) is only open to an account that works for a public body: a work email on a
public-sector domain, or an administrator. Market Radar stays open as research for everyone.

Pure functions, tested in tests/test_org_verify.py.
"""
from __future__ import annotations

import os
from typing import Any

# Domains public bodies sign in from. A council is camden.gov.uk, an NHS organisation is nhs.net or nhs.uk, a university is ac.uk.
PUBLIC_DOMAIN_SUFFIXES = (
    ".gov.uk", ".nhs.uk", ".nhs.net", ".nhs.scot", ".police.uk", ".mod.uk", ".parliament.uk", ".gov.scot", ".gov.wales", ".llyw.cymru",
    ".ac.uk", ".sch.uk", ".gov.ie", ".hse.ie", ".gov.im", ".nic.in",
)
EXACT_DOMAINS = ("nhs.net", "mod.gov.uk", "parliament.uk")

MESSAGE = ("Planning a market engagement is for people who work for the public body. Sign in with your work email "
           "(for example name@camden.gov.uk or name@nhs.net), or ask an administrator to approve your account. "
           "Market Radar stays open to everyone.")


def enforced() -> bool:
    return os.environ.get("ENFORCE_ORG_VERIFICATION", "1").strip().lower() not in ("0", "false", "no")


def email_domain(email: str | None) -> str:
    text = str(email or "").strip().lower()
    return text.rsplit("@", 1)[1] if "@" in text else ""


def is_public_domain(domain: str) -> bool:
    d = (domain or "").strip().lower().strip(".")
    return bool(d) and (d in EXACT_DOMAINS or any(d.endswith(suffix) for suffix in PUBLIC_DOMAIN_SUFFIXES))


def status(email: str | None, *, is_admin: bool = False, approved: bool = False) -> dict[str, Any]:
    """{'verified': bool, 'via': 'admin' | 'approved' | 'work email' | None, 'message': str}."""
    if not enforced():
        return {"verified": True, "via": "not enforced", "message": ""}
    if is_admin:
        return {"verified": True, "via": "admin", "message": ""}
    if approved:
        return {"verified": True, "via": "approved", "message": ""}
    if is_public_domain(email_domain(email)):
        return {"verified": True, "via": "work email", "message": ""}
    return {"verified": False, "via": None, "message": MESSAGE}


def status_for_request(conn) -> dict[str, Any]:
    """The signed-in user's status: their session email, whether they are an administrator, and whether an administrator approved
    them (user_prefs buyer_org_approved = '1', set by hand for an account whose work email is not on a public-sector domain)."""
    from flask import session

    username = (session.get("username") or "").strip()
    email = session.get("email") or username
    try:
        from tender_app.blueprints.admin_bp import _is_admin
        admin = bool(_is_admin())
    except Exception:  # noqa: BLE001 - no admin check available: not an admin
        admin = False
    approved = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pref_value FROM user_prefs WHERE username = %s AND pref_key = 'buyer_org_approved'", (username,))
            row = cur.fetchone()
            approved = bool(row and str(row[0]) == "1")
    except Exception:  # noqa: BLE001
        conn.rollback()
    return status(email, is_admin=admin, approved=approved)
