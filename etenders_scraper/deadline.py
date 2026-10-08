from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

_DATE_PATTERNS = (
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d %B %Y, %I:%M%p",   # "19 June 2026, 12:00pm"
    "%d %B %Y",             # "19 June 2026"
    "%B %d, %Y",            # "June 19, 2026"
)


def parse_deadline(value: str | None) -> date | None:
    """Parse common UK/IE tender deadline strings to a date."""
    if not value:
        return None
    text = re.sub(r"\s+", " ", str(value).strip())
    if not text or text in {"-", "N/A", "n/a", "TBC"}:
        return None
    # Take date portion before extra labels
    text = text.split("|")[0].strip()

    # Normalise "19 June 2026, 12:00pm" → "19 June 2026 12:00pm"
    text_norm = re.sub(r",\s*", " ", text)

    for fmt in _DATE_PATTERNS:
        for candidate in (text, text_norm):
            try:
                return datetime.strptime(candidate[: len(fmt) + 6], fmt).date()
            except ValueError:
                continue

    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        d, mo, y = (int(m.group(i)) for i in range(1, 4))
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def enrich_deadline_fields(row: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Add deadline_date (ISO), days_until_deadline, deadline_urgency."""
    today = today or date.today()
    raw = row.get("submission_deadline") or row.get("deadline") or ""
    d = parse_deadline(str(raw))
    row["deadline_date"] = d.isoformat() if d else None
    if d is None:
        row["days_until_deadline"] = None
        row["deadline_urgency"] = "unknown"
        return row
    days = (d - today).days
    row["days_until_deadline"] = days
    if days < 0:
        row["deadline_urgency"] = "past"
    elif days <= 10:
        row["deadline_urgency"] = "critical"
    elif days <= 30:
        row["deadline_urgency"] = "soon"
    else:
        row["deadline_urgency"] = "normal"
    return row
