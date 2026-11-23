"""Re-test 23 Nov (item 11): the proposal's Word file used the "Normal" style for every paragraph, so it had no navigation pane,
no table of contents source and nothing for a screen reader to jump between. This holds the styles part and the two package
entries a file needs to use Heading 1 to 3."""

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
STYLES_CONTENT_TYPE = '  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
STYLES_REL = '  <Relationship Id="rIdStyles1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'


def _heading(n, size):
    return (f'<w:style w:type="paragraph" w:styleId="Heading{n}"><w:name w:val="heading {n}"/><w:basedOn w:val="Normal"/>'
            f'<w:next w:val="Normal"/><w:uiPriority w:val="9"/><w:qFormat/>'
            f'<w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="240" w:after="80"/><w:outlineLvl w:val="{n - 1}"/></w:pPr>'
            f'<w:rPr><w:b/><w:color w:val="1E3A8A"/><w:sz w:val="{size}"/></w:rPr></w:style>')


STYLES_XML = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              f'<w:styles xmlns:w="{W_NS}">'
              '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI" w:cs="Segoe UI"/><w:sz w:val="22"/></w:rPr></w:rPrDefault>'
              '<w:pPrDefault><w:pPr><w:spacing w:after="120"/></w:pPr></w:pPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
              + _heading(1, 32) + _heading(2, 26) + _heading(3, 24)
              + '</w:styles>')


def heading_style_for(size, explicit=None):
    """Section and appendix titles (size 30+) are Heading 1, other headings Heading 2, unless the caller names one."""
    if explicit in ("Heading1", "Heading2", "Heading3"):
        return explicit
    return "Heading1" if int(size or 0) >= 30 else "Heading2"


STYLES_RELS_XML = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                   + STYLES_REL + '\n</Relationships>')

import re as _re

_PARA = _re.compile(r"<w:p>(.*?)</w:p>", _re.S)
_TBL_TOKEN = _re.compile(r"<w:tbl>|</w:tbl>")


def apply_heading_styles(doc_xml: str) -> str:
    """For the smaller exports (covering letter, questionnaire, dissection, checklist, clarifications), whose titles are bold runs of
    14pt or more written straight into the document: give each such title paragraph a Heading style, so the file has a navigation
    pane. A paragraph is a title when it is outside any table, has no style, is one or two runs, all bold, its first run is 12pt or
    more, and its text is short. Everything else is left exactly as it was."""
    out, pos, depth = [], 0, 0
    tokens = [(m.start(), m.group(0)) for m in _TBL_TOKEN.finditer(doc_xml)]
    ti = 0

    def depth_at(i):
        nonlocal ti, depth
        while ti < len(tokens) and tokens[ti][0] < i:
            depth += 1 if tokens[ti][1] == "<w:tbl>" else -1
            ti += 1
        return depth

    for m in _PARA.finditer(doc_xml):
        body = m.group(1)
        if depth_at(m.start()) > 0 or "<w:pStyle" in body:
            continue
        runs = _re.findall(r"<w:r>(.*?)</w:r>", body, _re.S)
        if not runs or len(runs) > 2:
            continue
        if not all("<w:b/>" in r for r in runs):
            continue
        sz = _re.search(r'<w:sz w:val="(\d+)"/>', runs[0])
        if not sz or int(sz.group(1)) < 24:
            continue
        text = "".join(_re.findall(r"<w:t[^>]*>(.*?)</w:t>", body, _re.S))
        if not text.strip() or len(text) > 150:
            continue
        size = int(sz.group(1))
        style = "Heading1" if size >= 32 else "Heading2" if size >= 26 else "Heading3"
        tag = f'<w:pStyle w:val="{style}"/>'
        if "<w:pPr>" in body:
            new = body.replace("<w:pPr>", "<w:pPr>" + tag, 1)
        else:
            new = "<w:pPr>" + tag + "</w:pPr>" + body
        out.append(doc_xml[pos:m.start(1)])
        out.append(new)
        pos = m.end(1)
    out.append(doc_xml[pos:])
    return "".join(out)
