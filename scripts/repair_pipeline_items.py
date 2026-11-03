#!/usr/bin/env python3
"""Repair existing Pipeline items (Re-test 5 PL3 and PL4).

PL3: deadlines were saved as the portal's own text ("01/11/2026 12:00", "05 October 2026", "Deadline Time:", "N/A"). Each one that reads as a
date becomes an ISO date (YYYY-MM-DD, day first for UK portals); text that is not a date becomes empty, so "due in 7 days" counts real dates only.
PL4: titles saved as "View Notice" or as a SAM.gov page crumb are replaced from the notice kept in tender_notices, when it holds one.

Dry run by default; --apply commits. The old values are printed first, so they can be put back by hand.

    python scripts/repair_pipeline_items.py
    python scripts/repair_pipeline_items.py --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etenders_scraper.deadline import parse_deadline  # noqa: E402
from tender_app.db import get_db_connection  # noqa: E402

_BAD_TITLE = re.compile(r"^\s*(view notice|sam\.gov.*|untitled.*)\s*$", re.I)


def iso_deadline(value: str | None) -> str | None:
    """The ISO date for a saved deadline, "" for text that is not a date, None when it is already ISO (nothing to change)."""
    text = (value or "").strip()
    if not text or re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    found = parse_deadline(text)
    return found.isoformat() if found else ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, username, tender_key, source, title, submission_deadline FROM pipeline")
    rows = cur.fetchall()
    deadline_changes, title_changes = [], []
    for pid, user, key, source, title, deadline in rows:
        rid = (key or "").split(":", 1)[1] if ":" in (key or "") else ""
        new = iso_deadline(deadline)
        if new is not None and new != (deadline or ""):
            deadline_changes.append((pid, user, key, deadline, new))
        if title and _BAD_TITLE.match(title) and source and rid:
            cur.execute("SELECT title FROM tender_notices WHERE source = %s AND resource_id = %s", (source, str(rid)))
            hit = cur.fetchone()
            if hit and hit[0] and not _BAD_TITLE.match(hit[0]):
                title_changes.append((pid, user, key, title, hit[0].strip()))
    for pid, user, key, old, new in deadline_changes:
        print(f"deadline  {user}  {key}:  {old!r} -> {new!r}")
    for pid, user, key, old, new in title_changes:
        print(f"title     {user}  {key}:  {old!r} -> {new!r}")
    print(f"{len(rows)} items: {len(deadline_changes)} deadlines and {len(title_changes)} titles would change")
    if not args.apply:
        print("dry run: nothing changed (use --apply)")
        return
    for pid, _u, _k, _old, new in deadline_changes:
        cur.execute("UPDATE pipeline SET submission_deadline = %s WHERE id = %s", (new, pid))
    for pid, _u, _k, _old, new in title_changes:
        cur.execute("UPDATE pipeline SET title = %s WHERE id = %s", (new, pid))
    conn.commit()
    print("applied")


if __name__ == "__main__":
    main()
