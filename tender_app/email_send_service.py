"""Sending email through Outreach: doing what email_send.plan_send decided, and keeping the record.

Every function takes an open psycopg2 connection and (where Outreach is called) an OutreachClient, so tests give
it a fake transport and never touch the network. Nothing here reads the API key; the client holds it.

A recipient row is CLAIMED (inserted as "pending", committed) before Outreach is called. The unique dedupe hash
makes a second request for the same text and address lose that claim and skip, so a double click, two tabs or a
retry after a timeout cannot send twice. The same hash seeds the Idempotency-Key, which protects the call itself.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from tender_app import email_send as es
from tender_app import outreach_client as oc

STALE_PENDING_MINUTES = 60  # a claim whose outcome never came back may be retried after this long (same idempotency key)
MAX_REFRESH_LOOKUPS = 50


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def setup_hint(exc: oc.OutreachError) -> str | None:
    """What to fix in Outreach (not automated), when the error says it is a setup problem."""
    text = f"{exc.code} {exc.message}".lower()
    if "mailbox" in text:
        return "In Outreach, connect a mailbox (Email, Mailboxes), set up DKIM/DMARC and start its warm-up. Sending needs a mailbox that can send."
    if exc.status == 403 or "scope" in text:
        return "The Outreach key needs the email:write and email:approve permissions (Outreach, Keys)."
    if exc.status == 402:
        return "Top up credits in Outreach."
    if exc.status == 401:
        return "Outreach did not accept the key. Check OUTREACH_API_KEY on the server."
    return None


# ── what is already recorded ────────────────────────────────────────────────────────────────────

def sent_today(conn, key_mode: str) -> int:
    """Single sends made today (UTC) by the whole company with this kind of key: the daily cap is per company."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) FROM email_sends
               WHERE mode = 'single' AND key_mode = %s AND status IN ('sent', 'replied', 'bounced')
                 AND sent_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC')""",
            (key_mode,),
        )
        return int(cur.fetchone()[0])


def existing_sends(conn, hashes: Iterable[str]) -> dict[str, dict[str, Any]]:
    hashes = list(hashes)
    if not hashes:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT dedupe_hash, status, outreach_id, mode,
                      (status = 'pending' AND created_at < NOW() - make_interval(mins => %s)) AS stale
               FROM email_sends WHERE dedupe_hash = ANY(%s)""",
            (STALE_PENDING_MINUTES, hashes),
        )
        return {r[0]: {"status": r[1], "outreach_id": r[2], "mode": r[3], "stale": bool(r[4])} for r in cur.fetchall()}


def suppressed_among(conn, username: str, emails: Iterable[str]) -> set[str]:
    """Of these addresses, the ones we must not email: the suppression list (including bounces) and anyone this
    user marked as opted out in Growth Studio."""
    emails = sorted({e.strip().lower() for e in emails if e})
    if not emails:
        return set()
    out: set[str] = set()
    with conn.cursor() as cur:
        cur.execute("SELECT email FROM email_send_suppressions WHERE email = ANY(%s)", (emails,))
        out.update(r[0] for r in cur.fetchall())
        cur.execute(
            """SELECT LOWER(t.contact_email) FROM growth_campaign_targets t JOIN growth_campaigns c ON c.id = t.campaign_id
               WHERE c.username = %s AND t.status = 'opted_out' AND LOWER(t.contact_email) = ANY(%s)""",
            (username, emails),
        )
        out.update(r[0] for r in cur.fetchall())
    return out


def add_suppression(conn, email: str, reason: str, added_by: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO email_send_suppressions (email, reason, added_by) VALUES (%s, %s, %s)
               ON CONFLICT (email) DO NOTHING""",
            (email.strip().lower(), reason[:40], added_by),
        )


# ── claiming a row ──────────────────────────────────────────────────────────────────────────────

def _claim(conn, username: str, batch_id: str, item: Mapping[str, Any], mode: str, key_mode: str) -> bool:
    """Take the right to send this text to this address. False when it is already sent or in progress."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO email_sends (username, batch_id, recipient_email, first_name, company, subject, dedupe_hash,
                                        mode, key_mode, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
               ON CONFLICT (dedupe_hash) DO UPDATE SET username = EXCLUDED.username, batch_id = EXCLUDED.batch_id,
                   mode = EXCLUDED.mode, status = 'pending', error = NULL, outreach_id = NULL, message_id = NULL,
                   created_at = NOW(), checked_at = NULL
               WHERE email_sends.status IN ('failed', 'not_sent')
                  OR (email_sends.status = 'pending' AND email_sends.created_at < NOW() - make_interval(mins => %s))
               RETURNING id""",
            (username, batch_id, item["email"], item.get("first_name") or None, item.get("company") or None,
             item["send_subject"][:500], item["hash"], mode, key_mode, STALE_PENDING_MINUTES),
        )
        claimed = cur.fetchone() is not None
    conn.commit()
    return claimed


def _set(conn, hashes: list[str], **fields: Any) -> None:
    if not hashes:
        return
    cols = ", ".join(f"{k} = %s" for k in fields)
    with conn.cursor() as cur:
        cur.execute(f"UPDATE email_sends SET {cols}, checked_at = NOW() WHERE dedupe_hash = ANY(%s)",
                    (*fields.values(), hashes))
    conn.commit()


# ── sending ─────────────────────────────────────────────────────────────────────────────────────

def execute(conn, client: oc.OutreachClient, username: str, plan: Mapping[str, Any], *,
            with_first_name: bool = False) -> dict[str, Any]:
    """Carry out a plan from email_send.plan_send. Never raises for one recipient's problem; a fatal Outreach
    error (bad key, no credits, missing permission, rate limit, daily cap) stops the batch and says what was left."""
    batch_id = uuid.uuid4().hex[:24]
    key_mode = client.mode
    result: dict[str, Any] = {
        "batch_id": batch_id, "mode": key_mode, "route": plan.get("route"),
        "sent": [], "campaigns": [], "failed": [], "skipped": list(plan.get("skipped") or []),
        "daily_limited": [], "deferred": [], "stopped": None, "hint": None,
    }
    stop: dict[str, str | None] = {"reason": None}

    def halt(exc: oc.OutreachError) -> None:
        stop["reason"] = exc.message
        result["stopped"] = exc.message
        result["hint"] = setup_hint(exc)

    def defer(items: Iterable[Mapping[str, Any]], why: str) -> None:
        for it in items:
            result["deferred"].append({"email": it["email"], "reason": why})

    # 1. campaigns that were created earlier but never approved: approve the same campaign, do not create another
    for group in plan.get("resume") or []:
        cid = group["outreach_id"]
        if stop["reason"]:
            defer(group["items"], "Not attempted: " + stop["reason"])
            continue
        hashes = [it["hash"] for it in group["items"]]
        try:
            decided = oc.campaign_summary(client.decide_email_campaign(cid, "approve", es.campaign_idempotency_key(hashes, "approve")))
        except oc.OutreachError as exc:
            _set(conn, hashes, error=exc.message[:300])
            result["failed"].append({"email": ", ".join(it["email"] for it in group["items"][:3]), "step": "approve",
                                     "reason": exc.message, "recipients": len(hashes)})
            if exc.fatal:
                halt(exc)
            continue
        _set(conn, hashes, status="approved", campaign_state=decided["status"], error=None)
        result["campaigns"].append({"outreach_id": cid, "recipients": len(hashes), "status": "approved",
                                    "label": es.status_label("approved", "campaign"), "resumed": True})

    # 2. one campaign per group of two or more identical messages
    for group in plan.get("campaigns") or []:
        items = group["items"]
        if stop["reason"]:
            defer(items, "Not attempted: " + stop["reason"])
            continue
        claimed = []
        for it in items:
            if _claim(conn, username, batch_id, it, "campaign", key_mode):
                claimed.append(it)
            else:
                result["skipped"].append({"email": it["email"], "reason": "This message was already sent, or is being sent, to this address."})
        if not claimed:
            continue
        hashes = [it["hash"] for it in claimed]
        step = "create"
        cid = None
        try:
            payload = es.campaign_payload(group["subject"], group["body"], claimed, with_first_name=with_first_name)
            made = oc.campaign_summary(client.create_email_campaign(payload, es.campaign_idempotency_key(hashes)))
            if not made["id"]:
                raise oc.OutreachError("Outreach did not return a campaign id.", 502, "bad_response")
            cid = made["id"]
            _set(conn, hashes, outreach_id=cid, status="created", campaign_state=made["status"], error=None)
            step = "approve"
            decided = oc.campaign_summary(client.decide_email_campaign(cid, "approve", es.campaign_idempotency_key(hashes, "approve")))
        except oc.OutreachError as exc:
            if cid:  # made, not approved: kept as "created" so a retry approves this one
                _set(conn, hashes, error=exc.message[:300])
            else:
                _set(conn, hashes, status="pending" if exc.code == "unreachable" else "failed", error=exc.message[:300])
            result["failed"].append({"email": ", ".join(it["email"] for it in claimed[:3]), "step": step,
                                     "reason": exc.message, "recipients": len(claimed)})
            if exc.fatal:
                halt(exc)
            continue
        _set(conn, hashes, status="approved", campaign_state=decided["status"], error=None)
        result["campaigns"].append({"outreach_id": cid, "recipients": len(claimed), "status": "approved",
                                    "label": es.status_label("approved", "campaign"), "resumed": False})

    # 3. one /send each (a message nobody else shares, or a single recipient)
    singles = list(plan.get("singles") or [])
    for index, it in enumerate(singles):
        if stop["reason"]:
            defer(singles[index:], "Not attempted: " + stop["reason"])
            break
        if not _claim(conn, username, batch_id, it, "single", key_mode):
            result["skipped"].append({"email": it["email"], "reason": "This message was already sent, or is being sent, to this address."})
            continue
        try:
            answer = oc.send_summary(client.send_email(es.single_payload(it), es.single_idempotency_key(it["hash"])))
        except oc.OutreachError as exc:
            if exc.daily_limit:
                _set(conn, [it["hash"]], status="not_sent", error=exc.message[:300])
                result["daily_limited"].append({"email": it["email"], "reason": "Not sent: daily limit reached."})
                result["daily_limited"].extend({"email": r["email"], "reason": "Not sent: daily limit reached."}
                                               for r in singles[index + 1:])
                result["stopped"] = exc.message
                result["hint"] = setup_hint(exc)
                stop["reason"] = exc.message
                break
            # the request may have gone through when we could not reach Outreach: keep the claim, do not mark it failed
            _set(conn, [it["hash"]], status="pending" if exc.code == "unreachable" else "failed", error=exc.message[:300])
            result["failed"].append({"email": it["email"], "step": "send", "reason": exc.message})
            if exc.fatal:
                halt(exc)
            continue
        if answer["status"] == "sent":
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE email_sends SET status = 'sent', outreach_id = %s, message_id = %s, sent_at = NOW(),
                              error = NULL, checked_at = NOW() WHERE dedupe_hash = %s""",
                    (answer["id"], answer["id"], it["hash"]),
                )
            conn.commit()
            result["sent"].append({"email": it["email"], "outreach_id": answer["id"], "status": "sent"})
        else:  # only status == "sent" counts as sent
            note = f"Outreach answered \"{answer['status'] or 'no status'}\", not \"sent\"."
            _set(conn, [it["hash"]], status="not_sent", outreach_id=answer["id"], error=note)
            result["failed"].append({"email": it["email"], "step": "send", "reason": note})

    for it in plan.get("daily_limited") or []:
        result["daily_limited"].append({"email": it["email"], "reason": "Not sent: daily limit reached."})

    _audit(conn, username, batch_id, plan, result, key_mode)
    return result


def _audit(conn, username: str, batch_id: str, plan: Mapping[str, Any], result: Mapping[str, Any], key_mode: str) -> None:
    requested = (len(plan.get("singles") or []) + len(plan.get("daily_limited") or [])
                 + sum(len(g["items"]) for g in plan.get("campaigns") or [])
                 + sum(len(g["items"]) for g in plan.get("resume") or []) + len(plan.get("skipped") or []))
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO email_send_audit (username, batch_id, route, key_mode, requested, sent, campaigns, skipped, failed, deferred)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (username, batch_id, plan.get("route"), key_mode, requested, len(result["sent"]),
             sum(c["recipients"] for c in result["campaigns"]), len(result["skipped"]) + len(result["daily_limited"]),
             len(result["failed"]), len(result["deferred"])),
        )
    conn.commit()


# ── reading the record ──────────────────────────────────────────────────────────────────────────

def list_sends(conn, username: str, limit: int = 100, batch_id: str | None = None) -> list[dict[str, Any]]:
    args: list[Any] = [username]
    where = "username = %s"
    if batch_id:
        where += " AND batch_id = %s"
        args.append(batch_id)
    args.append(max(1, min(int(limit), 500)))
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT recipient_email, subject, mode, key_mode, outreach_id, status, campaign_state, error, sent_at,
                       created_at, checked_at, batch_id
                FROM email_sends WHERE {where} ORDER BY id DESC LIMIT %s""",
            args,
        )
        rows = cur.fetchall()
    return [
        {"email": r[0], "subject": r[1], "mode": r[2], "key_mode": r[3], "outreach_id": r[4], "status": r[5],
         "label": es.status_label(r[5], r[2]), "campaign_state": r[6], "error": r[7], "sent_at": _iso(r[8]),
         "created_at": _iso(r[9]), "checked_at": _iso(r[10]), "batch_id": r[11]}
        for r in rows
    ]


# ── refreshing from Outreach ────────────────────────────────────────────────────────────────────

def refresh(conn, client: oc.OutreachClient, username: str) -> dict[str, Any]:
    """Update this user's stored statuses from Outreach. Polling only; no webhook is needed.

    Campaigns: read the campaign (state, approval), then its emails (sent / replied / bounced) and match them to
    our rows by address. Single sends: read the email for a reply or bounce. A send whose outcome we never got
    is looked up by address. A row never moves backwards, and an answer with no status changes nothing."""
    out: dict[str, Any] = {"updated": 0, "checked": 0, "stopped": None, "hint": None}

    def apply(email: str, dedupe: str, incoming: str | None, *, reason: str | None = None,
              campaign_state: str | None = None, message_id: str | None = None) -> None:
        with conn.cursor() as cur:
            cur.execute("SELECT status, sent_at FROM email_sends WHERE dedupe_hash = %s AND username = %s", (dedupe, username))
            row = cur.fetchone()
        if not row:
            return
        status, sent_at = row
        new = es.merge_status(status, incoming)
        if new == "bounced" and status != "bounced":
            add_suppression(conn, email, "bounced", username)
        fields: dict[str, Any] = {"status": new}
        if campaign_state:
            fields["campaign_state"] = campaign_state[:60]
        if message_id:
            fields["message_id"] = message_id
        if reason and new in ("bounced", "replied"):
            fields["error"] = reason[:300]
        if new in ("sent", "replied", "bounced") and not sent_at:
            fields["sent_at"] = _now()
        _set(conn, [dedupe], **fields)
        if new != status:
            out["updated"] += 1

    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT outreach_id FROM email_sends
                   WHERE username = %s AND mode = 'campaign' AND outreach_id IS NOT NULL
                     AND status IN ('created', 'approved', 'sent') ORDER BY outreach_id DESC LIMIT %s""",
                (username, MAX_REFRESH_LOOKUPS),
            )
            campaign_ids = [r[0] for r in cur.fetchall()]
        for cid in campaign_ids:
            summary = oc.campaign_summary(client.get_email_campaign(cid))
            out["checked"] += 1
            approved = (summary.get("approval") or "").lower() in ("approved", "auto_approved")
            with conn.cursor() as cur:
                cur.execute("SELECT dedupe_hash, recipient_email FROM email_sends WHERE outreach_id = %s AND username = %s", (cid, username))
                rows = cur.fetchall()
            by_email = {}
            for h, email in rows:
                by_email[email] = h
                # Outreach says it is approved even though our approve call never came back: store that, never the reverse
                apply(email, h, "approved" if approved else None, campaign_state=summary.get("status"))
            for m in oc.message_list(client.list_email_messages(campaign_id=cid)):
                h = by_email.get(m["to"] or "")
                if h:
                    apply(m["to"], h, m["status"], reason=m["reason"], message_id=m["id"])
        with conn.cursor() as cur:
            cur.execute(
                """SELECT recipient_email, outreach_id, dedupe_hash FROM email_sends
                   WHERE username = %s AND mode = 'single' AND status IN ('sent', 'pending')
                   ORDER BY id DESC LIMIT %s""",
                (username, MAX_REFRESH_LOOKUPS),
            )
            singles = cur.fetchall()
        for email, mid, h in singles:
            if mid:
                m = oc.message_summary(client.get_email_message(mid))
                out["checked"] += 1
                apply(email, h, m["status"], reason=m["reason"])
            else:  # claimed, but the answer never came back: look for it by address
                found = [m for m in oc.message_list(client.list_email_messages(q=email))
                         if m["to"] == email and m["status"] in ("sent", "replied", "bounced")]
                out["checked"] += 1
                if found:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE email_sends SET outreach_id = %s WHERE dedupe_hash = %s AND outreach_id IS NULL", (found[0]["id"], h))
                    conn.commit()
                    apply(email, h, found[0]["status"], reason=found[0]["reason"], message_id=found[0]["id"])
    except oc.OutreachError as exc:
        out["stopped"] = exc.message
        out["hint"] = setup_hint(exc)
    return out
