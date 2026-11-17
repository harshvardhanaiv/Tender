"""Release 8 (F3): a company's brand kit (heading colour, fonts, footer line, logo) and how it is applied to the Word export.

Set once per company profile; each bid can switch it off. Branding is applied when the file is built, so switching it needs no
regeneration. When the buyer asks for anonymised answers, the header and footer carry no company name or logo (they repeat on every
page, answers included); the logo then appears on the cover page only.
"""
import base64
import re

FONTS = ("Calibri", "Arial", "Segoe UI", "Georgia", "Times New Roman", "Verdana", "Cambria", "Tahoma")
DEFAULT_FONT = "Segoe UI"
DEFAULT_COLOR = "1E3A8A"
MAX_LOGO_BYTES = 400 * 1024
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _image_size(data):
    """(width, height, ext) for PNG or JPEG bytes, or None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"), "png"
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big"), "jpeg"
            i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


def sanitize_kit(raw):
    """Clean a kit from the browser. Returns (kit, problems). Anything unusable is dropped, never guessed."""
    raw = raw if isinstance(raw, dict) else {}
    problems = []
    kit = {"color": DEFAULT_COLOR, "font": DEFAULT_FONT, "footer_text": "", "logo_b64": "", "logo_ext": ""}
    m = _HEX.match(str(raw.get("color") or "").strip())
    if m:
        kit["color"] = m.group(1).upper()
    elif raw.get("color"):
        problems.append("The heading colour must be a six-digit hex value such as 1E3A8A.")
    font = str(raw.get("font") or "").strip()
    if font in FONTS:
        kit["font"] = font
    elif font:
        problems.append("Choose one of the listed fonts.")
    kit["footer_text"] = re.sub(r"[\x00-\x1f]+", " ", str(raw.get("footer_text") or "")).strip()[:160]
    logo = str(raw.get("logo_b64") or "").split(",")[-1].strip()
    if logo:
        try:
            data = base64.b64decode(logo, validate=False)
        except Exception:  # noqa: BLE001
            data = b""
        info = _image_size(data) if data else None
        if not info or info[0] <= 0 or info[1] <= 0:
            problems.append("The logo must be a PNG or JPEG image.")
        elif len(data) > MAX_LOGO_BYTES:
            problems.append("The logo is over 400 KB; use a smaller image.")
        else:
            kit["logo_b64"], kit["logo_ext"] = base64.b64encode(data).decode("ascii"), info[2]
    return kit, problems


def kit_exists(kit):
    return bool(kit) and bool(kit.get("logo_b64") or kit.get("footer_text") or kit.get("color", DEFAULT_COLOR) != DEFAULT_COLOR
                              or kit.get("font", DEFAULT_FONT) != DEFAULT_FONT)


def _logo_extent(kit, height_cm=1.2):
    data = base64.b64decode(kit["logo_b64"])
    w, h, _ = _image_size(data)
    cy = int(height_cm * 360000)
    return int(cy * w / h), cy


def logo_drawing(kit, rid, name="logo", height_cm=1.2):
    cx, cy = _logo_extent(kit, height_cm)
    return ('<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0" '
            'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
            f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="9001" name="{name}"/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="0" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')


def brand_document_xml(doc_xml, kit):
    """Font and heading colour. The default heading colour is the only colour swapped; status colours stay."""
    out = doc_xml.replace('w:ascii="%s" w:hAnsi="%s"' % (DEFAULT_FONT, DEFAULT_FONT),
                          'w:ascii="%s" w:hAnsi="%s"' % (kit["font"], kit["font"]))
    return out.replace('<w:color w:val="%s"/>' % DEFAULT_COLOR, '<w:color w:val="%s"/>' % kit["color"])


def cover_logo_paragraph(kit, rid="rIdBrandLogo"):
    return '<w:p><w:pPr><w:spacing w:after="240"/></w:pPr>' + logo_drawing(kit, rid, "cover_logo", 2.0) + "</w:p>"


def brand_header_footer(header_xml, footer_xml, kit, anonymise=False):
    """Returns (header_xml, footer_xml, header_has_logo). Anonymised: neither carries the company's name or logo."""
    h, f = header_xml, footer_xml
    font = kit["font"]
    h = h.replace('w:ascii="%s" w:hAnsi="%s"' % (DEFAULT_FONT, DEFAULT_FONT), 'w:ascii="%s" w:hAnsi="%s"' % (font, font))
    f = f.replace('w:ascii="%s" w:hAnsi="%s"' % (DEFAULT_FONT, DEFAULT_FONT), 'w:ascii="%s" w:hAnsi="%s"' % (font, font))
    if anonymise:
        return h, f, False
    has_logo = bool(kit.get("logo_b64"))
    if has_logo:
        h = h.replace("</w:hdr>", '<w:p>' + logo_drawing(kit, "rIdHdrLogo", "header_logo") + "</w:p></w:hdr>")
    if kit.get("footer_text"):
        text = (kit["footer_text"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        run = ('<w:rPr><w:rFonts w:ascii="%s" w:hAnsi="%s"/><w:sz w:val="16"/><w:color w:val="64748B"/></w:rPr>' % (font, font))
        f = f.replace("</w:ftr>", '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p></w:ftr>' % (run, text))
    return h, f, has_logo


def header_rels(ext):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdHdrLogo" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="media/brand_logo.%s"/></Relationships>' % ext)


def load_kit(conn, username, profile_id):
    """The saved kit for this company profile, or the account-wide one, or None."""
    import json
    cur = conn.cursor()
    try:
        cur.execute("SELECT kit_json FROM brand_kits WHERE username=%s AND profile_id IN (%s, 0) "
                    "ORDER BY profile_id DESC LIMIT 1", (username, int(profile_id or 0)))
        row = cur.fetchone()
    finally:
        cur.close()
    if not row or not row[0]:
        return None
    try:
        return sanitize_kit(json.loads(row[0]))[0]
    except ValueError:
        return None


def save_kit(conn, username, profile_id, kit):
    import json
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO brand_kits (username, profile_id, kit_json) VALUES (%s,%s,%s) "
                    "ON CONFLICT (username, profile_id) DO UPDATE SET kit_json=EXCLUDED.kit_json, updated_at=CURRENT_TIMESTAMP",
                    (username, int(profile_id or 0), json.dumps(kit)))
        conn.commit()
    finally:
        cur.close()
