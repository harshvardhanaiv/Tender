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


def fix_row_text(row: dict, fields=("title", "contracting_authority", "description", "authority_name", "supplier_name")) -> dict:
    for f in fields:
        v = row.get(f)
        if isinstance(v, str):
            fixed = fix_mojibake(v)
            if fixed is not v:
                row[f] = fixed
    return row
