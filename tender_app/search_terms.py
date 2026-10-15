"""Comma-separated keyword searches ("software, dashboard" = software OR dashboard).

One place decides how a search string becomes terms, so the portals, the saved-tender lookup and the page
agree. Words with no comma between them stay together as one AND-style term ("software development")."""
from __future__ import annotations

import re

MAX_TERMS = 5
_PREFIX = re.compile(r"^(?:supplier|winner|contractor|buyer|authority|title):\s*", re.I)


_OR = re.compile(r"\s+OR\s+|\s*\|\s*")
_NOT = re.compile(r'(?:(?<=\s)|^)(?:NOT\s+|-)("[^"]+"|[^\s,;]+)')


def parse_operators(query: str | None) -> tuple[str, list[str]]:
    """Splits "software OR cloud NOT legacy -hardware" into ("software, cloud", ["legacy", "hardware"]).

    OR (upper case, or "|") becomes the comma the rest of the app already treats as OR; NOT word / -word / -"a phrase"
    are returned as exclusions to be filtered from the results. A hyphen inside a word ("e-tender") is not an exclusion,
    and lower-case "or" / "not" stay ordinary words. Quote marks around a phrase are dropped, so it is searched as the plain words."""
    text = str(query or "")
    excludes = [m.group(1).strip('"').lower() for m in _NOT.finditer(text)]
    text = _NOT.sub(" ", text)
    # a quoted phrase ("fire safety") is one term: the quote marks are not part of it, or it would never match
    text = text.replace('"', " ").replace("\u201c", " ").replace("\u201d", " ")
    text = _OR.sub(", ", text)
    text = re.sub(r"\s*,\s*(,\s*)+", ", ", text)
    return re.sub(r"\s+", " ", text).strip(" ,;"), [e for e in excludes if e]


def split_terms(query: str | None, limit: int = MAX_TERMS) -> list[str]:
    """Lower-cased, trimmed, de-duplicated terms of a search string, at most `limit`. A leading
    supplier:/buyer:/title: prefix is dropped (the scope is handled elsewhere). "" and "all" give []."""
    text = _PREFIX.sub("", parse_operators(str(query or "").strip())[0])
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
