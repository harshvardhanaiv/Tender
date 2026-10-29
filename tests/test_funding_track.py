"""Saved grants: stage and notes, deadline reminders, the weekly new-grants email. A fake sender is always used."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_app import funding_track as ft  # noqa: E402

NOW = datetime(2026, 10, 28, 9, 0)  # UK local time


def test_allowed_days():
    assert ft.allowed_days("7 days before") == [7] and ft.allowed_days("3 days before") == [3] and ft.allowed_days("1 day before") == [1]
    assert ft.allowed_days("All") == [1, 3, 7] and ft.allowed_days(None) == [7]


def test_award_text_for_emails():
    assert ft.funding_award({"min_award": 150000.0, "max_award": 350000.0}) == "£150k to £350k"
    assert ft.funding_award({"max_award": 500000.0}) == "Up to £500k"
    assert ft.funding_award({"min_award": 1e6}) == "From £1m"
    assert ft.funding_award({"total_award": 3.8e6}) == "Pot £3.8m"
    assert ft.funding_award({}) == "Award size not stated"


def test_reminder_email_says_when_and_escapes():
    subject, html = ft.reminder_email({"name": "Grant <b>", "funder": "Defra & Co", "days": 3, "close_date": "2026-10-31", "close_at": "2026-10-31T23:59", "label": "g-1", "url": None})
    assert subject == "Grant closing in 3 days: Grant <b>"
    assert "Grant &lt;b&gt;" in html and "Defra &amp; Co" in html and "31 Oct 2026, 11:59pm" in html and "/funding/g-1" in html
    assert "closing today" not in html and "today." in ft.reminder_email({"name": "G", "funder": None, "days": 0, "close_date": "2026-10-28", "close_at": None, "label": "g", "url": None})[1]


def test_digest_email_lists_grants_and_unsubscribe():
    grants = [{"label": f"g{i}", "name": f"Grant {i}", "funder": "F", "min_award": None, "max_award": 5000.0, "ongoing": i == 1,
               "close_date": "2026-12-01", "close_at": "2026-12-01T11:00", "fit": {"why": "UK wide"}} for i in range(10)]
    subject, html = ft.digest_email(grants, "AIVHUB LTD", "https://x.example/unsub?a=1&b=2")
    assert subject == "10 new grants that fit AIVHUB LTD"
    assert "Closes 1 Dec 2026, 11:00am" in html and "Rolling, no fixed deadline" in html and "Why: UK wide" in html
    assert "And 2 more" in html and "Grant 7" in html and "Grant 8" not in html
    assert "Stop these emails" in html and "a=1&amp;b=2" in html
    assert ft.digest_email(grants[:1], "", "u")[0] == "1 new grant that fits your company"


def test_update_checks_input():
    class Cur:
        rowcount = 1

        def execute(self, *a, **k):
            pass
    cur = Cur()
    assert ft.update(cur, "u", "l", stage="bogus").startswith("stage must be")
    assert ft.update(cur, "u", "l", notes="x" * 3000).startswith("notes must be")
    assert ft.update(cur, "u", "l", remind="yes").startswith("remind must be")
    assert ft.update(cur, "u", "l") == "Nothing to change."
    assert ft.update(cur, "u", "l", stage="applying", notes="n", remind=False) is None
    cur.rowcount = 0
    assert ft.update(cur, "u", "l", stage="applying") == "That grant is not in your list."


def test_usage_events_are_checked_and_kept():
    class Cur:
        def __init__(self):
            self.rows = []

        def execute(self, sql, params=None):
            if sql.strip().startswith("INSERT"):
                self.rows.append(params)
    cur = Cur()
    assert ft.record_event(cur, "u", "funding_search", {"q": "ai", "results": 9}) is None
    assert cur.rows == [("u", "funding_search", '{"q":"ai","results":9}')]
    assert ft.record_event(cur, "u", "made_up", {}) == "Unknown event."
    assert ft.record_event(cur, "u", "funding_search", {"q": "x" * 500}) == "Event detail is too long."
    assert ft.record_event(cur, "u", "funding_saved", "not a dict") is None and cur.rows[-1][2] == "{}"
    assert len(cur.rows) == 2


def test_database_flow():
    import unittest
    try:
        from tender_app.db import get_db_connection
        conn = get_db_connection()
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"no database: {exc}")
    from tender_app.db_ext import create_funding_opportunities
    from tender_app.blueprints import funding_bp
    cur = conn.cursor()
    create_funding_opportunities(cur)
    ft.ensure_schema(cur)
    conn.commit()
    users = ["zz_track_a", "zz_track_b"]
    labels = ["zz-track-soon", "zz-track-closed", "zz-track-ongoing", "zz-track-ai", "zz-track-old"]
    sent: list = []

    def cleanup():
        cur.execute("DELETE FROM funding_saved WHERE username = ANY(%s)", (users,))
        cur.execute("DELETE FROM funding_digest_prefs WHERE username = ANY(%s)", (users,))
        cur.execute("DELETE FROM email_notifications WHERE username = ANY(%s)", (users,))
        cur.execute("DELETE FROM company_profiles WHERE username = ANY(%s)", (users,))
        cur.execute("DELETE FROM users WHERE username = ANY(%s)", (users,))
        cur.execute("DELETE FROM funding_opportunities WHERE label = ANY(%s)", (labels,))
        conn.commit()

    def fake_send(to, subject, html, *a, **k):
        sent.append((to, subject, html))
        return True

    cleanup()
    try:
        def grant(label, name, close_at, ongoing=False, first_seen=None, summary="", locations=("National",)):
            cur.execute(
                """INSERT INTO funding_opportunities (label, name, summary, locations, applicants, open_date, close_date, close_at, ongoing, url, first_seen)
                   VALUES (%s,%s,%s,%s,%s,'2026-01-01',%s,%s,%s,%s,COALESCE(%s, NOW() AT TIME ZONE 'utc'))""",
                (label, name, summary, list(locations), ["Private Sector"], close_at[:10] if close_at else None, close_at, ongoing, "https://x.example/" + label, first_seen))

        grant("zz-track-soon", "ZZ closing soon", "2026-10-31T23:59")           # 3 days left
        grant("zz-track-closed", "ZZ closed", "2026-10-28T08:00")                # closed at 08:00 today
        grant("zz-track-ongoing", "ZZ rolling", "2030-01-01T00:00", ongoing=True)
        for u, thr in (("zz_track_a", "3 days before"), ("zz_track_b", "7 days before")):
            cur.execute("INSERT INTO users (username, email, email_alerts_enabled, deadline_reminders_enabled, deadline_reminders_threshold) VALUES (%s,%s,TRUE,TRUE,%s)",
                        (u, u + "@example.invalid", thr))
        for lab in ("zz-track-soon", "zz-track-closed", "zz-track-ongoing", "zz-track-gone"):
            cur.execute("INSERT INTO funding_saved (username, label) VALUES ('zz_track_a', %s), ('zz_track_b', %s)", (lab, lab))
        conn.commit()

        # tracked list: stage, listing state, days left
        items = {i["label"]: i for i in ft.tracked(cur, "zz_track_a", NOW.date())}
        assert items["zz-track-soon"]["stage"] == "interested" and items["zz-track-soon"]["days_left"] == 3 and items["zz-track-soon"]["listed"]
        assert items["zz-track-gone"]["listed"] is False and items["zz-track-gone"]["days_left"] is None
        assert items["zz-track-ongoing"]["ongoing"] is True and items["zz-track-ongoing"]["days_left"] is None
        assert ft.update(cur, "zz_track_a", "zz-track-soon", stage="applying", notes="Call the funder", remind=True) is None
        assert ft.update(cur, "zz_track_a", "zz-nope", stage="applying") == "That grant is not in your list."
        conn.commit()
        mine = {i["label"]: i for i in ft.tracked(cur, "zz_track_a", NOW.date())}["zz-track-soon"]
        assert mine["stage"] == "applying" and mine["notes"] == "Call the funder"
        assert {i["label"]: i for i in ft.tracked(cur, "zz_track_b", NOW.date())}["zz-track-soon"]["stage"] == "interested", "stages are per user"

        # reminders: only a (3 days), only the grant that is still open and has a deadline, only active stages
        due = ft.due_reminders(cur, NOW)
        mine_due = [(d["username"], d["label"], d["days"]) for d in due if d["username"] in users]
        assert mine_due == [("zz_track_a", "zz-track-soon", 3)], mine_due
        ft.update(cur, "zz_track_a", "zz-track-soon", stage="submitted")
        conn.commit()
        assert not [d for d in ft.due_reminders(cur, NOW) if d["username"] in users], "a submitted grant needs no reminder"
        ft.update(cur, "zz_track_a", "zz-track-soon", stage="applying", remind=False)
        conn.commit()
        assert not [d for d in ft.due_reminders(cur, NOW) if d["username"] in users], "reminders switched off for the grant"
        ft.update(cur, "zz_track_a", "zz-track-soon", remind=True)
        conn.commit()

        # sending: once only; a failed send is retried next time
        conn.close()
        mine_only = lambda: [m for m in sent if m[0].startswith("zz_track")]  # noqa: E731
        assert ft.send_reminders(get_db_connection, fake_send, NOW) >= 1 and len(mine_only()) == 1
        assert mine_only()[0][0] == "zz_track_a@example.invalid" and "closing in 3 days" in mine_only()[0][1]
        ft.send_reminders(get_db_connection, fake_send, NOW)
        assert len(mine_only()) == 1, "the same reminder is never sent twice"
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM email_notifications WHERE username = ANY(%s)", (users,))
        conn.commit()
        conn.close()
        ft.send_reminders(get_db_connection, lambda *a, **k: False, NOW)
        ft.send_reminders(get_db_connection, fake_send, NOW)
        assert len(mine_only()) == 2, "a failed send leaves no record, so it is tried again"

        # weekly email: opt in starts the clock, then only new grants that fit, once a week
        conn = get_db_connection()
        cur = conn.cursor()
        meta = {"org_name": "ZZ Soft", "company_type": "private", "enterprise_type": "small", "business_type": "J", "postcode": "RG2", "country": "GB"}
        cur.execute("INSERT INTO company_profiles (username, name, profile_text, meta_json, is_default) VALUES ('zz_track_a','ZZ Soft','We build software and AI data products for councils',%s,TRUE)", (json.dumps(meta),))
        conn.commit()
        utc_now = datetime(2026, 10, 28, 9, 0)
        assert ft.get_digest_pref(cur, "zz_track_a") == {"enabled": False, "last_sent": None}
        ft.set_digest_pref(cur, "zz_track_a", True, utc_now - timedelta(days=8))
        conn.commit()
        grant("zz-track-old", "ZZ Track AI old grant", "2026-12-01T11:00", first_seen=utc_now - timedelta(days=20), summary="Open to SMEs using AI")
        grant("zz-track-ai", "ZZ Track AI grant", "2026-12-01T11:00", first_seen=utc_now - timedelta(days=1), summary="Open to SMEs using AI")
        conn.commit()
        conn.close()
        before = len(sent)
        assert ft.run_digest(get_db_connection, fake_send, utc_now) >= 1
        digest = [m for m in sent[before:] if m[0] == "zz_track_a@example.invalid"]
        assert len(digest) == 1 and "ZZ Track AI grant" in digest[0][2] and "ZZ Track AI old grant" not in digest[0][2], "only grants new since the last email"
        assert "Stop these emails" in digest[0][2]
        again = len(sent)
        ft.run_digest(get_db_connection, fake_send, utc_now + timedelta(days=1))
        assert not [m for m in sent[again:] if m[0] == "zz_track_a@example.invalid"], "at most one a week"
        assert not [m for m in sent if m[0] == "zz_track_b@example.invalid" and "new grant" in m[1]], "nobody who did not opt in is emailed"
        conn = get_db_connection()
        cur = conn.cursor()
        ft.set_digest_pref(cur, "zz_track_b", True, utc_now)
        conn.commit()
        conn.close()
        ft.run_digest(get_db_connection, fake_send, utc_now + timedelta(days=30))
        assert not [m for m in sent if m[0] == "zz_track_b@example.invalid" and "new grant" in m[1]], "no profile, so nothing to match"
        assert funding_bp is not None
    finally:
        conn = get_db_connection()
        cur = conn.cursor()
        cleanup()
        cur.close()
        conn.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
