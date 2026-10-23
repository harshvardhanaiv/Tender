"""Formatted (HTML) email bodies for Growth Studio: what the editor may produce, what is stored, and what each buyer gets.

Pure functions, no database and no network. The editor in the browser is only a convenience: whatever it sends is cleaned here
before it is stored or sent, so a pasted page or a hand-made request cannot add scripts, forms, images from elsewhere, styles or
links that are not web or mail addresses.

  sanitize_html(raw)        the allowed tags only, attributes dropped except a checked link address
  html_to_text(html)        the plain-text twin sent beside the HTML (and kept as the campaign's `body`)
  text_to_html(text)        a plain message as paragraphs, for campaigns written before formatting existed
  render_html(html, values) the merge fields ({{buyer_name}}) filled in for one buyer, values escaped
  has_formatting(html)      True when the message uses more than plain paragraphs
  attachment_problem(...)   why a file may not be attached, or None
"""
from __future__ import annotations

import html as _html
import mimetypes
import re
from html.parser import HTMLParser
from typing import Any, Mapping

MAX_HTML = 30000

# Tags kept as they are. div is written as p (the editor makes a div per line); everything else is dropped, its text kept.
_KEEP = {"p", "br", "b", "strong", "i", "em", "u", "s", "a", "ul", "ol", "li", "h2", "h3", "blockquote", "hr"}
_ALIAS = {"div": "p", "h1": "h2", "h4": "h3", "h5": "h3", "h6": "h3", "strike": "s", "del": "s"}
_VOID = {"br", "hr"}
_DROP_WITH_CONTENT = {"script", "style", "head", "title", "iframe", "object", "embed", "template", "noscript", "svg", "math"}
# these only count as formatting; paragraphs and line breaks are just text
_FORMATTING = {"b", "strong", "i", "em", "u", "s", "a", "ul", "ol", "li", "h2", "h3", "blockquote", "hr"}
_SAFE_LINK = re.compile(r"^(https?://|mailto:)[^\s<>\"']+$", re.IGNORECASE)
_FIELD = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip:
            return
        tag = _ALIAS.get(tag, tag)
        if tag not in _KEEP:
            return
        if tag in _VOID:
            self.out.append(f"<{tag}>")
            return
        if tag == "a":
            href = next((v for k, v in attrs if k == "href" and v), "")
            href = (href or "").strip()
            if not _SAFE_LINK.match(href):
                return  # a link that is not a web or mail address is just its text
            self.out.append(f'<a href="{_html.escape(href, quote=True)}">')
        else:
            self.out.append(f"<{tag}>")
        self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_WITH_CONTENT:
            return
        self.handle_starttag(tag, attrs)
        if _ALIAS.get(tag, tag) not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        tag = _ALIAS.get(tag, tag)
        if tag in _VOID or tag not in _KEEP or tag not in self.open:
            return
        while self.open:  # close anything left open inside it first, so the result is always well formed
            top = self.open.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(_html.escape(data, quote=False))

    def result(self) -> str:
        while self.open:
            self.out.append(f"</{self.open.pop()}>")
        return "".join(self.out)


def sanitize_html(raw: Any) -> str:
    """The editor's HTML reduced to the tags above. Empty for anything that is not text, or has no visible text in it."""
    if not isinstance(raw, str):
        return ""
    cleaner = _Cleaner()
    cleaner.feed(raw[: MAX_HTML * 2])
    cleaner.close()
    out = cleaner.result().strip()
    out = re.sub(r"(<br>\s*){3,}", "<br><br>", out)
    if not html_to_text(out).strip():
        return ""
    return out[:MAX_HTML] if len(out) <= MAX_HTML else _truncate_safely(out)


def _truncate_safely(out: str) -> str:
    # too long: cut after a paragraph or list item, then run it through the cleaner again so every tag is closed
    cut = out[:MAX_HTML]
    ends = [cut.rfind(t) + len(t) for t in ("</p>", "</li>", "<br>") if cut.rfind(t) > 0]
    if not ends:
        return _html.escape(re.sub(r"<[^>]*$", "", re.sub(r"<[^>]+>", "", cut)), quote=False)
    cleaner = _Cleaner()
    cleaner.feed(cut[: max(ends)])
    cleaner.close()
    return cleaner.result()


def html_to_text(html: str) -> str:
    """The plain-text version: paragraphs and list items on their own lines, a link as "text (address)"."""
    s = html or ""
    s = re.sub(r'<a href="([^"]*)">(.*?)</a>', lambda m: _link_text(m.group(2), _html.unescape(m.group(1))), s, flags=re.DOTALL)
    s = re.sub(r"<li>", "\n- ", s)
    s = re.sub(r"</(p|h2|h3|blockquote|ul|ol)>", "\n\n", s)
    s = re.sub(r"<(br|hr)>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+\n", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _link_text(inner: str, href: str) -> str:
    text = re.sub(r"<[^>]+>", "", inner)
    plain = _html.unescape(text).strip()
    bare = re.sub(r"^(mailto:|https?://)", "", href, flags=re.IGNORECASE).rstrip("/")
    if not plain or plain.rstrip("/") == bare or plain.rstrip("/") == href.rstrip("/"):
        return text if plain else href
    return f"{text} ({href})"


def text_to_html(text: str) -> str:
    """A plain message as HTML: a blank line starts a paragraph, a single line break stays a line break."""
    paras = [p for p in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()) if p.strip()]
    return "".join("<p>" + _html.escape(p.strip("\n"), quote=False).replace("\n", "<br>") + "</p>" for p in paras)


def render_html(html: str, values: Mapping[str, str | None]) -> str:
    """The merge fields filled in for one buyer. Values are escaped, so a buyer called "A & B <Ltd>" cannot add markup."""
    def fill(m: re.Match) -> str:
        v = values.get(m.group(1))
        return _html.escape(v, quote=True) if v is not None else m.group(0)
    return _FIELD.sub(fill, html or "")


_FORMATTING_TAG = re.compile(r"<(?:" + "|".join(sorted(_FORMATTING)) + r")[\s>]")


def has_formatting(html: str | None) -> bool:
    return bool(html) and bool(_FORMATTING_TAG.search(html))


# ── attachments ───────────────────────────────────────────────────────────────────────────────────────────────

MAX_FILES = 10
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
# mail servers refuse these, and a buyer's filter would treat them as hostile
_BLOCKED_EXT = {"exe", "bat", "cmd", "com", "scr", "pif", "msi", "msp", "jar", "js", "jse", "vbs", "vbe", "wsf", "wsh", "ps1", "psm1", "dll",
                "lnk", "reg", "hta", "cpl", "apk", "app", "dmg", "iso", "img", "sh", "bin", "docm", "xlsm", "pptm"}


def safe_filename(name: Any) -> str:
    base = re.split(r"[\\/]", str(name or ""))[-1]
    base = re.sub(r"[\x00-\x1f\"<>|:*?]", "", base).strip(" .")
    return base[:120]


def content_type_for(name: str, declared: str | None = None) -> str:
    guess = mimetypes.guess_type(name)[0]
    if guess:
        return guess
    d = (declared or "").lower()
    return d if re.fullmatch(r"[a-z]+/[a-z0-9.+-]+", d) else "application/octet-stream"


def attachment_problem(name: str, size: int, existing: list[Mapping[str, Any]]) -> str | None:
    """Why this file may not be attached to the campaign, or None. `existing` are the files already attached."""
    if not name:
        return "The file has no name."
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in _BLOCKED_EXT:
        return f"{name}: mail servers block .{ext} files. Send a link, or put it in a zip or PDF."
    if size <= 0:
        return f"{name} is empty."
    if size > MAX_FILE_BYTES:
        return f"{name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB."
    if len(existing) >= MAX_FILES:
        return f"A campaign can carry at most {MAX_FILES} files."
    if sum(int(e.get("size_bytes") or 0) for e in existing) + size > MAX_TOTAL_BYTES:
        return f"The files together may be at most {MAX_TOTAL_BYTES // (1024 * 1024)} MB."
    return None
