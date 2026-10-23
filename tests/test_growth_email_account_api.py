"""Run: python tests/test_growth_email_account_api.py   (plain asserts; needs the dev database, skips itself without one)

The user's own email account (SMTP) for Growth Studio, set up from the Connections window, through the real Flask app and its
test client, as throwaway users. The mail server is a fake: nothing is ever sent and no real password is used.

  * set up: the login is tried before anything is kept, the password is kept encrypted and never returned, a bad login or a
    bad form is refused and nothing is stored, a blank password keeps the saved one only while the login and server are the same,
  * a test email goes from the account to its own address, and the account can be forgotten,
  * Send mail with "smtp" as the sender goes out through the account and not Outreach: each buyer is recorded as sent as soon
    as the mail server accepts it, nobody is sent twice, a refused address does not stop the others, a bad login stops cleanly
    and records nothing as sent, and the daily limit holds,
  * one user can never use another's account.
"""
import json
import smtplib
import sys
import traceback

import test_growth_outreach_api as base
from test_growth_outreach_api import FAKE, TEST_KEY, USER_A, USER_B, call, client, db, install, make_campaign

APP_PASSWORD = "app-pass-" + "word-0123456789"
FORM = {"provider": "gmail", "from_name": "Sam Lee", "from_email": "sam@acme.example", "host": "smtp.gmail.com", "port": 465,
        "security": "ssl", "username": "sam@acme.example", "reply_to": "", "daily_cap": 50, "password": APP_PASSWORD}


class Smtp:
    """A fake mail server: records the logins and the messages it was given."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.connections = []
        self.logins = []
        self.sent = []
        self.fail_login = False
        self.refuse = set()

    def factory(self, host, port, security):
        owner = self
        owner.connections.append((host, port, security))

        class Conn:
            def ehlo(self):
                pass

            def starttls(self, context=None):
                pass

            def login(self, user, password):
                owner.logins.append((user, password))
                if owner.fail_login:
                    raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

            def send_message(self, msg):
                if msg["To"] in owner.refuse:
                    raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})
                owner.sent.append(msg)

            def quit(self):
                pass

        return Conn()


SMTP = Smtp()


def setup(key=True, outreach_key=""):
    """Own mail server on, Outreach with no key unless asked (so a send that wrongly went to Outreach would fail)."""
    from cryptography.fernet import Fernet
    from tender_app import user_mailer as um
    from tender_app.blueprints import growth_bp
    install(key=outreach_key)
    SMTP.reset()
    growth_bp.MAIL_CRED_KEY = Fernet.generate_key().decode() if key else ""
    growth_bp._SMTP_FACTORY = SMTP.factory
    growth_bp._SMTP_PAUSE = 0
    if not hasattr(setup, "real_host_problem"):
        setup.real_host_problem = um.host_problem
    um.host_problem = lambda host: None  # no name lookups in a test


def cleanup():
    from tender_app import security, user_mailer as um
    from tender_app.blueprints import growth_bp
    from tender_app.db_ext import create_user_mail_accounts
    security._buckets.clear()
    growth_bp._SMTP_FACTORY = None
    growth_bp._SMTP_PAUSE = um.SEND_PAUSE_SECONDS
    if hasattr(setup, "real_host_problem"):
        um.host_problem = setup.real_host_problem
    base.cleanup()
    conn = db()
    try:
        with conn.cursor() as cur:
            create_user_mail_accounts(cur)
            cur.execute("DELETE FROM user_mail_accounts WHERE username = ANY(%s)", (base.USERS,))
        conn.commit()
    finally:
        conn.close()


def account(c):
    code, body, _ = call(c, "get", "/api/growth/email-account")
    assert code == 200, body
    return body


def save(c, **changes):
    return call(c, "put", "/api/growth/email-account", {**FORM, **changes})


def stored(user):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT password_enc, verified_at FROM user_mail_accounts WHERE username = %s", (user,))
            return cur.fetchone()
    finally:
        conn.close()


def send(c, cid, **body):
    return call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {"mailbox": "smtp", **body})


def test_without_a_server_key_an_account_cannot_be_kept_and_the_page_is_told():
    setup(key=False)
    c = client(USER_A)
    s = account(c)
    assert s["available"] is False and s["account"] is None and {p["id"] for p in s["presets"]} >= {"gmail", "outlook", "onecom", "custom"}
    one = next(p for p in s["presets"] if p["id"] == "onecom")
    assert (one["host"], one["port"], one["security"]) == ("send.one.com", 465, "ssl")
    code, body, _ = save(c)
    assert code == 409 and body["code"] == "no_key" and "MAIL_CRED_KEY" in body["error"] and SMTP.connections == []


def test_setting_up_tries_the_login_keeps_the_password_encrypted_and_never_returns_it():
    setup()
    c = client(USER_A)
    code, body, text = save(c)
    assert code == 200, body
    a = body["account"]
    assert a["from_email"] == "sam@acme.example" and a["host"] == "smtp.gmail.com" and a["port"] == 465 and a["verified"] is True
    assert a["username"] == "sam@acme.example" and a["daily_cap"] == 50 and a["sent_today"] == 0
    assert APP_PASSWORD not in text and "password" not in json.dumps(a).lower().replace("username", "")
    assert APP_PASSWORD not in json.dumps(account(c))
    assert SMTP.logins == [("sam@acme.example", APP_PASSWORD)], "the login was tried once, before it was kept"
    enc, verified = stored(USER_A)
    assert enc and APP_PASSWORD not in enc and verified is not None
    assert account(client(USER_B))["account"] is None, "another user sees nothing of it"


def test_a_login_the_mail_server_refuses_is_explained_and_nothing_is_kept():
    setup()
    SMTP.fail_login = True
    code, body, text = save(client(USER_A))
    assert code == 400 and body["code"] == "auth_failed" and "rejected the login" in body["error"] and APP_PASSWORD not in text
    assert stored(USER_A) is None


def test_a_bad_form_is_refused_before_the_mail_server_is_asked():
    setup()
    c = client(USER_A)
    for change, word in (({"from_email": "nope"}, "From email"), ({"port": 21}, "port"), ({"security": "none"}, "SSL"),
                         ({"username": ""}, "login"), ({"username": "29, 2nd Floor, Deluxe Society"}, "no spaces"), ({"daily_cap": 9999}, "daily limit"), ({"reply_to": "x"}, "reply-to"),
                         ({"port": True}, "whole number"), ({"from_name": 5}, "must be text"), ({"password": "x" * 300}, "too long")):
        code, body, _ = save(c, **change)
        assert code == 400 and word.lower() in body["error"].lower(), (change, body)
    assert SMTP.connections == [] and stored(USER_A) is None


def test_a_blank_password_keeps_the_saved_one_only_while_the_login_and_server_are_unchanged():
    setup()
    c = client(USER_A)
    assert save(c)[0] == 200
    SMTP.logins.clear()
    code, body, _ = save(c, password="", daily_cap=20, from_name="Sam")
    assert code == 200 and body["account"]["daily_cap"] == 20 and SMTP.logins == [("sam@acme.example", APP_PASSWORD)]
    for change in ({"username": "other@acme.example"}, {"host": "smtp.other.example"}):
        code, body, _ = save(c, password="", **change)
        assert code == 400 and body["code"] == "password_required", change
    code, body, _ = save(client(USER_B), password="")
    assert code == 400 and body["code"] == "password_required", "a new account always needs its password"


def test_a_test_email_goes_from_the_account_to_its_own_address():
    setup()
    c = client(USER_A)
    assert call(c, "post", "/api/growth/email-account/test", {})[0] == 409, "no account yet"
    save(c)
    SMTP.sent.clear()
    code, body, _ = call(c, "post", "/api/growth/email-account/test", {})
    assert code == 200 and body == {"sent_to": "sam@acme.example"}
    assert len(SMTP.sent) == 1 and SMTP.sent[0]["To"] == "sam@acme.example" and "test" in SMTP.sent[0]["Subject"].lower()
    SMTP.fail_login = True
    code, body, _ = call(c, "post", "/api/growth/email-account/test", {})
    assert code == 400 and "rejected the login" in body["error"]
    assert account(c)["account"]["last_error"]


def test_the_account_can_be_forgotten_and_only_for_that_user():
    setup()
    a, b = client(USER_A), client(USER_B)
    save(a)
    save(b, from_email="bo@acme.example", username="bo@acme.example")
    code, body, _ = call(a, "delete", "/api/growth/email-account")
    assert code == 200 and body["account"] is None and stored(USER_A) is None
    assert account(b)["account"]["from_email"] == "bo@acme.example"


def test_the_status_carries_the_account_for_the_panels():
    setup()
    c = client(USER_A)
    code, body, _ = call(c, "get", "/api/growth/outreach/status")
    assert code == 200 and body["mail_available"] is True and body["mail_account"] is None
    save(c)
    code, body, text = call(c, "get", "/api/growth/outreach/status")
    assert body["mail_account"]["from_email"] == "sam@acme.example" and body["mail_account"]["verified"] is True and APP_PASSWORD not in text


def test_send_mail_goes_out_through_the_account_not_outreach_and_each_buyer_is_recorded_sent():
    setup()
    c = client(USER_A)
    save(c)
    SMTP.sent.clear()
    cid, ids = make_campaign(USER_A)
    code, body, text = send(c, cid)
    assert code == 200, body
    assert body["mode"] == "smtp" and body["mailbox"] == "sam@acme.example" and body["failed"] == [] and body["stopped"] is None
    assert [x["buyer"] for x in body["sent"]] == ["Alpha Borough Council", "Beta District Council"]
    assert [s["buyer"] for s in body["skipped"]] == ["Gamma NHS Trust"]
    assert FAKE.calls == [], "Outreach is not asked at all"
    assert [m["To"] for m in SMTP.sent] == ["procurement@alpha.example", "contracts@beta.example"]
    m = SMTP.sent[0]
    assert "Sam Lee" in m["From"] and "sam@acme.example" in m["From"] and "support ahead of your contract renewal" in m["Subject"]
    assert "Alpha Borough Council" in m.get_content() and "{{" not in m.get_content() and "stop" in m.get_content()
    assert m["Message-ID"].endswith("@acme.example>") and "unsubscribe" in m["List-Unsubscribe"]
    assert APP_PASSWORD not in text
    assert base.target_statuses(c, cid) == {"Alpha Borough Council": "sent", "Beta District Council": "sent", "Gamma NHS Trust": "not_sent"}
    assert all(d["mode"] == "smtp" and d["status"] == "sent" and d["outreach_id"].startswith("<") for d in body["drafts"])
    assert account(c)["account"]["sent_today"] == 2


def test_a_buyer_is_never_sent_twice_and_the_outreach_refresh_ignores_these_sends():
    setup(outreach_key=TEST_KEY)
    c = client(USER_A)
    save(c)
    cid, ids = make_campaign(USER_A)
    assert send(c, cid)[0] == 200
    SMTP.sent.clear()
    code, body, _ = send(c, cid)
    assert code == 400 and "Already" in body["error"] and SMTP.sent == [], body
    FAKE.calls.clear()
    code, body, _ = call(c, "get", f"/api/growth/campaigns/{cid}/outreach?refresh=1")
    assert code == 200 and len(body["drafts"]) == 2 and FAKE.calls == [], "a message sent from the account has nothing to ask Outreach about"


def test_a_refused_address_does_not_stop_the_others_and_can_be_retried():
    setup()
    c = client(USER_A)
    save(c)
    cid, ids = make_campaign(USER_A)
    SMTP.refuse = {"procurement@alpha.example"}
    code, body, _ = send(c, cid)
    assert code == 200 and [x["buyer"] for x in body["sent"]] == ["Beta District Council"]
    assert [f["buyer"] for f in body["failed"]] == ["Alpha Borough Council"] and "refused" in body["failed"][0]["reason"] and body["stopped"] is None
    assert base.target_statuses(c, cid)["Alpha Borough Council"] == "not_sent"
    SMTP.refuse = set()
    code, body, _ = send(c, cid)
    assert code == 200 and [x["buyer"] for x in body["sent"]] == ["Alpha Borough Council"]


def test_a_login_that_stops_working_stops_the_send_cleanly_and_records_nothing_as_sent():
    setup()
    c = client(USER_A)
    save(c)
    cid, ids = make_campaign(USER_A)
    SMTP.fail_login = True
    code, body, text = send(c, cid)
    assert code == 200 and body["sent"] == [] and len(body["failed"]) == 2 and "rejected the login" in body["stopped"]
    assert APP_PASSWORD not in text
    assert set(base.target_statuses(c, cid).values()) == {"not_sent"}
    SMTP.fail_login = False
    code, body, _ = send(c, cid)
    assert code == 200 and len(body["sent"]) == 2, "fixed, the same send now goes"


def test_the_daily_limit_holds_across_sends():
    setup()
    c = client(USER_A)
    save(c, daily_cap=1)
    cid, ids = make_campaign(USER_A)
    code, body, _ = send(c, cid)
    assert code == 200 and len(body["sent"]) == 1 and body["deferred"] == 1 and "1 a day" in body["warnings"][0]
    SMTP.sent.clear()
    code, body, _ = send(c, cid)
    assert code == 400 and body["code"] == "daily_limit" and SMTP.sent == []


def test_without_an_account_nothing_is_sent_and_another_users_account_is_not_borrowed():
    setup()
    save(client(USER_A))
    b = client(USER_B)
    cid, _ = make_campaign(USER_B)
    SMTP.connections.clear()
    code, body, _ = send(b, cid)
    assert code == 409 and body["code"] == "no_mail_account" and SMTP.connections == [] and FAKE.calls == []


def test_a_message_that_cannot_go_is_refused_before_the_mail_server_is_asked():
    setup()
    c = client(USER_A)
    save(c)
    cid, _ = make_campaign(USER_A, body="Hello {{buyer_contact}}, a short message with nothing else.")
    SMTP.connections.clear()
    code, body, _ = send(c, cid)
    assert code == 400 and "opt out" in body["error"] and SMTP.connections == [], body


def test_choosing_outreach_as_before_still_goes_through_outreach():
    setup(outreach_key=TEST_KEY)
    c = client(USER_A)
    save(c)
    cid, _ = make_campaign(USER_A)
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 200 and body["mode"] == "test" and len(FAKE.posts()) == 2 and SMTP.sent == []


def test_a_request_without_the_csrf_token_is_refused():
    setup()
    c = client(USER_A)
    assert call(c, "put", "/api/growth/email-account", FORM, headers={})[0] in (400, 403)
    assert call(c, "delete", "/api/growth/email-account", headers={})[0] in (400, 403)
    assert call(c, "post", "/api/growth/email-account/default", {"id": 1}, headers={})[0] in (400, 403)


# ── several accounts ──────────────────────────────────────────────────────────────────────────

def second(**changes):
    return {"from_name": "Bids Team", "from_email": "bids@acme.example", "username": "bids@acme.example", **changes}


def ids_of(state):
    return [a["id"] for a in state["accounts"]]


def test_a_user_can_connect_several_accounts_the_first_is_the_default_and_the_status_lists_them_all():
    setup()
    c = client(USER_A)
    code, body, _ = save(c)
    assert code == 200 and [a["is_default"] for a in body["accounts"]] == [True] and body["max_accounts"] == 5
    code, body, text = save(c, **second())
    assert code == 200 and APP_PASSWORD not in text
    assert [a["from_email"] for a in body["accounts"]] == ["sam@acme.example", "bids@acme.example"]
    assert [a["is_default"] for a in body["accounts"]] == [True, False] and body["account"]["from_email"] == "sam@acme.example"
    assert SMTP.logins == [("sam@acme.example", APP_PASSWORD), ("bids@acme.example", APP_PASSWORD)], "each login is tried before it is kept"
    code, status, _ = call(c, "get", "/api/growth/outreach/status")
    assert [a["from_email"] for a in status["mail_accounts"]] == ["sam@acme.example", "bids@acme.example"]
    assert status["mail_account"]["from_email"] == "sam@acme.example"
    assert account(client(USER_B))["accounts"] == [], "another user sees none of them"


def test_adding_an_address_that_is_already_connected_replaces_it_and_a_limit_stops_a_sixth_account():
    setup()
    c = client(USER_A)
    save(c)
    code, body, _ = save(c, from_email="SAM@acme.example", daily_cap=10)
    assert code == 200 and len(body["accounts"]) == 1 and body["accounts"][0]["daily_cap"] == 10, "same address, any case: one account"
    for n in range(2, 6):
        assert save(c, **second(from_email=f"box{n}@acme.example", username=f"box{n}@acme.example"))[0] == 200
    SMTP.connections.clear()
    code, body, _ = save(c, **second(from_email="box6@acme.example", username="box6@acme.example"))
    assert code == 409 and body["code"] == "too_many_accounts" and "up to 5" in body["error"]
    assert len(account(c)["accounts"]) == 5
    assert save(c, from_email="box3@acme.example", daily_cap=7)[0] == 200, "an account already there can still be changed at the limit"


def test_an_account_is_changed_by_its_id_and_a_taken_address_or_a_stranger_s_id_is_refused():
    setup()
    a, b = client(USER_A), client(USER_B)
    save(a)
    first, other = ids_of(save(a, **second())[1])
    code, body, _ = save(a, id=other, password="", from_email="bids@acme.example", username="bids@acme.example", daily_cap=33, from_name="Bids")
    assert code == 200 and [x["daily_cap"] for x in body["accounts"] if x["id"] == other] == [33] and len(body["accounts"]) == 2
    code, body, _ = save(a, id=other, from_email="sam@acme.example", username="sam@acme.example")
    assert code == 409 and body["code"] == "duplicate_account", "that address belongs to the other account"
    code, body, _ = save(b, id=first, **second(from_email="bo@acme.example", username="bo@acme.example"))
    assert code == 404 and body["code"] == "no_mail_account", "someone else's account is not there for this user"
    assert [x["from_email"] for x in account(a)["accounts"]] == ["sam@acme.example", "bids@acme.example"] and account(b)["accounts"] == []
    assert save(a, id="abc")[0] == 400 and save(a, id=True)[0] == 400


def test_the_default_can_be_changed_and_when_it_is_forgotten_the_oldest_other_account_takes_over():
    setup()
    a, b = client(USER_A), client(USER_B)
    save(a)
    first, second_id = ids_of(save(a, **second())[1])
    third = ids_of(save(a, **second(from_email="ops@acme.example", username="ops@acme.example"))[1])[2]
    code, body, _ = call(a, "post", "/api/growth/email-account/default", {"id": third})
    assert code == 200 and body["account"]["id"] == third and [x["id"] for x in body["accounts"]][0] == third
    assert [x["id"] for x in body["accounts"] if x["is_default"]] == [third]
    assert call(a, "post", "/api/growth/email-account/default", {})[0] == 400
    assert call(b, "post", "/api/growth/email-account/default", {"id": first})[0] == 404, "not another user's account"
    assert account(a)["account"]["id"] == third
    code, body, _ = call(a, "delete", f"/api/growth/email-account?id={third}")
    assert code == 200 and ids_of(body) == [first, second_id] and body["account"]["id"] == first and body["accounts"][0]["is_default"]
    code, body, _ = call(a, "delete", f"/api/growth/email-account?id={second_id}")
    assert ids_of(body) == [first] and body["accounts"][0]["is_default"], "a default that is not the one removed stays"
    assert call(b, "delete", f"/api/growth/email-account?id={first}")[0] == 200 and ids_of(account(a)) == [first], "B cannot remove A's account"
    assert call(a, "delete", "/api/growth/email-account?id=abc")[0] == 400
    code, body, _ = call(a, "delete", "/api/growth/email-account")
    assert body["accounts"] == [] and body["account"] is None, "no id: the default account, as before"


def test_a_test_email_goes_from_the_account_asked_for():
    setup()
    c = client(USER_A)
    save(c)
    first, other = ids_of(save(c, **second())[1])
    SMTP.sent.clear()
    code, body, _ = call(c, "post", "/api/growth/email-account/test", {"id": other})
    assert code == 200 and body == {"sent_to": "bids@acme.example"} and SMTP.sent[0]["To"] == "bids@acme.example"
    assert call(c, "post", "/api/growth/email-account/test", {})[1] == {"sent_to": "sam@acme.example"}, "no id: the default account"
    assert call(client(USER_B), "post", "/api/growth/email-account/test", {"id": other})[0] == 409
    SMTP.fail_login = True
    assert call(c, "post", "/api/growth/email-account/test", {"id": other})[0] == 400
    by_id = {x["id"]: x for x in account(c)["accounts"]}
    assert by_id[other]["last_error"] and not by_id[first]["last_error"], "the failure is remembered against the account that failed"


def test_send_mail_goes_out_through_the_chosen_account_and_each_account_has_its_own_daily_limit():
    setup()
    c = client(USER_A)
    save(c, daily_cap=1)
    first, other = ids_of(save(c, **second(daily_cap=50))[1])
    cid, _ = make_campaign(USER_A)
    SMTP.sent.clear()
    code, body, _ = send(c, cid, mailbox="smtp")
    assert code == 200 and body["mailbox"] == "sam@acme.example" and len(body["sent"]) == 1 and body["deferred"] == 1, "no id: the default account"
    assert "sam@acme.example" in SMTP.sent[0]["From"]
    code, body, _ = send(c, cid, mailbox="smtp")
    assert code == 400 and body["code"] == "daily_limit", "the default account's own limit is used up"
    SMTP.sent.clear()
    code, body, _ = send(c, cid, mailbox=f"smtp:{other}")
    assert code == 200 and body["mailbox"] == "bids@acme.example" and len(body["sent"]) == 1 and FAKE.calls == []
    assert "Bids Team" in SMTP.sent[0]["From"] and "bids@acme.example" in SMTP.sent[0]["From"]
    by_id = {x["id"]: x for x in account(c)["accounts"]}
    assert (by_id[first]["sent_today"], by_id[other]["sent_today"]) == (1, 1), "each send counts against the account it went from"
    assert base.target_statuses(c, cid)["Alpha Borough Council"] == "sent" and base.target_statuses(c, cid)["Beta District Council"] == "sent"


def test_a_send_names_an_account_the_user_owns_and_a_malformed_choice_is_refused():
    setup()
    a, b = client(USER_A), client(USER_B)
    save(a)
    mine = ids_of(account(a))[0]
    cid, _ = make_campaign(USER_B)
    SMTP.connections.clear()
    code, body, _ = send(b, cid, mailbox=f"smtp:{mine}")
    assert code == 409 and body["code"] == "no_mail_account" and SMTP.connections == [], "not another user's account"
    for bad in ("smtp:abc", "smtp:", "smtp:-1", "smtp:1;2", "smtp:99999999999"):
        code, body, _ = send(b, cid, mailbox=bad)
        assert code == 400, (bad, body)
    code, body, _ = send(a, make_campaign(USER_A)[0], mailbox="smtp:999999")
    assert code == 409 and body["code"] == "no_mail_account", "an account that is gone is not swapped for the default"
    assert SMTP.connections == [] and FAKE.calls == []


def test_sends_recorded_before_accounts_were_told_apart_count_against_the_default_account():
    setup()
    c = client(USER_A)
    save(c)
    other = ids_of(save(c, **second())[1])[1]
    cid, _ = make_campaign(USER_A)
    assert send(c, cid)[0] == 200
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE growth_outreach_drafts SET mail_account_id = NULL WHERE username = %s AND mode = 'smtp'", (USER_A,))
        conn.commit()
    finally:
        conn.close()
    by_id = {x["id"]: x["sent_today"] for x in account(c)["accounts"]}
    assert by_id[ids_of(account(c))[0]] == 2 and by_id[other] == 0


def test_a_table_made_when_one_account_was_allowed_is_upgraded_keeping_each_users_account_as_their_default():
    from tender_app.db_ext import create_user_mail_accounts
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS mail_upgrade_test CASCADE")
            cur.execute("CREATE SCHEMA mail_upgrade_test")
            cur.execute("SET search_path TO mail_upgrade_test")  # the real table is never touched
            cur.execute("""CREATE TABLE user_mail_accounts (username VARCHAR(255) PRIMARY KEY, provider VARCHAR(16) NOT NULL DEFAULT 'custom',
                           host VARCHAR(253) NOT NULL, port INTEGER NOT NULL, security VARCHAR(10) NOT NULL, login VARCHAR(254) NOT NULL,
                           password_enc TEXT NOT NULL, from_email VARCHAR(254) NOT NULL, from_name VARCHAR(120), reply_to VARCHAR(254),
                           daily_cap INTEGER NOT NULL DEFAULT 100, verified_at TIMESTAMP, last_error TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            for user in ("u1", "u2"):
                cur.execute("INSERT INTO user_mail_accounts (username, host, port, security, login, password_enc, from_email) "
                            "VALUES (%s, 'smtp.x.example', 465, 'ssl', %s, 'enc', %s)", (user, f"{user}@x.example", f"{user}@x.example"))
            create_user_mail_accounts(cur)
            create_user_mail_accounts(cur)  # running it again changes nothing
            cur.execute("SELECT username, id IS NOT NULL, is_default FROM user_mail_accounts ORDER BY username")
            assert cur.fetchall() == [("u1", True, True), ("u2", True, True)]
            cur.execute("INSERT INTO user_mail_accounts (username, host, port, security, login, password_enc, from_email) "
                        "VALUES ('u1', 'smtp.x.example', 465, 'ssl', 'b', 'enc', 'b@x.example')")
            cur.execute("SELECT COUNT(*) FROM user_mail_accounts WHERE username = 'u1'")
            assert cur.fetchone()[0] == 2, "a user can now have two"
            try:
                cur.execute("INSERT INTO user_mail_accounts (username, host, port, security, login, password_enc, from_email) "
                            "VALUES ('u1', 'smtp.x.example', 465, 'ssl', 'b', 'enc', 'B@X.example')")
                raise AssertionError("the same address twice for one user should be refused")
            except Exception as exc:
                assert "user_mail_accounts_user_from" in str(exc), exc
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("SET search_path TO public")
            cur.execute("DROP SCHEMA IF EXISTS mail_upgrade_test CASCADE")
        conn.commit()
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
