"""PDF export of a bid with a page count for every section (QA scorecard N6: buyers who require a PDF and limit a
method statement to N A4 pages). Uses PyMuPDF's HTML layout (already a dependency); no new packages.

Each section starts on its own page, so the page count of a section is exact, and a section over its page limit is
reported. Pure function: bid pieces in, (pdf bytes, [{label, pages, limit, over}]) out.
"""
from __future__ import annotations

import html
import io
import re

_CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.35; color: #0f172a; }
h1 { font-size: 17pt; color: #1e3a8a; margin: 0 0 6pt 0; }
h2 { font-size: 13pt; color: #1e3a8a; margin: 0 0 4pt 0; }
.q { font-size: 9.5pt; color: #475569; font-style: italic; margin: 0 0 8pt 0; }
p { margin: 0 0 6pt 0; }
.ph { background-color: #fef08a; color: #854d0e; font-weight: bold; }
.note { color: #b45309; font-weight: bold; }
li { margin: 0 0 3pt 0; }
h3 { font-size: 11.5pt; color: #1e3a8a; margin: 8pt 0 4pt 0; }
h4 { font-size: 10.5pt; color: #1e3a8a; margin: 6pt 0 3pt 0; }
table { border-collapse: collapse; margin: 0 0 8pt 0; width: 100%; }
th, td { border: 0.5pt solid #94a3b8; padding: 3pt 4pt; font-size: 9.5pt; vertical-align: top; }
th { background-color: #e2e8f0; font-weight: bold; text-align: left; }
"""

_PH = re.compile(r"(\[(?:PLACEHOLDER|NEEDS INPUT|CONFIRM|Add|Insert|Verify|Status)[^\]]*\])", re.I)


_HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
_OL = re.compile(r"^(\d{1,3})[.)]\s+(.*)$")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")


def _inline(body: str) -> str:
    parts = []
    for i, chunk in enumerate(body.split("**")):
        seg = "".join(f'<span class="ph">{html.escape(c)}</span>' if _PH.fullmatch(c) else html.escape(c)
                      for c in _PH.split(chunk) if c)
        parts.append(f"<b>{seg}</b>" if i % 2 == 1 and seg else seg)
    return "".join(parts)


def _cells(line: str) -> list:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def text_to_html(text: str) -> str:
    """The same Markdown the Word file renders: '#' headings, pipe tables, bullet and numbered lists, **bold** and
    highlighted placeholders. Release 12: the PDF showed '###' and '| --- |' as text (56 lines on one bid)."""
    out: list = []
    lst = ""                       # "ul" / "ol" while a list is open
    table: list = []

    def close_list():
        nonlocal lst
        if lst:
            out.append(f"</{lst}>")
            lst = ""

    def flush_table():
        if not table:
            return
        rows = [r for r in table if not _TABLE_SEP.match(r.strip())]
        head_done = len(table) > 1 and _TABLE_SEP.match(table[1].strip())
        h = ["<table>"]
        for n, r in enumerate(rows):
            tag = "th" if n == 0 and head_done else "td"
            h.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in _cells(r)) + "</tr>")
        h.append("</table>")
        out.append("".join(h))
        table.clear()

    for raw in (text or "").split("\n"):
        line = raw.strip()
        if line.startswith("|") and line.count("|") >= 2:
            close_list()
            table.append(line)
            continue
        flush_table()
        if not line:
            close_list()
            continue
        hm = _HEAD.match(line)
        if hm:
            close_list()
            level = 3 if len(hm.group(1)) <= 3 else 4
            out.append(f"<h{level}>{_inline(hm.group(2).strip('*'))}</h{level}>")
            continue
        if re.fullmatch(r"[-*_]{3,}", line):
            close_list()
            continue
        om = _OL.match(line)
        bullet = line.startswith(("* ", "- ", "• "))
        if bullet or om:
            kind = "ol" if om and not bullet else "ul"
            if lst != kind:
                close_list()
                out.append(f"<{kind}>")
                lst = kind
            out.append(f"<li>{_inline(om.group(2) if kind == 'ol' else line[2:])}</li>")
            continue
        close_list()
        out.append(f"<p>{_inline(line)}</p>")
    flush_table()
    close_list()
    return "\n".join(out)


def _render(body_html: str):
    import pymupdf
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    story = pymupdf.Story(html=f"<body>{body_html}</body>", user_css=_CSS)
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (54, 54, -54, -60)  # margins
    more = 1
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()


def _brand_css(kit):
    """Heading colour and font family from the brand kit (release 8). Serif fonts map to serif, the rest to sans-serif."""
    serif = kit["font"] in ("Georgia", "Times New Roman", "Cambria")
    return "body { font-family: %s; } h1, h2, h3, h4 { color: #%s; } th { background-color: #%s; color: #ffffff; } " % ("serif" if serif else "sans-serif", kit["color"], kit["color"])


def build_pdf(title: str, sections: list, front_html: str = "", brand=None, anonymise: bool = False):
    """sections: [{label, html, page_limit}]. Returns (pdf_bytes, counts).
    `brand` is a sanitised brand kit: heading colour and font, a logo on every page, a footer line. Anonymised: the logo
    is on the cover page only and there is no company footer line, so the answers carry no company mark."""
    import pymupdf
    global _CSS
    base_css = _CSS
    if brand:
        _CSS = base_css + _brand_css(brand)
    try:
        return _build_pdf(title, sections, front_html, brand, anonymise)
    finally:
        _CSS = base_css


def _build_pdf(title, sections, front_html, brand, anonymise):
    import pymupdf
    merged = pymupdf.open()
    counts: list = []
    parts = ([{"label": "Cover", "html": front_html, "page_limit": None}] if front_html else []) + list(sections)
    for s in parts:
        if not str(s.get("html") or "").strip():
            continue
        data = _render(s["html"])
        doc = pymupdf.open("pdf", data)
        pages = doc.page_count
        merged.insert_pdf(doc)
        doc.close()
        limit = s.get("page_limit")
        try:
            limit = int(limit) if limit else None
        except (TypeError, ValueError):
            limit = None
        counts.append({"label": s["label"], "pages": pages, "limit": limit, "over": bool(limit and pages > limit)})
    merged.set_metadata({"title": title or "Bid response"})
    # Page numbers, bottom centre.
    total = merged.page_count
    logo = None
    if brand and brand.get("logo_b64"):
        import base64
        from tender_app.brand_kit import _logo_extent
        logo = base64.b64decode(brand["logo_b64"])
        lcx, lcy = _logo_extent(brand, 1.0)
        lw, lh = lcx / 12700.0, lcy / 12700.0           # EMU to points
    for i, page in enumerate(merged, 1):
        page.insert_text((page.rect.width / 2 - 25, page.rect.height - 30), f"Page {i} of {total}", fontsize=8, color=(0.4, 0.4, 0.4))
        if brand and not anonymise and brand.get("footer_text"):
            page.insert_text((54, page.rect.height - 18), brand["footer_text"][:110], fontsize=7.5, color=(0.4, 0.4, 0.4))
        if logo and (i == 1 or not anonymise):
            size = (lw * 2.0, lh * 2.0) if i == 1 else (lw, lh)
            page.insert_image(pymupdf.Rect(54, 16, 54 + size[0], 16 + size[1]), stream=logo)
    out = merged.tobytes(garbage=3, deflate=True)
    merged.close()
    return out, counts
