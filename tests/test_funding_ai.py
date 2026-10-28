"""Run: python tests/test_funding_ai.py   (plain asserts; the API test needs the db container).

The AI tools on one grant (Funding page): the prompts, the cleaning of the AI's answer, and the routes: saved results are
free, a run is charged, a failed run is refunded, nothing is charged when the profile is missing."""
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
os.environ["ENABLE_SCHEDULERS"] = "0"

from tender_app import funding_ai as fai  # noqa: E402

RUN = uuid.uuid4().hex[:8]
USER = f"zz_funding_ai_{RUN}"
NOPROFILE = f"zz_funding_ai_{RUN}_np"
LABEL = f"zz-funding-ai-{RUN}"
GRANT = {"name": "ZZ Boiler grant", "funder": "Energy Dept", "min_award": 1000.0, "max_award": 50000.0, "total_award": None,
         "open_date": "2026-01-01", "close_date": "2026-12-31", "ongoing": False, "applicants": ["Private Sector"],
         "locations": ["England"], "summary": "Boiler upgrades.", "eligibility": "UK businesses only."}


def test_label_check():
    assert fai.valid_label("zz-grant-1") and fai.valid_label("a_b.c")
    for bad in ("", None, 5, "../x", "a b", "x" * 300, "-lead"):
        assert not fai.valid_label(bad), bad


def test_full_text_flattens_every_rich_text_field():
    doc = lambda t: {"nodeType": "document", "content": [{"nodeType": "paragraph", "content": [{"nodeType": "text", "value": t}]}]}  # noqa: E731
    fields = {"grantEligibilityTab": doc("UK only."), "grantSupportingInfoTab": doc("Match funding of 10%."), "grantWebpageUrl": "https://x.gov.uk", "grantFunder": "Defra", "n": 5}
    text, read = fai.full_text("x", fetch=lambda label: {"grantDetail": {"fields": fields}})
    assert read and "UK only." in text and "Match funding of 10%." in text and "Defra" in text and "https://x.gov.uk" not in text
    assert fai.full_text("x", fetch=lambda label: (_ for _ in ()).throw(RuntimeError("down"))) == ("", False), "a failed fetch is not an error"


def test_prompts_carry_the_grant_and_the_company_and_forbid_inventing():
    block = fai.grant_block(GRANT, "PAGE TEXT HERE")
    assert "£1,000 to £50,000" in block and "closes: 2026-12-31" in block and "PAGE TEXT HERE" in block and "total pot not stated" in block
    assert "open all year" in fai.grant_block({**GRANT, "ongoing": True}, "")
    for kind in fai.KINDS:
        system, user = fai.build_prompt(kind, block, "Acme", "We service boilers.", "[A] Q: x\nA: y")
        assert "Never invent" in system and "ZZ Boiler grant" in user and "We service boilers." in user, kind
    assert "ANSWER BANK" in fai.build_prompt("draft", block, "Acme", "c", "[A] Q: x")[1]
    assert "ANSWER BANK" not in fai.build_prompt("summary", block, "Acme", "c", "[A] Q: x")[1]
    try:
        fai.build_prompt("nope", block, "", "")
        raise AssertionError("an unknown tool is refused")
    except ValueError:
        pass


def test_answers_are_cut_down_to_known_values():
    r = fai.clean("eligibility", {"verdict": "definitely!", "headline": "h", "criteria": [
        {"criterion": "UK business", "status": "maybe", "evidence": "e"}, {"criterion": ""}, "junk"], "missing": ["m", 5, None], "x": 1})
    assert r["verdict"] == "unclear" and r["criteria"] == [{"criterion": "UK business", "status": "unclear", "evidence": "e"}] and r["missing"] == ["m"]
    assert "x" not in r
    assert fai.clean("analysis", {"recommendation": "go", "reason": "Fits."})["recommendation"] == "go"
    assert fai.clean("analysis", {"recommendation": "buy"})["recommendation"] == "maybe"
    d = fai.clean("draft", {"sections": [{"heading": "Need", "text": "t", "gaps": ["staff"]}, {"text": "no heading"}], "gaps": ["staff"]})
    assert d["sections"] == [{"heading": "Need", "text": "t", "gaps": ["staff"]}]
    assert fai.clean("summary", "not a dict")["overview"] == ""
    assert fai.is_empty("summary", fai.clean("summary", {})) and not fai.is_empty("summary", fai.clean("summary", {"overview": "x"}))
    assert fai.is_empty("draft", fai.clean("draft", {"sections": []}))


def test_new_tools_prompts_and_cleaning():
    block = fai.grant_block(GRANT, "")
    for kind in ("questions", "budget", "evidence"):
        system, user = fai.build_prompt(kind, block, "", "")
        assert "Never invent" in system and "ZZ Boiler grant" in user, kind
    q = fai.clean("questions", {"questions": [{"question": "Is VAT eligible?", "topic": "eligible costs", "why": "Not stated"}, {"topic": "x"}, "junk"]})
    assert q["questions"] == [{"question": "Is VAT eligible?", "topic": "eligible costs", "why": "Not stated"}]
    assert fai.clean("budget", {"funding_rate_percent": 150})["funding_rate_percent"] is None
    assert fai.clean("budget", {"funding_rate_percent": True})["funding_rate_percent"] is None
    assert fai.clean("budget", {"funding_rate_percent": 40})["funding_rate_percent"] == 40.0
    assert fai.is_empty("budget", fai.clean("budget", {})) and not fai.is_empty("budget", fai.clean("budget", {"match_required": "10% cash"}))
    e = fai.clean("evidence", {"items": [{"item": "Accounts", "stage": "later", "required": "yes"}, {"why": "no item"}]})
    assert e["items"] == [{"item": "Accounts", "why": "", "stage": "application", "required": False}]
    assert fai.is_empty("questions", {"questions": []}) and fai.is_empty("evidence", {"items": []})


def test_documents_are_matched_by_meaningful_words():
    from datetime import date
    docs = [{"filename": "Public_Liability_Insurance_2026.pdf", "expiry_date": "2025-01-01"}, {"filename": "accounts-2025.pdf", "expiry_date": None},
            {"filename": "Equal Opportunities Policies.docx", "expiry_date": "not a date"}, {"filename": "holiday photo.jpg", "expiry_date": None}]
    items = [{"item": "Public liability insurance certificate"}, {"item": "Latest annual accounts"}, {"item": "Equal opportunities policy"}, {"item": "Bank statement"}]
    got = fai.match_documents(items, docs, today=date(2026, 10, 28))
    assert got[0]["documents"] == [{"filename": "Public_Liability_Insurance_2026.pdf", "expiry_date": "2025-01-01", "expired": True}]
    assert got[1]["documents"][0]["filename"] == "accounts-2025.pdf" and got[1]["documents"][0]["expired"] is False
    assert got[2]["documents"][0]["expired"] is False, "an unreadable expiry date is not called expired"
    assert got[3]["documents"] == [], "nothing shares a word with a bank statement"


def test_best_picks():
    from tender_app import funding
    def g(name, words, days, band, ongoing=False):
        return {"name": name, "matches": words, "days_left": days, "band": band, "ongoing": ongoing}
    items = [g("weak", ["a"], 5, "low"), g("mid-far", ["a", "b"], 90, "mid"), g("mid-soon", ["a", "b"], 10, "mid"),
             g("high-far", list("abcd"), 120, "high"), g("ongoing", ["a", "b"], None, "mid", True)]
    got = funding.best_of(items)
    assert [r["name"] for r in got] == ["high-far", "mid-soon", "mid-far", "ongoing"], [r["name"] for r in got]
    assert got[1]["closing_soon"] is True and got[0]["closing_soon"] is False and got[3]["closing_soon"] is False
    assert len(funding.best_of(items * 4)) == funding.BEST_LIMIT and funding.best_of([]) == []


# ─────────────────────────────────────── API ──────────────────────────────────────────────────────

import unittest


class Skip(unittest.SkipTest):
    pass


def test_api_saves_charges_and_refunds():
    try:
        from tender_app.db import get_db_connection
        from tender_app.db_ext import create_funding_opportunities
        conn = get_db_connection()
    except Exception as exc:  # noqa: BLE001
        raise Skip(f"no database: {exc}")
    import server
    from tender_app.credits import get_balance, grant_credits

    def wallet(user):
        c = get_db_connection()
        try:
            return get_balance(c, user)
        finally:
            c.close()

    cur = conn.cursor()
    create_funding_opportunities(cur)
    cur.execute("INSERT INTO funding_opportunities (label, name, funder, summary, max_award, applicants, locations, close_date, eligibility, url) "
                "VALUES (%s, 'ZZ AI grant', 'Energy Dept', 'Boiler upgrades', 50000, '{\"Private Sector\"}', '{\"England\"}', '2030-12-31', 'UK only', 'https://x.gov.uk')", (LABEL,))
    cur.execute("INSERT INTO company_profiles (username, name, profile_text, is_default) VALUES (%s, 'ZZ Co', 'We service gas boilers.', TRUE) RETURNING id", (USER,))
    cur.execute("INSERT INTO user_documents (username, filename, file_path, file_size, expiry_date) VALUES (%s, 'Public_Liability_Insurance.pdf', '/x', 1, '2025-01-01')", (USER,))
    conn.commit()
    original, original_text = server.call_chat_json, fai.full_text
    fai.full_text = lambda label, fetch=None: ("ELIGIBILITY TAB: UK businesses.", True)
    seen = []

    def fake(system, user, **kw):
        seen.append(user)
        return {"verdict": "probably", "headline": "Looks fine", "criteria": [{"criterion": "UK business", "status": "met", "evidence": "profile"}]}

    server.call_chat_json = fake
    try:
        app = server.create_app()

        def client(user):
            c = app.test_client()
            with c.session_transaction() as s:
                s.update(logged_in=True, username=user, email=f"{user}@test.invalid", last_activity=time.time(), csrf_token="t")
            return c

        def post(c, kind, body):
            r = c.post(f"/api/funding/ai/{kind}", json=body, headers={"X-CSRF-Token": "t"})
            return r.status_code, (r.get_json(silent=True) or {})

        c = client(USER)
        g = get_db_connection()
        grant_credits(g, USER, 10, "test")
        g.close()
        assert post(client(NOPROFILE), "eligibility", {"label": LABEL})[0] == 400, "no company profile: refused before any charge"
        assert post(c, "nope", {"label": LABEL})[0] == 404 and post(c, "summary", {"label": "../x"})[0] == 400 and post(c, "summary", {"label": "zz-missing"})[0] == 404

        st, r = post(c, "eligibility", {"label": LABEL, "saved_only": True})
        assert st == 200 and r["none"] is True and wallet(USER) == 10 and not seen, "opening a tab never runs or charges"
        st, r = post(c, "eligibility", {"label": LABEL})
        assert st == 200 and r["cached"] is False and r["cost"] == 1 and r["result"]["verdict"] == "probably" and wallet(USER) == 9
        assert "ELIGIBILITY TAB: UK businesses." in seen[0] and "We service gas boilers." in seen[0]
        st, r = post(c, "eligibility", {"label": LABEL})
        assert st == 200 and r["cached"] is True and r["cost"] == 0 and wallet(USER) == 9 and len(seen) == 1, "a saved result is free"
        assert post(c, "eligibility", {"label": LABEL, "saved_only": True})[1]["result"]["verdict"] == "probably"
        st, r = post(c, "eligibility", {"label": LABEL, "refresh": True})
        assert st == 200 and r["cached"] is False and wallet(USER) == 8 and len(seen) == 2, "Run again is charged"
        assert post(client(NOPROFILE), "summary", {"label": LABEL})[0] != 400, "the summary needs no profile"

        # questions, budget and evidence need no profile; what the user ticks is kept per grant and tool
        server.call_chat_json = lambda system, user, **kw: {"questions": [{"question": "Is VAT eligible?", "topic": "eligible costs", "why": "Not stated"}],
                                                          "items": [{"item": "Public liability insurance", "why": "Needed", "stage": "award", "required": True}],
                                                          "funding_rate_percent": 50, "rate_note": "Up to 50%"}
        before = wallet(USER)
        st, r = post(c, "questions", {"label": LABEL})
        assert st == 200 and r["result"]["questions"][0]["question"] == "Is VAT eligible?" and r["state"] == {} and wallet(USER) == before - 2
        st, r = post(c, "budget", {"label": LABEL})
        assert st == 200 and r["result"]["funding_rate_percent"] == 50.0 and wallet(USER) == before - 4
        st, r = post(c, "evidence", {"label": LABEL})
        assert st == 200 and r["result"]["items"][0]["documents"][0]["filename"] == "Public_Liability_Insurance.pdf" and r["result"]["items"][0]["documents"][0]["expired"] is True
        h = {"X-CSRF-Token": "t"}
        keep = {"q": {"Is VAT eligible?": {"status": "answered", "note": "Yes"}}}
        assert c.post("/api/funding/ai/questions/state", json={"label": LABEL, "state": keep}, headers=h).status_code == 200
        assert c.post("/api/funding/ai/questions/state", json={"label": LABEL, "state": "x"}, headers=h).status_code == 400
        assert c.post("/api/funding/ai/questions/state", json={"label": "../x", "state": {}}, headers=h).status_code == 400
        assert c.post("/api/funding/ai/questions/state", json={"label": LABEL, "state": {"big": "x" * 30000}}, headers=h).status_code == 400
        assert c.post("/api/funding/ai/nope/state", json={"label": LABEL, "state": {}}, headers=h).status_code == 404
        st, r = post(c, "questions", {"label": LABEL, "saved_only": True})
        assert st == 200 and r["state"] == keep and wallet(USER) == before - 6, "the ticks come back with the saved result, free"
        assert post(client(NOPROFILE), "questions", {"label": LABEL, "saved_only": True})[1].get("none") is True, "another user's ticks and results are not shared"
        st, r = c.get("/api/funding/best").status_code, c.get("/api/funding/best").get_json()
        assert st == 200 and r["has_profile"] is True and isinstance(r["items"], list)
        assert client(NOPROFILE).get("/api/funding/best").get_json() == {"has_profile": False, "items": []}

        top = get_db_connection()
        grant_credits(top, USER, 20, "test")
        top.close()

        def down(system, user, **kw):
            raise server.AIServiceError("The AI provider is unavailable", status_code=503)

        server.call_chat_json = down
        before = wallet(USER)
        st, r = post(c, "analysis", {"label": LABEL})
        assert st == 503 and "unavailable" in r["error"] and wallet(USER) == before, "a failed run is refunded"
        server.call_chat_json = lambda system, user, **kw: {"unrelated": 1}
        st, r = post(c, "analysis", {"label": LABEL})
        assert st == 502 and "not been charged" in r["error"] and wallet(USER) == before, "an empty answer is refunded and not saved"
        server.call_chat_json = fake
        g = get_db_connection()
        cc = g.cursor()
        cc.execute("UPDATE credit_wallet SET balance = 0 WHERE username = %s", (USER,))
        g.commit()
        g.close()
        st, r = post(c, "analysis", {"label": LABEL})
        assert st == 402 and r["code"] == "INSUFFICIENT_CREDITS", (st, r)
    finally:
        server.call_chat_json, fai.full_text = original, original_text
        g = get_db_connection()
        cc = g.cursor()
        for table in ("credit_ledger", "credit_wallet", "company_profiles", "funding_ai_results", "funding_ai_state", "user_documents"):
            try:
                cc.execute(f"DELETE FROM {table} WHERE username = ANY(%s)", ([USER, NOPROFILE],))
            except Exception:  # noqa: BLE001
                g.rollback()
        cc.execute("DELETE FROM funding_opportunities WHERE label = %s", (LABEL,))
        g.commit()
        g.close()
        conn.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ran = 0
    for t in tests:
        try:
            t()
        except Skip as exc:
            print("skip", t.__name__, exc)
            continue
        ran += 1
        print("ok", t.__name__)
    print(f"{ran} passed")
