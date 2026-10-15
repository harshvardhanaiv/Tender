"""Run: python tests/test_email_send_api.py   (plain asserts; needs the dev database, skips itself without one)

Email through Outreach (tender_app/blueprints/email_bp.py, email_send_service.py), through the real Flask app and
its test client, as throwaway users (zz_email_test_*). Outreach itself is a fake transport: this file never touches
the network, never uses a real key and emails nobody. The keys below are made-up strings in the sk_test_ / sk_live_
shape, only there so the key checks have something to check.

  * status: not connected / bad key / live key refused / ready; the key is never in any answer, row or log line,
  * permission: nobody sends without it; administrators always may,
  * one recipient -> POST /v1/email/send, recorded as sent only when Outreach says "sent",
  * 401 / 402 / 403 / 429 stop the batch, 422 does not, the daily cap (ours and Outreach's) sends no more,
  * two or more with the same message -> one campaign, created then approved, never shown as sent,
  * approve failing after create: the retry approves the same campaign and does not create another,
  * the same text is never sent to the same address twice, even when two requests race,
  * refresh updates the record and never moves it backwards.
"""
import itertools
import json
import logging
import os
import re
import sys
import time
import traceback
import uuid
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# create_app() starts the email scheduler, which really sends mail to the dev database's addresses.
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
os.environ["ENABLE_SCHEDULERS"] = "0"

RUN = uuid.uuid4().hex[:8]
DOMAIN = f"t{RUN}.example"
USER_A = f"zz_email_test_{RUN}_a"
USER_B = f"zz_email_test_{RUN}_b"
ADMIN = f"zz_email_test_{RUN}_admin"
USERS = [USER_A, USER_B, ADMIN]
CSRF = "test-csrf-token"
H = {"X-CSRF-Token": CSRF}
TEST_KEY = "sk_test_ZzEmailFake000001"
LIVE_KEY = "sk_live_ZzEmailFake000002"
OPT_OUT = 'If you would rather not hear from us about this, reply "stop" and we will not contact you again.'
SUBJECT = f"Repairs support {RUN}"
BODY = f"Hello,\n\nWe help councils with repairs ({RUN}).\n\n" + OPT_OUT


class Skip(Exception):
    pass


_app = None


def app():
    global _app
    if _app is None:
        from server import create_app
        _app = create_app()
    return _app


def db():
    from tender_app.db import get_db_connection
    return get_db_connection()


def client(user=None):
    c = app().test_client()
    if user:
        with c.session_transaction() as s:
            s.update(logged_in=True, username=user, email=f"{user}@test.invalid", last_activity=time.time(), csrf_token=CSRF)
    return c


def addr(n):
    return f"p{n}@{DOMAIN}"


def cleanup():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM email_sends WHERE username = ANY(%s) OR recipient_email LIKE %s", (USERS, f"%@{DOMAIN}"))
            cur.execute("DELETE FROM email_send_audit WHERE username = ANY(%s)", (USERS,))
            cur.execute("DELETE FROM email_send_permissions WHERE username = ANY(%s)", (USERS,))
            cur.execute("DELETE FROM email_send_suppressions WHERE email LIKE %s", (f"%@{DOMAIN}",))
            cur.execute("DELETE FROM admin_audit_log WHERE target_username = ANY(%s)", (USERS,))
            cur.execute("DELETE FROM growth_campaigns WHERE username = ANY(%s)", (USERS,))
            cur.execute("DELETE FROM users WHERE username = ANY(%s)", (USERS,))
        conn.commit()
    finally:
        conn.close()


def make_admin():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (username, email, role) VALUES (%s, %s, 'admin')", (ADMIN, f"{ADMIN}@test.invalid"))
        conn.commit()
    finally:
        conn.close()


def allow(user=USER_A):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO email_send_permissions (username, granted_by) VALUES (%s, 'test') ON CONFLICT DO NOTHING", (user,))
        conn.commit()
    finally:
        conn.close()


def rows(where="TRUE", args=()):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT recipient_email, mode, status, outreach_id, error, campaign_state, sent_at, key_mode
                    FROM email_sends WHERE recipient_email LIKE %s AND {where} ORDER BY id""",
                (f"%@{DOMAIN}", *args),
            )
            return [dict(zip(("email", "mode", "status", "outreach_id", "error", "campaign_state", "sent_at", "key_mode"), r)) for r in cur.fetchall()]
    finally:
        conn.close()


def call(c, method, url, body=None, headers=H):
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    r = getattr(c, method)(url, **kwargs)
    return r.status_code, (r.get_json(silent=True) or {}), r.get_data(as_text=True)


class Outreach:
    """The fake Outreach: records every request, answers from `handler`, and is installed over the client class."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.calls = []
        self.handler = self.default
        self.ids = itertools.count()
        self.messages = {}  # campaign id -> [message dicts]; message id -> message dict
        self.campaign_approval = "approved"

    def default(self, method, url, headers, data):
        body = json.loads(data) if data else None
        if url.endswith("/v1/wallets"):
            return 200, {"credits": 40, "connected": {"mailboxes": 1}, "livemode": False}
        if method == "POST" and url.endswith("/v1/email/send"):
            return 200, {"id": f"msg_{next(self.ids):04d}", "status": "sent", "to": body["to"], "from": "me@box.example"}
        if method == "POST" and url.endswith("/v1/email/campaigns"):
            return 200, {"id": f"cp_fake{next(self.ids):04d}", "status": "needs_approval", "approval": "pending"}
        if method == "POST" and url.endswith("/decision"):
            return 200, {"id": url.split("/")[-2], "status": "scheduled", "approval": "approved"}
        if method == "GET" and "/v1/email/messages?" in url:
            cid = re.search(r"campaign_id=([^&]+)", url)
            if cid:
                return 200, {"messages": self.messages.get(cid.group(1), [])}
            q = re.search(r"q=([^&]+)", url)
            found = [m for m in self.messages.get("*", []) if q and m["to"] in q.group(1).replace("%40", "@")]
            return 200, {"messages": found}
        if method == "GET" and "/v1/email/messages/" in url:
            return 200, self.messages.get(url.rsplit("/", 1)[1], {"id": url.rsplit("/", 1)[1], "status": "sent"})
        if method == "GET" and "/v1/email/campaigns/" in url:
            return 200, {"id": url.rsplit("/", 1)[1], "status": "scheduled", "approval": self.campaign_approval}
        return 404, {"error": {"code": "not_found", "message": "unknown"}}

    def transport(self, method, url, headers, data, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": json.loads(data) if data else None})
        status, payload = self.handler(method, url, headers, data)
        return status, {}, json.dumps(payload)

    def where(self, suffix, method="POST"):
        return [c for c in self.calls if c["method"] == method and c["url"].endswith(suffix)]

    def sends(self):
        return self.where("/v1/email/send")

    def creates(self):
        return self.where("/v1/email/campaigns")

    def approvals(self):
        return self.where("/decision")


FAKE = Outreach()


def current_sent_today(key_mode="test"):
    from tender_app import email_send_service as svc
    conn = db()
    try:
        return svc.sent_today(conn, key_mode)
    finally:
        conn.close()


def install(key=TEST_KEY, allow_live=False, cap=None, max_batch=200, merge=False):
    from tender_app import outreach_client as oc
    from tender_app import security
    from tender_app.blueprints import email_bp

    if not hasattr(install, "real"):
        install.real = oc.OutreachClient
    real = install.real
    email_bp.OUTREACH_API_KEY = key
    email_bp.OUTREACH_ALLOW_LIVE = allow_live
    email_bp.OUTREACH_API_BASE = "https://api.example.test"
    # the daily cap is per company: leave room for whatever the dev database already sent today
    email_bp.OUTREACH_DAILY_SEND_CAP = (current_sent_today() + (25 if cap is None else cap))
    email_bp.OUTREACH_MAX_BATCH = max_batch
    email_bp.OUTREACH_CAMPAIGN_MERGE_FIELDS = merge
    FAKE.reset()
    security._buckets.clear()
    oc.OutreachClient = lambda k, base=None, timeout=15.0, **kw: real(k, base or "https://api.example.test", timeout=timeout, transport=FAKE.transport)


def send(c, recipients, subject=SUBJECT, body=BODY, **extra):
    return call(c, "post", "/api/email/outreach/send", {"subject": subject, "body": body, "recipients": recipients, **extra})


def people(n, start=0):
    return [{"email": addr(start + i)} for i in range(n)]


# ───────────────────────────────────────────── status ──────────────────────────────────────────────

def test_status_not_connected_and_bad_keys():
    allow()
    c = client(USER_A)
    install(key="")
    code, body, _ = call(c, "get", "/api/email/outreach/status")
    assert code == 200 and body["configured"] is False and body["ready"] is False
    assert "OUTREACH_API_KEY" in body["problem"] and FAKE.calls == []
    install(key="not-a-key-at-all")
    code, body, text = call(c, "get", "/api/email/outreach/status")
    assert body["configured"] is True and body["ready"] is False and "sk_test_" in body["problem"] and FAKE.calls == []
    assert "not-a-key-at-all" not in text
    install(key=LIVE_KEY, allow_live=False)
    code, body, text = call(c, "get", "/api/email/outreach/status")
    assert body["ready"] is False and "OUTREACH_ALLOW_LIVE" in body["problem"] and body["mode"] == "live"
    assert LIVE_KEY not in text and FAKE.calls == []


def test_status_ready_shows_the_masked_key_and_the_wallet_never_the_key():
    allow()
    install()
    code, body, text = call(client(USER_A), "get", "/api/email/outreach/status")
    assert code == 200 and body["ready"] is True and body["mode"] == "test"
    assert body["key_hint"] == "sk_test_…" and TEST_KEY not in text and TEST_KEY[8:] not in text
    assert body["wallet"]["credits"] == 40 and body["wallet"]["connected"] == {"mailboxes": 1}
    assert body["usage"]["daily_cap"] >= 25 and body["setup_hints"]
    assert [c["url"] for c in FAKE.calls] == ["https://api.example.test/v1/wallets"]
    # a live key is usable once it is switched on
    install(key=LIVE_KEY, allow_live=True)
    code, body, text = call(client(USER_A), "get", "/api/email/outreach/status")
    assert body["ready"] is True and body["mode"] == "live" and LIVE_KEY not in text


def test_status_reports_outreach_errors_with_setup_hints():
    allow()
    install()
    FAKE.handler = lambda m, u, h, d: (401, {"error": {"code": "unauthorized", "message": f"key {TEST_KEY} unknown"}})
    code, body, text = call(client(USER_A), "get", "/api/email/outreach/status")
    assert body["ready"] is False and "did not accept" in body["hint"] and TEST_KEY not in text


def test_the_default_base_url_is_the_main_host():
    from tender_app import config
    if "OUTREACH_API_BASE" in os.environ:
        raise Skip("OUTREACH_API_BASE is set in this environment")
    assert config.OUTREACH_API_BASE == "https://outreach.aivhub.com"


# ───────────────────────────────────────────── permission ──────────────────────────────────────────

def test_nobody_sends_without_permission_and_nothing_reaches_outreach():
    install()
    c = client(USER_A)
    code, body, _ = call(c, "get", "/api/email/outreach/status")
    assert code == 200 and body["can_send"] is False and body["wallet"] is None and body["key_hint"] is None
    code, body, _ = send(c, people(1))
    assert code == 403 and "permission" in body["error"]
    assert call(c, "post", "/api/email/outreach/refresh", {})[0] == 403
    assert call(c, "get", "/api/email/outreach/sends")[0] == 403
    assert FAKE.calls == [] and rows() == []


def test_admins_always_may_and_can_grant_and_revoke():
    install()
    make_admin()
    admin, user = client(ADMIN), client(USER_B)
    assert call(admin, "get", "/api/email/outreach/status")[1]["can_send"] is True
    assert call(user, "post", "/api/email/outreach/permissions", {"username": USER_B, "allowed": True})[0] == 403  # not an admin
    code, body, _ = call(admin, "post", "/api/email/outreach/permissions", {"username": USER_B, "allowed": True})
    assert code == 200 and USER_B in [p["username"] for p in body["permissions"]]
    assert send(user, people(1))[0] == 200
    code, body, _ = call(admin, "post", "/api/email/outreach/permissions", {"username": USER_B, "allowed": False})
    assert USER_B not in [p["username"] for p in body["permissions"]]
    assert send(user, people(1, 5))[0] == 403
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT action FROM admin_audit_log WHERE target_username = %s ORDER BY id", (USER_B,))
            assert [r[0] for r in cur.fetchall()] == ["email_send_granted", "email_send_revoked"]
    finally:
        conn.close()


# ───────────────────────────────────────────── one recipient ───────────────────────────────────────

def test_one_recipient_uses_the_immediate_send_endpoint():
    allow()
    install()
    c = client(USER_A)
    code, body, _ = send(c, [{"email": addr(0).upper(), "first_name": "Sam", "company": "Acme"}])
    assert code == 200, body
    assert body["route"] == "single" and body["mode"] == "test"
    assert [s["email"] for s in body["sent"]] == [addr(0)] and body["campaigns"] == [] and body["failed"] == []
    assert len(FAKE.sends()) == 1 and FAKE.creates() == [] and FAKE.approvals() == []
    req = FAKE.sends()[0]
    assert req["url"] == "https://api.example.test/v1/email/send"
    assert req["body"] == {"to": addr(0), "subject": SUBJECT, "body": BODY, "first_name": "Sam", "company": "Acme"}
    assert req["headers"]["Authorization"] == f"Bearer {TEST_KEY}" and req["headers"]["Idempotency-Key"].startswith("tf-mail-")
    [row] = rows()
    assert row["mode"] == "single" and row["status"] == "sent" and row["outreach_id"].startswith("msg_") and row["sent_at"] and row["key_mode"] == "test"
    code, hist, _ = call(c, "get", "/api/email/outreach/sends")
    assert hist["sends"][0]["label"] == "Sent"


def test_only_status_sent_counts_as_sent():
    allow()
    install()
    FAKE.handler = lambda m, u, h, d: (200, {"id": "msg_x1", "status": "queued", "to": addr(0)})
    code, body, _ = send(client(USER_A), people(1))
    assert code == 200 and body["sent"] == [] and len(body["failed"]) == 1
    assert "not \"sent\"" in body["failed"][0]["reason"]
    [row] = rows()
    assert row["status"] == "not_sent" and row["sent_at"] is None
    # it can be sent again later
    FAKE.handler = FAKE.default
    code, body, _ = send(client(USER_A), people(1))
    assert code == 200 and len(body["sent"]) == 1 and rows()[0]["status"] == "sent"


def test_merge_fields_are_filled_per_recipient():
    allow()
    install()
    recs = [{"email": addr(0), "first_name": "Sam"}, {"email": addr(1), "first_name": "Lee"}]
    code, body, _ = send(client(USER_A), recs, subject="Hi {{firstName}}", body="Hello {{firstName}}\n" + OPT_OUT)
    assert code == 200 and body["route"] == "per_recipient"
    assert sorted(c["body"]["subject"] for c in FAKE.sends()) == ["Hi Lee", "Hi Sam"]
    assert FAKE.creates() == []


# ───────────────────────────────────────────── the guards ──────────────────────────────────────────

def test_a_bad_message_is_refused_before_any_call():
    allow()
    install()
    c = client(USER_A)
    for subject, body, word in [("", BODY, "subject"), (SUBJECT, "", "message"), (SUBJECT, "Hello there.", "opt out"),
                                (SUBJECT, "Hi {{surname}}\n" + OPT_OUT, "{{surname}}")]:
        code, out, _ = send(c, people(1), subject, body)
        assert code == 400 and word in out["error"], (word, out)
    assert call(c, "post", "/api/email/outreach/send", {"subject": SUBJECT, "body": BODY, "recipients": []})[0] == 400
    assert call(c, "post", "/api/email/outreach/send", {"subject": SUBJECT, "body": BODY})[0] == 400
    assert call(c, "post", "/api/email/outreach/send", {"subject": SUBJECT, "body": BODY, "recipients": [5]})[0] == 400
    assert FAKE.calls == [] and rows() == []


def test_a_key_that_cannot_be_used_refuses_the_send():
    allow()
    install(key=LIVE_KEY, allow_live=False)
    code, body, text = send(client(USER_A), people(1))
    assert code == 409 and "OUTREACH_ALLOW_LIVE" in body["error"] and LIVE_KEY not in text
    install(key="")
    assert send(client(USER_A), people(1))[0] == 409
    assert FAKE.calls == [] and rows() == []


def test_skipped_recipients_are_reported_and_the_rest_still_go():
    allow()
    install()
    conn = db()
    try:
        with conn.cursor() as cur:  # an address this user marked as opted out in Growth Studio
            cur.execute("INSERT INTO growth_campaigns (username, name, category_label, channel, status, subject, body) VALUES (%s, 'x', 'x', 'email', 'draft', 's', 'b') RETURNING id", (USER_A,))
            cid = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO growth_campaign_targets (campaign_id, buyer_key, buyer_name, signal_key, signal_type, snapshot_json, included, contact_email, status)
                   VALUES (%s, 'OPTED', 'Opted', 'renewal:o:0', 'renewal', '{}', TRUE, %s, 'opted_out')""", (cid, addr(2)))
        conn.commit()
    finally:
        conn.close()
    bounced = addr(3)
    call(client(USER_A), "post", "/api/email/outreach/suppress", {"email": bounced})
    recs = ["", "nonsense", addr(0), addr(0).upper(), addr(2), bounced, addr(4), addr(5)]
    code, body, _ = send(client(USER_A), recs)
    assert code == 200, body
    reasons = {s["email"]: s["reason"] for s in body["skipped"]}
    assert reasons[""] == "No email address." and "valid" in reasons["nonsense"] and "more than once" in reasons[addr(0)]
    assert "Opted out" in reasons[addr(2)] and "Opted out" in reasons[bounced]
    campaign = FAKE.creates()[0]["body"]
    assert sorted(l["email"] for l in campaign["leads"]) == [addr(0), addr(4), addr(5)]
    assert call(client(USER_A), "post", "/api/email/outreach/suppress", {"email": "nope"})[0] == 400


def test_the_same_text_is_not_sent_to_the_same_address_twice():
    allow()
    install()
    c = client(USER_A)
    assert send(c, people(1))[0] == 200 and len(FAKE.sends()) == 1
    code, body, _ = send(c, people(1))
    assert code == 400 and "nobody to send" in body["error"] and "already sent" in body["error"]
    assert len(FAKE.sends()) == 1 and len(rows()) == 1
    # a different text to the same address is a different send
    assert send(c, people(1), subject=SUBJECT + " again")[0] == 200 and len(FAKE.sends()) == 2
    # a different user cannot repeat it either: the record is the company's
    allow(USER_B)
    code, body, _ = send(client(USER_B), people(1))
    assert code == 400 and len(FAKE.sends()) == 2


def test_two_requests_cannot_both_claim_the_same_send():
    from tender_app import email_send as es
    from tender_app import email_send_service as svc
    install()
    item = es.plan_send(SUBJECT, BODY, people(1), key_mode="test")["singles"][0]
    c1, c2 = db(), db()
    try:
        assert svc._claim(c1, USER_A, "b1", item, "single", "test") is True
        assert svc._claim(c2, USER_B, "b2", item, "single", "test") is False  # in progress
        svc._set(c1, [item["hash"]], status="failed", error="x")
        assert svc._claim(c2, USER_B, "b2", item, "single", "test") is True  # a failed one may be retried
        svc._set(c2, [item["hash"]], status="sent")
        assert svc._claim(c1, USER_A, "b3", item, "single", "test") is False  # sent: never again
    finally:
        c1.close()
        c2.close()


def test_the_batch_is_capped_and_the_overflow_is_reported():
    allow()
    install(max_batch=3)
    code, body, _ = send(client(USER_A), people(5))
    assert code == 200 and len(FAKE.creates()[0]["body"]["leads"]) == 3
    over = [s for s in body["skipped"] if "Over the limit of 3" in s["reason"]]
    assert len(over) == 2


def test_dry_run_shows_the_plan_and_sends_nothing():
    allow()
    install()
    c = client(USER_A)
    code, body, _ = send(c, people(3) + ["bad"], dry_run=True)
    assert code == 200 and body["dry_run"] is True and body["route"] == "campaign"
    assert len(body["campaigns"][0]["recipients"]) == 3 and body["skipped"][0]["email"] == "bad"
    assert FAKE.calls == [] and rows() == []
    code, body, _ = send(c, people(1), dry_run=True)
    assert body["route"] == "single" and body["single"] == [addr(0)] and FAKE.calls == []


# ───────────────────────────────────────────── caps and errors ─────────────────────────────────────

def distinct(n, start=0):
    return [{"email": addr(start + i), "first_name": f"Name{i}"} for i in range(n)]


def personal(c, recs, **extra):
    return send(c, recs, subject="Hello {{firstName}}", body="Hello {{firstName}}\n" + OPT_OUT, **extra)


def test_the_daily_cap_sends_no_more_and_queues_nothing():
    allow()
    install(cap=2)
    code, body, _ = personal(client(USER_A), distinct(4))
    assert code == 200 and len(body["sent"]) == 2 and len(FAKE.sends()) == 2
    assert [d["email"] for d in body["daily_limited"]] == [addr(2), addr(3)]
    assert all(d["reason"] == "Not sent: daily limit reached." for d in body["daily_limited"])
    assert FAKE.creates() == [] and {r["email"] for r in rows()} == {addr(0), addr(1)}  # the others are not even recorded
    assert body["usage"]["remaining_today"] == 0
    # the cap is spent: another request sends nothing at all
    code, body, _ = personal(client(USER_A), distinct(2, 10))
    assert code == 200 and body["sent"] == [] and len(body["daily_limited"]) == 2 and len(FAKE.sends()) == 2


def test_outreach_saying_the_daily_cap_is_reached_stops_the_rest():
    allow()
    install()
    state = {"n": 0}

    def handler(method, url, headers, data):
        if url.endswith("/v1/email/send"):
            state["n"] += 1
            if state["n"] == 2:
                return 429, {"error": {"code": "daily_limit_reached", "message": "You can send 25 emails a day."}}
        return FAKE.default(method, url, headers, data)

    FAKE.handler = handler
    code, body, _ = personal(client(USER_A), distinct(4))
    assert code == 200 and len(body["sent"]) == 1 and len(FAKE.sends()) == 2  # no third attempt
    assert [d["email"] for d in body["daily_limited"]] == [addr(1), addr(2), addr(3)]
    assert body["stopped"] and "25 emails a day" in body["stopped"]
    by_email = {r["email"]: r["status"] for r in rows()}
    assert by_email[addr(0)] == "sent" and by_email[addr(1)] == "not_sent" and addr(2) not in by_email


def _fatal(status, code, message="no"):
    install_state = {"n": 0}

    def handler(method, url, headers, data):
        if url.endswith("/v1/email/send"):
            install_state["n"] += 1
            return status, {"error": {"code": code, "message": message}}
        return FAKE.default(method, url, headers, data)

    FAKE.handler = handler


def test_401_402_403_429_stop_the_batch():
    allow()
    for status, code, hint in ((401, "unauthorized", "did not accept"), (402, "insufficient_credits", "credits"),
                               (403, "missing_scope", "email:write and email:approve"), (429, "rate_limited", None)):
        cleanup()
        allow()
        install()
        _fatal(status, code, "stop")
        out_code, body, text = personal(client(USER_A), distinct(3))
        assert out_code == 200, (status, body)
        assert len(FAKE.sends()) == 1, (status, len(FAKE.sends()))  # the next ones are not attempted
        assert body["sent"] == [] and len(body["failed"]) == 1 and body["stopped"] == "stop"
        assert [d["email"] for d in body["deferred"]] == [addr(1), addr(2)]
        assert all(d["reason"].startswith("Not attempted") for d in body["deferred"])
        if hint:
            assert hint in (body["hint"] or ""), (status, body["hint"])
        statuses = {r["email"]: r["status"] for r in rows()}
        assert statuses == {addr(0): "failed"}, statuses  # the deferred ones were never claimed, so they can go later
        install()
        assert personal(client(USER_A), distinct(3))[1]["sent"] and len(rows()) == 3  # and a retry sends all three


def test_422_rejects_one_recipient_and_the_batch_carries_on():
    allow()
    install()
    state = {"n": 0}

    def handler(method, url, headers, data):
        if url.endswith("/v1/email/send"):
            state["n"] += 1
            if state["n"] == 1:
                return 422, {"error": {"code": "invalid_request", "message": "to is not deliverable"}}
        return FAKE.default(method, url, headers, data)

    FAKE.handler = handler
    code, body, _ = personal(client(USER_A), distinct(3))
    assert code == 200 and len(FAKE.sends()) == 3 and len(body["sent"]) == 2 and len(body["failed"]) == 1
    assert body["failed"][0]["email"] == addr(0) and "not deliverable" in body["failed"][0]["reason"] and body["stopped"] is None
    assert {r["email"]: r["status"] for r in rows()} == {addr(0): "failed", addr(1): "sent", addr(2): "sent"}


def test_missing_mailbox_is_a_setup_hint():
    allow()
    install()
    FAKE.handler = lambda m, u, h, d: (422, {"error": {"code": "no_mailbox", "message": "Connect a mailbox first (Email, Mailboxes), and start its warm-up"}}) \
        if u.endswith("/v1/email/campaigns") else FAKE.default(m, u, h, d)
    code, body, _ = send(client(USER_A), people(3))
    assert code == 200 and body["campaigns"] == [] and "mailbox" in body["failed"][0]["reason"]
    assert "warm-up" in body["hint"] and "DKIM" in body["hint"] and body["stopped"]
    assert [r["status"] for r in rows()] == ["failed"] * 3
    # the same answer to a single send stops the rest too: nothing can send until a mailbox is connected
    cleanup()
    allow()
    install()
    FAKE.handler = lambda m, u, h, d: (422, {"error": {"code": "no_mailbox", "message": "Connect a mailbox first (Email, Mailboxes), and start its warm-up"}}) \
        if u.endswith("/v1/email/send") else FAKE.default(m, u, h, d)
    code, body, _ = personal(client(USER_A), distinct(3))
    assert len(FAKE.sends()) == 1 and len(body["deferred"]) == 2 and "mailbox" in body["hint"]


def test_an_unreachable_outreach_keeps_the_claim_so_it_is_not_sent_twice():
    allow()
    install()

    def boom(method, url, headers, data, timeout):
        raise ConnectionError(f"cannot connect with {TEST_KEY}")

    from tender_app import outreach_client as oc
    real = install.real
    oc.OutreachClient = lambda k, base=None, timeout=15.0, **kw: real(k, base or "https://api.example.test", timeout=timeout, transport=boom)
    code, body, text = personal(client(USER_A), distinct(2))
    assert code == 200 and body["sent"] == [] and body["stopped"] and TEST_KEY not in text
    assert [r["status"] for r in rows()] == ["pending"]  # outcome unknown: not marked failed
    install()
    code, body, _ = personal(client(USER_A), distinct(2))
    assert [s["email"] for s in body["sent"]] == [addr(1)]
    assert any("in progress" in s["reason"] for s in body["skipped"])  # the unknown one is not blindly repeated


# ───────────────────────────────────────────── campaigns ───────────────────────────────────────────

def test_two_or_more_with_the_same_message_make_one_campaign_then_approve_it():
    allow()
    install()
    c = client(USER_A)
    code, body, _ = send(c, [{"email": addr(0), "company": "Acme"}, {"email": addr(1)}, {"email": addr(2)}])
    assert code == 200, body
    assert body["route"] == "campaign" and body["sent"] == [] and body["failed"] == []
    assert len(FAKE.creates()) == 1 and len(FAKE.approvals()) == 1 and FAKE.sends() == []
    create, approve = FAKE.creates()[0], FAKE.approvals()[0]
    assert create["url"] == "https://api.example.test/v1/email/campaigns"
    payload = create["body"]
    assert payload["emails"] == 1 and payload["steps"] == [{"subject": SUBJECT, "body": BODY, "delay_days": 0}]
    assert payload["leads"] == [{"email": addr(0), "company": "Acme"}, {"email": addr(1)}, {"email": addr(2)}]
    assert "personalise" not in json.dumps(payload) and payload["name"] and payload["goal"] and payload["audience"]
    cid = body["campaigns"][0]["outreach_id"]
    assert approve["url"] == f"https://api.example.test/v1/email/campaigns/{cid}/decision" and approve["body"] == {"decision": "approve"}
    assert create["headers"]["Idempotency-Key"] and approve["headers"]["Idempotency-Key"] != create["headers"]["Idempotency-Key"]
    assert body["campaigns"][0]["recipients"] == 3 and "sending window" in body["campaigns"][0]["label"]
    assert [r["status"] for r in rows()] == ["approved"] * 3 and all(r["sent_at"] is None for r in rows())
    assert {r["outreach_id"] for r in rows()} == {cid} and {r["mode"] for r in rows()} == {"campaign"}
    code, hist, _ = call(c, "get", "/api/email/outreach/sends")
    assert all("sending window" in s["label"] and s["label"] != "Sent" for s in hist["sends"])


def test_approve_failing_after_create_is_retried_on_the_same_campaign():
    allow()
    install()
    c = client(USER_A)
    FAKE.handler = lambda m, u, h, d: (500, {"error": {"code": "server_error", "message": "try later"}}) \
        if u.endswith("/decision") else FAKE.default(m, u, h, d)
    code, body, _ = send(c, people(3))
    assert code == 200 and body["campaigns"] == [] and body["failed"][0]["step"] == "approve"
    cid = rows()[0]["outreach_id"]
    assert cid and [r["status"] for r in rows()] == ["created"] * 3 and "try later" in rows()[0]["error"]
    assert len(FAKE.creates()) == 1
    code, hist, _ = call(c, "get", "/api/email/outreach/sends")
    assert all("not approved" in s["label"] for s in hist["sends"])  # shown as not sending, never as sent
    # the retry approves that campaign and does not make a second one
    FAKE.handler = FAKE.default
    code, body, _ = send(c, people(3))
    assert code == 200, body
    assert len(FAKE.creates()) == 1 and len(FAKE.approvals()) == 2
    assert FAKE.approvals()[-1]["url"].endswith(f"/{cid}/decision")
    assert body["campaigns"][0]["outreach_id"] == cid and body["campaigns"][0]["resumed"] is True
    assert [r["status"] for r in rows()] == ["approved"] * 3 and {r["outreach_id"] for r in rows()} == {cid}
    # and now it is done: sending again does nothing
    code, body, _ = send(c, people(3))
    assert code == 400 and len(FAKE.creates()) == 1 and len(FAKE.approvals()) == 2


def test_a_missing_approve_permission_stops_and_the_retry_approves_the_same_campaign():
    allow()
    install()
    c = client(USER_A)
    FAKE.handler = lambda m, u, h, d: (403, {"error": {"code": "missing_scope", "message": "missing email:approve"}}) \
        if u.endswith("/decision") else FAKE.default(m, u, h, d)
    code, body, _ = send(c, people(2))
    assert body["stopped"] == "missing email:approve" and "email:approve" in body["hint"]
    assert [r["status"] for r in rows()] == ["created"] * 2
    FAKE.handler = FAKE.default
    code, body, _ = send(c, people(2))
    assert len(FAKE.creates()) == 1 and [r["status"] for r in rows()] == ["approved"] * 2


def test_create_failing_leaves_nothing_half_made_and_can_be_retried():
    allow()
    install()
    c = client(USER_A)
    FAKE.handler = lambda m, u, h, d: (422, {"error": {"code": "invalid_request", "message": "audience is required"}}) \
        if u.endswith("/v1/email/campaigns") else FAKE.default(m, u, h, d)
    code, body, _ = send(c, people(2))
    assert body["failed"][0]["step"] == "create" and FAKE.approvals() == []
    assert [r["status"] for r in rows()] == ["failed"] * 2 and all(r["outreach_id"] is None for r in rows())
    FAKE.handler = FAKE.default
    code, body, _ = send(c, people(2))
    assert len(body["campaigns"]) == 1 and [r["status"] for r in rows()] == ["approved"] * 2


# ───────────────────────────────────────────── refresh ─────────────────────────────────────────────

def test_refresh_updates_the_record_and_never_moves_it_backwards():
    allow()
    install()
    c = client(USER_A)
    send(c, people(2))  # a campaign: addr(0), addr(1)
    send(c, [{"email": addr(5)}], subject="Single " + SUBJECT)  # a single send
    camp_id = [r for r in rows() if r["mode"] == "campaign"][0]["outreach_id"]
    msg_id = [r for r in rows() if r["mode"] == "single"][0]["outreach_id"]
    FAKE.messages[camp_id] = [{"id": "m_a", "to": addr(0).upper(), "status": "sent"},
                              {"id": "m_b", "to": addr(1), "status": "bounced", "bounce_reason": "mailbox full"}]
    FAKE.messages[msg_id] = {"id": msg_id, "status": "replied"}
    code, body, _ = call(c, "post", "/api/email/outreach/refresh", {})
    assert code == 200 and body["stopped"] is None and body["updated"] >= 3, body
    status = {r["email"]: r["status"] for r in rows()}
    assert status == {addr(0): "sent", addr(1): "bounced", addr(5): "replied"}
    bounced = [r for r in rows() if r["email"] == addr(1)][0]
    assert bounced["error"] == "mailbox full"
    assert [s["status"] for s in body["sends"] if s["email"] == addr(5)] == ["replied"]
    # the bounced address is now suppressed
    code, out, _ = send(c, [{"email": addr(1)}, {"email": addr(7)}], subject="Another " + SUBJECT)
    assert any("Opted out" in s["reason"] for s in out["skipped"] if s["email"] == addr(1))
    # Outreach later answers with an older or empty state: nothing goes backwards
    FAKE.campaign_approval = "pending"
    FAKE.messages[camp_id] = [{"id": "m_a", "to": addr(0), "status": "draft"}, {"id": "m_b", "to": addr(1)}]
    FAKE.messages[msg_id] = {"id": msg_id}
    call(c, "post", "/api/email/outreach/refresh", {})
    assert {r["email"]: r["status"] for r in rows() if r["email"] in (addr(0), addr(1), addr(5))} == {addr(0): "sent", addr(1): "bounced", addr(5): "replied"}


def test_refresh_never_turns_approved_back_into_created():
    allow()
    install()
    c = client(USER_A)
    send(c, people(2))
    FAKE.campaign_approval = "pending"
    call(c, "post", "/api/email/outreach/refresh", {})
    assert [r["status"] for r in rows()] == ["approved"] * 2
    assert all(r["campaign_state"] == "scheduled" for r in rows())


def test_refresh_adopts_an_approval_our_own_call_never_saw():
    allow()
    install()
    c = client(USER_A)
    FAKE.handler = lambda m, u, h, d: (500, {"error": {"code": "server_error", "message": "boom"}}) \
        if u.endswith("/decision") else FAKE.default(m, u, h, d)
    send(c, people(2))
    assert [r["status"] for r in rows()] == ["created"] * 2
    FAKE.handler = FAKE.default
    FAKE.campaign_approval = "approved"  # it was approved after all
    call(c, "post", "/api/email/outreach/refresh", {})
    assert [r["status"] for r in rows()] == ["approved"] * 2


def test_refresh_finds_a_send_whose_answer_never_came_back():
    allow()
    install()
    c = client(USER_A)
    from tender_app import outreach_client as oc
    real = install.real
    oc.OutreachClient = lambda k, base=None, timeout=15.0, **kw: real(k, base or "https://api.example.test", timeout=timeout,
                                                                      transport=lambda *a: (_ for _ in ()).throw(TimeoutError()))
    send(c, people(1))
    assert [r["status"] for r in rows()] == ["pending"]
    install()
    FAKE.messages["*"] = [{"id": "msg_found", "to": addr(0), "status": "sent"}]
    call(c, "post", "/api/email/outreach/refresh", {})
    [row] = rows()
    assert row["status"] == "sent" and row["outreach_id"] == "msg_found" and row["sent_at"]


def test_refresh_stops_on_a_fatal_error_and_says_so():
    allow()
    install()
    c = client(USER_A)
    send(c, people(2))
    FAKE.handler = lambda m, u, h, d: (401, {"error": {"code": "unauthorized", "message": "bad key"}})
    code, body, _ = call(c, "post", "/api/email/outreach/refresh", {})
    assert code == 200 and body["stopped"] == "bad key" and "OUTREACH_API_KEY" in body["hint"]
    assert [r["status"] for r in rows()] == ["approved"] * 2


def test_one_user_never_sees_anothers_sends():
    allow(USER_A)
    allow(USER_B)
    install()
    send(client(USER_A), people(1))
    code, body, _ = call(client(USER_B), "get", "/api/email/outreach/sends")
    assert code == 200 and body["sends"] == []
    assert len(call(client(USER_A), "get", "/api/email/outreach/sends")[1]["sends"]) == 1


# ───────────────────────────────────────────── audit and the secret ────────────────────────────────

def test_every_send_action_leaves_an_audit_row():
    allow()
    install()
    c = client(USER_A)
    send(c, people(1))
    send(c, people(3, 10))
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT route, key_mode, requested, sent, campaigns, failed FROM email_send_audit WHERE username = %s ORDER BY id", (USER_A,))
            audit = cur.fetchall()
    finally:
        conn.close()
    assert audit == [("single", "test", 1, 1, 0, 0), ("campaign", "test", 3, 0, 3, 0)], audit


def test_the_key_never_appears_in_any_output_row_or_log():
    allow()
    install()
    c = client(USER_A)
    logs = StringIO()
    handler = logging.StreamHandler(logs)
    logging.getLogger().addHandler(handler)
    old_level = logging.getLogger().level
    logging.getLogger().setLevel(logging.DEBUG)
    out, err = StringIO(), StringIO()
    texts = []
    try:
        with redirect_stdout(out), redirect_stderr(err):
            # Outreach echoes the key in its errors, on every kind of call
            FAKE.handler = lambda m, u, h, d: (401, {"error": {"code": "unauthorized", "message": f"key {TEST_KEY} rejected", "details": {"key": TEST_KEY}}})
            texts.append(call(c, "get", "/api/email/outreach/status")[2])
            texts.append(send(c, people(1))[2])
            texts.append(send(c, people(3, 5))[2])
            texts.append(call(c, "post", "/api/email/outreach/refresh", {})[2])
            FAKE.handler = FAKE.default
            texts.append(send(c, people(1, 20))[2])
            texts.append(call(c, "get", "/api/email/outreach/status")[2])
            texts.append(call(c, "get", "/api/email/outreach/sends")[2])
            texts.append(send(c, people(2, 30), dry_run=True)[2])
    finally:
        logging.getLogger().removeHandler(handler)
        logging.getLogger().setLevel(old_level)
    everything = "\n".join(texts + [out.getvalue(), err.getvalue(), logs.getvalue()])
    assert TEST_KEY not in everything and TEST_KEY[8:] not in everything
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(string_agg(COALESCE(error, '') || COALESCE(campaign_state, '') || subject, ' '), '') FROM email_sends WHERE recipient_email LIKE %s", (f"%@{DOMAIN}",))
            stored = cur.fetchone()[0]
    finally:
        conn.close()
    assert TEST_KEY not in stored and "sk_test_Zz" not in stored


def test_the_browser_files_never_hold_a_key():
    root = ROOT / "web"
    for path in list(root.glob("*.js")) + list(root.glob("*.html")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not re.search(r"sk_(test|live)_[A-Za-z0-9_\-]{6,}", text), path.name
        assert "OUTREACH_API_KEY" not in text.replace("OUTREACH_API_KEY on the server", ""), path.name


if __name__ == "__main__":
    try:
        conn = db()
        conn.close()
    except Exception as exc:  # no database: nothing to test against
        print(f"SKIP  no database ({type(exc).__name__})")
        sys.exit(0)
    app()  # create_app() runs the migrations, so a fresh database has the tables before the first cleanup
    failures = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    try:
        for name, fn in tests:
            cleanup()
            try:
                fn()
                print(f"ok    {name}")
            except Skip as why:
                print(f"skip  {name} ({why})")
            except Exception:
                failures += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
    finally:
        cleanup()
    print(f"\n{len(tests) - failures} of {len(tests)} passed")
    sys.exit(1 if failures else 0)
