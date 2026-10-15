"""Growth Studio -> Outreach: which campaign buyers are sent through Outreach, and what each email says.

Pure logic, no network and no database (the HTTP layer is in blueprints/growth_bp.py, the API client in
outreach_client.py), so every rule here is tested without either.

One Outreach campaign is made per buyer. Growth Studio messages carry per-buyer facts (the contract
title, its end date, the planning scheme), and Outreach's own merge fields are only first name, last
name and company, so the message is filled in here, for that buyer, and sent as a one-lead, one-step
campaign. The route then approves it, which is what makes Outreach send it.

A buyer is left out, with the reason shown to the user, when:
  * the buyer is switched off, opted out, on the do-not-contact list, or was already marked sent;
  * there is no valid contact email, or it looks like a named person's address (unless the user said so);
  * it was already sent through Outreach (so sending twice never emails a buyer twice);
  * the message needs a value this buyer has none for (it would read "ends on ." );
and the whole send is refused when the message has no subject or body, no opt-out line, or a merge
field that does not exist.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping

from tender_app import growth_studio as gs

MAX_PER_PUSH = 25  # Outreach allows 30 creating requests a minute per key; stay under it

STATUS_LABELS = {
    "created": "Draft in Outreach",
    "failed": "Could not be created",
}


def _name_part(text: str, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


def idempotency_key(username: str, campaign_id: int, target_id: int, tag: str = "") -> str:
    """Stable for one buyer in one campaign (and `tag`, such as the message hash or "approve"), so a retried
    request is answered with the first result instead of doing the thing twice."""
    digest = hashlib.sha256((username or "").encode("utf-8")).hexdigest()[:10]
    base = f"tf-growth-{digest}-{int(campaign_id)}-{int(target_id)}"
    return f"{base}-{tag}" if tag else base


def retry_tag(record: Mapping[str, Any] | None) -> str:
    """"" for a first attempt; a short tag for a retry after a request that definitely FAILED, so Outreach does
    not answer the retry by replaying the first failure (an Idempotency-Key makes a repeat return the first
    answer, errors included). A request that may have gone through (unreachable, kept as "pending") keeps its key,
    so the repeat is answered with what really happened and nobody is emailed twice."""
    rec = record or {}
    if not rec.get("error") or rec.get("status") == "pending":
        return ""
    return "-r" + hashlib.sha256(str(rec.get("checked_at") or rec.get("error") or "").encode("utf-8")).hexdigest()[:6]


def message_hash(subject: str, body: str) -> str:
    """Identifies the exact text a draft was made from, so an edited message is never sent under an old draft."""
    return hashlib.sha256((subject + chr(10) + body).encode("utf-8")).hexdigest()[:16]


def message_blockers(subject: str | None, body: str | None) -> list[str]:
    """Reasons the whole push cannot go ahead, in the user's words. Empty when it can."""
    out: list[str] = []
    if not (subject or "").strip() or not (body or "").strip():
        out.append("Write the subject and the message before you send it to Outreach.")
        return out
    if not gs.has_opt_out(body):
        out.append("The message has no way to opt out. Add a line such as: " + gs.OPT_OUT_LINE)
    unknown = sorted({n for n in gs.template_names(subject) + gs.template_names(body) if n not in gs.MERGE_FIELD_NAMES})
    if unknown:
        out.append("Not a merge field: " + ", ".join("{{" + n + "}}" for n in unknown) + ". Fix it before you send.")
    return out


def build_payload(campaign_name: str, buyer: str, email: str, subject: str, body: str) -> dict[str, Any]:
    """The Outreach request for one buyer: the text is already filled in for them."""
    return {
        "name": _name_part(f"{campaign_name} - {buyer}", 120),
        "goal": _name_part(subject, 200),
        "audience": _name_part(f"Procurement team at {buyer}", 120),
        "emails": 1,
        "steps": [{"subject": subject, "body": body, "delay_days": 0}],
        "leads": [{"email": email, "company": _name_part(buyer, 120)}],
    }


SENT_NOW = "sent_now"  # stored as the approval of a buyer sent with /v1/email/send (there is no campaign to approve)


def find_mailbox(boxes: Iterable[Mapping[str, Any]], ref: str | None) -> dict[str, Any] | None:
    """The connected mailbox named by `ref` (its id or its address, any case), as {id, email, can_send, status, ref}.
    `ref` is what is sent to Outreach: the id when there is one, else the address."""
    want = (ref or "").strip().lower()
    if not want:
        return None
    for b in boxes or []:
        ident = str(b.get("id") or "")
        email = str(b.get("email") or "")
        if want in (ident.lower(), email.lower()) and email:
            return {"id": b.get("id"), "email": email, "can_send": b.get("can_send"), "status": b.get("status"),
                    "ref": ident or email}
    return None


def build_send_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    """The /v1/email/send request for one buyer: the text is already filled in, so no merge fields are left."""
    return {
        "to": item["email"],
        "subject": item["subject"],
        "body": item["text"],
        "company": _name_part(item.get("buyer") or "", 120),
    }


def use_single_send(items: list[Mapping[str, Any]], records: Mapping[int, Mapping[str, Any]]) -> bool:
    """Exactly one email to go out, and no Outreach campaign was already made for it (that one is approved instead,
    so the buyer is never emailed twice): send it now, without a campaign's warm-up and sending hours."""
    if len(items) != 1:
        return False
    rec = records.get(items[0]["target_id"]) or {}
    return not rec.get("outreach_id")


def plan_push(
    campaign: Mapping[str, Any],
    targets: Iterable[Mapping[str, Any]],
    opted_out: set[str],
    sender: Mapping[str, str],
    already_pushed: set[int],
    *,
    only_ids: Iterable[int] | None = None,
    allow_personal: bool = False,
    limit: int = MAX_PER_PUSH,
) -> dict[str, Any]:
    """What would be sent: {"items": [{target_id, buyer, email, payload}], "skipped": [{target_id, buyer,
    reason}], "deferred": n, "blockers": [..]}. `deferred` counts buyers that qualify but are past `limit`."""
    subject = campaign.get("subject") or ""
    body = campaign.get("body") or ""
    blockers = message_blockers(subject, body)
    plan: dict[str, Any] = {"items": [], "skipped": [], "deferred": 0, "blockers": blockers}
    if blockers:
        return plan
    wanted = None if only_ids is None else set(only_ids)
    for t in targets:
        tid = t.get("id")
        buyer = t.get("buyer_name") or ""
        if wanted is not None and tid not in wanted:
            continue

        def skip(reason: str) -> None:
            plan["skipped"].append({"target_id": tid, "buyer": buyer, "reason": reason})

        if not t.get("included"):
            skip("Switched off in this campaign.")
        elif t.get("status") == "opted_out" or t.get("buyer_key") in opted_out:
            skip("Opted out or on your do-not-contact list.")
        elif t.get("status") not in (None, "not_sent"):
            skip("Already recorded as contacted.")
        elif tid in already_pushed:
            skip("Already sent through Outreach.")
        else:
            email = (t.get("contact_email") or "").strip()
            if not email:
                skip("No contact email. Add a published, role-based inbox.")
            elif not gs.valid_email(email):
                skip("The contact email is not a valid address.")
            elif gs.looks_personal_email(email) and not allow_personal:
                skip("The address looks like a named person's. Use a role inbox such as procurement@, or confirm it.")
            else:
                values = gs.fields_for_target(t, sender)
                subj, miss_s, _ = gs.render_template(subject, values)
                text, miss_b, _ = gs.render_template(body, values)
                missing = sorted(set(miss_s + miss_b))
                if missing:
                    skip("This buyer has no value for " + ", ".join("{{" + m + "}}" for m in missing) + ".")
                elif len(plan["items"]) >= limit:
                    plan["deferred"] += 1
                else:
                    plan["items"].append({
                        "target_id": tid, "buyer": buyer, "email": email, "hash": message_hash(subj, text),
                        "subject": subj, "text": text,
                        "payload": build_payload(campaign.get("name") or "Growth Studio", buyer, email, subj, text),
                    })
    return plan
