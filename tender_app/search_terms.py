"""Comma-separated keyword searches ("software, dashboard" = software OR dashboard).

One place decides how a search string becomes terms, so the portals, the saved-tender lookup and the page
agree. Words with no comma between them stay together as one AND-style term ("software development")."""
from __future__ import annotations

import re

MAX_TERMS = 5
_PREFIX = re.compile(r"^(?:supplier|winner|contractor|buyer|authority|title):\s*", re.I)


def split_terms(query: str | None, limit: int = MAX_TERMS) -> list[str]:
    """Lower-cased, trimmed, de-duplicated terms of a search string, at most `limit`. A leading
    supplier:/buyer:/title: prefix is dropped (the scope is handled elsewhere). "" and "all" give []."""
    text = _PREFIX.sub("", str(query or "").strip())
    seen: list[str] = []
    for part in re.split(r"[,;]+", text):
        term = " ".join(part.split()).lower()
        if term and term != "all" and term not in seen:
            seen.append(term)
        if len(seen) >= limit:
            break
    return seen


def matched_terms(terms: list[str], *texts: str) -> list[str]:
    """The terms that appear in any of the texts."""
    hay = " ".join(t for t in texts if t).lower()
    return [t for t in terms if t in hay]
