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


_TERM_STOP = {"and", "the", "of", "for", "to", "a", "an", "in", "on", "at", "by", "with", "or"}


def term_matches(term: str, hay: str) -> bool:
    """A term matches when it appears as written, or when every meaningful word of it starts a word in the text.
    "EEF Digital Public Service" finds "...the EEF&#039;s Digital Public Service" (fix plan H5: the notice was
    missing from its own title search), and "print and signage" finds "Print & Signage" (R11)."""
    if not term:
        return True
    if re.search(r"(?<![a-z0-9])" + re.escape(term), hay):  # as written, starting a word ("eef" is not in "beef")
        return True
    words = [w for w in re.findall(r"[a-z0-9]+", term.lower().replace("&", " and ")) if w not in _TERM_STOP]
    if not words:
        return False
    return all(re.search(r"(?<![a-z0-9])" + re.escape(w), hay) for w in words)


def matched_terms(terms: list[str], *texts: str) -> list[str]:
    """The terms that appear in any of the texts."""
    hay = " ".join(t for t in texts if t).lower()
    return [t for t in terms if term_matches(t, hay)]
