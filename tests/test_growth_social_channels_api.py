"""Run: python tests/test_growth_social_channels_api.py   (plain asserts; needs the dev database, skips itself without one)

Connecting social accounts from the Connections window (Outreach's `social` plugin channel.* actions), through the real Flask app
and its test client. Outreach is a fake: nothing leaves the machine and no network is signed in to.

  * the networks and accounts are read, and a missing feature at Outreach's end answers "not supported" instead of failing,
  * Connect sends the network and TenderFlow's return address with the user's key and hands back only a safe link,
  * a refused return address says what the administrator must do, a bad network is refused before Outreach is asked,
  * Disconnect names the account, and nothing works without an Outreach connection,
  * the page the sign-in window ends on tells the opener and closes itself, whatever is in the address.
"""
import sys
import traceback

import test_growth_outreach_api as base
from test_growth_outreach_api import FAKE, TEST_KEY, USER_A, call, client, db, install

RETURN = "http://localhost/outreach/connected"
PROVIDERS = {"providers": [{"provider": "linkedin", "available": True}, {"provider": "facebook", "available": True},
                           {"provider": "instagram", "available": False}, {"provider": "x", "available": True}, {"provider": "threads", "available": True}]}
CHANNELS = {"channels": [{"id": "ch_1", "provider": "linkedin", "display_name": "Acme Ltd", "status": "connected"},
                         {"id": "ch_2", "provider": "facebook", "name": "Acme Page", "status": "needs_reauth"}]}


def cleanup():
    base.cleanup()


def runs(action=None):
    """The /v1/run requests the fake saw (optionally only one action)."""
    out = [c for c in FAKE.calls if c["url"].endswith("/v1/run")]
    return [c for c in out if action is None or (c["body"] or {}).get("action") == action]


def outreach(connect=None, providers=PROVIDERS, channels=CHANNELS, unknown=False):
    def handler(method, url, headers, data):
        import json
        if not url.endswith("/v1/run"):
            return FAKE.default(method, url, headers, data)
        body = json.loads(data)
        if unknown:
            return 422, {"error": {"code": "unknown_action", "message": "Unknown action."}}
        action = body.get("action")
        if action == "channel.providers":
            return 200, providers
        if action == "channel.list":
            return 200, channels
        if action == "channel.connect":
            return connect(body) if connect else (200, {"auth_url": "https://www.linkedin.com/oauth/v2/authorization?state=abc", "state": "abc"})
        if action == "channel.disconnect":
            return 200, {"ok": True}
        return 404, {"error": {"code": "not_found", "message": "unknown"}}
    FAKE.handler = handler


def test_networks_and_accounts_are_read():
    install()
    outreach()
    code, body, _ = call(client(USER_A), "get", "/api/growth/social/channels")
    assert code == 200 and body["supported"] is True, body
    assert [(p["provider"], p["available"]) for p in body["providers"]] == [("linkedin", True), ("facebook", True), ("instagram", False), ("x", True), ("threads", True)]
    assert [(c["id"], c["provider"], c["status"], c["name"]) for c in body["channels"]] == [("ch_1", "linkedin", "connected", "Acme Ltd"), ("ch_2", "facebook", "needs_reauth", "Acme Page")]
    assert body["return_url"].endswith("/outreach/connected")
    assert body["outreach_origin"].startswith("https://") and body["outreach_origin"].count("/") == 2, "just the origin, which the page checks messages against"
    assert all(c["headers"]["Authorization"] == f"Bearer {TEST_KEY}" for c in runs())
    assert [c["body"]["action"] for c in runs()] == ["channel.providers", "channel.list"]


def test_a_test_keys_sample_accounts_are_flagged_and_never_listed_as_connected():
    install()
    outreach(channels={"channels": CHANNELS["channels"], "simulated": True})
    body = call(client(USER_A), "get", "/api/growth/social/channels")[1]
    assert body["simulated"] is True and body["channels"] == [], "they are not the user's accounts"
    assert [p["provider"] for p in body["providers"]][:2] == ["linkedin", "facebook"], "every network is still offered"
    outreach()
    body = call(client(USER_A), "get", "/api/growth/social/channels")[1]
    assert body["simulated"] is False and len(body["channels"]) == 2


def test_when_outreach_has_not_shipped_the_actions_the_page_is_told_so_rather_than_given_an_error():
    install()
    outreach(unknown=True)
    code, body, _ = call(client(USER_A), "get", "/api/growth/social/channels")
    assert code == 200 and body["supported"] is False and body["providers"] == [] and body["channels"] == []


def test_a_list_with_no_networks_still_offers_the_usual_five():
    install()
    outreach(providers={"providers": []}, channels={"channels": []})
    body = call(client(USER_A), "get", "/api/growth/social/channels")[1]
    assert [p["provider"] for p in body["providers"]] == ["linkedin", "facebook", "instagram", "x", "threads"] and all(p["available"] for p in body["providers"])


def test_connect_sends_the_network_and_the_return_address_and_returns_the_link():
    install()
    outreach()
    code, body, _ = call(client(USER_A), "post", "/api/growth/social/channels/connect", {"provider": "LinkedIn"})
    assert code == 200 and body["auth_url"].startswith("https://www.linkedin.com/") and body["provider"] == "linkedin" and body["connected"] is False
    sent = runs("channel.connect")[0]["body"]["data"]
    assert sent["provider"] == "linkedin" and sent["return_url"].endswith("/outreach/connected")
    assert "password" not in str(runs("channel.connect")[0]["body"]).lower() and TEST_KEY not in str(body)


def test_a_test_key_answer_is_the_return_address_itself_and_counts_as_a_link():
    install()
    outreach(connect=lambda b: (200, {"auth_url": RETURN + "?status=connected&provider=linkedin&channel_id=ch_9", "connected": True}))
    code, body, _ = call(client(USER_A), "post", "/api/growth/social/channels/connect", {"provider": "linkedin"})
    assert code == 200 and body["auth_url"].startswith("http://localhost/outreach/connected") and body["connected"] is True


def test_an_unsafe_link_is_never_passed_on():
    install()
    for bad in ("javascript:alert(1)", "http://evil.example/x", "", None):
        outreach(connect=lambda b, bad=bad: (200, {"auth_url": bad}))
        code, body, _ = call(client(USER_A), "post", "/api/growth/social/channels/connect", {"provider": "linkedin"})
        assert code == 502 and "sign-in link" in body["error"] and "auth_url" not in body, (bad, body)


def test_a_bad_network_is_refused_before_outreach_is_asked():
    install()
    outreach()
    c = client(USER_A)
    for bad in ({}, {"provider": ""}, {"provider": "../x"}, {"provider": "a b"}, {"provider": "x" * 40}, {"provider": None}):
        assert call(c, "post", "/api/growth/social/channels/connect", bad)[0] == 400, bad
    assert runs("channel.connect") == []
    assert call(c, "post", "/api/growth/social/channels/connect", {"provider": "x"})[0] == 200, "X is a one-letter network"


def test_a_return_address_outreach_does_not_know_says_what_the_administrator_must_do():
    install()
    outreach(connect=lambda b: (422, {"error": {"code": "return_url_not_allowed", "message": "Return URL not allowed."}}))
    code, body, _ = call(client(USER_A), "post", "/api/growth/social/channels/connect", {"provider": "linkedin"})
    assert code == 409 and body["code"] == "return_url_not_allowed" and "Partner programme" in body["error"] and "/outreach/connected" in body["error"]
    outreach(connect=lambda b: (422, {"error": {"code": "origin_not_allowed", "message": "no"}}))
    code, body, _ = call(client(USER_A), "post", "/api/growth/social/channels/connect", {"provider": "linkedin"})
    assert code == 409 and "web address" in body["error"]


def test_a_network_that_is_not_switched_on_is_explained_in_the_users_words():
    install()
    outreach(connect=lambda b: (422, {"error": {"code": "provider_unavailable", "message": "Instagram is not set up yet."}}))
    code, body, _ = call(client(USER_A), "post", "/api/growth/social/channels/connect", {"provider": "instagram"})
    assert code == 409 and body["code"] == "not_available" and "Instagram cannot be connected from here yet" in body["error"] and "not set up yet" in body["error"]


def test_a_key_without_the_permission_is_reported_as_such():
    install()
    outreach(connect=lambda b: (403, {"error": {"code": "missing_scope", "message": "The key needs the social:write permission."}}))
    code, body, _ = call(client(USER_A), "post", "/api/growth/social/channels/connect", {"provider": "linkedin"})
    assert code == 403 and "social:write" in body["error"]


def test_disconnect_names_the_account_and_a_bad_id_never_reaches_outreach():
    install()
    outreach()
    c = client(USER_A)
    code, body, _ = call(c, "delete", "/api/growth/social/channels/ch_1")
    assert code == 200 and body == {"disconnected": "ch_1"}
    sent = runs("channel.disconnect")[0]["body"]
    assert sent["id"] == "ch_1" and "data" not in sent, "Outreach reads the id from the top level of the request"
    code, body, _ = call(c, "delete", "/api/growth/social/channels/a%20b")
    assert code == 400 and len(runs("channel.disconnect")) == 1


def test_nothing_works_without_an_outreach_connection():
    install(key="")
    outreach()
    c = client(USER_A)
    for method, url, payload in (("get", "/api/growth/social/channels", None), ("post", "/api/growth/social/channels/connect", {"provider": "linkedin"}),
                                 ("delete", "/api/growth/social/channels/ch_1", None)):
        code, body, _ = call(c, method, url, payload)
        assert code == 409 and "not connected" in body["error"].lower(), (url, body)
    assert runs() == []


def test_the_window_a_sign_in_ends_on_tells_the_opener_and_closes_itself():
    install()
    c = client(USER_A)
    r = c.get("/outreach/connected?status=connected&provider=linkedin&channel_id=ch_1")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and r.mimetype == "text/html" and "window.close()" in html
    assert 'type:"tf.outreach.channel",status:"connected",provider:"linkedin"' in html and "window.location.origin" in html
    assert 'new BroadcastChannel("tf-outreach-channel")' in html, "also told to every tab of the site, in case the window's link back is gone"
    assert 'id="c" hidden' in html and 'hidden=false' in html, "a browser that will not close the window gets a Close button"
    assert "no-store" in r.headers.get("Cache-Control", "")
    err = c.get("/outreach/connected?status=error&provider=facebook").get_data(as_text=True)
    assert 'status:"error"' in err and "could not be connected" in err
    # whatever is in the address is cut down to letters, so it cannot add markup or script
    evil = c.get("/outreach/connected?status=%22%3E%3Cscript%3Ealert(1)%3C/script%3E&provider=%3Cscript%3Ealert(1)%3C/script%3Elinkedin").get_data(as_text=True)
    import re
    assert "<script>alert" not in evil and 'status:"error"' in evil
    assert re.search(r'provider:"[a-z0-9_]*"', evil) and evil.count("<script>") == 1, "only the page's own script"
    # no provider: the old behaviour (back into the app with Connections open)
    r = c.get("/outreach/connected")
    assert r.status_code == 302 and r.headers["Location"].endswith("/?outreach=connected")


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
