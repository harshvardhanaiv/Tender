"""Grants a user has saved: their stage, notes and reminders, the weekly "new grants that fit" email, and the emails.

A saved grant is not a tender, so it lives here and not in the pipeline table: it never reaches tender totals, buyer
analytics or the tender emails. The Pipeline page shows it under its own "Grants" kind.

Nothing here sends mail by itself. `send_reminders` and `run_digest` take the sender as an argument (the app passes its
SMTP function), and the digest only goes to users who switched it on."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from html import escape
from typing import Any, Callable
from urllib.parse import quote

from tender_app import funding, uk_time

STAGES = ("interested", "applying", "submitted", "awarded", "not_funded")
STAGE_LABEL = {"interested": "Interested", "applying": "Applying", "submitted": "Submitted", "awarded": "Awarded", "not_funded": "Not funded"}
ACTIVE_STAGES = ("interested", "applying")  # a grant already submitted, won or lost needs no deadline reminder
DIGEST_EVERY_DAYS = 7
DIGEST_MAX = 8
NOTES_MAX = 2000


def ensure_schema(cur: Any) -> None:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS funding_saved (
               username TEXT NOT NULL,
               label    VARCHAR(300) NOT NULL,
               saved_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
               PRIMARY KEY (username, label)
           )"""
    )
    cur.execute("ALTER TABLE funding_saved ADD COLUMN IF NOT EXISTS stage VARCHAR(20) NOT NULL DEFAULT 'interested'")
    cur.execute("ALTER TABLE funding_saved ADD COLUMN IF NOT EXISTS notes TEXT NOT NULL DEFAULT ''")
    cur.execute("ALTER TABLE funding_saved ADD COLUMN IF NOT EXISTS remind BOOLEAN NOT NULL DEFAULT TRUE")
    cur.execute("ALTER TABLE funding_saved ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')")
    cur.execute(
        """CREATE TABLE IF NOT EXISTS funding_digest_prefs (
               username  TEXT PRIMARY KEY,
               enabled   BOOLEAN NOT NULL DEFAULT FALSE,
               last_sent TIMESTAMP
           )"""
    )


# ── usage events ─────────────────────────────────────────────────────────────────────────────────

EVENTS = ("funding_filter_changed", "funding_search", "funding_card_opened", "funding_apply_clicked", "funding_saved", "funding_ai_run", "funding_export")
_DETAIL_MAX = 400


def record_event(cur: Any, username: str, event: str, detail: Any) -> str | None:
    """Keep one usage event. Returns an error message, or None when it was kept."""
    import json

    if event not in EVENTS:
        return "Unknown event."
    text = json.dumps(detail if isinstance(detail, dict) else {}, separators=(",", ":"), ensure_ascii=False)
    if len(text) > _DETAIL_MAX:
        return "Event detail is too long."
    cur.execute(
        """CREATE TABLE IF NOT EXISTS funding_events (
               id SERIAL PRIMARY KEY, username TEXT NOT NULL, event VARCHAR(40) NOT NULL, detail TEXT NOT NULL,
               at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'))"""
    )
    cur.execute("INSERT INTO funding_events (username, event, detail) VALUES (%s, %s, %s)", (username, event, text))
    return None


# ── the user's tracked grants ───────────────────────────────────────────────────────────────────

_TRACKED = """
    SELECT s.label, s.stage, s.notes, s.remind, s.saved_at, s.updated_at,
           g.name, g.funder, g.min_award, g.max_award, g.total_award, g.close_date, g.close_at, g.ongoing, g.url, g.apply_url
    FROM funding_saved s LEFT JOIN funding_opportunities g ON g.label = s.label
    WHERE s.username = %s ORDER BY s.updated_at DESC
"""


def tracked(cur: Any, username: str, today: date | None = None) -> list[dict[str, Any]]:
    """The user's saved grants with what the service says about each now. A grant that has left the service is `listed: False`."""
    today = today or uk_time.london_now().date()
    ensure_schema(cur)
    cur.execute("SELECT to_regclass('public.funding_opportunities') IS NOT NULL")
    if not cur.fetchone()[0]:
        return []
    cur.execute(_TRACKED, (username,))
    out = []
    for (label, stage, notes, remind, saved_at, updated_at, name, funder, mn, mx, total, close_date, close_at, ongoing, url, apply_url) in cur.fetchall():
        listed = name is not None
        days = None if (not listed or ongoing or not close_date) else (date.fromisoformat(close_date) - today).days
        out.append({
            "label": label, "stage": stage if stage in STAGES else "interested", "notes": notes or "", "remind": bool(remind),
            "saved_at": saved_at.isoformat() if saved_at else None, "updated_at": updated_at.isoformat() if updated_at else None,
            "listed": listed, "name": name or label.replace("-", " ").title(), "funder": funder,
            "min_award": float(mn) if mn is not None else None, "max_award": float(mx) if mx is not None else None,
            "total_award": float(total) if total is not None else None,
            "close_date": close_date, "close_at": close_at, "ongoing": bool(ongoing), "days_left": days,
            "url": url, "apply_url": apply_url,
        })
    return out


def update(cur: Any, username: str, label: str, *, stage: Any = None, notes: Any = None, remind: Any = None) -> str | None:
    """Change a tracked grant. Returns an error message, or None when it was changed."""
    if stage is not None and stage not in STAGES:
        return "stage must be one of: " + ", ".join(STAGES) + "."
    if notes is not None and (not isinstance(notes, str) or len(notes) > NOTES_MAX):
        return f"notes must be text of up to {NOTES_MAX} characters."
    if remind is not None and not isinstance(remind, bool):
        return "remind must be true or false."
    ensure_schema(cur)
    sets, params = [], []
    for column, value in (("stage", stage), ("notes", notes), ("remind", remind)):
        if value is not None:
            sets.append(f"{column} = %s")
            params.append(value)
    if not sets:
        return "Nothing to change."
    cur.execute(f"UPDATE funding_saved SET {', '.join(sets)}, updated_at = (NOW() AT TIME ZONE 'utc') WHERE username = %s AND label = %s", (*params, username, label))
    return None if cur.rowcount else "That grant is not in your list."


# ── deadline reminders ───────────────────────────────────────────────────────────────────────────

def allowed_days(threshold: Any) -> list[int]:
    """The days-before-closing the user asked to be reminded on (same wording as the tender reminders)."""
    text = str(threshold or "7 days before").lower()
    for days in (7, 3, 1):
        if str(days) in text:
            return [days]
    return [1, 3, 7]


def due_reminders(cur: Any, now: datetime | None = None) -> list[dict[str, Any]]:
    """Saved grants closing on a day the owner wants a reminder for, owners with reminders switched on."""
    now = now or uk_time.london_now()
    ensure_schema(cur)
    cur.execute(
        """SELECT s.username, s.label, g.name, g.funder, g.close_date, g.close_at, g.url, g.apply_url,
                  u.email, u.email_alerts_enabled, u.deadline_reminders_enabled, u.deadline_reminders_threshold
           FROM funding_saved s
           JOIN funding_opportunities g ON g.label = s.label
           JOIN users u ON u.username = s.username
           WHERE s.remind AND s.stage = ANY(%s) AND NOT g.ongoing AND g.close_date IS NOT NULL""",
        (list(ACTIVE_STAGES),),
    )
    out = []
    for username, label, name, funder, close_date, close_at, url, apply_url, email, alerts_on, reminders_on, threshold in cur.fetchall():
        if alerts_on is False or reminders_on is False:
            continue
        if close_at and close_at < now.strftime("%Y-%m-%dT%H:%M"):
            continue  # already closed
        days = (date.fromisoformat(close_date) - now.date()).days
        if days not in allowed_days(threshold):
            continue
        to = (email or username or "").strip()
        if "@" not in to:
            continue
        out.append({"username": username, "to": to, "label": label, "name": name, "funder": funder, "close_date": close_date,
                    "close_at": close_at, "days": days, "url": apply_url or url})
    return out


def _base_url() -> str:
    try:
        from tender_app.config import get_app_base_url
        return get_app_base_url()
    except Exception:  # noqa: BLE001
        return os.environ.get("APP_BASE_URL", "https://tender.civenta.co.uk").rstrip("/")


def _time(close_at: str | None) -> str:
    if not close_at or "T" not in close_at:
        return ""
    hour, minute = int(close_at[11:13]), close_at[14:16]
    return f", {hour % 12 or 12}:{minute}{'pm' if hour >= 12 else 'am'}"


def _day(iso: str | None) -> str:
    try:
        d = date.fromisoformat(iso or "")
    except ValueError:
        return ""
    return f"{d.day} {d.strftime('%b %Y')}"


def _shell(title: str, body: str, footer: str) -> str:
    return (
        '<div style="font-family:Inter,-apple-system,Segoe UI,sans-serif;max-width:560px;margin:0 auto;padding:24px;color:#0C1D30">'
        f'<h2 style="margin:0 0 12px;font-size:20px">{escape(title)}</h2>{body}'
        f'<p style="margin:24px 0 0;font-size:12px;color:#4A5C70">{footer}</p></div>'
    )


def reminder_email(item: dict[str, Any]) -> tuple[str, str]:
    days = item["days"]
    when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
    subject = f"Grant closing {when}: {item['name'][:70]}"
    closes = _day(item["close_date"]) + _time(item["close_at"])
    link = f'{_base_url()}/funding/{quote(item["label"])}'
    body = (
        f'<p style="margin:0 0 8px"><strong>{escape(item["name"])}</strong>' + (f" ({escape(item['funder'])})" if item.get("funder") else "") + "</p>"
        f'<p style="margin:0 0 16px">Applications close <strong>{escape(closes)}</strong>, {when}.</p>'
        f'<p style="margin:0"><a href="{escape(link)}" style="background:#0F6B66;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:600">Open in TenderFlow</a></p>'
    )
    return subject, _shell("A grant you saved is closing soon", body, "You get this because you saved the grant and have deadline reminders on. Turn reminders off for it in Pipeline, Grants.")


def send_reminders(get_db: Callable[[], Any], send: Callable[..., bool] | None = None, now: datetime | None = None) -> int:
    """Email each due reminder once per grant and day-threshold. Returns how many were sent."""
    if send is None:
        from server import _send_smtp_email as send  # the app's own sender
    conn = get_db()
    sent = 0
    try:
        cur = conn.cursor()
        for item in due_reminders(cur, now):
            key = "grant:" + item["label"]
            cur.execute("SELECT 1 FROM email_notifications WHERE username = %s AND tender_key = %s AND days_threshold = %s", (item["username"], key, item["days"]))
            if cur.fetchone():
                continue
            subject, html = reminder_email(item)
            if send(item["to"], subject, html):
                cur.execute("INSERT INTO email_notifications (username, tender_key, days_threshold) VALUES (%s, %s, %s)", (item["username"], key, item["days"]))
                conn.commit()
                sent += 1
        cur.close()
    finally:
        conn.close()
    return sent


# ── weekly email of new grants that fit ──────────────────────────────────────────────────────────

def get_digest_pref(cur: Any, username: str) -> dict[str, Any]:
    ensure_schema(cur)
    cur.execute("SELECT enabled, last_sent FROM funding_digest_prefs WHERE username = %s", (username,))
    row = cur.fetchone()
    return {"enabled": bool(row[0]) if row else False, "last_sent": row[1].isoformat() if row and row[1] else None}


def set_digest_pref(cur: Any, username: str, enabled: bool, now: datetime | None = None) -> None:
    """Switching on starts the clock now: only grants that appear after this moment are ever emailed."""
    ensure_schema(cur)
    now = now or datetime.utcnow()
    cur.execute(
        """INSERT INTO funding_digest_prefs (username, enabled, last_sent) VALUES (%s, %s, %s)
           ON CONFLICT (username) DO UPDATE SET enabled = EXCLUDED.enabled,
           last_sent = CASE WHEN EXCLUDED.enabled AND NOT funding_digest_prefs.enabled THEN EXCLUDED.last_sent ELSE funding_digest_prefs.last_sent END""",
        (username, enabled, now),
    )


def digest_email(grants: list[dict[str, Any]], profile_name: str, unsubscribe_url: str) -> tuple[str, str]:
    n = len(grants)
    subject = f"{n} new grant{'s' if n != 1 else ''} that fit{'s' if n == 1 else ''} {profile_name or 'your company'}"
    rows = ""
    for g in grants[:DIGEST_MAX]:
        link = f'{_base_url()}/funding/{quote(g["label"])}'
        closes = "Rolling, no fixed deadline" if g.get("ongoing") else ("Closes " + _day(g.get("close_date")) + _time(g.get("close_at")) if g.get("close_date") else "")
        why = (g.get("fit") or {}).get("why") or ""
        rows += (
            '<div style="border:1px solid #DCE2E9;border-radius:10px;padding:12px 14px;margin:0 0 10px">'
            f'<div style="font-weight:700"><a href="{escape(link)}" style="color:#0C1D30;text-decoration:none">{escape(g["name"])}</a></div>'
            f'<div style="font-size:13px;color:#4A5C70">{escape(g.get("funder") or "")}</div>'
            f'<div style="font-size:13px;margin-top:4px">{escape(funding_award(g))}' + (f" · {escape(closes)}" if closes else "") + "</div>"
            + (f'<div style="font-size:12.5px;color:#1e6b3a;margin-top:4px">Why: {escape(why)}</div>' if why else "") + "</div>"
        )
    more = f'<p style="font-size:13px">And {n - DIGEST_MAX} more in TenderFlow.</p>' if n > DIGEST_MAX else ""
    footer = f'This is a guide based on your company profile; check eligibility with the funder. <a href="{escape(unsubscribe_url)}">Stop these emails</a>.'
    return subject, _shell("New grants open that look like a fit", rows + more, footer)


def funding_award(g: dict[str, Any]) -> str:
    def money(n: float) -> str:
        return f"£{n / 1e6:.1f}m".replace(".0m", "m") if n >= 1e6 else (f"£{n / 1e3:.1f}k".replace(".0k", "k") if n >= 1e4 else f"£{int(n):,}")
    mn, mx, total = g.get("min_award"), g.get("max_award"), g.get("total_award")
    if mn is not None and mx is not None and mn != mx:
        return f"{money(mn)} to {money(mx)}"
    one = mx if mx is not None else mn
    if one is not None:
        return ("Up to " if mx is not None and mn is None else "From " if mx is None else "") + money(one)
    return f"Pot {money(total)}" if total is not None else "Award size not stated"


def unsubscribe_url(username: str) -> str:
    from tender_app.email_svc import alert_pref_token
    return f"{_base_url()}/api/funding/alerts/unsubscribe?user={quote(username)}&token={alert_pref_token(username)}"


def new_fitting_grants(cur: Any, username: str, since: datetime, profile_ctx: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    """Open grants first seen after `since` that the free eligibility guide rates likely for the profile."""
    result = funding.search(cur, keywords=profile_ctx["keywords"], fit_applicant=profile_ctx["applicant"], now=now,
                            applicant=profile_ctx["applicant"]["kind"] or "", page=1)
    cur.execute("SELECT label FROM funding_opportunities WHERE first_seen > %s", (since,))
    fresh = {r[0] for r in cur.fetchall()}
    # search pages by 20: walk every page so the whole open list is considered
    items = list(result["items"])
    for page in range(2, result["pages"] + 1):
        items += funding.search(cur, keywords=profile_ctx["keywords"], fit_applicant=profile_ctx["applicant"], now=now,
                                applicant=profile_ctx["applicant"]["kind"] or "", page=page)["items"]
    return [g for g in items if g["label"] in fresh and (g.get("fit") or {}).get("verdict") == "likely"]


def run_digest(get_db: Callable[[], Any], send: Callable[..., bool] | None = None, now: datetime | None = None) -> int:
    """Weekly, for users who opted in. The first run after opting in only starts the clock. Returns emails sent."""
    from tender_app.blueprints.funding_bp import _profile_context

    if send is None:
        from server import _send_smtp_email as send
    now = now or datetime.utcnow()
    conn = get_db()
    sent = 0
    try:
        cur = conn.cursor()
        ensure_schema(cur)
        cur.execute("SELECT to_regclass('public.funding_opportunities') IS NOT NULL")
        if not cur.fetchone()[0]:
            return 0
        cur.execute("ALTER TABLE funding_opportunities ADD COLUMN IF NOT EXISTS first_seen TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')")
        conn.commit()
        cur.execute("SELECT p.username, p.last_sent, u.email FROM funding_digest_prefs p JOIN users u ON u.username = p.username WHERE p.enabled")
        for username, last_sent, email in cur.fetchall():
            if last_sent is None:
                cur.execute("UPDATE funding_digest_prefs SET last_sent = %s WHERE username = %s", (now, username))
                conn.commit()
                continue
            if now - last_sent < timedelta(days=DIGEST_EVERY_DAYS):
                continue
            to = (email or username or "").strip()
            if "@" not in to:
                continue
            ctx = _profile_context(cur, username, None)
            grants = new_fitting_grants(cur, username, last_sent, ctx, uk_time.to_london(now)) if ctx["keywords"] else []
            if grants:
                subject, html = digest_email(grants, ctx["name"], unsubscribe_url(username))
                if not send(to, subject, html, list_unsubscribe_url=unsubscribe_url(username)):
                    continue  # try again next run: the marker stays where it was
                sent += 1
            cur.execute("UPDATE funding_digest_prefs SET last_sent = %s WHERE username = %s", (now, username))
            conn.commit()
        cur.close()
    finally:
        conn.close()
    return sent
