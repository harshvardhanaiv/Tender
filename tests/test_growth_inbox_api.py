"""Run: python tests/test_growth_inbox_api.py   (plain asserts; needs the dev database, skips itself without one)

The Growth Studio inbox routes (replies to emails sent through Outreach), through the real Flask app and its test
client, as throwaway users, with the fake Outreach of test_growth_outreach_api.py (no network, no real key).

  * a user sees only their own emails although the Outreach key, and so its list, is shared by everyone,
  * a campaign email to an address another TenderFlow user also emailed is not shown to either (whose it is cannot be told),
  * a reply marks the buyer replied (once, and never over a meeting or an opt-out),
  * opening a reply marks it read, and shows the reply, who to answer, and whether it asks to stop.
"""
import json
import sys
import traceback
from urllib.parse import parse_qs, urlparse

import test_growth_outreach_api as base
from test_growth_outreach_api import FAKE, USER_A, USER_B, call, client, db, install, make_campaign

OWN_MSG = "msg_own0001"
CAMP_ID = "cp_own0001"


def cleanup():
    base.cleanup()
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM growth_inbox_seen WHERE username = ANY(%s)", (base.USERS,))
            cur.execute("CREATE TABLE IF NOT EXISTS growth_inbox_hidden (username VARCHAR(255) NOT NULL, message_id VARCHAR(64) NOT NULL, hidden_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (username, message_id))")
            cur.execute("DELETE FROM growth_inbox_hidden WHERE username = ANY(%s)", (base.USERS,))
        conn.commit()
    finally:
        conn.close()


def sent_to(user, buyer, email, outreach_id, approval, status="sent"):
    """A campaign with one buyer recorded as sent, and the Outreach draft row that says how it went out."""
    cid, ids = make_campaign(user, targets=[(buyer, email, status, True)])
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO growth_outreach_drafts (username, campaign_id, target_id, outreach_id, mode, status, approval)
                   VALUES (%s, %s, %s, %s, 'test', 'sent', %s)""", (user, cid, ids[0], outreach_id, approval))
        conn.commit()
    finally:
        conn.close()
    return cid, ids[0]


def msg(mid, to, status="replied", kind=None, subject="Gas servicing"):
    return {"id": mid, "to": to, "subject": subject, "status": status, "sent_at": "2026-11-03T10:30:00Z", "mailbox": "sales@x.example", "kind": kind}


def outreach(pages=None, detail=None):
    """pages: {cursor or None: (messages, next cursor)}; detail: {id: answer}."""
    pages = pages or {None: ([], None)}

    def h(method, url, headers, data):
        u = urlparse(url)
        if method == "GET" and u.path.endswith("/v1/email/messages"):
            q = parse_qs(u.query)
            items, nxt = pages.get((q.get("cursor") or [None])[0], ([], None))
            return 200, {"data": items, "next_cursor": nxt}
        if method == "GET" and "/v1/email/messages/" in u.path:
            mid = u.path.rsplit("/", 1)[1]
            if mid in (detail or {}):
                return 200, detail[mid]
            return 404, {"error": {"code": "not_found", "message": "No such email."}}
        return 404, {"error": {"code": "not_found", "message": "unknown"}}
    return h


def use(**kw):
    install()
    FAKE.handler = outreach(**kw)


def listed(c, query="?filter=replies"):
    code, body, _ = call(c, "get", "/api/growth/inbox" + query)
    assert code == 200, body
    return body


def test_a_user_sees_only_their_own_replies_from_the_shared_list():
    sent_to(USER_A, "Alpha Council", "contracts@alpha.example", OWN_MSG, "sent_now")
    sent_to(USER_A, "Beta Council", "procurement@beta.example", CAMP_ID, "approved")
    use(pages={None: ([msg(OWN_MSG, "contracts@alpha.example", kind="one_to_one"),
                       msg("m_camp", "procurement@beta.example", kind="campaign"),
                       msg("m_other", "someone@else.example", kind="campaign"),
                       msg("m_other_one", "contracts@alpha.example", kind="one_to_one")], None)})
    body = listed(client(USER_A))
    got = {m["id"]: m for m in body["items"]}
    assert set(got) == {OWN_MSG, "m_camp"}, set(got)
    assert got[OWN_MSG]["buyer"] == "Alpha Council" and got["m_camp"]["buyer"] == "Beta Council"
    assert got["m_camp"]["campaign_name"] and got[OWN_MSG]["unread"] is True and body["unread"] == 2
    assert listed(client(USER_B))["items"] == []


def test_the_filter_is_sent_to_outreach_and_a_bad_one_is_refused():
    use()
    c = client(USER_A)
    for name, status in (("replies", "replied"), ("bounced", "bounced"), ("sent", "sent")):
        FAKE.calls.clear()
        listed(c, f"?filter={name}")
        assert f"status={status}" in FAKE.calls[0]["url"], FAKE.calls[0]["url"]
    FAKE.calls.clear()
    listed(c, "?filter=all&q=alpha")
    assert "status=" not in FAKE.calls[0]["url"] and "q=alpha" in FAKE.calls[0]["url"]
    assert call(c, "get", "/api/growth/inbox?filter=everything")[0] == 400
    assert call(c, "get", "/api/growth/inbox?cursor=bad%20cursor")[0] in (400, 502)


def test_later_pages_are_read_until_the_users_own_emails_are_found():
    sent_to(USER_A, "Alpha Council", "contracts@alpha.example", OWN_MSG, "sent_now")
    use(pages={None: ([msg("m1", "x@else.example")], "c2"), "c2": ([msg("m2", "y@else.example")], "c3"),
               "c3": ([msg(OWN_MSG, "contracts@alpha.example", kind="one_to_one")], None)})
    body = listed(client(USER_A))
    assert [m["id"] for m in body["items"]] == [OWN_MSG] and body["next_cursor"] is None
    cursors = [parse_qs(urlparse(c["url"]).query).get("cursor") for c in FAKE.calls]
    assert cursors == [None, ["c2"], ["c3"]], cursors


def test_a_campaign_email_to_an_address_two_users_emailed_is_shown_to_neither():
    sent_to(USER_A, "Shared Council", "shared@council.example", CAMP_ID, "approved")
    sent_to(USER_B, "Shared Council", "shared@council.example", "cp_other01", "approved")
    sent_to(USER_A, "Alpha Council", "contracts@alpha.example", OWN_MSG, "sent_now")
    use(pages={None: ([msg("m_shared", "shared@council.example", kind="campaign"), msg(OWN_MSG, "contracts@alpha.example", kind="one_to_one")], None)})
    assert [m["id"] for m in listed(client(USER_A))["items"]] == [OWN_MSG]
    assert listed(client(USER_B))["items"] == []


def test_a_reply_marks_the_buyer_replied_once_and_never_over_a_meeting_or_an_opt_out():
    ca, ta = sent_to(USER_A, "Alpha Council", "contracts@alpha.example", OWN_MSG, "sent_now")
    cb, tb = sent_to(USER_A, "Beta Council", "procurement@beta.example", CAMP_ID, "approved", status="meeting")
    cc, tc = sent_to(USER_A, "Gamma Council", "legal@gamma.example", "cp_own0003", "approved", status="opted_out")
    use(pages={None: ([msg(OWN_MSG, "contracts@alpha.example", kind="one_to_one"), msg("m2", "procurement@beta.example"), msg("m3", "legal@gamma.example")], None)})
    c = client(USER_A)
    body = listed(c)
    assert body["marked"] == ["Alpha Council"], body["marked"]
    assert base.target_statuses(c, ca) == {"Alpha Council": "replied"}
    assert base.target_statuses(c, cb) == {"Beta Council": "meeting"} and base.target_statuses(c, cc) == {"Gamma Council": "opted_out"}
    assert listed(c)["marked"] == [], "a second look changes nothing"
    code, perf, _ = call(c, "get", "/api/growth/performance?days=all")
    assert perf["tiles"]["replied"] >= 1


def test_sent_and_bounced_emails_do_not_mark_anything():
    ca, _ = sent_to(USER_A, "Alpha Council", "contracts@alpha.example", OWN_MSG, "sent_now")
    use(pages={None: ([msg(OWN_MSG, "contracts@alpha.example", status="sent", kind="one_to_one")], None)})
    c = client(USER_A)
    assert listed(c, "?filter=sent")["marked"] == [] and base.target_statuses(c, ca) == {"Alpha Council": "sent"}


def test_opening_a_reply_shows_it_marks_it_read_and_says_who_to_answer():
    sent_to(USER_A, "Alpha Council", "contracts@alpha.example", OWN_MSG, "sent_now")
    detail = {OWN_MSG: {"id": OWN_MSG, "to": "contracts@alpha.example", "subject": "Gas servicing", "status": "replied", "kind": "one_to_one",
                        "body": "<p>Hello Alpha</p>", "reply": {"from": "Sam@Alpha.example", "received_at": "2026-11-04T09:00:00Z",
                                                                 "body": "Thanks. Please stop emailing us."}}}
    use(pages={None: ([msg(OWN_MSG, "contracts@alpha.example", kind="one_to_one")], None)}, detail=detail)
    c = client(USER_A)
    assert listed(c)["items"][0]["unread"] is True
    code, body, _ = call(c, "get", f"/api/growth/inbox/{OWN_MSG}")
    assert code == 200, body
    assert body["message"]["replies"][0]["text"] == "Thanks. Please stop emailing us." and body["message"]["original"] == "Hello Alpha"
    assert body["reply_to"] == "Sam@Alpha.example" and body["opt_out_hint"] is True and body["buyer"] == "Alpha Council"
    assert body["target_id"] and body["campaign"]["name"]
    again = listed(c)
    assert again["items"][0]["unread"] is False and again["unread"] == 0


def test_a_reply_that_is_not_an_opt_out_has_no_hint_and_a_bounce_has_its_reason():
    sent_to(USER_A, "Alpha Council", "contracts@alpha.example", OWN_MSG, "sent_now")
    sent_to(USER_A, "Beta Council", "procurement@beta.example", "msg_own0002", "sent_now")
    use(detail={OWN_MSG: {"id": OWN_MSG, "to": "contracts@alpha.example", "status": "replied", "reply": "Yes, call us Monday."},
                "msg_own0002": {"id": "msg_own0002", "to": "procurement@beta.example", "status": "bounced", "bounce_reason": "Mailbox full"}})
    c = client(USER_A)
    one = call(c, "get", f"/api/growth/inbox/{OWN_MSG}")[1]
    assert one["opt_out_hint"] is False and one["reply_to"] == "contracts@alpha.example"
    two = call(c, "get", "/api/growth/inbox/msg_own0002")[1]
    assert two["message"]["bounce_reason"] == "Mailbox full" and two["message"]["replies"] == [] and two["opt_out_hint"] is False


def test_another_users_message_is_not_found_and_odd_ids_are_refused():
    sent_to(USER_A, "Alpha Council", "contracts@alpha.example", OWN_MSG, "sent_now")
    use(detail={OWN_MSG: {"id": OWN_MSG, "to": "contracts@alpha.example", "status": "replied", "kind": "one_to_one", "reply": "Hi"},
                "m_foreign": {"id": "m_foreign", "to": "x@else.example", "status": "replied", "kind": "one_to_one", "reply": "secret"}})
    b = client(USER_B)
    assert call(b, "get", f"/api/growth/inbox/{OWN_MSG}")[0] == 404
    a = client(USER_A)
    code, body, text = call(a, "get", "/api/growth/inbox/m_foreign")
    assert code == 404 and "secret" not in text
    assert call(a, "get", "/api/growth/inbox/no_such_id")[0] == 404
    assert call(a, "get", "/api/growth/inbox/bad%20id!")[0] in (400, 404)


def _own_reply_detail():
    return {OWN_MSG: {"id": OWN_MSG, "to": "contracts@alpha.example", "subject": "Gas servicing", "status": "replied", "kind": "one_to_one",
                      "reply": {"from": "sam@alpha.example", "body": "Thanks"}}}


def test_a_deleted_email_leaves_the_users_list_but_outreach_is_never_asked_to_erase_it():
    sent_to(USER_A, "Alpha Council", "contracts@alpha.example", OWN_MSG, "sent_now")
    sent_to(USER_A, "Beta Council", "procurement@beta.example", "msg_own0002", "sent_now")
    use(pages={None: ([msg(OWN_MSG, "contracts@alpha.example", kind="one_to_one"), msg("msg_own0002", "procurement@beta.example", kind="one_to_one")], None)},
        detail=_own_reply_detail())
    c = client(USER_A)
    assert {m["id"] for m in listed(c)["items"]} == {OWN_MSG, "msg_own0002"}
    FAKE.calls.clear()
    code, body, _ = call(c, "delete", f"/api/growth/inbox/{OWN_MSG}")
    assert code == 200 and body == {"deleted": OWN_MSG}
    assert [x["method"] for x in FAKE.calls] == ["GET"], "nothing is sent to Outreach that could erase anything"
    assert {m["id"] for m in listed(c)["items"]} == {"msg_own0002"}
    assert {m["id"] for m in listed(c, "?filter=all")["items"]} == {"msg_own0002"}, "gone from every filter"
    assert call(c, "delete", f"/api/growth/inbox/{OWN_MSG}")[0] == 200, "deleting twice is harmless"


def test_a_delete_can_be_undone():
    sent_to(USER_A, "Alpha Council", "contracts@alpha.example", OWN_MSG, "sent_now")
    use(pages={None: ([msg(OWN_MSG, "contracts@alpha.example", kind="one_to_one")], None)}, detail=_own_reply_detail())
    c = client(USER_A)
    call(c, "delete", f"/api/growth/inbox/{OWN_MSG}")
    assert listed(c)["items"] == []
    code, body, _ = call(c, "post", f"/api/growth/inbox/{OWN_MSG}/restore", {})
    assert code == 200 and body == {"restored": OWN_MSG}
    assert [m["id"] for m in listed(c)["items"]] == [OWN_MSG]
    assert call(c, "post", f"/api/growth/inbox/{OWN_MSG}/restore", {})[0] == 200


def test_deleting_is_for_this_user_only_and_only_for_their_own_emails():
    sent_to(USER_A, "Alpha Council", "contracts@alpha.example", OWN_MSG, "sent_now")
    use(pages={None: ([msg(OWN_MSG, "contracts@alpha.example", kind="one_to_one")], None)},
        detail={**_own_reply_detail(), "m_foreign": {"id": "m_foreign", "to": "x@else.example", "status": "replied", "kind": "one_to_one", "reply": "secret"}})
    a, b = client(USER_A), client(USER_B)
    assert call(b, "delete", f"/api/growth/inbox/{OWN_MSG}")[0] == 404, "another user cannot hide it"
    assert call(a, "delete", "/api/growth/inbox/m_foreign")[0] == 404, "an email that is not theirs cannot be hidden either"
    assert call(a, "delete", "/api/growth/inbox/no_such_id")[0] == 404
    assert call(a, "delete", "/api/growth/inbox/bad%20id!")[0] in (400, 404)
    assert [m["id"] for m in listed(a)["items"]] == [OWN_MSG]
    # A's delete does not touch what B (or anyone) is shown
    sent_to(USER_B, "Alpha Council", "contracts@alpha.example", "msg_b0001", "sent_now")
    call(a, "delete", f"/api/growth/inbox/{OWN_MSG}")
    assert listed(a)["items"] == []


def test_deleting_needs_the_csrf_token_and_a_connected_outreach():
    use(detail=_own_reply_detail())
    c = client(USER_A)
    r = c.delete(f"/api/growth/inbox/{OWN_MSG}")  # no CSRF header
    assert r.status_code in (400, 403)
    install(key="")
    assert call(client(USER_A), "delete", f"/api/growth/inbox/{OWN_MSG}")[0] == 409


def test_not_connected_is_a_409_and_no_key_appears_in_an_answer():
    install(key="")
    assert call(client(USER_A), "get", "/api/growth/inbox")[0] == 409 and FAKE.calls == []
    sent_to(USER_A, "Alpha Council", "contracts@alpha.example", OWN_MSG, "sent_now")
    use(pages={None: ([msg(OWN_MSG, "contracts@alpha.example", kind="one_to_one")], None)})
    assert base.TEST_KEY not in call(client(USER_A), "get", "/api/growth/inbox")[2]


def test_an_outreach_refusal_is_shown_as_a_clear_error():
    use()
    FAKE.handler = lambda m, u, h, d: (403, {"error": {"code": "missing_scope", "message": "The key needs email:write."}})
    code, body, _ = call(client(USER_A), "get", "/api/growth/inbox")
    assert code == 403 and "email:write" in body["error"]


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
