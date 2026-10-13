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
    assert w == {"credits": 42, "connected": {"mailboxes": 1}, "livemode": False}
    assert oc.wallet_summary({"balance": 3})["credits"] == 3
    assert oc.wallet_summary({})["credits"] is None


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
