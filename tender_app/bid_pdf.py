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
"""

_PH = re.compile(r"(\[(?:PLACEHOLDER|NEEDS INPUT|CONFIRM|Add|Insert|Verify|Status)[^\]]*\])", re.I)


def text_to_html(text: str) -> str:
    out: list = []
    in_list = False
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if not line:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        bullet = line.startswith(("* ", "- ", "• "))
        body = line[2:] if bullet else line
        parts = []
        for i, chunk in enumerate(body.split("**")):
            seg = "".join(f'<span class="ph">{html.escape(c)}</span>' if _PH.fullmatch(c) else html.escape(c)
                          for c in _PH.split(chunk) if c)
            parts.append(f"<b>{seg}</b>" if i % 2 == 1 and seg else seg)
        rendered = "".join(parts)
        if bullet:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{rendered}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{rendered}</p>")
    if in_list:
        out.append("</ul>")
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
    return "body { font-family: %s; } h1, h2 { color: #%s; } " % ("serif" if serif else "sans-serif", kit["color"])


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
