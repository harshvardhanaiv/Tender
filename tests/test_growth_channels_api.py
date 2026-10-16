"""Run: python tests/test_growth_channels_api.py   (plain asserts; needs the dev database, skips itself without one)

The Growth Studio -> Outreach social post and phone call routes, through the real Flask app and its test client, as
throwaway users (zz_outreach_test_*). Outreach is the fake transport of test_growth_outreach_api.py: this file never
touches the network, never uses a real key, posts nowhere and phones nobody.

  * posts: written (never approved by creating), refused when the prompt / channel / time is wrong, a double click is
    one request, a refresh reads the state, approving needs a confirmation and a post that is ready,
  * calls: refused unless both confirmations were given, refused for mobiles, do-not-contact buyers, a repeat within
    24 hours and a line with no phone number; one number per request; ended; one user never sees another's.
"""
import json
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone

import test_growth_outreach_api as base
from test_growth_outreach_api import FAKE, H, USER_A, USER_B, call, client, db, install

NUM = "020 7946 0000"
GOOD_PROMPT = "Announce our new Leeds office opening on 1 November"


def attempt():
    return uuid.uuid4().hex[:16]


def wallet(**connected):
    return {"credits": 40, "livemode": False, "connected": {"mailboxes": [], **connected}}


def handler(*, social=("linkedin", "facebook"), phones=1, post_status="writing", call_status="ringing", extra=None):
    """Answers wallets, posts and calls; `extra` may answer first (a function returning None to pass)."""
    state = {"post_status": post_status, "call_status": call_status}

    def h(method, url, headers, data):
        if extra:
            got = extra(method, url, headers, data)
            if got is not None:
                return got
        if url.endswith("/v1/wallets"):
            return 200, wallet(social_channels=[{"platform": s} for s in social], phone_numbers=[{"n": i} for i in range(phones)])
        if method == "POST" and url.endswith("/v1/social/posts"):
            return 200, {"id": "v2_fake0001", "status": "writing", "channels": json.loads(data)["channels"],
                         "preview_url": "https://outreach.example/api/p/abc"}
        if method == "GET" and "/v1/social/posts/" in url:
            return 200, {"id": "v2_fake0001", "status": state["post_status"], "caption": "Our new Leeds office opens 1 November",
                         "preview_url": "https://outreach.example/api/p/abc"}
        if method == "POST" and url.endswith("/decision") and "/social/posts/" in url:
            d = json.loads(data)["decision"]
            return 200, {"id": "v2_fake0001", "status": {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[d]}
        if method == "POST" and url.endswith("/v1/voice/calls"):
            return 200, {"id": "call_fake0001", "status": "ringing", "to": json.loads(data)["to"],
                         "live_url": "https://outreach.example/api/p/call", "capabilities": {"end": True}}
        if method == "GET" and "/v1/voice/calls/" in url:
            return 200, {"id": "call_fake0001", "status": state["call_status"], "outcome": "meeting_booked", "duration_seconds": 61,
                         "meeting_booked": True, "answers": {"system": "Acuity"}, "transcript": [{"who": "ai", "text": "Hello"}]}
        if method == "DELETE" and "/v1/voice/calls/" in url:
            return 200, {"id": "call_fake0001", "status": "ended"}
        if method == "GET" and url.endswith("/v1/voice/scripts"):
            return 200, {"scripts": [{"id": "script_intro", "name": "Intro"}]}
        return 404, {"error": {"code": "not_found", "message": "unknown"}}
    h.state = state
    return h


def use(**kw):
    install()
    from tender_app import security
    security._buckets.clear()  # the create / call routes are rate limited per minute; each test starts fresh
    FAKE.handler = handler(**kw)


def cleanup():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM growth_social_posts WHERE username = ANY(%s)", (base.USERS,))
            cur.execute("DELETE FROM growth_voice_calls WHERE username = ANY(%s)", (base.USERS,))
            cur.execute("DELETE FROM growth_suppressions WHERE username = ANY(%s)", (base.USERS,))
        conn.commit()
    finally:
        conn.close()


def only(method, suffix):
    return [c for c in FAKE.calls if c["method"] == method and c["url"].endswith(suffix)]


def make_post(c, **over):
    body = {"prompt": GOOD_PROMPT, "channels": ["linkedin"], "attempt": attempt(), **over}
    return call(c, "post", "/api/growth/social/posts", body)


def make_call(c, **over):
    body = {"buyer": "Alpha Borough Council", "number": NUM, "goal": "Ask who handles gas servicing", "business_line": True,
            "tps_checked": True, "confirm": True, "attempt": attempt(), **over}
    return call(c, "post", "/api/growth/voice/calls", body)


# ───────────────────────────────────────────── social posts ──────────────────────────────────────────

def test_a_post_is_written_and_waits_for_approval():
    use()
    c = client(USER_A)
    code, body, _ = make_post(c, publish_at=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                              picture={"instruction": "A bright modern office"})
    assert code == 200, body
    assert body["post"]["status"] == "writing" and body["post"]["channels"] == ["linkedin"] and body["post"]["id"]
    sent = only("POST", "/v1/social/posts")[0]
    assert sent["body"]["prompt"] == GOOD_PROMPT and sent["body"]["channels"] == ["linkedin"]
    assert sent["body"]["image"] == {"mode": "ai", "instruction": "A bright modern office"}
    assert sent["body"]["publish_at"].endswith("Z") and sent["headers"]["Idempotency-Key"].startswith("tf-post-")
    assert only("POST", "/decision") == [], "creating a post must never approve it"
    assert call(c, "get", "/api/growth/social/posts")[1]["posts"][0]["id"] == body["post"]["id"]


def test_a_bad_post_is_refused_before_it_is_made():
    use()
    c = client(USER_A)
    for over, word in (({"prompt": "short"}, "at least a sentence"), ({"channels": []}, "at least one channel"),
                       ({"channels": ["instagram"]}, "Not connected in Outreach"),
                       ({"publish_at": "2020-01-01T09:00:00Z"}, "at least 5 minutes"), ({"publish_at": "tomorrow"}, "valid date")):
        code, body, _ = make_post(c, **over)
        assert code == 400 and word in body["error"], (over, body)
    code, body, _ = call(c, "post", "/api/growth/social/posts", {"prompt": GOOD_PROMPT, "channels": ["linkedin"]})
    assert code == 400 and "attempt token" in body["error"]
    assert only("POST", "/v1/social/posts") == []


def test_no_connected_social_channel_is_said_plainly():
    use(social=())
    code, body, _ = make_post(client(USER_A))
    assert code == 409 and "No social channel is connected" in body["error"] and only("POST", "/v1/social/posts") == []


def test_a_double_click_is_the_same_request_to_outreach():
    use()
    c = client(USER_A)
    token = attempt()
    make_post(c, attempt=token)
    make_post(c, attempt=token)
    keys = {x["headers"]["Idempotency-Key"] for x in only("POST", "/v1/social/posts")}
    assert len(keys) == 1, keys


def test_a_failed_post_is_not_recorded_and_the_reason_is_shown():
    use(extra=lambda m, u, h, d: (402, {"error": {"code": "insufficient_credits", "message": "Not enough credits."}})
        if m == "POST" and u.endswith("/v1/social/posts") else None)
    c = client(USER_A)
    code, body, _ = make_post(c)
    assert code == 402 and "Not enough credits" in body["error"]
    assert call(c, "get", "/api/growth/social/posts")[1]["posts"] == []


def test_a_refresh_reads_the_state_and_the_caption():
    use()
    c = client(USER_A)
    make_post(c)
    FAKE.handler.state["post_status"] = "needs_approval"
    code, body, _ = call(c, "get", "/api/growth/social/posts?refresh=1")
    assert code == 200 and body["refreshed"] is True
    p = body["posts"][0]
    assert p["status"] == "needs_approval" and p["caption"].startswith("Our new Leeds office")
    assert call(c, "get", "/api/growth/social/posts")[1]["posts"][0]["status"] == "needs_approval"


def test_approving_needs_a_confirmation_and_a_post_that_is_ready():
    use()
    c = client(USER_A)
    pid = make_post(c)[1]["post"]["id"]
    url = f"/api/growth/social/posts/{pid}/decision"
    code, body, _ = call(c, "post", url, {"decision": "approve", "attempt": attempt()})
    assert code == 400 and "Confirm" in body["error"] and only("POST", "/decision") == []
    code, body, _ = call(c, "post", url, {"decision": "approve", "confirm": True, "attempt": attempt()})
    assert code == 409 and "still writing" in body["error"] and only("POST", "/decision") == [], body
    FAKE.handler.state["post_status"] = "needs_approval"
    code, body, _ = call(c, "post", url, {"decision": "approve", "confirm": True, "attempt": attempt()})
    assert code == 200 and body["post"]["status"] == "approved" and body["post"]["decided_at"], body
    assert len(only("POST", "/decision")) == 1
    FAKE.handler.state["post_status"] = "published"
    code, body, _ = call(c, "post", url, {"decision": "cancel", "attempt": attempt()})
    assert code == 409 and "already published" in body["error"]
    assert call(c, "post", url, {"decision": "bogus", "attempt": attempt()})[0] == 400


def test_a_post_can_be_rejected_without_a_confirmation():
    use(post_status="needs_approval")
    c = client(USER_A)
    pid = make_post(c)[1]["post"]["id"]
    code, body, _ = call(c, "post", f"/api/growth/social/posts/{pid}/decision", {"decision": "reject", "attempt": attempt()})
    assert code == 200 and body["post"]["status"] == "rejected"


def test_a_missing_approve_permission_is_shown_as_a_403():
    use(post_status="needs_approval", extra=lambda m, u, h, d: (403, {"error": {"code": "missing_scope", "message": "Needs social:approve."}})
        if m == "POST" and "/social/posts/" in u and u.endswith("/decision") else None)
    c = client(USER_A)
    pid = make_post(c)[1]["post"]["id"]
    code, body, _ = call(c, "post", f"/api/growth/social/posts/{pid}/decision", {"decision": "approve", "confirm": True, "attempt": attempt()})
    assert code == 403 and "social:approve" in body["error"]
    assert call(c, "get", "/api/growth/social/posts")[1]["posts"][0]["status"] != "approved"


def test_another_user_cannot_see_or_decide_a_post():
    use(post_status="needs_approval")
    pid = make_post(client(USER_A))[1]["post"]["id"]
    b = client(USER_B)
    assert call(b, "get", "/api/growth/social/posts")[1]["posts"] == []
    FAKE.calls.clear()
    assert call(b, "post", f"/api/growth/social/posts/{pid}/decision", {"decision": "approve", "confirm": True, "attempt": attempt()})[0] == 404
    assert only("POST", "/decision") == []


# ───────────────────────────────────────────── phone calls ──────────────────────────────────────────

def test_a_call_is_made_to_one_number_after_both_confirmations():
    use()
    c = client(USER_A)
    code, body, _ = make_call(c, instructions="Be brief.", questions=["How many staff?"])
    assert code == 200, body
    assert body["call"]["status"] == "ringing" and body["call"]["number_masked"] == "+4420****0000"
    sent = only("POST", "/v1/voice/calls")
    assert len(sent) == 1 and sent[0]["body"]["to"] == "+442079460000" and sent[0]["body"]["lead_name"] == "Alpha Borough Council"
    assert sent[0]["body"]["questions"] == ["How many staff?"] and sent[0]["headers"]["Idempotency-Key"].startswith("tf-call-")
    assert call(c, "get", "/api/growth/voice/calls")[1]["calls"][0]["id"] == body["call"]["id"]


def test_a_call_is_refused_without_the_confirmations_and_nothing_is_dialled():
    use()
    c = client(USER_A)
    for over, word in (({"business_line": False}, "organisation's line"), ({"tps_checked": False}, "TPS / CTPS"),
                       ({"confirm": False}, "Confirm that you want"), ({"number": "07911 123456"}, "mobile"),
                       ({"number": "+1 202 555 0100"}, "UK numbers"), ({"goal": ""}, "what the call is for"), ({"buyer": ""}, "who you are calling")):
        code, body, _ = make_call(c, **over)
        assert code == 400 and word in body["error"], (over, body)
    assert only("POST", "/v1/voice/calls") == []


def test_a_buyer_on_the_do_not_contact_list_is_never_called():
    use()
    from tender_app import market_radar as mr
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO growth_suppressions (username, buyer_key, buyer_name, reason) VALUES (%s, %s, %s, 'manual')",
                        (USER_A, mr.canonical_buyer_key("Alpha Borough Council"), "Alpha Borough Council"))
        conn.commit()
    finally:
        conn.close()
    code, body, _ = make_call(client(USER_A))
    assert code == 400 and "do-not-contact" in body["error"] and only("POST", "/v1/voice/calls") == []


def test_the_same_number_is_not_called_twice_in_a_day():
    use()
    c = client(USER_A)
    assert make_call(c)[0] == 200
    code, body, _ = make_call(c, buyer="Alpha Council again")
    assert code == 409 and "last 24 hours" in body["error"] and len(only("POST", "/v1/voice/calls")) == 1


def test_a_call_that_was_not_answered_may_be_tried_again():
    use()
    c = client(USER_A)
    assert make_call(c)[0] == 200
    FAKE.handler.state["call_status"] = "no_answer"
    call(c, "get", "/api/growth/voice/calls?refresh=1")
    assert make_call(c)[0] == 200 and len(only("POST", "/v1/voice/calls")) == 2


def test_the_daily_cap_stops_further_calls():
    use()
    from tender_app.blueprints import growth_bp
    old = growth_bp.OUTREACH_DAILY_CALL_CAP
    growth_bp.OUTREACH_DAILY_CALL_CAP = 1
    try:
        c = client(USER_A)
        assert make_call(c)[0] == 200
        code, body, _ = make_call(c, number="0161 496 0000")
        assert code == 429 and "1 calls in the last 24 hours" in body["error"]
    finally:
        growth_bp.OUTREACH_DAILY_CALL_CAP = old


def test_no_connected_phone_number_is_said_plainly_and_nothing_is_dialled():
    use(phones=0)
    code, body, _ = make_call(client(USER_A))
    assert code == 409 and "No phone number is connected" in body["error"] and only("POST", "/v1/voice/calls") == []


def test_a_refresh_reads_the_outcome_and_a_call_can_be_read_in_full():
    use()
    c = client(USER_A)
    cid = make_call(c)[1]["call"]["id"]
    FAKE.handler.state["call_status"] = "completed"
    code, body, _ = call(c, "get", "/api/growth/voice/calls?refresh=1")
    row = body["calls"][0]
    assert row["status"] == "completed" and row["outcome"] == "meeting_booked" and row["duration_seconds"] == 61 and row["meeting_booked"] is True
    assert row["can_end"] is False
    full = call(c, "get", f"/api/growth/voice/calls/{cid}")[1]["call"]
    assert full["transcript"] == [{"who": "ai", "text": "Hello"}] and full["answers"] == [{"key": "system", "answer": "Acuity"}]


def test_a_live_call_can_be_ended_and_a_finished_one_cannot():
    use()
    c = client(USER_A)
    cid = make_call(c)[1]["call"]["id"]
    code, body, _ = call(c, "delete", f"/api/growth/voice/calls/{cid}")
    assert code == 200 and body["call"]["status"] == "ended" and len(only("DELETE", "/v1/voice/calls/call_fake0001")) == 1
    code, body, _ = call(c, "delete", f"/api/growth/voice/calls/{cid}")
    assert code == 409 and "not in progress" in body["error"]


def test_another_user_cannot_see_read_or_end_a_call():
    use()
    cid = make_call(client(USER_A))[1]["call"]["id"]
    b = client(USER_B)
    assert call(b, "get", "/api/growth/voice/calls")[1]["calls"] == []
    FAKE.calls.clear()
    assert call(b, "get", f"/api/growth/voice/calls/{cid}")[0] == 404
    assert call(b, "delete", f"/api/growth/voice/calls/{cid}")[0] == 404
    assert FAKE.calls == []


def test_scripts_are_listed_and_the_lookup_never_invents_a_number():
    use()
    c = client(USER_A)
    assert call(c, "get", "/api/growth/voice/scripts")[1]["scripts"] == [{"id": "script_intro", "name": "Intro"}]
    code, body, _ = call(c, "get", "/api/growth/voice/lookup?buyer=Zz+Nonexistent+Buyer+" + uuid.uuid4().hex[:8])
    assert code == 200 and body == {"found": False, "phone": None, "number": None, "problem": None}
    assert call(c, "get", "/api/growth/voice/lookup?buyer=ab")[0] == 400


def test_not_connected_is_a_409_with_no_call_and_a_post_needs_the_csrf_token():
    install(key="")
    c = client(USER_A)
    assert make_post(c)[0] == 409 and make_call(c)[0] == 409 and FAKE.calls == []
    use()
    assert call(c, "post", "/api/growth/voice/calls", {"attempt": attempt()}, headers={})[0] == 403
    assert call(c, "post", "/api/growth/social/posts", {"attempt": attempt()}, headers={})[0] == 403
    assert FAKE.calls == []


def test_the_key_is_never_in_an_answer():
    use()
    c = client(USER_A)
    texts = [make_post(c)[2], make_call(c)[2], call(c, "get", "/api/growth/social/posts?refresh=1")[2],
             call(c, "get", "/api/growth/voice/calls?refresh=1")[2]]
    assert not any(base.TEST_KEY in t for t in texts)


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
