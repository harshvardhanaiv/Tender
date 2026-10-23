"""Run: python tests/test_outreach_client.py   (plain asserts, no network, no database)

The Outreach API client (tender_app/outreach_client.py): key handling, the request it builds, how each kind of
failure is reported, and that the secret key never appears in anything the module says.
"""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_app import outreach_client as oc  # noqa: E402

KEY = "sk_test_AbCdEf1234567890"
LIVE = "sk_live_ZyXwVu0987654321"


class Fake:
    """A transport that records the request and answers with what the test sets."""

    def __init__(self, status=200, body=None, headers=None, raises=None):
        self.status, self.body, self.headers, self.raises = status, body, headers or {}, raises
        self.calls = []

    def __call__(self, method, url, headers, data, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "data": data, "timeout": timeout})
        if self.raises:
            raise self.raises
        text = self.body if isinstance(self.body, str) else json.dumps(self.body if self.body is not None else {})
        return self.status, self.headers, text


def client(fake, key=KEY):
    return oc.OutreachClient(key, "https://api.example.test/", transport=fake, timeout=7)


def raises(fn, status=None, code=None):
    try:
        fn()
    except oc.OutreachError as exc:
        if status is not None:
            assert exc.status == status, (exc.status, exc.message)
        if code is not None:
            assert exc.code == code, (exc.code, exc.message)
        return exc
    raise AssertionError("expected OutreachError")


def test_social_channel_calls_go_through_run_with_the_users_key():
    fake = Fake(body={"providers": []})
    c = client(fake)
    c.channel_providers()
    c.channel_list()
    c.channel_list(refresh=True)
    c.channel_connect("linkedin", "https://app.example/outreach/connected")
    c.channel_disconnect("ch_5b3e92a1")
    bodies = [json.loads(x["data"]) for x in fake.calls]
    assert [(x["method"], x["url"]) for x in fake.calls] == [("POST", "https://api.example.test/v1/run")] * 5
    assert bodies[0] == {"plugin": "social", "action": "channel.providers"}
    assert bodies[1] == {"plugin": "social", "action": "channel.list"}
    assert bodies[2] == {"plugin": "social", "action": "channel.list", "data": {"refresh": True}}
    assert bodies[3] == {"plugin": "social", "action": "channel.connect", "data": {"provider": "linkedin", "return_url": "https://app.example/outreach/connected"}}
    # the id of something that already exists goes at the top level: Outreach answers id_required when it is inside data
    assert bodies[4] == {"plugin": "social", "action": "channel.disconnect", "id": "ch_5b3e92a1"}
    assert all(x["headers"]["Authorization"] == f"Bearer {KEY}" for x in fake.calls)


def test_a_network_or_channel_id_is_checked_before_it_is_sent():
    fake = Fake()
    c = client(fake)
    for bad in ("", "LinkedIn", "linked in", "../x", "a" * 40, None):
        raises(lambda b=bad: c.channel_connect(b, "https://app.example/x"), 400, "invalid_request")
    for bad in ("", "a b", "x/../y", "ab", None):
        raises(lambda b=bad: c.channel_disconnect(b), 400, "invalid_request")
    assert fake.calls == []


def test_the_networks_outreach_has_switched_on_are_read_in_any_of_the_likely_shapes():
    shapes = [
        {"providers": [{"provider": "linkedin", "available": True}, {"provider": "threads", "available": False}]},
        {"data": {"providers": ["linkedin", "threads"]}},
        {"providers": [{"id": "linkedin", "label": "LinkedIn page", "enabled": True}, {"id": "threads", "enabled": False}]},
        [{"provider": "linkedin"}, {"provider": "threads", "available": False}],
    ]
    for raw in shapes:
        got = oc.channel_providers_summary(raw)
        assert [g["provider"] for g in got] == ["linkedin", "threads"], raw
        assert got[0]["available"] is True and got[1]["available"] is (True if raw == shapes[1] else False), raw
    assert oc.channel_providers_summary({"providers": [{"provider": "x"}, {"provider": "X "}, {"provider": "bad name!"}, 5, None]}) == [
        {"provider": "x", "label": "X", "available": True}]
    assert oc.channel_providers_summary({}) == [] and oc.channel_providers_summary("nope") == []
    assert oc.channel_providers_summary({"providers": [{"provider": "linkedin", "label": "L" * 100}]})[0]["label"] == "L" * 40


def test_the_connected_accounts_are_read_with_a_status_that_is_never_mistaken_for_working():
    raw = {"channels": [
        {"id": "ch_1", "provider": "linkedin", "display_name": "Acme Ltd", "status": "connected", "connected_at": "2026-10-23T09:00:00Z"},
        {"id": "ch_2", "provider": "facebook", "name": "Acme Page", "status": "needs_reauth"},
        {"id": "ch_3", "provider": "x", "handle": "@acme", "status": "error"},
        {"id": "ch_4", "provider": "threads", "status": "something-new"},
        {"id": "ch_5", "provider": "instagram"},
        {"id": "bad id", "provider": "linkedin"}, {"provider": "linkedin"}, {"id": "ch_6"}, "junk",
    ]}
    got = oc.channel_list_summary(raw)
    assert [(g["id"], g["provider"], g["status"]) for g in got] == [
        ("ch_1", "linkedin", "connected"), ("ch_2", "facebook", "needs_reauth"), ("ch_3", "x", "error"), ("ch_4", "threads", "error"), ("ch_5", "instagram", "connected")]
    assert got[0]["name"] == "Acme Ltd" and got[0]["label"] == "LinkedIn" and got[2]["name"] == "@acme"
    assert oc.channel_list_summary({"data": {"channels": raw["channels"][:1]}})[0]["id"] == "ch_1"
    assert oc.channel_list_summary({}) == [] and oc.channel_list_summary(None) == []


def test_the_connect_answer_gives_a_safe_link_only():
    assert oc.connect_summary({"auth_url": "https://www.linkedin.com/oauth/v2/authorization?client_id=1&state=abc"})["auth_url"].startswith("https://www.linkedin.com/")
    long = "https://x.example/auth?state=" + "a" * 900
    assert oc.connect_summary({"auth_url": long})["auth_url"] == long, "an OAuth link is longer than an ordinary one"
    for bad in ("javascript:alert(1)", "http://evil.example/x", "https://", "https://a.example/x y", 'https://a.example/"><script>', "https://u@evil.example/", None, "", 5):
        assert oc.connect_summary({"auth_url": bad})["auth_url"] is None, bad
    # a test key answers with the return address itself, which is plain http on a developer's machine
    assert oc.connect_summary({"auth_url": "http://localhost:8092/outreach/connected?status=connected&provider=linkedin"})["auth_url"]
    assert oc.connect_summary({"data": {"auth_url": "https://a.example/x", "connected": True}}) == {"auth_url": "https://a.example/x", "connected": True}
    assert oc.connect_summary("nope") == {"auth_url": None, "connected": False}
    assert oc.provider_label("linkedin") == "LinkedIn" and oc.provider_label("my_net") == "My Net"


def test_key_mode_and_mask():
    assert oc.key_mode(KEY) == "test"
    assert oc.key_mode(" " + LIVE + " ") == "live"
    for bad in ("", None, "sk_test_", "sk_prod_abcdefgh", "Bearer sk_test_abcdefgh", "sk_test_ab cd1234"):
        assert oc.key_mode(bad) is None, bad
    assert oc.mask_key(KEY) == "sk_test_…"
    assert KEY not in oc.mask_key(KEY) and oc.mask_key("") == ""


def test_constructor_refuses_a_bad_key_without_echoing_it():
    exc = raises(lambda: oc.OutreachClient("not-a-key"), 400, "not_configured")
    assert "not-a-key" not in exc.message and exc.fatal


def test_request_shape():
    fake = Fake(body={"id": "cp_1", "status": "draft"})
    out = client(fake).create_email_campaign({"name": "A"}, "tf-key-1")
    call = fake.calls[0]
    assert out["id"] == "cp_1"
    assert call["method"] == "POST" and call["url"] == "https://api.example.test/v1/email/campaigns"
    assert call["headers"]["Authorization"] == "Bearer " + KEY
    assert call["headers"]["Idempotency-Key"] == "tf-key-1"
    assert call["headers"]["Content-Type"] == "application/json"
    assert json.loads(call["data"]) == {"name": "A"} and call["timeout"] == 7


def test_approving_a_campaign_is_a_post_to_its_decision_with_its_own_key():
    fake = Fake(body={"id": "cp_1", "status": "sending", "approval": "approved"})
    out = client(fake).decide_email_campaign("cp_1", "approve", "tf-key-approve")
    call = fake.calls[0]
    assert out["approval"] == "approved"
    assert call["method"] == "POST" and call["url"] == "https://api.example.test/v1/email/campaigns/cp_1/decision"
    assert json.loads(call["data"]) == {"decision": "approve"}
    assert call["headers"]["Idempotency-Key"] == "tf-key-approve"
    assert call["headers"]["Authorization"] == "Bearer " + KEY


def test_a_decision_is_checked_before_anything_is_sent():
    fake = Fake()
    raises(lambda: client(fake).decide_email_campaign("cp_1", "send-it", "k"), 400, "invalid_request")
    raises(lambda: client(fake).decide_email_campaign("../v1/wallets", "approve", "k"), 400, "invalid_request")
    raises(lambda: client(fake).decide_email_campaign("cp_1", "approve", ""), 400, "invalid_request")
    assert fake.calls == []


def test_a_key_without_the_approve_permission_is_reported_as_such():
    body = {"error": {"code": "missing_scope", "message": "This key is not allowed to do that. Give it the permission in the Keys tab."}}
    exc = raises(lambda: client(Fake(403, body)).decide_email_campaign("cp_1", "approve", "k"), 403, "missing_scope")
    assert "permission" in exc.message and exc.fatal


def test_reads_have_no_body_and_no_idempotency_key():
    fake = Fake(body={"credits": 12})
    client(fake).wallets()
    call = fake.calls[0]
    assert call["method"] == "GET" and call["url"].endswith("/v1/wallets") and call["data"] is None
    assert "Idempotency-Key" not in call["headers"] and "Content-Type" not in call["headers"]


def test_create_needs_an_idempotency_key():
    raises(lambda: client(Fake()).create_email_campaign({"name": "A"}, ""), 400, "invalid_request")


def test_campaign_id_is_checked_before_it_goes_in_a_path():
    fake = Fake()
    for bad in ("", "../v1/wallets", "cp_1/decision", "a b", "x" * 80):
        raises(lambda b=bad: client(fake).get_email_campaign(b), 400, "invalid_request")
    assert fake.calls == []
    client(Fake(body={"id": "cp_4a1b2c3d4e5f"})).get_email_campaign("cp_4a1b2c3d4e5f")


def test_error_body_is_read():
    body = {"error": {"code": "invalid_request", "message": "leads: email is required", "details": [{"field": "leads"}]}}
    exc = raises(lambda: client(Fake(422, body)).create_email_campaign({}, "k"), 422, "invalid_request")
    assert exc.message == "leads: email is required" and exc.details == [{"field": "leads"}] and not exc.fatal


def test_the_web_hosts_detail_format_is_read():
    exc = raises(lambda: client(Fake(401, {"detail": "Sign in required.", "code": "unauthorized", "section": ""})).wallets(), 401, "unauthorized")
    assert exc.message == "Sign in required." and exc.fatal


def test_fatal_statuses():
    for status in (401, 402, 403, 429):
        exc = raises(lambda s=status: client(Fake(s, {})).wallets(), status)
        assert exc.fatal and exc.message
    assert not raises(lambda: client(Fake(404, {})).wallets(), 404).fatal
    assert not raises(lambda: client(Fake(500, {})).wallets(), 500).fatal


def test_rate_limit_carries_retry_after():
    exc = raises(lambda: client(Fake(429, {"error": {"code": "rate_limited", "message": "slow"}}, {"Retry-After": "12"})).wallets(), 429, "rate_limited")
    assert exc.retry_after == 12
    assert raises(lambda: client(Fake(429, {}, {"Retry-After": "soon"})).wallets(), 429).retry_after is None


def test_network_failure_is_reported_without_the_request():
    exc = raises(lambda: client(Fake(raises=ConnectionError("boom " + KEY))).wallets(), 502, "unreachable")
    assert KEY not in exc.message and exc.fatal


def test_non_json_answers():
    raises(lambda: client(Fake(200, "<html>")).wallets(), 502, "bad_response")
    raises(lambda: client(Fake(200, "[1, 2]")).wallets(), 502, "bad_response")
    exc = raises(lambda: client(Fake(500, "<html>oops")).wallets(), 500)
    assert "500" in exc.message


def test_the_key_is_never_in_an_error():
    for status in (400, 401, 402, 403, 404, 422, 429, 500, 503):
        exc = raises(lambda s=status: client(Fake(s, {"error": "x"})).wallets(), status)
        assert KEY not in exc.message and KEY not in str(exc)


def test_mode_follows_the_key():
    assert client(Fake()).mode == "test" and client(Fake(), LIVE).mode == "live"


def test_campaign_summary_is_tolerant():
    assert oc.campaign_summary({"id": "cp_1", "status": "needs_approval", "approval": "pending", "sent": 0, "replies": 2}) == {
        "id": "cp_1", "status": "needs_approval", "approval": "pending", "sent": 0, "replies": 2}
    nested = oc.campaign_summary({"campaign": {"id": "cp_2", "state": "sending"}, "livemode": False})
    assert nested["id"] == "cp_2" and nested["status"] == "sending" and nested["sent"] is None
    assert oc.campaign_summary({})["id"] is None
    # objects and booleans are not shown as text or counts
    odd = oc.campaign_summary({"id": "cp_3", "status": {"a": 1}, "sent": True})
    assert odd["status"] is None and odd["sent"] is None


def test_wallet_summary():
    w = oc.wallet_summary({"credits": 42, "connected": {"mailboxes": 1}, "livemode": False})
    assert w == {"credits": 42, "connected": {"mailboxes": 1}, "mailboxes": [], "social_channels": [], "phone_numbers": None,
                 "livemode": False}
    # whatever else the answer carries is not passed on to the browser, only counts and flags
    w = oc.wallet_summary({"credits": 1, "connected": {"mailboxes": [{"email": "a@x.test", "token": "sk-secret"}],
                           "social": {"linkedin": {"access_token": "tok-secret"}}, "phone": True, "note": "tok-secret"}})
    assert w["connected"] == {"mailboxes": 1, "social": 1, "phone": True}, w["connected"]
    assert "secret" not in str(w)
    assert oc.wallet_summary({"balance": 3})["credits"] == 3
    assert oc.wallet_summary({})["credits"] is None


# ── POST /v1/email/send, messages, the shared config check ─────────────────────────────────────────

def test_default_host_is_the_main_host():
    assert oc.DEFAULT_BASE_URL == "https://outreach.aivhub.com"
    assert "api.outreach" not in oc.DEFAULT_BASE_URL


def test_config_problem_covers_every_state_without_quoting_the_key():
    assert "OUTREACH_API_KEY" in oc.config_problem("", False)
    assert "OUTREACH_API_KEY" in oc.config_problem(None, True)
    assert "sk_test_" in oc.config_problem("not-a-key", False)
    live = oc.config_problem(LIVE, False)
    assert live and "OUTREACH_ALLOW_LIVE" in live and LIVE not in live
    assert oc.config_problem(LIVE, True) is None and oc.config_problem(KEY, False) is None


def test_send_email_request_and_answer():
    f = Fake(200, {"id": "msg_1", "status": "sent", "to": "a@x.co.uk", "from": "me@y.co.uk"})
    answer = client(f).send_email({"to": "a@x.co.uk", "subject": "S", "body": "B"}, "tf-mail-abc")
    call = f.calls[0]
    assert call["method"] == "POST" and call["url"] == "https://api.example.test/v1/email/send"
    assert call["headers"]["Authorization"] == f"Bearer {KEY}" and call["headers"]["Idempotency-Key"] == "tf-mail-abc"
    assert json.loads(call["data"]) == {"to": "a@x.co.uk", "subject": "S", "body": "B"}
    assert oc.send_summary(answer) == {"id": "msg_1", "status": "sent", "to": "a@x.co.uk", "from": "me@y.co.uk"}
    raises(lambda: client(Fake()).send_email({"to": "a@x.co.uk"}, ""), 400, "invalid_request")


def test_send_failures_each_have_a_status_and_the_right_fatal_flag():
    for status, fatal in ((401, True), (402, True), (403, True), (429, True), (422, False), (404, False), (500, False)):
        exc = raises(lambda s=status: client(Fake(s, {"error": {"code": "x", "message": "m"}})).send_email({"to": "a@x.co.uk"}, "k"), status)
        assert exc.fatal is fatal, (status, exc.fatal)
    exc = raises(lambda: client(Fake(403, {"error": {"code": "missing_scope", "message": "needs email:approve"}})).send_email({}, "k"), 403, "missing_scope")
    assert "email:approve" in exc.message
    exc = raises(lambda: client(Fake(429, {"error": {"code": "rate_limited"}}, {"Retry-After": "7"})).send_email({}, "k"), 429)
    assert exc.retry_after == 7
    # the web host answers {"detail", "code"} instead
    exc = raises(lambda: client(Fake(401, {"detail": "bad key", "code": "unauthorized"})).send_email({}, "k"), 401, "unauthorized")
    assert exc.message == "bad key"


def test_the_daily_cap_is_recognised_however_it_is_worded():
    shapes = [
        (429, {"error": {"code": "daily_limit_reached", "message": "x"}}),
        (403, {"error": {"code": "forbidden", "message": "You can send 25 emails a day."}}),
        (422, {"error": {"code": "limit", "message": "Daily send limit reached"}}),
        (402, {"error": {"code": "quota_exceeded", "message": "x"}}),
        (409, {"detail": "Daily limit of 25 reached", "code": "conflict"}),
    ]
    for status, body in shapes:
        exc = raises(lambda s=status, b=body: client(Fake(s, b)).send_email({}, "k"), status)
        assert exc.daily_limit and exc.fatal, (status, body)
    # an ordinary rate limit, a missing permission and a rejected request are not the daily cap
    for status, body in ((429, {"error": {"code": "rate_limited", "message": "slow down"}}),
                         (403, {"error": {"code": "missing_scope", "message": "needs email:approve"}}),
                         (422, {"error": {"code": "invalid", "message": "subject is required"}}),
                         (422, {"error": {"code": "invalid", "message": "send_at must not be before today"}}),
                         (422, {"error": {"code": "invalid", "message": "Connect a mailbox first"}})):
        exc = raises(lambda s=status, b=body: client(Fake(s, b)).send_email({}, "k"), status)
        assert not exc.daily_limit, (status, body)


def test_message_lookups_build_safe_urls():
    f = Fake(200, {"messages": [{"id": "m1", "to": "A@X.co.uk", "status": "Replied", "reason": None}]})
    out = client(f).list_email_messages(campaign_id="cp_1", status="sent", q="a b&c")
    url = f.calls[0]["url"]
    assert f.calls[0]["method"] == "GET" and url.startswith("https://api.example.test/v1/email/messages?")
    assert "campaign_id=cp_1" in url and "status=sent" in url and "q=a+b%26c" in url
    listed = oc.message_list(out)
    assert [{k: m[k] for k in ("id", "to", "status", "reason")} for m in listed] == [{"id": "m1", "to": "a@x.co.uk", "status": "replied", "reason": None}]
    raises(lambda: client(Fake()).list_email_messages(campaign_id="../x"), 400, "invalid_request")
    g = Fake(200, {"id": "m9", "status": "bounced", "bounce_reason": "mailbox full"})
    assert oc.message_summary(client(g).get_email_message("msg_9"))["reason"] == "mailbox full"
    assert g.calls[0]["url"].endswith("/v1/email/messages/msg_9")
    raises(lambda: client(Fake()).get_email_message("a/b"), 400, "invalid_request")


def test_scrub_masks_keys_anywhere_in_text():
    text = f"rejected {KEY} and {LIVE}"
    out = oc.scrub(text)
    assert KEY not in out and LIVE not in out and "sk_test_…" in out and "sk_live_…" in out
    exc = oc.OutreachError(f"bad {KEY}", 401)
    assert KEY not in exc.message and KEY not in str(exc)
    echo = raises(lambda: client(Fake(401, {"error": {"message": f"key {KEY} unknown"}})).wallets(), 401)
    assert KEY not in echo.message


def test_wallet_summary_reads_the_connected_mailboxes_in_any_of_the_likely_shapes():
    one = oc.wallet_summary({"credits": 5, "connected": {"mailboxes": [{"id": "mb_1", "email": "hello@aivhub.com", "can_send": True,
                             "status": "warming", "smtp_password": "never-copied", "access_token": "never-copied"}]}})
    box = dict(one["mailboxes"][0])
    # only field NAMES are reported (a missing id is then diagnosable); no value other than the four is copied
    assert box.pop("fields") == ["access_token", "can_send", "email", "id", "smtp_password", "status"]
    assert box == {"id": "mb_1", "email": "hello@aivhub.com", "can_send": True, "status": "warming"}
    # an id may arrive as mailbox_id; an address in the id slot is not an id
    assert oc.wallet_summary({"mailboxes": [{"mailbox_id": "mb_9", "email": "z@x.test"}]})["mailboxes"][0]["id"] == "mb_9"
    assert oc.wallet_summary({"mailboxes": [{"id": "z@x.test", "email": "z@x.test"}]})["mailboxes"][0]["id"] is None
    assert "never-copied" not in str(one)
    assert oc.wallet_summary({"connected": {"mailboxes": ["a@x.test", "not an address"]}})["mailboxes"] == [
        {"id": None, "email": "a@x.test", "can_send": None, "status": None, "fields": []}]
    assert oc.wallet_summary({"mailboxes": [{"address": "b@x.test", "can_send": False}]})["mailboxes"][0]["can_send"] is False
    assert oc.wallet_summary({"connected": {"mailboxes": {"email": "c@x.test"}}})["mailboxes"][0]["email"] == "c@x.test"
    # a bare count, nothing, or junk gives no list and never raises
    for raw in ({"connected": {"mailboxes": 1}}, {}, {"connected": "yes"}, {"mailboxes": [1, None, {"x": 1}]}):
        assert oc.wallet_summary(raw)["mailboxes"] == []
    assert len(oc.wallet_summary({"mailboxes": [f"u{i}@x.test" for i in range(50)]})["mailboxes"]) == 20


def test_mailbox_ids_can_be_given_by_configuration_and_a_sent_message_reports_its_mailbox():
    assert oc.parse_mailbox_ids("Hello@AIVHub.com=mb_1, harsh@aivhub.com=2f9c-aa;bad=;=x;noat=zz;ok@x.test=a b") == {
        "hello@aivhub.com": "mb_1", "harsh@aivhub.com": "2f9c-aa"}
    assert oc.parse_mailbox_ids(None) == {} and oc.parse_mailbox_ids("") == {}
    boxes = [{"id": None, "email": "Hello@aivhub.com"}, {"id": "keep", "email": "harsh@aivhub.com"}]
    oc.apply_mailbox_ids(boxes, {"hello@aivhub.com": "mb_1", "harsh@aivhub.com": "other"})
    assert [b["id"] for b in boxes] == ["mb_1", "keep"], "a listed id is never overwritten"
    m = oc.message_summary({"id": "m1", "to": "x@y.z", "status": "sent", "mailbox": "harsh@aivhub.com", "mailbox_id": "mb_7",
                            "body": "hi", "token": "t"})
    assert m["mailbox"] == "harsh@aivhub.com" and m["mailbox_id"] == "mb_7" and "body" in m["fields"]
    assert oc.message_summary({"id": "m2", "mailbox": "no address here"})["mailbox"] is None


def test_only_plain_https_links_from_outreach_are_passed_on():
    good = "https://outreach.aivhub.com/p/abc?x=1"
    assert oc._https_url(good) == good
    # anything that is not a clean https link is dropped, never passed on to the page
    for bad in ("http://outreach.aivhub.com", "javascript:alert(1)", "//evil.example", "https://", "https://u:p@evil.example/",
                "https://x.example/a b", 'https://x.example/"onmouseover=1', "https://x.example/<script>", "ftp://x.example", None, ""):
        assert oc._https_url(bad) is None, bad


def test_social_post_requests_and_answers():
    f = Fake(200, {"id": "v2_5d1e0c9a7b21", "status": "writing", "channels": ["linkedin", "facebook"],
                   "preview_url": "https://outreach.aivhub.com/api/p/abc"})
    made = client(f).create_social_post({"prompt": "Announce it", "channels": ["linkedin"]}, "tf-post-1")
    c = f.calls[0]
    assert c["method"] == "POST" and c["url"] == "https://api.example.test/v1/social/posts"
    assert c["headers"]["Idempotency-Key"] == "tf-post-1" and json.loads(c["data"])["channels"] == ["linkedin"]
    s = oc.post_summary(made)
    assert s["id"] == "v2_5d1e0c9a7b21" and s["status"] == "writing" and s["channels"] == ["linkedin", "facebook"]
    assert s["preview_url"] == "https://outreach.aivhub.com/api/p/abc" and s["published_url"] is None
    raises(lambda: client(Fake()).create_social_post({}, ""), 400, "invalid_request")
    f = Fake(200, {"id": "v2_1", "status": "published", "caption": "Hello", "result": {"url": "https://www.linkedin.com/feed/update/1"}})
    got = oc.post_summary(client(f).get_social_post("v2_1"))
    assert f.calls[0]["method"] == "GET" and f.calls[0]["url"].endswith("/v1/social/posts/v2_1")
    assert got["caption"] == "Hello" and got["published_url"] == "https://www.linkedin.com/feed/update/1"
    raises(lambda: client(Fake()).get_social_post("../x"), 400, "invalid_request")


def test_a_post_decision_is_one_of_three_and_needs_a_key():
    f = Fake(200, {"id": "v2_1", "status": "approved"})
    client(f).decide_social_post("v2_1", "approve", "tf-d-1")
    c = f.calls[0]
    assert c["url"].endswith("/v1/social/posts/v2_1/decision") and json.loads(c["data"]) == {"decision": "approve"}
    assert c["headers"]["Idempotency-Key"] == "tf-d-1"
    for bad in ("publish", "", None):
        raises(lambda b=bad: client(Fake()).decide_social_post("v2_1", b, "k"), 400, "invalid_request")
    raises(lambda: client(Fake()).decide_social_post("v2_1", "cancel", ""), 400, "invalid_request")
    raises(lambda: client(Fake()).decide_social_post("a b", "cancel", "k"), 400, "invalid_request")


def test_a_post_is_changed_with_a_patch_and_an_approval_may_carry_a_new_time():
    f = Fake(200, {"id": "v2_1", "status": "writing"})
    client(f).update_social_post("v2_1", {"regenerate": "picture", "instruction": "Brighter"}, "tf-e-1")
    c = f.calls[0]
    assert c["method"] == "PATCH" and c["url"].endswith("/v1/social/posts/v2_1") and c["headers"]["Idempotency-Key"] == "tf-e-1"
    assert json.loads(c["data"]) == {"regenerate": "picture", "instruction": "Brighter"}
    raises(lambda: client(Fake()).update_social_post("v2_1", {}, "k"), 400, "invalid_request")
    raises(lambda: client(Fake()).update_social_post("v2_1", {"publish_at": "x"}, ""), 400, "invalid_request")
    raises(lambda: client(Fake()).update_social_post("a b", {"publish_at": "x"}, "k"), 400, "invalid_request")
    f = Fake(200, {"id": "v2_1", "status": "approved"})
    client(f).decide_social_post("v2_1", "approve", "k1", "2026-11-01T09:00:00Z")
    assert json.loads(f.calls[0]["data"]) == {"decision": "approve", "publish_at": "2026-11-01T09:00:00Z"}
    f = Fake(200, {"id": "v2_1", "status": "rejected"})
    client(f).decide_social_post("v2_1", "reject", "k2", "2026-11-01T09:00:00Z")
    assert json.loads(f.calls[0]["data"]) == {"decision": "reject"}, "a time only goes with an approval"


def test_the_post_picture_and_per_channel_text_are_read_in_any_shape_and_only_over_https():
    for shape in ("https://img.example/a.png", {"url": "https://img.example/a.png"}, ["https://img.example/a.png"], [{"src": "https://img.example/a.png"}]):
        assert oc.post_summary({"id": "v2_1", "image": shape})["image_url"] == "https://img.example/a.png", shape
    assert oc.post_summary({"id": "v2_1", "picture_url": "https://img.example/b.png"})["image_url"] == "https://img.example/b.png"
    for bad in ("http://img.example/a.png", "javascript:alert(1)", {"url": "data:image/png;base64,AAAA"}, None, 5):
        assert oc.post_summary({"id": "v2_1", "image": bad})["image_url"] is None, bad
    s = oc.post_summary({"id": "v2_1", "channels": [{"platform": "LinkedIn", "text": "<p>Long form</p>"}, {"platform": "x", "caption": "Short"}, "facebook"]})
    assert s["channels"] == ["linkedin", "x", "facebook"]
    assert s["variants"] == [{"channel": "linkedin", "text": "Long form"}, {"channel": "x", "text": "Short"}]
    s = oc.post_summary({"id": "v2_1", "previews": {"linkedin": "Hello <b>all</b>", "facebook": {"text": "Hi"}, "bad;": "no"}})
    assert s["variants"] == [{"channel": "linkedin", "text": "Hello all"}, {"channel": "facebook", "text": "Hi"}]
    assert oc.post_summary({"id": "v2_1"})["variants"] == []


def test_post_summary_drops_links_that_are_not_plain_https():
    s = oc.post_summary({"id": "v2_1", "preview_url": "javascript:alert(1)", "result": {"url": "http://insecure.example/x"},
                         "channels": [{"platform": "LinkedIn"}, "facebook", {"x": 1}, "bad;chan"]})
    assert s["preview_url"] is None and s["published_url"] is None and s["channels"] == ["linkedin", "facebook"]


def test_voice_call_requests_and_answers():
    f = Fake(200, {"id": "call_8f2a91c4", "status": "ringing", "to": "+442079460000",
                   "live_url": "https://outreach.aivhub.com/api/p/x", "capabilities": {"end": True, "guide": False}})
    made = client(f).make_call({"to": "+442079460000", "goal": "Ask"}, "tf-call-1")
    c = f.calls[0]
    assert c["method"] == "POST" and c["url"] == "https://api.example.test/v1/voice/calls"
    assert c["headers"]["Idempotency-Key"] == "tf-call-1" and json.loads(c["data"])["to"] == "+442079460000"
    s = oc.call_summary(made)
    assert s["id"] == "call_8f2a91c4" and s["status"] == "ringing" and s["can_end"] is True and s["live_url"].startswith("https://")
    raises(lambda: client(Fake()).make_call({"to": "+44"}, ""), 400, "invalid_request")
    done = {"id": "call_1", "status": "Completed", "outcome": "meeting_booked", "duration_seconds": 192, "meeting_booked": True,
            "answers": {"current_system": "Acuity", "nested": {"a": 1}}, "recording_url": "https://api.example/x",
            "transcript": [{"who": "ai", "text": "Hello Jo"}, {"who": "them", "text": 5}, "junk"]}
    f = Fake(200, done)
    got = oc.call_summary(client(f).get_call("call_1"))
    assert f.calls[0]["method"] == "GET" and f.calls[0]["url"].endswith("/v1/voice/calls/call_1")
    assert got["status"] == "completed" and got["outcome"] == "meeting_booked" and got["duration_seconds"] == 192
    assert got["meeting_booked"] is True and got["has_recording"] is True and got["can_end"] is False
    assert got["answers"] == [{"key": "current_system", "answer": "Acuity"}]
    assert got["transcript"] == [{"who": "ai", "text": "Hello Jo"}]
    f = Fake(200, {"id": "call_1", "status": "completed"})
    client(f).end_call("call_1")
    assert f.calls[0]["method"] == "DELETE" and f.calls[0]["url"].endswith("/v1/voice/calls/call_1")
    raises(lambda: client(Fake()).get_call("a/b"), 400, "invalid_request")
    raises(lambda: client(Fake()).end_call(""), 400, "invalid_request")


def test_saved_scripts_are_read_as_id_and_name():
    f = Fake(200, {"scripts": [{"id": "script_1", "name": "Intro"}, {"id": "bad id"}, {"script_id": "script_2"}, "junk"]})
    out = oc.script_list(client(f).voice_scripts())
    assert f.calls[0]["url"].endswith("/v1/voice/scripts")
    assert out == [{"id": "script_1", "name": "Intro"}, {"id": "script_2", "name": "script_2"}]
    assert oc.script_list({}) == []


def test_the_wallet_reports_which_social_channels_and_phone_numbers_are_connected():
    w = oc.wallet_summary({"credits": 9, "connected": {"mailboxes": [], "social_channels": [{"platform": "linkedin"}, "Facebook"],
                                                      "phone_numbers": [{"number": "+442000000000", "token": "secret"}]}})
    assert w["social_channels"] == ["linkedin", "facebook"] and w["phone_numbers"] == 1
    assert "secret" not in str(w) and w["connected"] == {"mailboxes": 0, "social_channels": 2, "phone_numbers": 1}
    w = oc.wallet_summary({"connected": {"social_channels": [], "phone_numbers": 0}})
    assert w["social_channels"] == [] and w["phone_numbers"] == 0
    w = oc.wallet_summary({"credits": 3})
    assert w["social_channels"] == [] and w["phone_numbers"] is None


def test_the_inbox_list_is_read_with_its_cursor_and_filters():
    f = Fake(200, {"data": [{"id": "m1", "to": "Jo@Bloggs.example", "subject": "Your tender", "status": "Replied", "sent_at": "2026-11-03T10:30:00Z",
                             "mailbox": "sales@x.example", "kind": "one_to_one", "secret": "no"}, "junk"], "next_cursor": "abc_123=="})
    page = oc.message_page(client(f).list_email_messages(status="replied", q="bloggs", cursor="tok_1"))
    url = f.calls[0]["url"]
    assert "status=replied" in url and "q=bloggs" in url and "cursor=tok_1" in url
    assert page["next_cursor"] == "abc_123==" and len(page["items"]) == 1
    m = page["items"][0]
    assert m == {"id": "m1", "to": "jo@bloggs.example", "subject": "Your tender", "status": "replied", "sent_at": "2026-11-03T10:30:00Z",
                 "replied_at": None, "mailbox": "sales@x.example", "kind": "one_to_one"}
    raises(lambda: client(Fake()).list_email_messages(cursor="bad cursor!"), 400, "invalid_request")
    assert oc.message_page({"data": [], "next_cursor": "bad cursor!"})["next_cursor"] is None
    assert oc.message_page({}) == {"items": [], "next_cursor": None}


def test_a_reply_is_read_whether_it_is_text_an_object_or_a_list_and_is_always_plain_text():
    base = {"id": "m1", "to": "jo@bloggs.example", "subject": "S", "status": "replied", "body": "<p>Hi Jo</p><p>Bye</p>"}
    one = oc.reply_detail({**base, "reply": "Thanks, call me."})
    assert one["replies"] == [{"text": "Thanks, call me.", "from": None, "at": None, "subject": None}]
    assert one["original"] == "Hi Jo\n\nBye"
    two = oc.reply_detail({**base, "reply": {"from": "Jo@Bloggs.example", "received_at": "2026-11-04T09:00:00Z", "body": "<div>Yes please</div><br>Jo &amp; team<script>x</script>"}})
    r = two["replies"][0]
    assert r["from"] == "Jo@Bloggs.example" and r["at"] == "2026-11-04T09:00:00Z" and r["text"] == "Yes please\n\nJo & team"
    assert "<" not in r["text"]
    many = oc.reply_detail({**base, "replies": [{"text": "one"}, {"snippet": "two", "sender": "not an address"}, 5, {}]})
    assert [x["text"] for x in many["replies"]] == ["one", "two"] and many["replies"][1]["from"] is None
    nested = oc.reply_detail({"data": {"id": "m2", "reply": {"text": "inside"}}})
    assert nested["id"] == "m2" and nested["replies"][0]["text"] == "inside"
    none = oc.reply_detail({**base, "status": "bounced", "bounce_reason": "Mailbox full"})
    assert none["replies"] == [] and none["bounce_reason"] == "Mailbox full"


def test_a_reply_passes_on_nothing_it_was_not_asked_for():
    out = oc.reply_detail({"id": "m1", "reply": {"text": "hi", "token": "sk-secret", "headers": {"x": "y"}}, "api_key": "sk-secret"})
    assert "secret" not in str(out)
    assert oc.plain_text("   ", 10) is None and oc.plain_text(5, 10) is None and oc.plain_text("x" * 50, 10) == "x" * 10


if __name__ == "__main__":
    failures = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception:
            failures += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures} of {len(tests)} passed")
    sys.exit(1 if failures else 0)
