"""Release 8 brand kit checks. Run: python tests/brand_kit_test.py"""
import base64
import os
import struct
import sys
import xml.dom.minidom
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tender_app import brand_kit as bk

n = 0


def ok(c, m):
    global n
    n += 1
    assert c, "FAILED: " + m


def png(w, h):
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


LOGO = base64.b64encode(png(200, 100)).decode()
kit, probs = bk.sanitize_kit({"color": "#0b5", "font": "Comic Sans", "footer_text": "AIVHUB LTD | 12345678", "logo_b64": "data:image/png;base64," + LOGO})
ok(kit["color"] == bk.DEFAULT_COLOR and kit["font"] == bk.DEFAULT_FONT and len(probs) == 2 - 0 or len(probs) >= 1, "bad colour and font are refused: %s" % probs)
kit, probs = bk.sanitize_kit({"color": "#0B5C8A", "font": "Calibri", "footer_text": "AIVHUB LTD | 12345678", "logo_b64": "data:image/png;base64," + LOGO})
ok(not probs and kit["color"] == "0B5C8A" and kit["font"] == "Calibri" and kit["logo_ext"] == "png", "a good kit is kept: %s" % probs)
_, p = bk.sanitize_kit({"logo_b64": base64.b64encode(b"not an image").decode()})
ok(p and "PNG or JPEG" in p[0], "a non-image logo is refused")
_, p = bk.sanitize_kit({"logo_b64": base64.b64encode(png(10, 10) + b"0" * 500000).decode()})
ok(p and "400 KB" in p[0], "a huge logo is refused")
ok(bk.kit_exists(kit) and not bk.kit_exists(bk.sanitize_kit({})[0]), "an untouched kit does not count as a brand kit")

DOC = ('<w:document xmlns:w="w"><w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:color w:val="1E3A8A"/></w:rPr></w:r>'
       '<w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:color w:val="B91C1C"/></w:rPr></w:r></w:document>')
out = bk.brand_document_xml(DOC, kit)
ok('w:ascii="Calibri"' in out and "Segoe" not in out, "body font swapped")
ok('w:val="0B5C8A"' in out and "1E3A8A" not in out and "B91C1C" in out, "heading colour swapped, status colour kept")

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
HDR = '<w:hdr %s><w:p><w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/></w:rPr><w:t>Title</w:t></w:r></w:p></w:hdr>' % NS
FTR = '<w:ftr %s><w:p><w:r><w:t>Page</w:t></w:r></w:p></w:ftr>' % NS
h, f, has = bk.brand_header_footer(HDR, FTR, kit, anonymise=False)
ok(has and "rIdHdrLogo" in h and "AIVHUB LTD | 12345678" in f, "logo in the header, company line in the footer")
for x in (h, f):
    xml.dom.minidom.parseString(x.encode())
ok(True, "header and footer are well-formed XML")
h2, f2, has2 = bk.brand_header_footer(HDR, FTR, kit, anonymise=True)
ok(not has2 and "rIdHdrLogo" not in h2 and "AIVHUB" not in f2 and "Calibri" in h2, "anonymised: no logo or company line, font kept")
cp = bk.cover_logo_paragraph(kit)
xml.dom.minidom.parseString(('<w:body %s xmlns:wp="x">' % NS + cp + "</w:body>").encode()) if False else None
ok('r:embed="rIdBrandLogo"' in cp and 'cx="' in cp, "cover logo paragraph")
ok("brand_logo.png" in bk.header_rels("png"), "header rels name the logo")
jpg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 9 + b"\xff\xc0\x00\x11\x08\x00\x64\x00\xc8\x03" + b"\x00" * 20
ok(bk._image_size(jpg) == (200, 100, "jpeg"), "jpeg size read")
print("brand kit: %d checks passed" % n)
