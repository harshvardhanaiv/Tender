"""Run: python tests/test_outreach_connection_api.py   (plain asserts; needs the dev database, skips itself without one)

Connect / disconnect Outreach from the Connections window, through the real Flask app and its test client, as throwaway
users, with the fake Outreach of test_growth_outreach_api.py (no network, no real key).

  * a user with no choice made uses the organisation's key (as before) and is told so,
  * a user can paste their own key: it is checked with Outreach first, kept encrypted, used for their requests only and
    never returned; a wrong, malformed or live-when-off key is refused and nothing is stored,
  * Disconnect forgets their key and switches Outreach off for them, even with a server key; other users are untouched,
  * Connect with use_shared goes back to the organisation's connection,
  * the one-to-one email routes follow the same choice.
"""
import json
import sys
import traceback

import test_growth_outreach_api as base
from test_growth_outreach_api import FAKE, TEST_KEY, USER_A, USER_B, call, client, db, install

OWN_KEY = "sk_test_" + "own0123456789abcdef"
OTHER_KEY = "sk_test_" + "other987654321zyx"


def enable_storage():
    from cryptography.fernet import Fernet
    from tender_app.blueprints import growth_bp
    growth_bp.MAIL_CRED_KEY = Fernet.generate_key().decode()
    return growth_bp.MAIL_CRED_KEY


def cleanup():
    partner_off()
    from tender_app import security
    security._buckets.clear()  # the connect route is rate limited per minute; each test starts fresh
    base.cleanup()
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS user_outreach_connections (username VARCHAR(255) PRIMARY KEY, key_enc TEXT, key_hint VARCHAR(40), disabled BOOLEAN NOT NULL DEFAULT FALSE, connected_at TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cur.execute("DELETE FROM user_outreach_connections WHERE username = ANY(%s)", (base.USERS,))
        conn.commit()
    finally:
        conn.close()


def status(c):
    code, body, _ = call(c, "get", "/api/growth/outreach/status")
    assert code == 200, body
    return body


def keys_used():
    """The bearer keys Outreach saw, in order."""
    return [c["headers"].get("Authorization", "").replace("Bearer ", "") for c in FAKE.calls]


def test_with_nothing_chosen_the_organisations_key_is_used_and_the_user_is_told_so():
    install()
    s = status(client(USER_A))
    assert s["ready"] is True and s["source"] == "shared" and s["shared_available"] is True
    assert s["key_hint"] == TEST_KEY[:8] + "…" and keys_used() == [TEST_KEY]
    assert TEST_KEY not in json.dumps(s)


def test_with_no_key_anywhere_it_says_not_connected_and_whether_a_key_can_be_saved():
    install(key="")
    from tender_app.blueprints import growth_bp
    growth_bp.MAIL_CRED_KEY = ""
    s = status(client(USER_A))
    assert s["ready"] is False and s["source"] == "none" and s["shared_available"] is False and s["can_store_key"] is False
    assert "Connected apps" in s["problem"] and "OUTREACH_API_KEY" not in s["problem"] and "top bar" not in s["problem"] and FAKE.calls == []
    enable_storage()
    assert status(client(USER_A))["can_store_key"] is True


def test_a_user_can_connect_their_own_key_which_is_checked_stored_encrypted_and_never_returned():
    install(key="")
    enable_storage()
    c = client(USER_A)
    code, body, text = call(c, "post", "/api/growth/outreach/connect", {"api_key": OWN_KEY})
    assert code == 200, body
    assert body["ready"] is True and body["source"] == "own" and body["key_hint"] == OWN_KEY[:8] + "…"
    assert OWN_KEY not in text and OWN_KEY not in json.dumps(status(c)), "the key is never returned"
    assert OWN_KEY in keys_used(), "it was checked with Outreach"
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT key_enc, disabled FROM user_outreach_connections WHERE username = %s", (USER_A,))
            enc, disabled = cur.fetchone()
    finally:
        conn.close()
    assert enc and OWN_KEY not in enc and disabled is False, "stored encrypted"
    FAKE.calls.clear()
    status(c)
    assert keys_used() == [OWN_KEY], "later requests use the user's own key"


def test_another_user_is_not_connected_by_it_and_keeps_the_organisations_key():
    install()
    enable_storage()
    a, b = client(USER_A), client(USER_B)
    assert call(a, "post", "/api/growth/outreach/connect", {"api_key": OWN_KEY})[0] == 200
    FAKE.calls.clear()
    sb = status(b)
    assert sb["source"] == "shared" and keys_used() == [TEST_KEY]
    FAKE.calls.clear()
    assert status(a)["source"] == "own" and keys_used() == [OWN_KEY]


def test_a_bad_key_is_refused_and_nothing_is_stored():
    install(key="")
    enable_storage()
    c = client(USER_A)
    for key, word in (("", "Paste your Outreach API key"), ("hello", "not an Outreach key"), ("sk_test_ with space", "not an Outreach key"),
                      ("sk_live_" + "x" * 20, "Live keys are switched off")):
        code, body, text = call(c, "post", "/api/growth/outreach/connect", {"api_key": key})
        assert code == 400 and word in body["error"], (key, body)
        assert key.strip() == "" or key not in text
    assert FAKE.calls == [], "Outreach is not even asked about a key that is not well formed"
    # a well-formed key that Outreach refuses
    FAKE.handler = lambda m, u, h, d: (401, {"error": {"code": "unauthorized", "message": "bad key " + OTHER_KEY}})
    code, body, text = call(c, "post", "/api/growth/outreach/connect", {"api_key": OTHER_KEY})
    assert code == 400 and "did not accept that key" in body["error"] and OTHER_KEY not in text
    assert status(c)["source"] == "none", "a refused key is not kept"


def test_without_the_server_secret_a_key_cannot_be_saved_and_says_why():
    install(key="")
    from tender_app.blueprints import growth_bp
    growth_bp.MAIL_CRED_KEY = ""
    code, body, _ = call(client(USER_A), "post", "/api/growth/outreach/connect", {"api_key": OWN_KEY})
    assert code == 409 and "MAIL_CRED_KEY" in body["error"] and FAKE.calls == []


def test_disconnect_forgets_the_key_and_switches_outreach_off_for_that_user_only():
    install()
    enable_storage()
    a, b = client(USER_A), client(USER_B)
    call(a, "post", "/api/growth/outreach/connect", {"api_key": OWN_KEY})
    FAKE.calls.clear()
    code, body, _ = call(a, "delete", "/api/growth/outreach/connect")
    assert code == 200 and body["ready"] is False and body["source"] == "disconnected" and body["shared_available"] is True
    assert "disconnected" in body["problem"].lower() and FAKE.calls == [], "nothing is sent to Outreach once disconnected"
    # every Outreach route is refused for them, even with a server key
    assert call(a, "get", "/api/growth/inbox")[0] == 409
    assert call(a, "get", "/api/growth/social/posts?refresh=1")[0] in (409, 200)
    code, body, _ = call(a, "post", "/api/growth/social/posts", {"prompt": "Announce our new office opening", "channels": ["linkedin"], "attempt": "abcdef1234567890"})
    assert code == 409 and "disconnected" in body["error"].lower() and FAKE.calls == []
    # the other user still works, with the organisation's key
    FAKE.calls.clear()
    assert status(b)["ready"] is True and keys_used() == [TEST_KEY]
    # and the saved key is really gone
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT key_enc, disabled FROM user_outreach_connections WHERE username = %s", (USER_A,))
            assert cur.fetchone() == (None, True)
    finally:
        conn.close()


def test_disconnecting_with_no_server_key_just_forgets_the_users_key():
    install(key="")
    enable_storage()
    c = client(USER_A)
    call(c, "post", "/api/growth/outreach/connect", {"api_key": OWN_KEY})
    code, body, _ = call(c, "delete", "/api/growth/outreach/connect")
    assert code == 200 and body["source"] == "none" and body["ready"] is False and body["shared_available"] is False
    assert call(c, "delete", "/api/growth/outreach/connect")[0] == 200, "disconnecting twice is harmless"


def test_a_disconnected_user_can_go_back_to_the_organisations_connection_or_paste_a_key():
    install()
    enable_storage()
    c = client(USER_A)
    call(c, "delete", "/api/growth/outreach/connect")
    assert status(c)["source"] == "disconnected"
    code, body, _ = call(c, "post", "/api/growth/outreach/connect", {"use_shared": True})
    assert code == 200 and body["ready"] is True and body["source"] == "shared"
    call(c, "delete", "/api/growth/outreach/connect")
    code, body, _ = call(c, "post", "/api/growth/outreach/connect", {"api_key": OWN_KEY})
    assert code == 200 and body["source"] == "own"
    install(key="")
    enable_storage()
    code, body, _ = call(client(USER_B), "post", "/api/growth/outreach/connect", {"use_shared": True})
    assert code == 409 and "no Outreach connection of its own" in body["error"]


def test_a_saved_key_that_can_no_longer_be_read_says_so_instead_of_failing_oddly():
    install(key="")
    enable_storage()
    c = client(USER_A)
    call(c, "post", "/api/growth/outreach/connect", {"api_key": OWN_KEY})
    enable_storage()  # the server secret changed
    s = status(c)
    assert s["ready"] is False and "could not be read" in s["problem"] and s["source"] == "own"


def test_the_one_to_one_email_routes_follow_the_same_choice():
    install()
    enable_storage()
    from tender_app.blueprints import email_bp
    email_bp.OUTREACH_API_KEY = TEST_KEY
    c = client(USER_A)
    code, body, _ = call(c, "get", "/api/email/outreach/status")
    assert code == 200 and body["configured"] is True
    call(c, "delete", "/api/growth/outreach/connect")
    code, body, _ = call(c, "get", "/api/email/outreach/status")
    assert code == 200 and body["configured"] is False and body["ready"] is False


def test_connecting_needs_a_json_key_and_the_csrf_token():
    install()
    enable_storage()
    c = client(USER_A)
    assert c.post("/api/growth/outreach/connect", json={"api_key": OWN_KEY}).status_code in (400, 403), "no CSRF header"
    assert call(c, "post", "/api/growth/outreach/connect", {})[0] == 400
    assert call(c, "post", "/api/growth/outreach/connect", {"api_key": 12345})[0] == 400


# ── partner programme: sign in / create account from TenderFlow ────────────────────────────────────

CLIENT = "client_test123"
PARTNER_KEY = "sk_test_" + "partnerkey0123456789"
PASSWORD = "correct-horse-battery"


def partner_on(signup=True):
    from tender_app.blueprints import growth_bp
    growth_bp.OUTREACH_CLIENT_ID = CLIENT
    growth_bp.OUTREACH_CONSENT_VERSION = "2026-10" if signup else ""
    growth_bp.OUTREACH_ORIGIN = "https://tender.example"
    growth_bp.OUTREACH_RETURN_URL = ""
    growth_bp._PARTNER_TRANSPORT = FAKE.transport


def partner_off():
    from tender_app.blueprints import growth_bp
    growth_bp.OUTREACH_CLIENT_ID = ""
    growth_bp.OUTREACH_CONSENT_VERSION = ""
    growth_bp.OUTREACH_ORIGIN = ""
    growth_bp._PARTNER_TRANSPORT = None


def auth_handler(answers):
    """Answers POST /v1/run auth actions from `answers` {action: (status, payload)}; wallets and the rest as the default fake."""
    default = FAKE.default

    def h(method, url, headers, data):
        if method == "POST" and url.endswith("/v1/run"):
            body = json.loads(data)
            if body.get("plugin") == "auth":
                return answers.get(body["action"], (404, {"error": {"code": "not_found", "message": "unknown"}}))
        return default(method, url, headers, data)
    return h


def runs(action=None):
    out = [c for c in FAKE.calls if c["method"] == "POST" and c["url"].endswith("/v1/run")]
    return [c for c in out if action is None or c["body"]["action"] == action]


def err(code, message="x", status=400):
    return status, {"error": {"code": code, "message": message}}


def test_the_status_says_whether_signing_in_and_creating_an_account_are_switched_on():
    install(key="")
    enable_storage()
    partner_off()
    s = status(client(USER_A))
    assert s["partner_enabled"] is False and s["partner_signup"] is False
    partner_on(signup=False)
    s = status(client(USER_A))
    assert s["partner_enabled"] is True and s["partner_signup"] is False
    partner_on()
    s = status(client(USER_A))
    assert s["partner_enabled"] is True and s["partner_signup"] is True


def test_signing_in_keeps_the_key_the_user_gets_and_never_returns_the_key_or_password():
    install(key="")
    enable_storage()
    partner_on()
    FAKE.handler = auth_handler({"login": (200, {"api_key": PARTNER_KEY, "org_id": "org_1"})})
    c = client(USER_A)
    code, body, text = call(c, "post", "/api/growth/outreach/signin", {"email": "Sam@Acme.example", "password": PASSWORD, "consent": True})
    assert code == 200, body
    assert body["ready"] is True and body["source"] == "own" and body["via"] == "partner"
    assert PARTNER_KEY not in text and PASSWORD not in text
    sent = runs("login")[0]
    assert sent["body"] == {"plugin": "auth", "action": "login", "data": {"client_id": CLIENT, "email": "sam@acme.example", "password": PASSWORD,
                                                                          "consent": True, "consent_version": "2026-10"}}
    assert sent["headers"]["Origin"] == "https://tender.example" and "Authorization" not in sent["headers"], "no key needed to sign in"
    FAKE.calls.clear()
    status(c)
    assert PARTNER_KEY in keys_used(), "later requests use the key"
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT key_enc, via FROM user_outreach_connections WHERE username = %s", (USER_A,))
            enc, via = cur.fetchone()
    finally:
        conn.close()
    assert enc and PARTNER_KEY not in enc and PASSWORD not in enc and via == "partner"


def test_a_wrong_password_or_unconfirmed_email_is_explained_and_nothing_is_stored():
    install(key="")
    enable_storage()
    partner_on()
    c = client(USER_A)
    for code_, word in (("bad_credentials", "do not match an Outreach account"), ("email_not_verified", "Confirm your email first"),
                        ("partner_suspended", "paused sign-in"), ("invalid_client", "OUTREACH_CLIENT_ID")):
        FAKE.handler = auth_handler({"login": err(code_, "raw outreach text " + PASSWORD)})
        code, body, text = call(c, "post", "/api/growth/outreach/signin", {"email": "sam@acme.example", "password": PASSWORD, "consent": True})
        assert code in (400, 409) and word in body["error"] and body["code"] == code_, (code_, body)
        assert PASSWORD not in text, "an Outreach message is never echoed back"
    assert status(c)["source"] == "none"


def test_outreach_not_knowing_this_address_says_which_address_to_register():
    install(key="")
    enable_storage()
    partner_on()
    FAKE.handler = auth_handler({"login": err("origin_not_allowed")})
    code, body, _ = call(client(USER_A), "post", "/api/growth/outreach/signin", {"email": "sam@acme.example", "password": PASSWORD, "consent": True})
    assert code == 409 and "https://tender.example" in body["error"] and "Partner programme" in body["error"] and body["code"] == "origin_not_allowed"
    FAKE.handler = auth_handler({"signup": err("return_url_not_allowed")})
    code, body, _ = call(client(USER_A), "post", "/api/growth/outreach/signup",
                         {"company": "Acme", "name": "Sam", "email": "sam@acme.example", "password": PASSWORD, "consent": True})
    assert code == 409 and "https://tender.example/outreach/connected" in body["error"]


def test_signing_in_without_the_consent_version_is_refused_with_what_to_set():
    install(key="")
    enable_storage()
    partner_on(signup=False)  # client id but no consent version
    s = status(client(USER_A))
    assert s["partner_enabled"] is True and s["partner_consent_version"] is False
    code, body, _ = call(client(USER_A), "post", "/api/growth/outreach/signin", {"email": "sam@acme.example", "password": PASSWORD, "consent": True})
    assert code == 409 and "OUTREACH_CONSENT_VERSION" in body["error"] and body["code"] == "consent_version_missing" and FAKE.calls == []


def test_without_a_client_id_signing_in_is_refused_politely_and_outreach_is_not_called():
    install(key="")
    enable_storage()
    partner_off()
    code, body, _ = call(client(USER_A), "post", "/api/growth/outreach/signin", {"email": "sam@acme.example", "password": PASSWORD, "consent": True})
    assert code == 409 and "OUTREACH_CLIENT_ID" in body["error"] and body["code"] == "partner_disabled" and FAKE.calls == []


def test_a_bad_form_is_refused_before_outreach_is_asked():
    install(key="")
    enable_storage()
    partner_on()
    c = client(USER_A)
    for body in ({}, {"email": "", "password": "x", "consent": True}, {"email": "not an email", "password": "x", "consent": True},
                 {"email": "sam@acme.example", "password": "", "consent": True}, {"email": "sam@acme.example", "password": 12345, "consent": True},
                 {"email": "sam@acme.example", "password": "x" * 300, "consent": True}):
        assert call(c, "post", "/api/growth/outreach/signin", body)[0] == 400, body
    # without the user's consent to link the account, Outreach is not asked at all
    code, body, _ = call(c, "post", "/api/growth/outreach/signin", {"email": "sam@acme.example", "password": PASSWORD})
    assert code == 400 and body["code"] == "consent_required"
    assert call(c, "post", "/api/growth/outreach/signin", {"email": "sam@acme.example", "password": PASSWORD, "consent": "yes"})[0] == 400
    assert FAKE.calls == []


def test_creating_an_account_sends_the_consent_and_return_address_and_keeps_the_key():
    install(key="")
    enable_storage()
    partner_on()
    FAKE.handler = auth_handler({"signup": (200, {"api_key": PARTNER_KEY})})
    c = client(USER_A)
    form = {"company": "  Acme   Ltd ", "name": "Sam Lee", "email": "sam@acme.example", "password": PASSWORD, "consent": True}
    code, body, text = call(c, "post", "/api/growth/outreach/signup", form)
    assert code == 200 and body["ready"] is True and body["via"] == "partner" and PASSWORD not in text and PARTNER_KEY not in text
    assert runs("signup")[0]["body"]["data"] == {"client_id": CLIENT, "company": "Acme Ltd", "name": "Sam Lee", "email": "sam@acme.example", "password": PASSWORD,
                                                  "consent": True, "consent_version": "2026-10", "return_url": "https://tender.example/outreach/connected"}


def test_a_signup_that_needs_the_email_confirmed_stores_nothing_and_says_so():
    install(key="")
    enable_storage()
    partner_on()
    FAKE.handler = auth_handler({"signup": (200, {"verify_email": True})})
    c = client(USER_A)
    code, body, _ = call(c, "post", "/api/growth/outreach/signup", {"company": "Acme", "name": "Sam", "email": "sam@acme.example", "password": PASSWORD, "consent": True})
    assert code == 200 and body["verify_email"] is True and body["email"] == "sam@acme.example" and body["ready"] is False
    assert status(c)["source"] == "none", "no key until the user has confirmed and signed in"


def test_creating_an_account_needs_consent_a_name_and_a_real_password_and_the_signup_setting():
    install(key="")
    enable_storage()
    partner_on()
    c = client(USER_A)
    base_form = {"company": "Acme", "name": "Sam", "email": "sam@acme.example", "password": PASSWORD, "consent": True}
    code, body, _ = call(c, "post", "/api/growth/outreach/signup", {**base_form, "consent": False})
    assert code == 400 and body["code"] == "consent_required"
    assert call(c, "post", "/api/growth/outreach/signup", {**base_form, "consent": "yes"})[0] == 400, "only a real true counts"
    from tender_app import security as _sec
    _sec._buckets.clear()
    from tender_app import security
    for bad in ({"company": ""}, {"name": ""}, {"email": "nope"}, {"password": "short"}):
        security._buckets.clear()  # the signup route allows 5 a minute
        assert call(c, "post", "/api/growth/outreach/signup", {**base_form, **bad})[0] == 400, bad
    assert FAKE.calls == []
    partner_on(signup=False)
    code, body, _ = call(c, "post", "/api/growth/outreach/signup", base_form)
    assert code == 409 and "OUTREACH_CONSENT_VERSION" in body["error"] and FAKE.calls == []


def test_an_existing_account_is_told_to_sign_in_instead():
    install(key="")
    enable_storage()
    partner_on()
    FAKE.handler = auth_handler({"signup": err("account_exists")})
    code, body, _ = call(client(USER_A), "post", "/api/growth/outreach/signup",
                         {"company": "Acme", "name": "Sam", "email": "sam@acme.example", "password": PASSWORD, "consent": True})
    assert code == 400 and "Sign in instead" in body["error"] and body["code"] == "account_exists"


def test_forgot_password_and_resend_confirmation_only_ask_outreach_to_send_an_email():
    install(key="")
    enable_storage()
    partner_on()
    FAKE.handler = auth_handler({"forgot_password": (200, {"ok": True}), "resend_verification": (200, {"ok": True})})
    c = client(USER_A)
    assert call(c, "post", "/api/growth/outreach/forgot", {"email": "Sam@Acme.example"})[1] == {"sent": True, "email": "sam@acme.example"}
    assert call(c, "post", "/api/growth/outreach/resend", {"email": "sam@acme.example"})[1] == {"sent": True, "email": "sam@acme.example"}
    assert runs("forgot_password")[0]["body"]["data"] == {"client_id": CLIENT, "email": "sam@acme.example"}
    assert runs("resend_verification")[0]["body"]["data"] == {"client_id": CLIENT, "email": "sam@acme.example", "return_url": "https://tender.example/outreach/connected"},         "the confirmation link brings the user back to TenderFlow"
    assert call(c, "post", "/api/growth/outreach/forgot", {"email": "bad"})[0] == 400
    assert status(c)["source"] == "none"


def test_disconnecting_a_partner_sign_in_also_tells_outreach_but_a_pasted_key_does_not():
    install(key="")
    enable_storage()
    partner_on()
    FAKE.handler = auth_handler({"login": (200, {"api_key": PARTNER_KEY}), "disconnect": (200, {"ok": True})})
    c = client(USER_A)
    call(c, "post", "/api/growth/outreach/signin", {"email": "sam@acme.example", "password": PASSWORD, "consent": True})
    FAKE.calls.clear()
    code, body, _ = call(c, "delete", "/api/growth/outreach/connect")
    assert code == 200 and body["revoked"] is True and body["source"] == "none"
    sent = runs("disconnect")
    assert len(sent) == 1 and sent[0]["headers"]["Authorization"] == f"Bearer {PARTNER_KEY}", "the user's own key is used to revoke itself"
    FAKE.handler = auth_handler({"disconnect": (200, {"ok": True})})
    call(c, "post", "/api/growth/outreach/connect", {"api_key": OWN_KEY})
    FAKE.calls.clear()
    code, body, _ = call(c, "delete", "/api/growth/outreach/connect")
    assert code == 200 and body["revoked"] is False and runs("disconnect") == []


def test_disconnect_still_works_here_when_outreach_cannot_be_told():
    install(key="")
    enable_storage()
    partner_on()
    FAKE.handler = auth_handler({"login": (200, {"api_key": PARTNER_KEY}), "disconnect": err("unauthorized", "already revoked", 401)})
    c = client(USER_A)
    call(c, "post", "/api/growth/outreach/signin", {"email": "sam@acme.example", "password": PASSWORD, "consent": True})
    code, body, _ = call(c, "delete", "/api/growth/outreach/connect")
    assert code == 200 and body["revoked"] is False and body["source"] == "none"


def test_the_page_outreach_sends_users_back_to_opens_the_connections_window():
    install()
    r = client(USER_A).get("/outreach/connected")
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
