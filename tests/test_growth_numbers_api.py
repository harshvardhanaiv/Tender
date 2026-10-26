"""Run: python tests/test_growth_numbers_api.py   (plain asserts; needs the dev database, skips itself without one)

Getting a phone number for AI calls from the Connections window (Outreach's `numbers` plugin: connect, list, release), through the
real Flask app and its test client. Outreach is a fake: nothing leaves the machine and nothing is bought.

  * the numbers are read with their status, a missing feature at Outreach's end answers "not supported",
  * Connect sends only TenderFlow's return address and hands back a safe link (the purchase and documents stay in Outreach's window),
  * a refused return address says what the administrator must do,
  * Release names the number, and nothing works without an Outreach connection,
  * a call is refused with the right words while the number is still being checked,
  * the window the popup ends on says the request was received.
"""
import sys
import traceback

import test_growth_outreach_api as base
from test_growth_outreach_api import FAKE, TEST_KEY, USER_A, call, client, db, install

NUMBERS = {"numbers": [{"id": "num_1", "number": "+441132000111", "status": "active"},
                       {"id": "num_2", "number": "+441132000222", "status": "verification_pending"},
                       {"id": "num_3", "number": "+441132000333", "status": "rejected", "reason": "Utility bill too old"},
                       {"id": "num_4", "number": "+441132000444", "status": "released"},
                       {"id": "num_5", "number": "+441132000555", "status": "something_new"}]}


def cleanup():
    base.cleanup()


def runs(action=None):
    out = [c for c in FAKE.calls if c["url"].endswith("/v1/run")]
    return [c for c in out if action is None or (c["body"] or {}).get("action") == action]


def outreach(connect=None, numbers=NUMBERS, unknown=False):
    def handler(method, url, headers, data):
        import json
        if not url.endswith("/v1/run"):
            return FAKE.default(method, url, headers, data)
        body = json.loads(data)
        if unknown:
            return 422, {"error": {"code": "unknown_action", "message": "Unknown action."}}
        if body.get("plugin") != "numbers":
            return 404, {"error": {"code": "not_found", "message": "unknown"}}
        action = body.get("action")
        if action == "list":
            return 200, numbers
        if action == "connect":
            return connect(body) if connect else (200, {"connect_url": "https://outreach.aivhub.com/numbers/connect?state=abc"})
        if action == "release":
            return 200, {"ok": True}
        return 404, {"error": {"code": "not_found", "message": "unknown"}}
    FAKE.handler = handler


def test_numbers_are_read_with_their_status():
    install()
    outreach()
    code, body, _ = call(client(USER_A), "get", "/api/growth/numbers")
    assert code == 200 and body["supported"] is True, body
    assert [(n["id"], n["status"]) for n in body["numbers"]] == [("num_1", "active"), ("num_2", "pending"), ("num_3", "rejected"), ("num_5", "pending")], \
        "released ones are left out and an unknown status is never shown as working"
    assert body["numbers"][2]["reason"] == "Utility bill too old"
    assert all(c["headers"]["Authorization"] == f"Bearer {TEST_KEY}" for c in runs())


def test_when_outreach_has_not_shipped_the_actions_the_page_is_told_so():
    install()
    outreach(unknown=True)
    code, body, _ = call(client(USER_A), "get", "/api/growth/numbers")
    assert code == 200 and body["supported"] is False and body["numbers"] == []


def test_connect_sends_only_the_return_address_and_returns_the_link():
    install()
    outreach()
    code, body, _ = call(client(USER_A), "post", "/api/growth/numbers/connect", {})
    assert code == 200 and body["auth_url"].startswith("https://outreach.aivhub.com/") and TEST_KEY not in str(body)
    sent = runs("connect")[0]["body"]
    assert sent["plugin"] == "numbers" and list(sent["data"]) == ["return_url"] and sent["data"]["return_url"].endswith("/outreach/connected")


def test_a_test_key_answer_is_the_return_address_itself():
    install()
    outreach(connect=lambda b: (200, {"connect_url": "http://localhost/outreach/connected?status=connected&provider=number&number_id=num_test", "connected": True}))
    code, body, _ = call(client(USER_A), "post", "/api/growth/numbers/connect", {})
    assert code == 200 and body["connected"] is True and "number_id=num_test" in body["auth_url"]


def test_an_unsafe_link_is_never_passed_on():
    install()
    for bad in ("javascript:alert(1)", "http://evil.example/x", "", None):
        outreach(connect=lambda b, bad=bad: (200, {"connect_url": bad}))
        code, body, _ = call(client(USER_A), "post", "/api/growth/numbers/connect", {})
        assert code == 502 and "auth_url" not in body, (bad, body)


def test_a_return_address_outreach_does_not_know_says_what_the_administrator_must_do():
    install()
    outreach(connect=lambda b: (422, {"error": {"code": "return_url_not_allowed", "message": "Return URL not allowed."}}))
    code, body, _ = call(client(USER_A), "post", "/api/growth/numbers/connect", {})
    assert code == 409 and body["code"] == "return_url_not_allowed" and "Partner programme" in body["error"]


def test_release_names_the_number_and_a_bad_id_never_reaches_outreach():
    install()
    outreach()
    c = client(USER_A)
    code, body, _ = call(c, "delete", "/api/growth/numbers/num_1")
    assert code == 200 and body == {"released": "num_1"}
    sent = runs("release")[0]["body"]
    assert sent["id"] == "num_1" and "data" not in sent, "Outreach reads the id from the top level of the request"
    assert call(c, "delete", "/api/growth/numbers/a%20b")[0] == 400 and len(runs("release")) == 1


def test_nothing_works_without_an_outreach_connection():
    install(key="")
    outreach()
    c = client(USER_A)
    for method, url, payload in (("get", "/api/growth/numbers", None), ("post", "/api/growth/numbers/connect", {}), ("delete", "/api/growth/numbers/num_1", None)):
        code, body, _ = call(c, method, url, payload)
        assert code == 409 and "not connected" in body["error"].lower(), (url, body)
    assert runs() == []


def test_the_window_the_popup_ends_on_says_the_request_was_received():
    install()
    html = client(USER_A).get("/outreach/connected?status=connected&provider=number&number_id=num_1").get_data(as_text=True)
    assert 'provider:"number"' in html and "phone number request was received" in html.lower()


def test_wallet_numbers_status_and_pending_wording():
    from tender_app import outreach_client as oc
    w = oc.wallet_summary({"connected": {"phone_numbers": 1, "phone_number_status": {"active": 0, "pending": 1, "verification_pending": 1, "x": "no"}}})
    assert w["phone_status"] == {"active": 0, "pending": 1, "verification_pending": 1}
    assert oc.wallet_summary({"connected": {"phone_numbers": 0}})["phone_status"] is None


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
