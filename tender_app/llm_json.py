"""Tolerant JSON parsing for LLM output.

Models wrap JSON in markdown fences, add prose around it, put raw newlines inside string
values, or get cut off mid-object by max_tokens. Every AI feature used to handle (or ignore)
that on its own; this is the one shared implementation. It was lifted from the bid pipeline's
`heal_and_parse_json` so behaviour there is unchanged.
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?|\n?```\s*$")


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text.strip()).strip()


def _escape_control_chars_in_strings(text: str) -> str:
    """Escape raw newlines/tabs that appear inside JSON string literals (illegal in strict JSON)."""
    out: list[str] = []
    in_string = escaped = False
    for c in text:
        if escaped:
            out.append(c)
            escaped = False
        elif c == "\\":
            out.append(c)
            escaped = True
        elif c == '"':
            in_string = not in_string
            out.append(c)
        elif in_string and c == "\n":
            out.append("\\n")
        elif in_string and c == "\r":
            out.append("\\r")
        elif in_string and c == "\t":
            out.append("\\t")
        else:
            out.append(c)
    return "".join(out)


def _closing_suffix(text: str) -> str:
    """What must be appended to `text` to close its open string and brackets."""
    stack: list[str] = []
    in_string = escaped = False
    for c in text:
        if escaped:
            escaped = False
        elif c == "\\":
            escaped = True
        elif c == '"':
            in_string = not in_string
        elif not in_string:
            if c in "{[":
                stack.append(c)
            elif c in "}]" and stack:
                stack.pop()
    return ('"' if in_string else "") + "".join("}" if b == "{" else "]" for b in reversed(stack))


def _heal_truncated(text: str) -> Any:
    """Recover a response that stopped abruptly: close it, else drop the ragged tail step by step."""
    try:
        return json.loads(text + _closing_suffix(text), strict=False)
    except ValueError:
        pass
    for i in range(len(text) - 1, 0, -1):
        if text[i] in ",{[}]":
            head = text[:i].rstrip()
            try:
                return json.loads(head + _closing_suffix(head), strict=False)
            except ValueError:
                continue
    raise ValueError("unrecoverable")


def parse_llm_json(text: str) -> Any:
    """Parse JSON from model output, repairing the usual damage. Raises ValueError if hopeless."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Model returned an empty response.")
    cleaned = _strip_fences(text)

    try:
        return json.loads(cleaned, strict=False)
    except ValueError:
        pass

    # Prose around the JSON: take from the first opening bracket to the last closing one.
    start = min((i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1), default=-1)
    if start > 0:
        cleaned = cleaned[start:]
    end = max(cleaned.rfind("}"), cleaned.rfind("]"))
    candidate = cleaned[: end + 1] if end != -1 else cleaned

    for attempt in (candidate, _escape_control_chars_in_strings(candidate), _escape_control_chars_in_strings(cleaned)):
        try:
            return json.loads(attempt, strict=False)
        except ValueError:
            continue
    try:
        return _heal_truncated(_escape_control_chars_in_strings(cleaned))
    except ValueError:
        raise ValueError("Model returned invalid or truncated JSON that could not be repaired.") from None
