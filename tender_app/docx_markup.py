"""Turn the Markdown the model writes into real Word structure (QA release 5, new defect 1).

The proposal builder writes WordprocessingML by hand. Draft text often carries "### Heading" lines and
"| a | b |" pipe tables; before this they were printed as literal characters. parse_blocks() splits the text
into heading, table and plain-line blocks; table_xml() renders a table as a real w:tbl.
"""
import re
from xml.sax.saxutils import escape as xml_escape

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_SEP_CELL = re.compile(r"^\s*:?-{2,}:?\s*$")


def _is_table_line(line):
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_separator(cells):
    return bool(cells) and all(_SEP_CELL.match(c or "") for c in cells)


def parse_blocks(text):
    """Return a list of ("heading", level, text), ("table", rows) and ("line", text) blocks."""
    blocks = []
    lines = str(text or "").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_table_line(line):
            rows = []
            while i < len(lines) and _is_table_line(lines[i]):
                cells = _cells(lines[i])
                if not _is_separator(cells):
                    rows.append(cells)
                i += 1
            if rows:
                blocks.append(("table", rows))
            continue
        m = _HEADING.match(line)
        if m and m.group(2):
            blocks.append(("heading", len(m.group(1)), m.group(2).strip()))
        elif _RULE.match(line):
            pass  # a horizontal rule has no place in the Word document
        else:
            blocks.append(("line", line))
        i += 1
    return blocks


def _runs(text, bold_all=False, size=20):
    out = []
    for idx, part in enumerate(str(text or "").split("**")):
        if not part:
            continue
        bold = bold_all or idx % 2 == 1
        out.append(
            '<w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>'
            + ("<w:b/>" if bold else "")
            + f'<w:sz w:val="{size}"/></w:rPr><w:t xml:space="preserve">{xml_escape(part)}</w:t></w:r>'
        )
    return "".join(out) or '<w:r><w:t></w:t></w:r>'


def table_xml(rows):
    """A real Word table; the first row is the header. Short rows are padded so the grid stays square."""
    width = max(len(r) for r in rows)
    col = max(900, 9300 // width)
    x = ['    <w:tbl>', '      <w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>']
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        x.append(f'        <w:{side} w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
    x.append('      </w:tblBorders></w:tblPr>')
    x.append("      <w:tblGrid>" + "".join(f'<w:gridCol w:w="{col}"/>' for _ in range(width)) + "</w:tblGrid>")
    for ri, row in enumerate(rows):
        x.append("      <w:tr>")
        for ci in range(width):
            cell = row[ci] if ci < len(row) else ""
            shade = '<w:shd w:val="clear" w:color="auto" w:fill="F1F5F9"/>' if ri == 0 else ""
            x.append(
                f'        <w:tc><w:tcPr><w:tcW w:w="{col}" w:type="dxa"/>{shade}</w:tcPr>'
                f'<w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>{_runs(cell, bold_all=(ri == 0))}</w:p></w:tc>'
            )
        x.append("      </w:tr>")
    x.append("    </w:tbl>")
    x.append('    <w:p><w:pPr><w:spacing w:after="120"/></w:pPr></w:p>')
    return x


# size (half-points) per Markdown heading level
HEADING_SIZE = {1: 30, 2: 26, 3: 24, 4: 22, 5: 22, 6: 22}
