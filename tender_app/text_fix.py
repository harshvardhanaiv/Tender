"""Repair text that was decoded with the wrong charset ("Project 1283 â Installation" for an en dash)."""

from __future__ import annotations

import re

# UTF-8 bytes read as cp1252: a lead character (â, Ã, Â) followed by a cp1252 rendering of a continuation byte
_MOJIBAKE = re.compile("[ÂÃâ][\u0080-¿€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ]")


def fix_mojibake(text):
    """Return `text` with UTF-8-read-as-cp1252 damage undone; anything that does not round-trip is returned as it was."""
    if not isinstance(text, str) or not _MOJIBAKE.search(text):
        return text
    raw = bytearray()
    for ch in text:
        try:
            raw += ch.encode("cp1252")
        except UnicodeEncodeError:
            if ord(ch) > 0xFF:
                return text
            raw.append(ord(ch))  # a C1 control character stands for its own byte
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return text


def clean_org_name(name):
    """A supplier or buyer name without the ":" "," ";" a bad scrape leaves on the end ("Gristwood and Toms:"). A final "." is kept: "Ltd." is real."""
    if not isinstance(name, str):
        return name
    return re.sub(r"\s+", " ", name).strip().rstrip(":;, ").strip()


def fix_row_text(row: dict, fields=("title", "contracting_authority", "description", "authority_name", "supplier_name")) -> dict:
    for f in fields:
        v = row.get(f)
        if isinstance(v, str):
            fixed = fix_mojibake(v)
            if f in ("supplier_name", "contracting_authority", "authority_name"):
                fixed = clean_org_name(fixed)
            if fixed != v:
                row[f] = fixed
    return row


_TITLE_TAIL_RE = re.compile(r"\s*[-–—:]\s*(award(ed)?( notice)?|contract award|notice of award|ref\.?[^-–—]*)\s*$", re.IGNORECASE)
_TITLE_REF_RE = re.compile(r"^\s*[A-Z0-9][A-Z0-9/._-]{3,}\s*[-–—:]\s+")
# Titles that stand for "no title yet": a placeholder written while a document was being read, or a link's label.
_PLACEHOLDER_TITLE_RE = re.compile(r"^\s*(analy[sz]ing[\w\s]*?(details|documents|tender)?\s*(\.\.\.|…)?|view( official award)? notice( ↗)?|untitled|tender details|n/?a)\s*$", re.IGNORECASE)


def clean_notice_title(title) -> str:
    """A notice title as a person would say it: no "- AWARD" tail and no leading reference number
    ("PPCS/2024/12 - Works Services - AWARD" becomes "Works Services")."""
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    for _ in range(2):
        text = _TITLE_TAIL_RE.sub("", text).strip()
    text = _TITLE_REF_RE.sub("", text).strip() if re.search(r"[a-z]{3}", _TITLE_REF_RE.sub("", text)) else text
    return text.strip(" -–—:")


def clean_history_title(title, fallback: str = "") -> str:
    """The title a History entry is saved under: no markdown (**), no "- AWARD" tail, and `fallback` for a placeholder such as
    "Analyzing Pasted Details..." or "View Notice" (re-test 3 HS2)."""
    text = re.sub(r"(\*\*|__|`)", "", str(title or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if not text or _PLACEHOLDER_TITLE_RE.match(text):
        return fallback
    cleaned = clean_notice_title(text)
    return cleaned or fallback
