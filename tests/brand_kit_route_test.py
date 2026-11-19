"""Brand kit end to end: save a kit, export the Word file with branding on, off, and anonymised. No network, no Docker.
Run: python tests/brand_kit_route_test.py"""
from __future__ import annotations

import base64
import io
import json
import os
import struct
import sys
import time
import zipfile
import zlib
import xml.dom.minidom

os.environ["ENABLE_SCHEDULERS"] = "0"
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

STORE = {}
SIGN = {}


class Cur:
    def __init__(self):
        self._one = None
        self._all = []

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split()).lower()
        self._one, self._all = None, []
        if "select role from users" in s:
            self._one = ("member",)
        elif "select balance" in s or ("from credit_wallet" in s and "select" in s):
            self._one = (500,)
        elif "from brand_kits" in s:
            k = STORE.get((params[0], params[1])) or STORE.get((params[0], 0))
            self._one = (k,) if k else None
        elif "insert into brand_kits" in s:
            STORE[(params[0], params[1])] = params[2]
        elif s.startswith("select") and "from bid_signoffs" in s:
            self._all = [(k[2], v, "2026-11-17 10:00:00") for k, v in SIGN.items() if k[0] == params[0] and k[1] == params[1]]
        elif "insert into bid_signoffs" in s:
            SIGN[(params[0], params[1], params[2])] = params[3]
        elif "delete from bid_signoffs" in s:
            SIGN.pop((params[0], params[1], params[2]), None)
        elif "from company_profiles" in s and "select 1" in s:
            self._one = (1,)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all

    def close(self):
        pass


class Conn:
    def cursor(self):
        return Cur()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


server.get_db_connection = lambda *a, **k: Conn()
server.init_db = lambda: None
import tender_app.blueprints.profiles_bp as pbp  # noqa: E402
pbp._get_db = lambda: Conn()
import tender_app.credits as credits  # noqa: E402
import tender_app.metering as metering  # noqa: E402
metering.consume_credits = lambda conn, user, cost, reason, ref_id="": 500
credits.refund_credits = lambda conn, user, amount, reason, ref_id="": 500
credits.get_balance = lambda conn, user: 500

app = server.create_app()
app.testing = True
client = app.test_client()
with client.session_transaction() as s:
    s["logged_in"] = True
    s["username"] = "zz_brand_test"
    s["last_activity"] = time.time()
    s["csrf_token"] = "t"
H = {"X-CSRF-Token": "t"}
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


BID = {"cover_letter": {"content": "Dear Team,\n\nWe submit our bid.\n\nYours faithfully,\nAIVHUB LTD"},
       "executive_summary": {"content": "A short summary of our approach."}}
EXP = {"title": "Oldham Live Well Evaluation", "authority": "Oldham Council", "company_name": "AIVHUB LTD", "bid_response": BID,
       "checklist": [], "clarifications": [], "profile_id": 1}


def export(**extra):
    r = client.post("/api/download-proposal", headers=H, json={**EXP, **extra})
    assert r.status_code == 200, (r.status_code, r.data[:300])
    return zipfile.ZipFile(io.BytesIO(r.data))


# no kit yet: the file is as before
z = export()
ok("Segoe UI" in z.read("word/document.xml").decode() and "word/media/brand_logo.png" not in z.namelist(), "no kit, no branding")
g = client.get("/api/brand-kit?profile_id=1")
ok(g.status_code == 200 and g.get_json()["exists"] is False, "no kit saved yet")

# a bad kit is refused
r = client.put("/api/brand-kit", headers=H, json={"profile_id": 1, "color": "red"})
ok(r.status_code == 400, "a bad colour is refused: %s" % r.status_code)
# a good kit is saved
logo = base64.b64encode(png(120, 60)).decode()
r = client.put("/api/brand-kit", headers=H, json={"profile_id": 1, "color": "0B5C8A", "font": "Calibri",
                                                   "footer_text": "AIVHUB LTD | Company no. 12345678", "logo_b64": logo})
ok(r.status_code == 200 and r.get_json()["has_logo"], "kit saved")
g = client.get("/api/brand-kit?profile_id=1").get_json()
ok(g["exists"] and g["kit"]["has_logo"] and g["kit"]["font"] == "Calibri" and "logo_b64" not in g["kit"] and "logo" not in g["kit"], "kit read back without the logo bytes")
g = client.get("/api/brand-kit?profile_id=1&logo=1").get_json()
ok(g["kit"]["logo"].startswith("data:image/png;base64,") and g["kit"]["logo"].endswith(logo[-8:]), "?logo=1 sends the logo for the live preview")
r = client.put("/api/brand-kit", headers=H, json={"profile_id": 1, "color": "123456"})
ok(r.status_code == 200 and r.get_json()["has_logo"], "editing the colour keeps the saved logo")
client.put("/api/brand-kit", headers=H, json={"profile_id": 1, "color": "0B5C8A", "font": "Calibri", "footer_text": "AIVHUB LTD | Company no. 12345678"})

# branding on
z = export()
names = z.namelist()
doc, hdr, ftr = (z.read(p).decode() for p in ("word/document.xml", "word/header1.xml", "word/footer1.xml"))
ok('w:ascii="Calibri"' in doc and "Segoe UI" not in doc and "0B5C8A" in doc, "fonts and heading colour applied")
ok("word/media/brand_logo.png" in names and "word/_rels/header1.xml.rels" in names and "rIdHdrLogo" in hdr, "logo is in the header")
ok("Company no. 12345678" in ftr, "footer line added")
ct = z.read("[Content_Types].xml").decode()
ok(ct.count('Extension="png"') == 1, "no repeated content type")
for p in ("word/document.xml", "word/header1.xml", "word/footer1.xml", "[Content_Types].xml", "word/_rels/header1.xml.rels"):
    xml.dom.minidom.parseString(z.read(p))
ok(True, "every part is well-formed XML")

# branding switched off for this bid
z = export(branding_enabled=False)
ok("Segoe UI" in z.read("word/document.xml").decode() and "word/media/brand_logo.png" not in z.namelist(), "switched off: no branding")

# the buyer wants anonymised answers
z = export(anonymise_answers=True)
doc, hdr, ftr = (z.read(p).decode() for p in ("word/document.xml", "word/header1.xml", "word/footer1.xml"))
ok("rIdHdrLogo" not in hdr and "Company no." not in ftr, "anonymised: no logo or company line on every page")
ok("rIdBrandLogo" in doc and "rIdBrandLogo" in z.read("word/_rels/document.xml.rels").decode(), "anonymised: logo on the cover only")
xml.dom.minidom.parseString(z.read("word/document.xml"))
ok(True, "anonymised document is well-formed")
# ---- the submission pack's technical offer carries the brand too -------------------------------------------------------
PACK = {"title": "IT equipment", "authority": "Chemonics", "company_name": "AIVHUB LTD", "profile_id": 1,
        "price_schedules": [], "bid_response": {"cover_letter": {"content": "Dear Olha."},
                                                "itt_question_responses": [{"question_number": "1", "response": "We deliver in 20 days."}]}}


def pack_docx(**extra):
    r = client.post("/api/download-submission-pack", headers=H, json={**PACK, **extra})
    assert r.status_code == 200, (r.status_code, r.data[:300])
    return zipfile.ZipFile(io.BytesIO(zipfile.ZipFile(io.BytesIO(r.data)).read("technical_offer.docx")))


z = pack_docx()
ok(any(nm.startswith("word/media/") for nm in z.namelist()) and "Company no. 12345678" in z.read("word/footer1.xml").decode(), "pack: logo in the header and company line in the footer")
z = pack_docx(branding_enabled=False)
ok(not any(nm.startswith("word/media/") for nm in z.namelist()), "pack: switched off, no logo")
z = pack_docx(anonymise_answers=True)
ok("Company no." not in "".join(z.read(nm).decode("utf8", "ignore") for nm in z.namelist() if nm.startswith("word/footer")), "pack: anonymised, no company line")

# ---- readiness score and sign-off ----------------------------------------------------------------------------------------
GOODA = "We will run the centre with a trained team, clear opening hours and a monthly review with the council. " * 4
S1 = {"itt_questions": [{"number": "1", "question_text": "About you", "weighting": "100%"}],
      "submission_deadline": {"date": "08/12/2099", "time": "12:00"},
      "required_submission_documents": [{"name": "DDQ", "kind": "form"}]}
RBID = {"itt_question_responses": [{"question_number": "1", "question_text": "About you", "response": GOODA, "weighting": "100%", "budget_words": 80}]}
r = client.post("/api/bid-readiness", headers=H, json={"bid_response": RBID, "dissection": S1, "bid_key": "bk1"}).get_json()
ok(r["ok"] and r["readiness"]["score"] == 100 and r["readiness"]["signoff_open"] == 3, "readiness endpoint: score and open sign-offs: %s" % r["readiness"]["signoff_open"])
EXPORT = {**EXP, "final_export": True, "bid_response": RBID, "dissection": S1, "bid_key": "bk1", "profile_id": 1}
r = client.post("/api/download-proposal", headers=H, json=EXPORT)
ok(r.status_code == 409 and r.get_json()["code"] == "SIGNOFF_REQUIRED" and len(r.get_json()["signoff"]) == 3, "final export waits for the sign-off: %s" % r.status_code)
for k in ("insurance", "declarations", "doc-ddq"):
    ok(client.post("/api/bid-signoff", headers=H, json={"bid_key": "bk1", "item_key": k, "ticked": True}).get_json()["ok"], "tick " + k)
r = client.get_json if False else client.post("/api/bid-readiness", headers=H, json={"bid_response": RBID, "dissection": S1, "bid_key": "bk1"}).get_json()["readiness"]
ok(r["ready"] and r["signoff"][0]["by"] == "zz_brand_test" and r["signoff"][0]["at"], "who ticked and when is kept")
r = client.post("/api/download-proposal", headers=H, json=EXPORT)
ok(r.status_code == 200, "ticked: the final export goes ahead (%s)" % r.status_code)
client.post("/api/bid-signoff", headers=H, json={"bid_key": "bk1", "item_key": "insurance", "ticked": False})
ok(client.post("/api/download-proposal", headers=H, json=EXPORT).status_code == 409, "un-ticking blocks it again")
r = client.post("/api/download-proposal", headers=H, json={**EXPORT, "bid_key": ""})
ok(r.status_code == 200, "without a bid key (older clients) the gate is not applied")
print("brand kit route: %d checks passed" % n)
