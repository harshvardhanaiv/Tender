"""Tracking a planning lead in Pipeline, and the stage alerts that follow it (re-test 3 PN3).

A tracked lead is a Pipeline entry (source "planning") plus a row in planning_tracks holding the stage last seen. Three later stages
raise an alert when a lead reaches them:

  decided               the council has decided the application (permitted, permitted with conditions, refused or withdrawn)
  conditions_discharged a "discharge of conditions" application at the same address has been decided
  started               a notice of commencement / start of works is on the register for the same address

The register has no field for the last two, so they are inferred from related applications at the same address: the page says so.
Pure functions here, tested without a database (tests/test_planning_track.py); the database helpers are thin.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

STAGES = ("submitted", "decided", "conditions_discharged", "started")
STAGE_LABELS = {
    "submitted": "Submitted",
    "decided": "Decided",
    "conditions_discharged": "Conditions discharged",
    "started": "Start on site",
}
_DECIDED_STATES = {"permitted", "conditions", "approved", "rejected", "withdrawn"}
_DISCHARGE_RE = re.compile(r"\b(discharge|approval of details|details pursuant to condition|compliance with condition|conditions? \d)", re.IGNORECASE)
_START_RE = re.compile(r"\b(notice of (intention to )?(start|commenc)\w*|commencement (notice|of (works|development))|start of (works|development)|"
                       r"(works|development) (has |have )?(commenced|started)|start on site)\b", re.IGNORECASE)


def _decided(app: dict[str, Any]) -> bool:
    return str(app.get("app_state") or "").strip().lower() in _DECIDED_STATES or bool(app.get("decided_date"))


def _same_site(app: dict[str, Any], other: dict[str, Any]) -> bool:
    a, b = str(app.get("address") or "").strip().lower(), str(other.get("address") or "").strip().lower()
    return bool(a) and a == b and str(app.get("authority") or "").lower() == str(other.get("authority") or "").lower() and other.get("id") != app.get("id")


def stage_of(app: dict[str, Any], related: Iterable[dict[str, Any]] = ()) -> str:
    """The furthest stage a lead has reached, from the application and the other applications on the register."""
    if not _decided(app):
        return "submitted"
    if str(app.get("app_state") or "").strip().lower() in ("rejected", "withdrawn"):
        return "decided"
    stage = "decided"
    for other in related:
        if not _same_site(app, other):
            continue
        text = f"{other.get('description') or ''} {other.get('app_type') or ''}"
        if _START_RE.search(text):
            return "started"
        if (str(other.get("app_type") or "").lower() == "conditions" or _DISCHARGE_RE.search(text)) and _decided(other):
            stage = "conditions_discharged"
    return stage


def alerts_for_change(old: str | None, new: str, title: str) -> list[str]:
    """One message for each stage passed since `old` (nothing when the lead has not moved forward)."""
    if new not in STAGES:
        return []
    start = STAGES.index(old) + 1 if old in STAGES else STAGES.index(new)
    out = []
    for stage in STAGES[start:STAGES.index(new) + 1]:
        if stage == "submitted":
            continue
        out.append(f"Planning lead “{title[:90]}”: {STAGE_LABELS[stage].lower()}.")
    return out


def lead_title(app: dict[str, Any]) -> str:
    text = re.sub(r"\s+", " ", str(app.get("description") or "")).strip()
    return (text[:117] + "…") if len(text) > 120 else (text or str(app.get("address") or "Planning lead"))


def tender_key(app_id: str) -> str:
    return f"planning:{app_id}"


# ── database ──────────────────────────────────────────────────────────────────────────────────

def ensure_table(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS planning_tracks (
            username   VARCHAR(255) NOT NULL,
            app_id     VARCHAR(255) NOT NULL,
            stage      VARCHAR(32)  NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (username, app_id)
        )""")


_APP_COLUMNS = "id, authority, address, description, app_state, app_type, decided_date"


def _rows(cur) -> list[dict[str, Any]]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def current_stage(cur, app: dict[str, Any]) -> str:
    related: list[dict[str, Any]] = []
    if app.get("address"):
        cur.execute(f"SELECT {_APP_COLUMNS} FROM planning_applications WHERE LOWER(TRIM(address)) = LOWER(TRIM(%s)) AND authority = %s AND id <> %s LIMIT 50",
                    (app["address"], app.get("authority"), app["id"]))
        related = _rows(cur)
    return stage_of(app, related)


def track(conn, username: str, app_id: str) -> dict[str, Any] | None:
    """Add the lead to the user's Pipeline and start watching its stage. None when the application does not exist."""
    cur = conn.cursor()
    ensure_table(cur)
    cur.execute(f"SELECT {_APP_COLUMNS} FROM planning_applications WHERE id = %s", (app_id,))
    found = _rows(cur)
    if not found:
        return None
    app = found[0]
    stage = current_stage(cur, app)
    cur.execute(
        """INSERT INTO pipeline (username, tender_key, title, source, contracting_authority, submission_deadline, estimated_value,
                                 stage, notes, added_from, updated_at)
           VALUES (%s,%s,%s,'planning',%s,'','','watching','','planning',CURRENT_TIMESTAMP)
           ON CONFLICT (username, tender_key) DO UPDATE SET updated_at = CURRENT_TIMESTAMP""",
        (username, tender_key(app_id), lead_title(app), app.get("authority") or ""))
    cur.execute("""INSERT INTO planning_tracks (username, app_id, stage) VALUES (%s,%s,%s)
                   ON CONFLICT (username, app_id) DO NOTHING""", (username, app_id, stage))
    conn.commit()
    return {"app_id": app_id, "stage": stage, "stage_label": STAGE_LABELS[stage]}


def untrack(conn, username: str, app_id: str) -> None:
    cur = conn.cursor()
    ensure_table(cur)
    cur.execute("DELETE FROM planning_tracks WHERE username = %s AND app_id = %s", (username, app_id))
    cur.execute("DELETE FROM pipeline WHERE username = %s AND tender_key = %s AND added_from = 'planning'", (username, tender_key(app_id)))
    cur.execute("DELETE FROM deep_alerts WHERE username = %s AND tender_key = %s", (username, tender_key(app_id)))
    conn.commit()


def tracked_ids(conn, username: str) -> list[str]:
    cur = conn.cursor()
    ensure_table(cur)
    cur.execute("SELECT app_id FROM planning_tracks WHERE username = %s", (username,))
    return [r[0] for r in cur.fetchall()]


def check_tracked(conn, username: str) -> int:
    """Raise an alert (a deep_alerts row, shown with the other Pipeline alerts) for every tracked lead that has moved on. Returns how many."""
    cur = conn.cursor()
    ensure_table(cur)
    cur.execute("SELECT app_id, stage FROM planning_tracks WHERE username = %s", (username,))
    tracked = cur.fetchall()
    made = 0
    for app_id, old in tracked:
        cur.execute(f"SELECT {_APP_COLUMNS} FROM planning_applications WHERE id = %s", (app_id,))
        found = _rows(cur)
        if not found:
            continue
        new = current_stage(cur, found[0])
        if STAGES.index(new) <= STAGES.index(old if old in STAGES else "submitted"):
            continue
        for message in alerts_for_change(old, new, lead_title(found[0])):
            cur.execute("""INSERT INTO deep_alerts (username, tender_key, kind, message) VALUES (%s,%s,'planning_stage',%s)
                           ON CONFLICT (username, tender_key, kind, message) DO NOTHING""", (username, tender_key(app_id), message))
            made += 1
        cur.execute("UPDATE planning_tracks SET stage = %s, updated_at = CURRENT_TIMESTAMP WHERE username = %s AND app_id = %s", (new, username, app_id))
    conn.commit()
    return made
