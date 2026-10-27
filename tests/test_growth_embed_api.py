"""Run: python tests/test_growth_embed_api.py   (plain asserts; needs the dev database, skips itself without one)

Outreach's own screens inside the Connections window (embed.js): the server side, through the real Flask app and its test client.
Outreach is a fake: nothing leaves the machine.

  * the page is told whether screens are switched on, and gets the publishable key but never the partner secret,
  * a linked user gets a screen token, an unlinked one gets a connect token (and no screen token),
  * the secret is sent as a Bearer header from the server and never with an Origin, and never comes back in an answer or an error,
  * only the screens Connections shows are allowed, a refused secret says what the administrator must do, a live secret is held back.
"""
import json
import sys
import traceback

import test_growth_outreach_api as base
import test_outreach_connection_api as conn_tests
from test_growth_outreach_api import FAKE, USER_A, call, client, db, install

SECRET = "psk_test_abcdef123456"
PK = "pk_test_publishable1"


def cleanup():
    conn_tests.cleanup()   # also clears the saved Outreach keys of the test users and the per-minute limits


def configure(secret=SECRET, key=PK, allow_live=False, linked=True, connect=None, session=None, key_answer=None, shared=base.TEST_KEY):
    from tender_app.blueprints import growth_bp
    install(key=shared)                  # shared: the organisation's own key; "" leaves a user with no key until they link
    growth_bp.OUTREACH_PARTNER_SECRET = secret
    growth_bp.OUTREACH_PUBLISHABLE_KEY = key
    growth_bp.OUTREACH_ALLOW_LIVE = allow_live
    growth_bp._EMBED_TRANSPORT = FAKE.transport

    def handler(method, url, headers, data):
        body = json.loads(data) if data else {}
        if url.endswith("/api/embed/connect-sessions"):
            if connect:
                return connect(body)
            return (200, {"linked": True, "link_id": "plink_1", "mode": "existing"}) if linked else (200, {"linked": False, "connect_token": "ct_abc", "expires_at": 1767225600})
        if url.endswith("/api/embed/sessions"):
            if session:
                return session(body)
            return 200, {"token": "tok_screen", "expires_at": 1767225600, "component": body.get("component"), "access": "full", "link_id": "plink_1"}
        if url.endswith("/key"):
            return key_answer(body) if key_answer else (200, {"api_key": conn_tests.OWN_KEY})
        return 404, {"error": {"code": "not_found", "message": "unknown"}}
    FAKE.handler = handler


def embed_calls():
    return [c for c in FAKE.calls if "/api/embed/" in c["url"]]


def test_config_gives_the_publishable_key_and_never_the_secret():
    configure()
    code, body, text = call(client(USER_A), "get", "/api/growth/embed/config")
    assert code == 200 and body["enabled"] is True and body["key"] == PK and body["screens"] == ["mail", "social", "voice"], body
    assert SECRET not in text and body["mode"] == "test"


def test_config_says_why_screens_are_off():
    for kwargs, words in (({"secret": ""}, "not switched on"), ({"key": ""}, "not switched on"), ({"secret": "sk_test_wrong1234567"}, "psk_test_"),
                          ({"secret": "psk_live_abcdef123456"}, "live")):
        configure(**kwargs)
        code, body, text = call(client(USER_A), "get", "/api/growth/embed/config")
        assert code == 200 and body["enabled"] is False and words in body["reason"], (kwargs, body)
        assert "abcdef123456" not in text


def test_a_linked_user_gets_a_screen_token():
    configure()
    code, body, text = call(client(USER_A), "post", "/api/growth/embed/session", {"component": "voice"})
    assert code == 200 and body == {"linked": True, "token": "tok_screen", "expires_at": 1767225600, "component": "voice"}, body
    first, second = embed_calls()
    assert first["body"] == {"external_id": USER_A, "component": "voice"}
    assert second["body"] == {"external_id": USER_A, "component": "voice", "access": "full", "ttl_seconds": 900}
    for c in (first, second):
        assert c["headers"]["Authorization"] == f"Bearer {SECRET}" and "Origin" not in c["headers"], "server to server: a browser origin is refused by Outreach"
    assert SECRET not in text


def test_an_unlinked_user_gets_a_connect_token_and_no_screen_token():
    configure(linked=False)
    code, body, _ = call(client(USER_A), "post", "/api/growth/embed/session", {"component": "social"})
    assert code == 200 and body["linked"] is False and body["connect_token"] == "ct_abc" and "token" not in body, body
    assert len(embed_calls()) == 1, "no screen token is asked for until the person has connected"


def test_only_the_screens_connections_shows_are_allowed():
    configure()
    c = client(USER_A)
    for component in ("leads", "campaigns", "admin", "", "voice;x"):
        code, body, _ = call(c, "post", "/api/growth/embed/session", {"component": component})
        assert code == 400, (component, body)
    assert embed_calls() == []


def test_a_secret_outreach_refuses_says_what_the_administrator_must_do():
    configure(connect=lambda b: (401, {"error": {"code": "invalid_secret", "message": "Invalid secret key"}}))
    code, body, text = call(client(USER_A), "post", "/api/growth/embed/session", {"component": "voice"})
    assert code == 409 and body["code"] == "partner_secret_rejected" and "OUTREACH_PARTNER_SECRET" in body["error"], body
    assert SECRET not in text


def test_an_account_linked_to_someone_else_and_a_rate_limit_have_their_own_words():
    configure(connect=lambda b: (409, {"error": {"code": "already_linked", "message": "already linked"}}))
    code, body, _ = call(client(USER_A), "post", "/api/growth/embed/session", {"component": "voice"})
    assert code == 409 and body["code"] == "already_linked" and "different TenderFlow user" in body["error"], body
    configure(connect=lambda b: (429, {"error": {"code": "rate_limited", "message": "slow down"}}))
    code, body, _ = call(client(USER_A), "post", "/api/growth/embed/session", {"component": "voice"})
    assert code == 429 and body["code"] == "rate_limited", body


def test_nothing_is_asked_of_outreach_when_screens_are_off():
    configure(secret="")
    code, body, _ = call(client(USER_A), "post", "/api/growth/embed/session", {"component": "voice"})
    assert code == 409 and body["code"] == "embed_disabled" and embed_calls() == []


def key_calls():
    return [c for c in FAKE.calls if c["url"].endswith("/key")]


def test_a_linked_user_without_a_key_gets_one_kept_server_side_and_never_shown():
    configure(shared="")
    conn_tests.enable_storage()
    c = client(USER_A)
    assert call(c, "get", "/api/growth/outreach/status")[1]["configured"] is False
    code, body, text = call(c, "post", "/api/growth/embed/key", {})
    assert code == 200 and body["configured"] is True and body["source"] == "own" and body["via"] == "partner", body
    assert conn_tests.OWN_KEY not in text, "the key stays on the server"
    sent = key_calls()[0]
    assert sent["url"].endswith(f"/api/embed/links/{USER_A}/key") and sent["headers"]["Authorization"] == f"Bearer {SECRET}" and "Origin" not in sent["headers"]
    assert call(c, "get", "/api/growth/outreach/status")[1]["configured"] is True, "Connections now reads Outreach as connected"


def test_a_user_who_already_has_a_working_key_is_not_given_a_new_one():
    configure()                          # install() gave everyone the organisation key
    conn_tests.enable_storage()
    code, body, _ = call(client(USER_A), "post", "/api/growth/embed/key", {})
    assert code == 200 and body["configured"] is True and key_calls() == [], "Outreach replaces the old key when it issues a new one"


def test_a_user_who_has_not_linked_is_told_to_sign_in_first():
    configure(shared="", key_answer=lambda b: (404, {"error": {"code": "not_found", "message": "That account is not connected to you"}}))
    conn_tests.enable_storage()
    code, body, _ = call(client(USER_A), "post", "/api/growth/embed/key", {})
    assert code == 409 and body["code"] == "not_linked" and "Sign in with Outreach" in body["error"], body


def test_a_key_cannot_be_kept_when_the_server_cannot_encrypt_it():
    configure(shared="")
    from tender_app.blueprints import growth_bp
    growth_bp.MAIL_CRED_KEY = ""
    code, body, _ = call(client(USER_A), "post", "/api/growth/embed/key", {})
    assert code == 409 and "MAIL_CRED_KEY" in body["error"] and key_calls() == []


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
        from tender_app.blueprints import growth_bp
        growth_bp._EMBED_TRANSPORT = None
    print(f"\n{len(tests) - failures} of {len(tests)} passed")
    sys.exit(1 if failures else 0)
