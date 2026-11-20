"""POST /api/bid-style-preview end to end with a fake database. Run: python tests/bid_style_route_test.py"""
import os
import sys
import time

os.environ["ENABLE_SCHEDULERS"] = "0"
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

FILLER = " ".join(["We run the centre every day with a trained team and clear processes in place."] * 6)
PAST = ("Q1. Tell us about your organisation\nWe have 12 years of experience. " + FILLER + "\n\nHow we deliver:\n" + FILLER + "\n\n"
        "Q6. Equality, diversity, inclusion and safeguarding\nWokingham Borough Council requires safeguarding. We adopt an EDI policy and a "
        "safeguarding policy covering children and adults at risk, reviewed every 12 months, with a code of conduct for staff and volunteers "
        "and a complaints procedure shared with the council.\n\nHow we deliver:\n" + FILLER + "\n")
ROWS = [(1, "Matthewsgreen EOI", "Wokingham Borough Council", "2026", PAST, "won")]
SQL = []


class Cur:
    def __init__(self):
        self._one = None
        self._all = []

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split()).lower()
        SQL.append(s)
        self._one, self._all = None, []
        if "select role from users" in s:
            self._one = ("member",)
        elif "from winning_bids" in s:
            self._all = ROWS

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
app = server.create_app()
app.testing = True
c = app.test_client()
with c.session_transaction() as s:
    s["logged_in"] = True
    s["username"] = "zz_style"
    s["last_activity"] = time.time()
    s["csrf_token"] = "t"
H = {"X-CSRF-Token": "t"}
n = 0


def ok(cond, m):
    global n
    n += 1
    assert cond, "FAILED: " + m


Q = [{"number": "1", "question_text": "Describe your approach to ethics, safeguarding and equality", "weighting": "15%"},
     {"number": "2", "question_text": "Provide your price schedule", "weighting": "5%"}]
r = c.post("/api/bid-style-preview", headers=H, json={"tender": {"title": "Oldham Live Well Evaluation"}, "profile_id": 1, "questions": Q})
d = r.get_json()
ok(r.status_code == 200 and d["ok"] and d["mode"] == "auto", "auto mode by default: %s" % r.data[:200])
ok(d["profile"]["learned"] and d["profile"]["answers"] == 2 and d["bids"][0]["title"] == "Matthewsgreen EOI", "style learned from the library")
ok(d["plan"][0]["reusable"]["source_question"].startswith("Q6") and d["plan"][1]["reusable"] is None, "reuse only on the matching topic")
ok("Wokingham" not in d["plan"][0]["reusable"]["paragraph"], "old buyer's name replaced in the preview")
ok("sentences near" in d["style_brief"], "style brief returned")
r = c.post("/api/bid-style-preview", headers=H, json={"past_bids_mode": "off", "questions": Q})
d = r.get_json()
ok(not d["profile"]["learned"] and d["bids"] == [] and d["plan"][0]["reusable"] is None, "off: nothing learned, nothing reused")
ok(not any("insert" in s or "update" in s for s in SQL), "the preview writes nothing")
# release 12: the sector beside topic match, and a plan of sections when there are no questions
r = c.post("/api/bid-style-preview", headers=H, json={"tender": {"title": "Oldham Live Well Evaluation", "description": "Evaluation"}, "questions": []})
d = r.get_json()
ok("sector" in d["bids"][0] and isinstance(d["bids"][0]["sector"], str), "each bid carries a sector (empty when nothing fits): %s" % d["bids"][0])
ok(d["plan_source"] == "sections" and d["plan"][0]["number"] == "S1", "no questions: the plan lists the sections to be written")
# release 12: the bidder's own prices go into the commercial table (free, nothing stored)
SCH = [{"sheet": "Volume 3 prices", "header": ["Item", "Description", "Qty", "Unit price", "Total"],
        "rows": [["1", "Laptop 14in", "10", "", ""], ["2", "Docking station", "4", "", ""], ["3", "Training day", "2", "", ""]]}]
NOTES = "Prices are fixed.\n\n## Commercial offer: price schedule\nold table\nCurrency: OANDA rate on the day [CONFIRM: rate]"
SQL.clear()
r = c.post("/api/bid-apply-prices", headers=H, json={"price_schedules": SCH, "pricing_notes": NOTES,
                                                    "prices": {"0:0": "650"}, "price_text": "Docking station: £120.50"})
d = r.get_json()
ok(r.status_code == 200 and d["ok"] and d["priced"] == 2 and d["rows"] == 3, "two of three lines priced: %s" % r.data[:300])
t = d["pricing_notes"]
ok("GBP 650.00" in t and "GBP 6,500.00" in t and "GBP 482.00" in t, "unit prices and line totals: %s" % t)
ok("**GBP 6,982.00**" in t and "1 line(s) still have no price" in t, "a total, and the unpriced line is said")
ok(t.count("## Commercial offer: price schedule") == 1 and "old table" not in t and t.startswith("Prices are fixed."), "the old table is replaced in place")
ok("Currency: OANDA" in t and "[NEEDS INPUT: unit price]" in t, "the currency rule and the open line are kept")
ok(not any("insert" in s or "update" in s or "delete" in s for s in SQL), "applying prices writes nothing: %s" % SQL)
r = c.post("/api/bid-apply-prices", headers=H, json={"price_schedules": SCH, "pricing_notes": NOTES, "prices": {}, "price_text": "Nothing here"})
ok(r.status_code == 400 and "No price matched" in r.get_json()["error"], "no matching price: a clear message, nothing changed")
ok(c.post("/api/bid-apply-prices", headers=H, json={"price_schedules": [], "prices": {"0:0": 1}}).status_code == 400, "no schedule: refused")
print("bid style route: %d checks passed" % n)
