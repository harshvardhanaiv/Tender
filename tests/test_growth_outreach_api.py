"""Run: python tests/test_growth_outreach_api.py   (plain asserts; needs the dev database, skips itself without one)

The Growth Studio -> Outreach routes, through the real Flask app and its test client, as throwaway users
(zz_outreach_test_*) against campaigns and targets this file inserts and removes again. Outreach itself is a
fake transport: the file never touches the network, never uses a real key and emails nobody.

  * status: not connected / bad key / live key refused / ready, and the key is never in an answer,
  * sending: each buyer is made as an Outreach campaign and then approved, the buyer is recorded as sent,
    a buyer is never sent twice, a message that cannot go is refused before any call, a bad buyer does not
    stop the others, a fatal error (no credits, no permission) stops cleanly and says how many are left,
  * a campaign made but not approved is approved on retry only while the message is unchanged,
  * reading the state back, with and without asking Outreach, and one user can never see another's.
"""
import itertools
import json
import os
import sys
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# create_app() starts the email scheduler, which really sends mail to the dev database's addresses.
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
os.environ["ENABLE_SCHEDULERS"] = "0"
os.environ["ENABLE_GROWTH_STUDIO"] = "1"

RUN = uuid.uuid4().hex[:8]
USER_A = f"zz_outreach_test_{RUN}_a"
USER_B = f"zz_outreach_test_{RUN}_b"
USERS = [USER_A, USER_B]
CSRF = "test-csrf-token"
H = {"X-CSRF-Token": CSRF}
TEST_KEY = "sk_test_ZzOutreachFake0001"
LIVE_KEY = "sk_live_ZzOutreachFake0002"
OPT_OUT = 'If you would rather not hear from us about this, reply "stop" and we will not contact you again.'
SUBJECT = "{{category}} support ahead of your contract renewal"
BODY = "Hello {{buyer_contact}},\n\n{{buyer_name}}'s contract “{{contract_title}}” ends on {{renewal_date}}.\n\n" + OPT_OUT


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


def call(c, method, url, body=None, headers=H):
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    r = getattr(c, method)(url, **kwargs)
    return r.status_code, (r.get_json(silent=True) or {}), r.get_data(as_text=True)


class Outreach:
    """The fake Outreach: records every request, answers from `handler`, and is installed over the client class."""

    def __init__(self):
        self.calls = []
        self.handler = self.default
        self.ids = itertools.count()  # campaign ids cp_fake0000, cp_fake0001, ...; a test may restart it
        self.msg_ids = itertools.count()  # one-off sends: msg_fake0000, ...

    def default(self, method, url, headers, data):
        if url.endswith("/v1/wallets"):
            return 200, {"credits": 40, "connected": {"mailboxes": 1}, "livemode": False}
        if method == "POST" and url.endswith("/v1/email/send"):
            return 200, {"id": f"msg_fake{next(self.msg_ids):04d}", "status": "sent", "to": json.loads(data)["to"], "from": "sales@sender.example"}
        if method == "GET" and "/v1/email/messages/" in url:
            return 200, {"id": url.rsplit("/", 1)[1], "status": "sent", "to": "x@example.test"}
        if method == "POST" and url.endswith("/v1/email/campaigns"):
            n = next(self.ids)
            return 200, {"id": f"cp_fake{n:04d}", "status": "needs_approval", "approval": "pending"}
        if method == "POST" and url.endswith("/decision"):
            return 200, {"id": url.split("/")[-2], "status": "sending", "approval": "approved"}
        if method == "GET" and "/v1/email/campaigns/" in url:
            return 200, {"id": url.rsplit("/", 1)[1], "status": "sending", "approval": "approved", "sent": 1, "replies": 0}
        return 404, {"error": {"code": "not_found", "message": "unknown"}}

    def transport(self, method, url, headers, data, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": json.loads(data) if data else None})
        status, payload = self.handler(method, url, headers, data)
        return status, {}, json.dumps(payload)

    def posts(self):
        """Requests that make an Outreach campaign."""
        return [c for c in self.calls if c["method"] == "POST" and c["url"].endswith("/v1/email/campaigns")]

    def sends(self):
        """Requests that send one email straight away (/v1/email/send)."""
        return [c for c in self.calls if c["method"] == "POST" and c["url"].endswith("/v1/email/send")]

    def approvals(self):
        return [c for c in self.calls if c["method"] == "POST" and c["url"].endswith("/decision")]


FAKE = Outreach()


def install(key=TEST_KEY, allow_live=False):
    from tender_app import outreach_client as oc
    from tender_app.blueprints import growth_bp

    if not hasattr(install, "real"):
        install.real = oc.OutreachClient
    real = install.real
    growth_bp.OUTREACH_API_KEY = key
    growth_bp.OUTREACH_ALLOW_LIVE = allow_live
    FAKE.calls.clear()
    FAKE.handler = FAKE.default
    FAKE.ids = itertools.count()
    FAKE.msg_ids = itertools.count()
    oc.OutreachClient = lambda k, base=None, timeout=15.0, **kw: real(k, base or "https://api.example.test", timeout=timeout, transport=FAKE.transport)


def cleanup():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM growth_campaigns WHERE username = ANY(%s)", (USERS,))
        conn.commit()
    finally:
        conn.close()


def make_campaign(user, subject=SUBJECT, body=BODY, targets=None):
    """A campaign with targets: [(buyer, email, status, included)]. Returns (campaign id, [target ids])."""
    targets = targets if targets is not None else [
        ("Alpha Borough Council", "procurement@alpha.example", "not_sent", True),
        ("Beta District Council", "contracts@beta.example", "not_sent", True),
        ("Gamma NHS Trust", "", "not_sent", True),
    ]
    snap = json.dumps({"type": "renewal", "date": "2026-12-01",
                       "contracts": [{"title": "Gas servicing", "ends": "2026-12-01", "value": 250000, "suppliers": ["Old Co"]}]})
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO growth_campaigns (username, name, category_label, channel, status, subject, body, last_activity_at)
                   VALUES (%s, %s, %s, 'email', 'draft', %s, %s, NOW()) RETURNING id""",
                (user, f"Outreach test {uuid.uuid4().hex[:6]}", "Housing repairs & gas servicing", subject, body),
            )
            cid = cur.fetchone()[0]
            ids = []
            for i, (buyer, email, status, included) in enumerate(targets):
                cur.execute(
                    """INSERT INTO growth_campaign_targets
                           (campaign_id, buyer_key, buyer_name, signal_key, signal_type, snapshot_json, included, contact_email, status)
                       VALUES (%s, %s, %s, %s, 'renewal', %s, %s, %s, %s) RETURNING id""",
                    (cid, buyer.upper(), buyer, f"renewal:{buyer.lower()}:{i}", snap, included, email or None, status),
                )
                ids.append(cur.fetchone()[0])
        conn.commit()
        return cid, ids
    finally:
        conn.close()


def target_statuses(c, cid):
    code, body, _ = call(c, "get", f"/api/growth/campaigns/{cid}")
    assert code == 200, body
    return {t["buyer_name"]: t["status"] for t in body["targets"]}


# ───────────────────────────────────────────── tests ──────────────────────────────────────────────

def test_status_not_connected():
    install(key="")
    code, body, raw = call(client(USER_A), "get", "/api/growth/outreach/status")
    assert code == 200 and body["configured"] is False and body["ready"] is False and body["mode"] is None
    assert "OUTREACH_API_KEY" in body["problem"] and FAKE.calls == []


def test_status_sends_the_page_nowhere_else_connecting_happens_in_the_connections_window():
    install(key="")
    code, body, raw = call(client(USER_A), "get", "/api/growth/outreach/status")
    assert code == 200 and "connect_url" not in body and "manage_url" not in body and "https://" not in raw
    assert FAKE.calls == []


def test_status_bad_key_and_live_key_are_refused_without_a_call():
    install(key="hello")
    code, body, _ = call(client(USER_A), "get", "/api/growth/outreach/status")
    assert body["configured"] is True and body["ready"] is False and "sk_test_" in body["problem"]
    install(key=LIVE_KEY, allow_live=False)
    code, body, raw = call(client(USER_A), "get", "/api/growth/outreach/status")
    assert body["mode"] == "live" and body["ready"] is False and "live sending is switched off" in body["problem"]
    assert LIVE_KEY not in raw and FAKE.calls == []


def test_status_ready_shows_a_hint_never_the_key():
    install()
    code, body, raw = call(client(USER_A), "get", "/api/growth/outreach/status")
    assert body["ready"] is True and body["mode"] == "test" and body["key_hint"] == "sk_test_…"
    assert body["wallet"]["credits"] == 40 and body["max_per_push"] == 25
    assert TEST_KEY not in raw
    assert FAKE.calls[0]["headers"]["Authorization"] == "Bearer " + TEST_KEY


def test_status_reports_a_key_outreach_rejects():
    install()
    FAKE.handler = lambda m, u, h, d: (401, {"error": {"code": "invalid_key", "message": "That key is not valid."}})
    code, body, _ = call(client(USER_A), "get", "/api/growth/outreach/status")
    assert code == 200 and body["ready"] is False and body["problem"] == "That key is not valid."


def test_status_needs_login():
    install()
    code, _, _ = call(client(None), "get", "/api/growth/outreach/status")
    assert code in (401, 302, 403)


def test_send_makes_and_approves_one_campaign_per_buyer_and_records_them_sent():
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    code, body, raw = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 200, body
    assert [x["buyer"] for x in body["sent"]] == ["Alpha Borough Council", "Beta District Council"]
    assert body["mode"] == "test" and body["failed"] == [] and body["deferred"] == 0 and body["stopped"] is None
    assert [s["buyer"] for s in body["skipped"]] == ["Gamma NHS Trust"] and "No contact email" in body["skipped"][0]["reason"]
    assert len(FAKE.posts()) == 2 and len(FAKE.approvals()) == 2
    # each campaign is approved right after it is made, and only the one that was made
    order = [("make" if c_["url"].endswith("/v1/email/campaigns") else "approve") for c_ in FAKE.calls if c_["method"] == "POST"]
    assert order == ["make", "approve", "make", "approve"]
    first = FAKE.posts()[0]
    assert first["headers"]["Idempotency-Key"].startswith("tf-growth-") and first["url"].endswith("/v1/email/campaigns")
    step = first["body"]["steps"][0]
    assert "Alpha Borough Council" in step["body"] and "Gas servicing" in step["body"] and "{{" not in step["body"]
    assert first["body"]["leads"] == [{"email": "procurement@alpha.example", "company": "Alpha Borough Council"}]
    assert FAKE.approvals()[0]["body"] == {"decision": "approve"} and FAKE.approvals()[0]["url"].endswith("/cp_fake0000/decision")
    assert FAKE.approvals()[0]["headers"]["Idempotency-Key"] != first["headers"]["Idempotency-Key"]
    assert TEST_KEY not in raw
    # the buyers who were sent are recorded as sent, the one left out is not
    assert target_statuses(c, cid) == {"Alpha Borough Council": "sent", "Beta District Council": "sent", "Gamma NHS Trust": "not_sent"}
    assert len(body["drafts"]) == 2 and all(d["approval"] == "approved" and "body_hash" not in d for d in body["drafts"])


def test_a_buyer_is_never_sent_twice():
    install()
    c = client(USER_A)
    cid, _ = make_campaign(USER_A)
    assert call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})[0] == 200
    FAKE.calls.clear()
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    # the buyer is recorded as sent here, which is the first thing that stops a second email
    assert code == 400 and "Already recorded as contacted" in body["error"] and FAKE.calls == []


def test_the_outreach_record_alone_also_stops_a_second_send():
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A, targets=[("Alpha Borough Council", "procurement@alpha.example", "not_sent", True)])
    assert call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})[0] == 200
    # the user sets the buyer back to "not sent" by hand: Outreach has already been told to send, so nothing goes again
    assert call(c, "patch", f"/api/growth/campaigns/{cid}/targets/{ids[0]}", {"status": "not_sent"})[0] == 200
    FAKE.calls.clear()
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 400 and "Already sent through Outreach" in body["error"] and FAKE.calls == []


def test_a_message_that_cannot_go_is_refused_before_any_call():
    install()
    c = client(USER_A)
    cid, _ = make_campaign(USER_A, body="Hello {{buyer_contact}}, we can help with {{category}}.")
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 400 and "opt out" in body["error"] and FAKE.calls == []
    cid2, _ = make_campaign(USER_A, subject="", body="")
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid2}/outreach", {})
    assert code == 400 and "subject and the message" in body["error"] and FAKE.calls == []
    assert set(target_statuses(c, cid).values()) == {"not_sent"}


def test_a_campaign_made_but_not_approved_is_approved_on_retry_without_making_another():
    install()
    c = client(USER_A)
    cid, _ = make_campaign(USER_A, targets=[("Alpha Borough Council", "procurement@alpha.example", "not_sent", True),
                                            ("Beta District Council", "contracts@beta.example", "not_sent", True)])
    state = {"approve": 0}

    def handler(method, url, headers, data):
        if method == "POST" and url.endswith("/decision"):
            state["approve"] += 1
            if state["approve"] == 1:
                return 403, {"error": {"code": "missing_scope", "message": "This key is not allowed to do that."}}
        return FAKE.default(method, url, headers, data)

    FAKE.handler = handler
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 200 and body["sent"] == [] and body["failed"][0]["step"] == "approve" and body["stopped"] == "This key is not allowed to do that."
    assert len(FAKE.posts()) == 1
    assert body["deferred"] == 1, "a fatal answer stops the batch and counts the buyer still to send"
    assert target_statuses(c, cid) == {"Alpha Borough Council": "not_sent", "Beta District Council": "not_sent"}, "not approved, so not recorded as sent"
    d = body["drafts"][0]
    assert d["outreach_id"] == "cp_fake0000" and d["approval"] is None and d["error"] == "This key is not allowed to do that."
    # retry: the same message, so the same Outreach campaign is approved and no second one is made
    FAKE.calls.clear()
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 200 and [x["buyer"] for x in body["sent"]] == ["Alpha Borough Council", "Beta District Council"]
    assert len(FAKE.posts()) == 1, "only the buyer with no campaign yet gets a new one"
    assert len(FAKE.approvals()) == 2 and FAKE.approvals()[0]["url"].endswith("/cp_fake0000/decision")
    assert target_statuses(c, cid) == {"Alpha Borough Council": "sent", "Beta District Council": "sent"}


def test_an_edited_message_is_not_sent_under_the_old_campaign():
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A, targets=[("Alpha Borough Council", "procurement@alpha.example", "not_sent", True),
                                              ("Beta District Council", "contracts@beta.example", "not_sent", True)])
    FAKE.handler = lambda m, u, h, d: (403, {"error": {"code": "missing_scope", "message": "no"}}) if u.endswith("/decision") else FAKE.default(m, u, h, d)
    assert call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})[1]["failed"][0]["step"] == "approve"
    # the user edits the message, then sends again: a new campaign is made from the new text and that one is approved
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE growth_campaigns SET body = body || %s WHERE id = %s", (" Thank you for reading.", cid))
        conn.commit()
    finally:
        conn.close()
    FAKE.handler = FAKE.default
    FAKE.calls.clear()
    FAKE.ids = itertools.count(100)
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 200 and [x["outreach_id"] for x in body["sent"]] == ["cp_fake0100", "cp_fake0101"], body
    assert len(FAKE.posts()) == 2 and all("Thank you for reading." in p["body"]["steps"][0]["body"] for p in FAKE.posts())
    assert len(FAKE.approvals()) == 2 and FAKE.approvals()[0]["url"].endswith("/cp_fake0100/decision"), "the old campaign is left unapproved"


def test_not_connected_is_a_409_with_no_call():
    install(key="")
    cid, _ = make_campaign(USER_A)
    code, body, _ = call(client(USER_A), "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 409 and "OUTREACH_API_KEY" in body["error"] and FAKE.calls == []


def test_a_live_key_is_refused_unless_switched_on():
    install(key=LIVE_KEY, allow_live=False)
    cid, _ = make_campaign(USER_A)
    code, body, _ = call(client(USER_A), "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 409 and FAKE.calls == []
    install(key=LIVE_KEY, allow_live=True)
    code, body, _ = call(client(USER_A), "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 200 and body["mode"] == "live"


def test_one_bad_buyer_does_not_stop_the_others_and_can_be_retried():
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    state = {"made": 0}

    def handler(method, url, headers, data):
        if method == "POST" and url.endswith("/v1/email/campaigns"):
            state["made"] += 1
            if state["made"] == 1:
                return 422, {"error": {"code": "invalid_request", "message": "leads[0].email was refused"}}
        return FAKE.default(method, url, headers, data)

    FAKE.handler = handler
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 200 and [f["buyer"] for f in body["failed"]] == ["Alpha Borough Council"]
    assert body["failed"][0]["reason"] == "leads[0].email was refused" and body["failed"][0]["step"] == "create"
    assert [x["buyer"] for x in body["sent"]] == ["Beta District Council"] and body["stopped"] is None
    assert target_statuses(c, cid)["Alpha Borough Council"] == "not_sent"
    # the failed one is tried again, the one that went is not
    FAKE.handler = FAKE.default
    FAKE.calls.clear()
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 200 and [x["buyer"] for x in body["sent"]] == ["Alpha Borough Council"]
    # one email is left, so it goes straight out with /send instead of making another campaign
    assert body["route"] == "single" and len(FAKE.sends()) == 1 and FAKE.posts() == [] and FAKE.approvals() == []


def test_a_fatal_error_stops_and_counts_what_is_left():
    install()
    c = client(USER_A)
    targets = [(f"Buyer {i}", f"team{i}@buyer{i}.example", "not_sent", True) for i in range(4)]
    cid, _ = make_campaign(USER_A, targets=targets)
    state = {"made": 0}

    def handler(method, url, headers, data):
        if method == "POST" and url.endswith("/v1/email/campaigns"):
            state["made"] += 1
            if state["made"] == 2:
                return 402, {"error": {"code": "insufficient_credits", "message": "You need 1 more credit."}}
        return FAKE.default(method, url, headers, data)

    FAKE.handler = handler
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 200
    assert len(body["sent"]) == 1 and len(body["failed"]) == 1 and body["stopped"] == "You need 1 more credit."
    assert body["deferred"] == 2 and len(FAKE.posts()) == 2 and len(FAKE.approvals()) == 1
    assert sorted(target_statuses(c, cid).values()) == ["not_sent", "not_sent", "not_sent", "sent"]


def test_only_the_chosen_buyers_and_input_is_checked():
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    assert call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": "all"})[0] == 400
    assert call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [True]})[0] == 400
    assert FAKE.calls == []
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[1]]})
    assert code == 200 and [x["buyer"] for x in body["sent"]] == ["Beta District Council"]
    assert target_statuses(c, cid)["Alpha Borough Council"] == "not_sent"


def test_a_named_person_address_is_left_out_unless_allowed():
    install()
    c = client(USER_A)
    cid, _ = make_campaign(USER_A, targets=[("Alpha Borough Council", "jo.bloggs@alpha.example", "not_sent", True)])
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 400 and "named person" in body["error"] and FAKE.calls == []
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"allow_personal": True})
    assert code == 200 and len(body["sent"]) == 1


def test_state_reads_back_with_and_without_asking_outreach():
    install()
    c = client(USER_A)
    cid, _ = make_campaign(USER_A)
    call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    FAKE.calls.clear()
    code, body, _ = call(c, "get", f"/api/growth/campaigns/{cid}/outreach")
    assert code == 200 and body["refreshed"] is False and FAKE.calls == []
    assert {d["status"] for d in body["drafts"]} == {"sending"} and {d["approval"] for d in body["drafts"]} == {"approved"}
    FAKE.handler = lambda m, u, h, d: (200, {"id": u.rsplit("/", 1)[1], "status": "completed", "approval": "approved", "sent": 3, "replies": 1}) if m == "GET" else FAKE.default(m, u, h, d)
    code, body, _ = call(c, "get", f"/api/growth/campaigns/{cid}/outreach?refresh=1")
    assert code == 200 and body["refreshed"] is True and len(FAKE.calls) == 2
    assert {d["status"] for d in body["drafts"]} == {"completed"}
    assert [d["sent"] for d in body["drafts"]] == [3, 3] and [d["replies"] for d in body["drafts"]] == [1, 1]
    # and the new state was kept
    code, body, _ = call(c, "get", f"/api/growth/campaigns/{cid}/outreach")
    assert {d["status"] for d in body["drafts"]} == {"completed"}


def test_a_refresh_with_no_approval_in_the_answer_never_clears_approved():
    install()
    c = client(USER_A)
    cid, _ = make_campaign(USER_A)
    call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    FAKE.handler = lambda m, u, h, d: (200, {"id": u.rsplit("/", 1)[1], "status": "sending"}) if m == "GET" else FAKE.default(m, u, h, d)
    code, body, _ = call(c, "get", f"/api/growth/campaigns/{cid}/outreach?refresh=1")
    assert {d["approval"] for d in body["drafts"]} == {"approved"}
    # so a second Send still does nothing: nobody is emailed twice
    FAKE.calls.clear()
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 400 and "Already recorded as contacted" in body["error"] and FAKE.calls == []


def test_refresh_keeps_the_old_state_when_outreach_cannot_be_read():
    install()
    c = client(USER_A)
    cid, _ = make_campaign(USER_A)
    call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    FAKE.handler = lambda m, u, h, d: (404, {"error": {"code": "not_found", "message": "gone"}})
    code, body, _ = call(c, "get", f"/api/growth/campaigns/{cid}/outreach?refresh=1")
    assert code == 200 and {d["status"] for d in body["drafts"]} == {"sending"} and {d["approval"] for d in body["drafts"]} == {"approved"}
    assert {d["error"] for d in body["drafts"]} == {"gone"}


def test_one_buyer_is_sent_straight_away_without_a_campaign():
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    code, body, raw = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    assert code == 200, body
    assert body["route"] == "single" and [x["buyer"] for x in body["sent"]] == ["Alpha Borough Council"]
    assert body["sent"][0]["immediate"] is True and body["sent"][0]["status"] == "sent" and body["failed"] == []
    # one request, to /send: no campaign was made and nothing was approved
    assert len(FAKE.sends()) == 1 and FAKE.posts() == [] and FAKE.approvals() == []
    req = FAKE.sends()[0]
    assert req["body"]["to"] == "procurement@alpha.example" and req["body"]["company"] == "Alpha Borough Council"
    assert req["body"]["subject"] and "Gas servicing" in req["body"]["body"] and "{{" not in req["body"]["body"] + req["body"]["subject"]
    assert req["headers"]["Idempotency-Key"].startswith("tf-growth-") and TEST_KEY not in raw
    assert target_statuses(c, cid)["Alpha Borough Council"] == "sent"
    d = body["drafts"][0]
    assert d["outreach_id"] == "msg_fake0000" and d["status"] == "sent" and d["approval"] == "sent_now" and "body_hash" not in d


def test_one_email_left_after_skips_is_still_sent_straight_away():
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A, targets=[("Alpha Borough Council", "procurement@alpha.example", "not_sent", True),
                                              ("Beta District Council", "contracts@beta.example", "not_sent", False),
                                              ("Gamma NHS Trust", "", "not_sent", True)])
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 200 and body["route"] == "single" and len(body["sent"]) == 1 and len(body["skipped"]) == 2
    assert len(FAKE.sends()) == 1 and FAKE.posts() == []


def test_several_buyers_still_go_as_campaigns_and_never_through_send():
    install()
    c = client(USER_A)
    cid, _ = make_campaign(USER_A)
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 200 and body["route"] == "campaign" and len(body["sent"]) == 2
    assert FAKE.sends() == [] and len(FAKE.posts()) == 2 and len(FAKE.approvals()) == 2
    assert all(not x.get("immediate") for x in body["sent"])


def test_a_one_off_send_is_never_repeated():
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    assert call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})[0] == 200
    FAKE.calls.clear()
    assert call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})[0] == 400
    # even after the user sets the buyer back to "not sent" by hand
    assert call(c, "patch", f"/api/growth/campaigns/{cid}/targets/{ids[0]}", {"status": "not_sent"})[0] == 200
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    assert code == 400 and "Already sent through Outreach" in body["error"] and FAKE.calls == []


def test_a_failed_one_off_send_is_not_recorded_and_can_be_retried():
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    FAKE.handler = lambda m, u, h, d: (422, {"error": {"code": "invalid_request", "message": "to was refused"}}) if u.endswith("/v1/email/send") else FAKE.default(m, u, h, d)
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    assert code == 200 and body["sent"] == [] and body["failed"][0]["reason"] == "to was refused" and body["failed"][0]["step"] == "send"
    assert target_statuses(c, cid)["Alpha Borough Council"] == "not_sent"
    assert body["drafts"][0]["status"] == "failed" and body["drafts"][0]["outreach_id"] is None
    FAKE.handler = FAKE.default
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    assert code == 200 and len(body["sent"]) == 1 and target_statuses(c, cid)["Alpha Borough Council"] == "sent"


def test_the_daily_limit_and_a_not_sent_answer_leave_the_buyer_unsent():
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    FAKE.handler = lambda m, u, h, d: (429, {"error": {"code": "daily_limit", "message": "Daily limit of 25 emails reached."}}) if u.endswith("/v1/email/send") else FAKE.default(m, u, h, d)
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    assert code == 200 and body["sent"] == [] and body["stopped"] and "Daily limit" in body["failed"][0]["reason"]
    assert "several buyers" in body["failed"][0]["reason"]
    assert target_statuses(c, cid)["Alpha Borough Council"] == "not_sent"
    FAKE.handler = lambda m, u, h, d: (200, {"id": "msg_x", "status": "queued", "to": "a@b.c"}) if u.endswith("/v1/email/send") else FAKE.default(m, u, h, d)
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    assert body["sent"] == [] and 'not "sent"' in body["failed"][0]["reason"]
    assert target_statuses(c, cid)["Alpha Borough Council"] == "not_sent"


def test_refresh_reads_a_one_off_send_as_a_message_not_a_campaign():
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    FAKE.calls.clear()
    FAKE.handler = lambda m, u, h, d: (200, {"id": u.rsplit("/", 1)[1], "status": "replied"}) if (m == "GET" and "/v1/email/messages/" in u) else FAKE.default(m, u, h, d)
    code, body, _ = call(c, "get", f"/api/growth/campaigns/{cid}/outreach?refresh=1")
    assert code == 200 and [x["url"].rsplit("/v1/", 1)[1] for x in FAKE.calls] == ["email/messages/msg_fake0000"]
    assert body["drafts"][0]["status"] == "replied" and body["drafts"][0]["approval"] == "sent_now"


def test_a_campaign_already_made_for_the_buyer_is_approved_not_sent_twice():
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    FAKE.handler = lambda m, u, h, d: (403, {"error": {"code": "missing_scope", "message": "no"}}) if u.endswith("/decision") else FAKE.default(m, u, h, d)
    call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})  # a campaign is made for the first buyer, approval refused
    FAKE.handler = FAKE.default
    FAKE.calls.clear()
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    # this buyer already has a campaign in Outreach: approving it is the only thing that sends it
    assert code == 200 and body["route"] == "campaign" and FAKE.sends() == [] and FAKE.posts() == []
    assert len(FAKE.approvals()) == 1 and FAKE.approvals()[0]["url"].endswith("/cp_fake0000/decision")


MAILBOXES = [{"id": "mb_hello", "email": "hello@aivhub.com", "can_send": True},
             {"id": "mb_harsh", "email": "harshvardhan@aivhub.com", "can_send": True},
             {"id": "mb_cold", "email": "cold@aivhub.com", "can_send": False}]


def two_mailboxes(send_from=None):
    """A fake Outreach that reports three mailboxes; /send answers from `send_from` (default: the one asked for)."""
    def handler(method, url, headers, data):
        if method == "GET" and url.endswith("/v1/wallets"):
            return 200, {"credits": 40, "connected": {"mailboxes": MAILBOXES}, "livemode": False}
        if method == "POST" and url.endswith("/v1/email/send"):
            req = json.loads(data)
            by_id = {m["id"]: m["email"] for m in MAILBOXES}
            frm = send_from or by_id.get(req.get("mailbox_id"), "hello@aivhub.com")
            return 200, {"id": "msg_x1", "status": "sent", "to": req["to"], "from": frm}
        return FAKE.default(method, url, headers, data)
    FAKE.handler = handler


def test_the_status_lists_the_connected_mailboxes_for_the_dropdown():
    install()
    two_mailboxes()
    code, body, _ = call(client(USER_A), "get", "/api/growth/outreach/status")
    assert code == 200 and body["ready"] is True
    assert [m["email"] for m in body["wallet"]["mailboxes"]] == ["hello@aivhub.com", "harshvardhan@aivhub.com", "cold@aivhub.com"]
    assert set(body["wallet"]["mailboxes"][0]) == {"id", "email", "can_send", "status", "fields"}


def test_a_chosen_mailbox_is_sent_with_one_email_and_checked_against_the_answer():
    install()
    two_mailboxes()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]], "mailbox": "mb_harsh"})
    assert code == 200 and body["route"] == "single" and len(body["sent"]) == 1, body
    assert FAKE.sends()[0]["body"]["mailbox_id"] == "mb_harsh"
    assert body["mailbox"] == "harshvardhan@aivhub.com" and body["warnings"] == []
    assert body["sent"][0]["from"] == "harshvardhan@aivhub.com"


def test_the_address_works_as_the_choice_too_and_is_matched_in_any_case():
    install()
    two_mailboxes()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]], "mailbox": "HELLO@aivhub.com"})
    assert code == 200 and FAKE.sends()[0]["body"]["mailbox_id"] == "mb_hello"


def test_several_buyers_get_the_chosen_mailbox_on_every_campaign():
    install()
    two_mailboxes()
    c = client(USER_A)
    cid, _ = make_campaign(USER_A)
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"mailbox": "mb_harsh"})
    assert code == 200 and body["route"] == "campaign" and len(FAKE.posts()) == 2
    assert all(p["body"]["mailbox_ids"] == ["mb_harsh"] for p in FAKE.posts())
    assert FAKE.sends() == []


def test_without_a_choice_no_mailbox_field_is_sent():
    install()
    two_mailboxes()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    assert code == 200 and "mailbox_id" not in FAKE.sends()[0]["body"] and body["mailbox"] is None
    assert not any(c_["url"].endswith("/v1/wallets") for c_ in FAKE.calls), "no extra call when nothing was chosen"


def test_a_mailbox_that_is_not_connected_or_cannot_send_is_refused_before_anything_is_sent():
    install()
    two_mailboxes()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"mailbox": "someone@else.example"})
    assert code == 400 and "not connected in Outreach" in body["error"]
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"mailbox": "mb_cold"})
    assert code == 400 and "cannot send yet" in body["error"]
    assert call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"mailbox": 5})[0] == 400
    assert FAKE.sends() == [] and FAKE.posts() == []
    assert target_statuses(c, cid)["Alpha Borough Council"] == "not_sent"


def test_a_mailbox_with_no_id_is_refused_before_anything_is_sent_unless_the_fields_take_an_address():
    from tender_app.blueprints import growth_bp
    install()

    def no_ids(method, url, headers, data):
        if url.endswith("/v1/wallets"):
            return 200, {"credits": 40, "connected": {"mailboxes": [
                {"email": "hello@aivhub.com", "can_send": True}, {"email": "harshvardhan@aivhub.com", "can_send": True}]}}
        return FAKE.default(method, url, headers, data)

    FAKE.handler = no_ids
    c = client(USER_A)
    status = call(c, "get", "/api/growth/outreach/status")[1]
    assert status["wallet"]["select_by_address"] is False
    cid, ids = make_campaign(USER_A)
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]], "mailbox": "harshvardhan@aivhub.com"})
    assert code == 400 and "no mailbox id" in body["error"] and "Let Outreach choose" in body["error"], body
    assert FAKE.sends() == [] and FAKE.posts() == []
    # "Let Outreach choose" still sends, without any mailbox field
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    assert code == 200 and "mailbox_id" not in FAKE.sends()[0]["body"]
    # once the sending fields take an address, the address is what is sent
    old = growth_bp.OUTREACH_SEND_MAILBOX_FIELD, growth_bp.OUTREACH_CAMPAIGN_MAILBOX_FIELD
    growth_bp.OUTREACH_SEND_MAILBOX_FIELD = growth_bp.OUTREACH_CAMPAIGN_MAILBOX_FIELD = "from"
    try:
        assert call(c, "get", "/api/growth/outreach/status")[1]["wallet"]["select_by_address"] is True
        cid2, ids2 = make_campaign(USER_A)
        code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid2}/outreach", {"target_ids": [ids2[0]], "mailbox": "harshvardhan@aivhub.com"})
        assert code == 200 and FAKE.sends()[-1]["body"]["from"] == "harshvardhan@aivhub.com", body
    finally:
        growth_bp.OUTREACH_SEND_MAILBOX_FIELD, growth_bp.OUTREACH_CAMPAIGN_MAILBOX_FIELD = old


def test_a_send_that_left_from_another_mailbox_is_flagged():
    install()
    two_mailboxes(send_from="hello@aivhub.com")  # Outreach ignored the choice
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]], "mailbox": "mb_harsh"})
    assert code == 200 and len(body["sent"]) == 1
    assert len(body["warnings"]) == 1 and "harshvardhan@aivhub.com" in body["warnings"][0] and "hello@aivhub.com" in body["warnings"][0]


def test_a_retry_under_another_mailbox_is_a_new_request_not_the_first_answer():
    install()
    two_mailboxes()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    two_mailboxes()
    ok = FAKE.handler
    state = {"n": 0}

    def flaky(method, url, headers, data):
        if method == "POST" and url.endswith("/v1/email/send"):
            state["n"] += 1
            if state["n"] == 1:
                return 422, {"error": {"code": "invalid_request", "message": "no"}}
        return ok(method, url, headers, data)
    FAKE.handler = flaky
    call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]], "mailbox": "mb_hello"})
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]], "mailbox": "mb_harsh"})
    assert code == 200 and len(body["sent"]) == 1
    keys = [s["headers"]["Idempotency-Key"] for s in FAKE.sends()]
    assert len(keys) == 2 and keys[0] != keys[1], keys


def test_a_retry_after_a_failed_send_gets_a_new_idempotency_key():
    # same text, same mailbox: an Idempotency-Key replays the first answer, errors included, so a retry after a
    # definite failure must not reuse it (this is the "still says no mailbox can send" case)
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    state = {"n": 0}

    def handler(method, url, headers, data):
        if method == "POST" and url.endswith("/v1/email/send"):
            state["n"] += 1
            if state["n"] == 1:
                return 422, {"error": {"code": "invalid_request", "message": "No mailbox can send yet."}}
            if headers.get("Idempotency-Key") == state.get("first"):
                return 422, {"error": {"code": "invalid_request", "message": "No mailbox can send yet."}}  # replayed
        return FAKE.default(method, url, headers, data)
    FAKE.handler = handler
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    assert body["sent"] == [] and body["failed"][0]["reason"] == "No mailbox can send yet."
    state["first"] = FAKE.sends()[0]["headers"]["Idempotency-Key"]
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    keys = [s["headers"]["Idempotency-Key"] for s in FAKE.sends()]
    assert len(keys) == 2 and keys[0] != keys[1], keys
    assert code == 200 and len(body["sent"]) == 1, body


def test_a_retry_after_an_unreachable_answer_keeps_its_key_so_nobody_is_emailed_twice():
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    state = {"n": 0}

    def handler(method, url, headers, data):
        if method == "POST" and url.endswith("/v1/email/send"):
            state["n"] += 1
            if state["n"] == 1:
                raise OSError("connection reset")  # the request may or may not have reached Outreach
        return FAKE.default(method, url, headers, data)
    FAKE.handler = handler
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    assert body["sent"] == [] and body["drafts"][0]["status"] == "pending"
    call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    keys = [s["headers"]["Idempotency-Key"] for s in FAKE.sends()]
    assert len(keys) == 2 and keys[0] == keys[1], keys


def test_an_approve_that_was_refused_is_retried_under_a_new_key():
    install()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A, targets=[("Alpha Borough Council", "procurement@alpha.example", "not_sent", True),
                                              ("Beta District Council", "contracts@beta.example", "not_sent", True)])
    state = {"n": 0}

    def handler(method, url, headers, data):
        if method == "POST" and url.endswith("/decision"):
            state["n"] += 1
            if state["n"] == 1:
                return 403, {"error": {"code": "missing_scope", "message": "no"}}
        return FAKE.default(method, url, headers, data)
    FAKE.handler = handler
    call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    keys = [a["headers"]["Idempotency-Key"] for a in FAKE.approvals()]
    assert keys[0] != keys[1], keys


def test_a_refusal_after_a_choice_says_what_was_sent_for_the_mailbox():
    install()
    two_mailboxes()
    refuse = lambda m, u, h, d: (422, {"error": {"code": "no_mailbox", "message": "No mailbox can send yet. Connect one under Mailboxes."}})         if u.endswith("/v1/email/send") else None
    base = FAKE.handler
    FAKE.handler = lambda m, u, h, d: refuse(m, u, h, d) or base(m, u, h, d)
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]], "mailbox": "mb_harsh"})
    reason = body["failed"][0]["reason"]
    assert "No mailbox can send yet" in reason and "mailbox_id = mb_harsh" in reason and "Let Outreach choose" in reason, reason
    assert "mailbox_id = mb_harsh" in body["drafts"][0]["error"], "the note is kept with the buyer so the panel shows it"
    # nothing chosen: Outreach's own text only
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"target_ids": [ids[0]]})
    assert body["failed"][0]["reason"] == "No mailbox can send yet. Connect one under Mailboxes."


def test_another_user_gets_404_and_no_call():
    install()
    cid, _ = make_campaign(USER_A)
    b = client(USER_B)
    FAKE.calls.clear()
    assert call(b, "post", f"/api/growth/campaigns/{cid}/outreach", {})[0] == 404
    assert call(b, "get", f"/api/growth/campaigns/{cid}/outreach")[0] == 404
    assert call(b, "get", f"/api/growth/campaigns/{cid}/outreach?refresh=1")[0] == 404
    assert FAKE.posts() == []


def test_a_post_without_the_csrf_token_is_refused():
    install()
    cid, _ = make_campaign(USER_A)
    code, _, _ = call(client(USER_A), "post", f"/api/growth/campaigns/{cid}/outreach", {}, headers={})
    assert code == 403 and FAKE.calls == []


def test_a_draft_goes_when_its_campaign_is_deleted():
    install()
    c = client(USER_A)
    cid, _ = make_campaign(USER_A)
    call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert call(c, "delete", f"/api/growth/campaigns/{cid}")[0] == 200
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM growth_outreach_drafts WHERE campaign_id = %s", (cid,))
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        conn = db()
        conn.close()
    except Exception as exc:  # no database: nothing to test against
        print(f"SKIP  no database ({type(exc).__name__})")
        sys.exit(0)
    failures = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    try:
        for name, fn in tests:
            cleanup()
            try:
                fn()
                print(f"ok    {name}")
            except Exception:
                failures += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
    finally:
        cleanup()
    print(f"\n{len(tests) - failures} of {len(tests)} passed")
    sys.exit(1 if failures else 0)
