"""Preliminary market engagement (PME) planning for public sector buyers: the pure logic.

A buyer who is about to write a specification talks to the market first. This module holds the
parts of that workflow that need no database: validating a plan, drafting the notice text,
working out which of the five steps a plan is on, shortlisting suppliers from the award history,
and assembling the hand-off pack for the tender that follows. The routes and SQL are in
tender_app/blueprints/market_engagement_bp.py.

What TenderFlow does NOT do, on purpose:
  * it does not publish anything to Find a Tender / the Central Digital Platform -- the buyer copies
    the drafted notice there and records the link here ("Mark as published");
  * it does not email suppliers -- the supplier list is a record of who the buyer has approached and
    what they said, and doubles as the fairness log;
  * the drafted text is a starting point, not legal advice: it says so in its first lines.

The shortlist of suppliers comes from who has actually won work in the category (Market Radar's
supplier comparison). "Smaller suppliers first" orders by typical contract size as a proxy for SME
participation: award notices do not say whether a supplier is an SME, and the response labels it a proxy.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

ENGAGEMENT_TYPES = {
    "questionnaire": "Supplier questionnaire",
    "supplier_day": "Supplier day",
    "both": "Questionnaire and supplier day",
    "meetings": "One-to-one meetings",
}
STATUSES = ("draft", "published", "closed", "converted")
SUPPLIER_STATUSES = {
    "not_contacted": "Not yet contacted",
    "invited": "Invited",
    "responded": "Responded",
    "declined": "Declined",
    "no_response": "No response",
}
# status -> statuses it may move to
TRANSITIONS = {
    "draft": {"published"},
    "published": {"closed", "converted"},
    "closed": {"converted", "published"},
    "converted": set(),
}
STEP_TITLES = (
    "Define scope & category",
    "Identify suppliers to engage",
    "Publish the engagement notice",
    "Collect responses & keep it fair",
    "Hand over to the tender",
)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TODO = "[to be confirmed]"


class ValidationError(ValueError):
    """A field is missing or malformed (maps to HTTP 400)."""


def _text(value: Any, field: str, maximum: int, required: bool = False) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValidationError(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text")
    value = value.strip()
    if len(value) > maximum:
        raise ValidationError(f"{field} must be at most {maximum} characters")
    return value


def _number(value: Any, field: str, lo: float, hi: float) -> float | None:
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number")
    if not (lo <= n <= hi) or n != n:
        raise ValidationError(f"{field} must be between {lo:g} and {hi:g}")
    return n


def validate_plan(data: dict[str, Any], partial: bool = False) -> dict[str, Any]:
    """Validate and normalise the editable fields of a plan; unknown keys are ignored.

    With partial=True only the keys present are checked (PATCH); otherwise title is required."""
    out: dict[str, Any] = {}

    def wanted(key: str) -> bool:
        return key in data or not partial

    if wanted("title"):
        out["title"] = _text(data.get("title"), "title", 200, required=True)
        if len(out["title"]) < 3:
            raise ValidationError("title must be at least 3 characters")
    if wanted("organisation"):
        out["organisation"] = _text(data.get("organisation"), "organisation", 200)
    if wanted("est_value"):
        out["est_value"] = _number(data.get("est_value"), "est_value", 0, 1e11)
    if wanted("term_years"):
        out["term_years"] = _number(data.get("term_years"), "term_years", 0.1, 30)
    if wanted("engagement_type"):
        kind = data.get("engagement_type") or "questionnaire"
        if kind not in ENGAGEMENT_TYPES:
            raise ValidationError("engagement_type must be one of: " + ", ".join(ENGAGEMENT_TYPES))
        out["engagement_type"] = kind
    for key, limit in (("objectives", 4000), ("supplier_day_at", 120), ("supplier_day_place", 200), ("contact_name", 120)):
        if key in data:
            out[key] = _text(data.get(key), key, limit)
    if "contact_email" in data:
        email = _text(data.get("contact_email"), "contact_email", 200)
        if email and not _EMAIL.match(email):
            raise ValidationError("contact_email is not a valid email address")
        out["contact_email"] = email
    if "response_deadline" in data:
        raw = _text(data.get("response_deadline"), "response_deadline", 10)
        if raw:
            try:
                out["response_deadline"] = date.fromisoformat(raw)
            except ValueError:
                raise ValidationError("response_deadline must be a date in YYYY-MM-DD format")
        else:
            out["response_deadline"] = None
    if "notice_text" in data:
        out["notice_text"] = _text(data.get("notice_text"), "notice_text", 20000)
    if "published_url" in data:
        url = _text(data.get("published_url"), "published_url", 500)
        if url and not re.match(r"^https?://\S+$", url, re.IGNORECASE):
            raise ValidationError("published_url must be an http(s) link")
        out["published_url"] = url
    return out


def validate_supplier_update(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "included" in data:
        if not isinstance(data["included"], bool):
            raise ValidationError("included must be true or false")
        out["included"] = data["included"]
    if "status" in data:
        if data["status"] not in SUPPLIER_STATUSES:
            raise ValidationError("status must be one of: " + ", ".join(SUPPLIER_STATUSES))
        out["status"] = data["status"]
    if "note" in data:
        out["note"] = _text(data.get("note"), "note", 1000)
    if not out:
        raise ValidationError("nothing to update")
    return out


def money(value: Any) -> str | None:
    if value in (None, ""):
        return None
    n = float(value)
    if n >= 1_000_000:
        return "£" + f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "m"
    return f"£{n:,.0f}"


def _uk_date(value: Any) -> str | None:
    if not value:
        return None
    d = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    return f"{d.day} {d.strftime('%B %Y')}"


def build_notice_text(plan: dict[str, Any]) -> tuple[str, list[str]]:
    """Draft notice text and the list of details still to fill in (each shown as "[to be confirmed]")."""
    missing: list[str] = []

    def need(value: Any, label: str) -> str:
        if value in (None, ""):
            missing.append(label)
            return TODO
        return str(value)

    org = need(plan.get("organisation"), "contracting authority")
    kind = plan.get("engagement_type") or "questionnaire"
    deadline = _uk_date(plan.get("response_deadline"))
    contact = " ".join(
        part for part in (
            plan.get("contact_name"),
            f"<{plan['contact_email']}>" if plan.get("contact_email") else None,
        ) if part
    )

    objectives = plan.get("objectives") or (
        f"{org} is carrying out preliminary market engagement to understand how the market can meet "
        f"its needs for {plan.get('category_label') or 'this requirement'}, to test its assumptions, and to "
        "shape the specification, any lots, the contract term and the procurement route."
    )

    lines = [
        "PRELIMINARY MARKET ENGAGEMENT NOTICE (DRAFT)",
        "Drafted in TenderFlow. Check it against current Procurement Act 2023 guidance and your organisation's "
        "own notice template before you publish it. TenderFlow does not publish notices on your behalf.",
        "",
        f"Contracting authority: {org}",
        f"Title: {plan.get('title') or TODO}",
        "",
        "1. Purpose of this engagement",
        objectives,
        "",
        "2. What we are planning to buy",
        f"Category: {plan.get('category_label') or TODO}",
    ]
    value = money(plan.get("est_value"))
    lines.append(f"Indicative contract value: {value + ' (an estimate, not a commitment)' if value else need(None, 'indicative contract value')}")
    term = plan.get("term_years")
    if term:
        term_n = float(term)
        lines.append(f"Indicative term: {term_n:g} year{'' if term_n == 1 else 's'}")
    else:
        lines.append(f"Indicative term: {need(None, 'indicative term')}")

    lines += ["", "3. How to take part"]
    if kind in ("questionnaire", "both"):
        lines.append(
            f"Complete our supplier questionnaire and return it by {need(deadline, 'response deadline')}. "
            "Tell us where to find the questionnaire in the notice you publish."
        )
    if kind in ("supplier_day", "both"):
        lines.append(f"Supplier day: {need(plan.get('supplier_day_at'), 'supplier day date and time')}, "
                     f"{need(plan.get('supplier_day_place'), 'supplier day place or online link')}.")
    if kind == "meetings":
        lines.append(f"We will hold short one-to-one meetings with interested suppliers. Ask to take part by {need(deadline, 'response deadline')}.")
    if kind in ("supplier_day", "both") and deadline:
        lines.append(f"Please confirm your attendance by {deadline}.")
    if kind == "supplier_day" and not deadline:
        lines.append(f"Please confirm your attendance by {need(None, 'response deadline')}.")
    lines.append(f"Response deadline: {need(deadline, 'response deadline')}")
    lines.append(f"Contact: {need(contact, 'contact name and email')}")

    lines += [
        "",
        "4. How we will keep this fair",
        "- Every supplier that takes part will receive the same information at the same time.",
        "- Individual responses will be treated as confidential and used only to inform our requirements, "
        "specification and procurement approach.",
        "- Taking part will not give any supplier an advantage in a later procurement, and not taking part will "
        "not disadvantage you.",
        "- We will consider the barriers that small and medium-sized enterprises and voluntary, community and "
        "social enterprise organisations may face.",
        "",
        "5. What happens next",
        "We may use what we learn to refine the specification, the lot structure and the procurement route. "
        "Any procurement that follows will be published separately.",
    ]
    return "\n".join(lines), sorted(set(missing), key=missing.index)


def step_states(plan: dict[str, Any], included_suppliers: int) -> list[dict[str, Any]]:
    """Which of the five steps are done, which one is current, and which are still ahead."""
    status = plan.get("status") or "draft"
    _, missing = build_notice_text(plan)
    step1_done = bool(plan.get("title") and plan.get("category_label") and not missing)
    done = [
        step1_done,
        included_suppliers > 0,
        status in ("published", "closed", "converted"),
        status in ("closed", "converted"),
        status == "converted",
    ]
    steps = []
    active_assigned = False
    for index, title in enumerate(STEP_TITLES):
        if done[index]:
            state = "done"
        elif not active_assigned:
            state, active_assigned = "active", True
        else:
            state = "upcoming"
        steps.append({"n": index + 1, "title": title, "state": state})
    return steps


def shortlist_suppliers(analysis_suppliers: list[dict[str, Any]], limit: int = 40, smaller_first: bool = False) -> list[dict[str, Any]]:
    """Suppliers worth approaching, from Market Radar's supplier comparison for the category."""
    candidates = [s for s in analysis_suppliers if s.get("buyers", 0) >= 1]

    def typical(s: dict[str, Any]) -> Any:
        # the MEDIAN contract: one very large award must not make a heating firm look like a GBP 45m supplier
        return s.get("median_contract") if s.get("median_contract") is not None else s.get("avg_contract")

    if smaller_first:
        candidates.sort(key=lambda s: (typical(s) is None, typical(s) or 0, -s["buyers"]))
    else:
        candidates.sort(key=lambda s: (s["buyers"], s["contracts"] + s["framework_appointments"]), reverse=True)
    out = []
    for s in candidates[:limit]:
        avg = typical(s)
        out.append({
            "supplier_key": s["key"],
            "supplier_name": s["supplier"],
            "supplier_id": s.get("supplier_id"),
            "stats": {
                "awards": s["contracts"] + s["framework_appointments"],
                "buyers": s["buyers"],
                "total_value": s.get("total_value"),
                "typical_contract": avg,
                "avg_contract": s.get("avg_contract"),
                "latest_signed": s.get("latest_signed"),
                "size_hint": None if avg is None else ("smaller contracts" if avg < 250_000 else ("larger contracts" if avg > 2_000_000 else "mid-sized contracts")),
            },
        })
    return out


def build_handoff_markdown(plan: dict[str, Any], suppliers: list[dict[str, Any]], log: list[dict[str, Any]]) -> str:
    """Everything the tender team needs from the engagement, as a markdown document."""
    included = [s for s in suppliers if s.get("included")]
    counts: dict[str, int] = {}
    for s in included:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    lines = [
        f"# Market engagement hand-over: {plan.get('title')}",
        "",
        f"- **Organisation:** {plan.get('organisation') or TODO}",
        f"- **Category:** {plan.get('category_label') or TODO}",
        f"- **Indicative value:** {money(plan.get('est_value')) or TODO}",
        f"- **Indicative term:** {('%g years' % float(plan['term_years'])) if plan.get('term_years') else TODO}",
        f"- **Engagement type:** {ENGAGEMENT_TYPES.get(plan.get('engagement_type'), plan.get('engagement_type'))}",
        f"- **Status:** {plan.get('status')}",
    ]
    if plan.get("published_url"):
        lines.append(f"- **Published notice:** {plan['published_url']}")
    if plan.get("published_at"):
        lines.append(f"- **Published on:** {_uk_date(plan['published_at'])}")
    lines += ["", "## Suppliers engaged", ""]
    if included:
        lines.append("| Supplier | Status | Note |")
        lines.append("|---|---|---|")
        for s in included:
            note = (s.get("note") or "").replace("|", "/").replace("\n", " ")
            lines.append(f"| {s['supplier_name']} | {SUPPLIER_STATUSES.get(s['status'], s['status'])} | {note} |")
        lines += ["", "Summary: " + ", ".join(f"{n} {SUPPLIER_STATUSES.get(k, k).lower()}" for k, n in sorted(counts.items()))]
    else:
        lines.append("No suppliers were recorded.")
    lines += ["", "## Engagement record", ""]
    if log:
        for entry in log:
            at = entry.get("at")
            stamp = at.strftime("%d %b %Y %H:%M") if isinstance(at, datetime) else str(at or "")[:16]
            lines.append(f"- {stamp}: {entry.get('message')}")
    else:
        lines.append("No entries.")
    lines += [
        "",
        "## Before you publish the tender",
        "",
        "- Reflect what you learned in the specification, lots and procurement route.",
        "- Make sure any information shared with suppliers during the engagement is available to every bidder.",
        "- Keep individual responses confidential, as set out in the engagement notice.",
        "- Record why you did or did not act on what you heard.",
        "",
        "_Generated by TenderFlow. Not legal advice._",
    ]
    return "\n".join(lines)
