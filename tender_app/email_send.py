"""Sending email through Outreach by Aivhub: the rules, with no network and no database.

The HTTP layer is blueprints/email_bp.py, the persistence is email_send_store.py, the API client is
outreach_client.py. Everything decided here is unit-tested without any of them.

ROUTING (choose_route / plan_send)
    one recipient                          -> POST /v1/email/send          ("single"; sent at once)
    two or more, the same message          -> one campaign with all of them as leads, then approved
    two or more, different messages        -> one /v1/email/send each, up to the daily cap; the rest are
                                              reported as "not sent: daily limit" and nothing is queued
A batch can mix the two (three people get one text, a fourth gets another): every group of two or more
identical messages becomes a campaign and every message nobody else shares is a single send.

"The same message" means the same text after {{firstName}} / {{company}} are filled in for each person,
because it is not confirmed that Outreach fills merge fields per lead inside a campaign step. Once that is
confirmed with a test key, OUTREACH_CAMPAIGN_MERGE_FIELDS=1 sends the unfilled template with each lead's
first_name and company, and people are grouped by template instead.

A recipient is left out, with the reason, and never fails the batch, when there is no valid address, the
address is listed twice, it is on our suppression list, or the same text was already sent to it.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from tender_app import growth_studio as gs

DEFAULT_DAILY_CAP = 25  # Outreach: 25 single sends a day per company
DEFAULT_MAX_BATCH = 200  # one user action; the rest is reported as skipped
MAX_SUBJECT = gs.MAX_SUBJECT
MAX_BODY = gs.MAX_BODY

# {{firstName}} and {{company}} are the only merge fields; they map to the fields Outreach takes.
MERGE_FIELDS = {"firstname": "first_name", "company": "company"}
_TOKEN = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")

# Row statuses. "created" is a campaign that exists in Outreach but was not approved yet (so it sends nothing).
ACTIVE_STATUSES = ("sent", "approved", "replied", "bounced")
RETRYABLE_STATUSES = ("failed", "not_sent")
_RANK = {"pending": 0, "failed": 0, "not_sent": 0, "draft": 0, "created": 1, "approved": 2, "sent": 3,
         "replied": 4, "bounced": 4}

STATUS_LABELS = {
    "pending": "In progress, outcome not confirmed yet",
    "created": "Campaign created, not approved yet (nothing is sent until it is approved)",
    "approved": "Approved, sending in the sending window",
    "sent": "Sent",
    "replied": "Replied",
    "bounced": "Bounced",
    "failed": "Not sent",
    "not_sent": "Not sent",
}


# ── routing ─────────────────────────────────────────────────────────────────────────────────────

def choose_route(recipients: int, distinct_messages: int) -> str:
    """The routing rule as one function: "none", "single" (one /send), "campaign" (one campaign for all of
    them) or "per_recipient" (one /send each, up to the daily cap)."""
    if recipients <= 0:
        return "none"
    if recipients == 1:
        return "single"
    if distinct_messages <= 1:
        return "campaign"
    return "per_recipient"


# ── recipients and message checks ───────────────────────────────────────────────────────────────

def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def normalise_recipient(raw: Any) -> dict[str, str]:
    """{email, first_name, company} from a dict (or a bare address), trimmed and lower-cased where it is an address."""
    if isinstance(raw, str):
        raw = {"email": raw}
    if not isinstance(raw, Mapping):
        return {"email": "", "first_name": "", "company": ""}
    email = str(raw.get("email") or raw.get("to") or "").strip().lower()
    return {"email": email, "first_name": _clean(raw.get("first_name") or raw.get("firstName"), 120),
            "company": _clean(raw.get("company"), 200)}


def template_fields(text: str | None) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in _TOKEN.finditer(text or "")))


def message_blockers(subject: str | None, body: str | None) -> list[str]:
    """Reasons the whole send is refused, in the user's words. Empty when it can go."""
    subject, body = (subject or "").strip(), (body or "").strip()
    out: list[str] = []
    if not subject:
        out.append("Write a subject before you send.")
    if not body:
        out.append("Write the message before you send.")
    if len(subject) > MAX_SUBJECT:
        out.append(f"The subject is longer than {MAX_SUBJECT} characters.")
    if len(body) > MAX_BODY:
        out.append(f"The message is longer than {MAX_BODY} characters.")
    unknown = sorted({n for n in template_fields(subject) + template_fields(body) if n.lower() not in MERGE_FIELDS})
    if unknown:
        out.append("Not a merge field: " + ", ".join("{{" + n + "}}" for n in unknown)
                   + ". Use {{firstName}} or {{company}}, or remove it.")
    if body and not gs.has_opt_out(body):
        out.append("The message has no way to opt out. Add a line such as: " + gs.OPT_OUT_LINE)
    return out


def render(text: str, recipient: Mapping[str, str]) -> tuple[str, list[str]]:
    """Fill the merge fields for one person: (text, names that had no value for them)."""
    missing: list[str] = []

    def fill(match: re.Match) -> str:
        name = match.group(1)
        key = MERGE_FIELDS.get(name.lower())
        if key is None:
            return match.group(0)
        value = (recipient.get(key) or "").strip()
        if not value and name not in missing:
            missing.append(name)
        return value

    return _TOKEN.sub(fill, text or ""), missing


# ── identity of a send ──────────────────────────────────────────────────────────────────────────

def dedupe_hash(key_mode: str, email: str, subject: str, body: str) -> str:
    """Identifies "this exact text to this address", in test or live mode (a test send must not block a live one)."""
    raw = "\n".join((key_mode, email.strip().lower(), subject, body))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def single_idempotency_key(hash_: str) -> str:
    return "tf-mail-" + hash_[:40]


def campaign_idempotency_key(hashes: Iterable[str], tag: str = "") -> str:
    digest = hashlib.sha256("\n".join(sorted(hashes)).encode("utf-8")).hexdigest()[:40]
    return "tf-mailc-" + digest + (f"-{tag}" if tag else "")


# ── status ──────────────────────────────────────────────────────────────────────────────────────

def merge_status(current: str | None, incoming: str | None) -> str:
    """The status to store after Outreach reports `incoming`. A refresh never moves an email backwards
    (approved/sent never become draft) and an answer with no status, or one we do not know, changes nothing."""
    cur = (current or "pending").lower()
    new = (incoming or "").lower()
    if new not in _RANK:
        return cur
    if cur not in _RANK:
        return new
    return new if _RANK[new] >= _RANK[cur] else cur


def status_label(status: str | None, mode: str | None = None) -> str:
    s = (status or "pending").lower()
    if s == "approved" and mode == "single":
        return "Sent"
    return STATUS_LABELS.get(s, s.replace("_", " ").capitalize())


# ── planning ────────────────────────────────────────────────────────────────────────────────────

def _item(rec: Mapping[str, str], subject: str, body: str, key_mode: str, send_subject: str, send_body: str) -> dict[str, Any]:
    return {
        "email": rec["email"], "first_name": rec["first_name"], "company": rec["company"],
        "subject": subject, "body": body, "send_subject": send_subject, "send_body": send_body,
        "hash": dedupe_hash(key_mode, rec["email"], subject, body),
    }


def plan_send(
    subject: str,
    body: str,
    recipients: Iterable[Any],
    *,
    key_mode: str = "test",
    suppressed: "set[str] | frozenset[str] | Callable[[list[str]], set[str]]" = frozenset(),
    existing: "Mapping[str, Mapping[str, Any]] | Callable[[list[str]], Mapping[str, Mapping[str, Any]]] | None" = None,
    sent_today: int = 0,
    daily_cap: int = DEFAULT_DAILY_CAP,
    max_batch: int = DEFAULT_MAX_BATCH,
    merge_in_campaign: bool = False,
) -> dict[str, Any]:
    """What would be sent, and what would not and why.

    `existing` maps a dedupe hash to {"status", "outreach_id", "mode", "stale"} for sends already recorded, and
    `suppressed` is the set of addresses not to email; either may instead be a function that is given the
    candidate hashes / addresses once and returns that, so a route can answer both with one query each.
    Returns {"blockers", "singles", "campaigns", "resume", "daily_limited", "skipped", "route"}:
      singles       [item]                      one POST /v1/email/send each
      campaigns     [{"subject","body","items"}] one new campaign each, then approved
      resume        [{"outreach_id","items"}]    campaigns created earlier but never approved: approve, do not recreate
      daily_limited [item]                      "not sent: daily limit"
      skipped       [{"email","reason"}]
    """
    subject = (subject or "").strip()
    body = (body or "").strip()
    plan: dict[str, Any] = {"blockers": message_blockers(subject, body), "singles": [], "campaigns": [], "resume": [],
                            "daily_limited": [], "skipped": [], "route": "none"}
    if plan["blockers"]:
        return plan

    skipped = plan["skipped"]
    seen: set[str] = set()
    staged: list[dict[str, Any]] = []
    from_tokens = bool(template_fields(subject) or template_fields(body))
    for raw in recipients:
        rec = normalise_recipient(raw)
        email = rec["email"]
        if not email:
            skipped.append({"email": "", "reason": "No email address."})
            continue
        if not gs.valid_email(email):
            skipped.append({"email": email, "reason": "Not a valid email address."})
            continue
        if email in seen:
            skipped.append({"email": email, "reason": "Listed more than once; sent once."})
            continue
        seen.add(email)
        send_subject, miss_s = render(subject, rec)
        send_body, miss_b = render(body, rec)
        missing = sorted(set(miss_s + miss_b))
        if missing and not (merge_in_campaign and from_tokens):
            skipped.append({"email": email, "reason": "No value for " + ", ".join("{{" + m + "}}" for m in missing) + "."})
            continue
        staged.append(_item(rec, send_subject, send_body, key_mode, send_subject, send_body))

    blocked = set(suppressed([i["email"] for i in staged]) if callable(suppressed) else suppressed)
    prior_sends = existing([i["hash"] for i in staged]) if callable(existing) else (existing or {})
    candidates: list[dict[str, Any]] = []
    resume: dict[str, list[dict[str, Any]]] = {}
    for item in staged:
        email = item["email"]
        if email in blocked:
            skipped.append({"email": email, "reason": "Opted out or suppressed. Not emailed."})
            continue
        prior = prior_sends.get(item["hash"])
        if prior:
            st = (prior.get("status") or "").lower()
            if st in ACTIVE_STATUSES:
                skipped.append({"email": email, "reason": "This message was already sent to this address."})
                continue
            if st == "created" and prior.get("mode") == "campaign" and prior.get("outreach_id"):
                resume.setdefault(str(prior["outreach_id"]), []).append(item)
                continue
            if st == "pending" and not prior.get("stale"):
                skipped.append({"email": email, "reason": "A send to this address is still in progress; refresh to see its outcome."})
                continue
        candidates.append(item)

    # one user action is capped; the overflow is reported, not queued
    limit = max(1, int(max_batch))
    kept = candidates[:limit]
    for item in candidates[limit:]:
        skipped.append({"email": item["email"], "reason": f"Over the limit of {limit} emails for one send. Send these in the next batch."})
    plan["resume"] = [{"outreach_id": cid, "items": items} for cid, items in resume.items()]

    # group by what each person would actually receive (or by the template when Outreach fills the fields)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in kept:
        if merge_in_campaign and from_tokens:
            key = (subject, body)
        else:
            key = (item["send_subject"], item["send_body"])
        groups.setdefault(key, []).append(item)

    singles: list[dict[str, Any]] = []
    for (g_subject, g_body), items in groups.items():
        if choose_route(len(items), 1) == "campaign":
            plan["campaigns"].append({"subject": g_subject, "body": g_body, "items": items})
        else:
            singles.extend(items)

    remaining = max(0, int(daily_cap) - int(sent_today))
    plan["singles"] = singles[:remaining]
    plan["daily_limited"] = singles[remaining:]
    n_people = len(kept)
    kinds = {k for k, present in (("single", plan["singles"] or plan["daily_limited"]), ("campaign", plan["campaigns"])) if present}
    if plan["resume"] and not kinds:
        plan["route"] = "campaign"
    elif len(kinds) == 2:
        plan["route"] = "mixed"
    elif "campaign" in kinds:
        plan["route"] = "campaign"
    elif n_people == 1:
        plan["route"] = "single"
    elif n_people > 1:
        plan["route"] = "per_recipient"
    return plan


# ── request bodies ──────────────────────────────────────────────────────────────────────────────

def single_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"to": item["email"], "subject": item["send_subject"], "body": item["send_body"]}
    if item.get("first_name"):
        out["first_name"] = item["first_name"]
    if item.get("company"):
        out["company"] = item["company"]
    return out


def campaign_payload(subject: str, body: str, items: list[Mapping[str, Any]], when: datetime | None = None,
                     with_first_name: bool = False) -> dict[str, Any]:
    """Our own `steps`; never `personalise` (that would ask Outreach's AI to write the emails)."""
    day = (when or datetime.utcnow()).strftime("%Y-%m-%d")
    leads = []
    for item in items:
        lead: dict[str, Any] = {"email": item["email"]}
        if item.get("company"):
            lead["company"] = item["company"]
        if with_first_name and item.get("first_name"):
            lead["first_name"] = item["first_name"]
        leads.append(lead)
    return {
        "name": f"TenderFlow email {day} ({len(items)})"[:120],
        "goal": subject[:200],
        "audience": "Contacts chosen in TenderFlow",
        "emails": 1,
        "steps": [{"subject": subject, "body": body, "delay_days": 0}],
        "leads": leads,
    }
