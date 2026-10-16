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
                         "preview_url": "https://outreach.example/api/p/abc", "image": {"url": "https://outreach.example/img/office.png"},
                         "previews": {"linkedin": "Long LinkedIn text", "facebook": "Short FB text"}, "publish_at": state.get("publish_at")}
        if method == "PATCH" and "/v1/social/posts/" in url:
            b = json.loads(data)
            if b.get("publish_at"):
                state["publish_at"] = b["publish_at"]
            return 200, {"id": "v2_fake0001", "status": "writing" if b.get("regenerate") else "needs_approval", "publish_at": state.get("publish_at")}
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
            cur.execute("DELETE FROM company_profiles WHERE username = ANY(%s)", (base.USERS,))
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


def add_company(user, name="TVD Driveways Ltd", text="TVD Driveways lays resin and block-paved driveways for councils and housing associations across Hampshire.",
                meta=None, default=True):
    meta = meta if meta is not None else {"org_name": "TVD Driveways Limited", "city": "Winchester", "county": "Hampshire", "website": "tvd-driveways.example",
                                          "contact_name": "Sam Private", "email": "sam.private@tvd.example", "phone": "01962 000000", "vat_number": "GB123456789"}
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO company_profiles (username, name, profile_text, meta_json, is_default) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                        (user, name, text, json.dumps(meta), default))
            pid = cur.fetchone()[0]
        conn.commit()
        return pid
    finally:
        conn.close()


def test_a_post_is_written_as_the_chosen_company_using_only_what_its_profile_says():
    use()
    c = client(USER_A)
    pid = add_company(USER_A)
    code, body, _ = make_post(c, profile_id=pid, prompt="Announce our new Leeds office opening")
    assert code == 200, body
    sent = only("POST", "/v1/social/posts")[0]["body"]["prompt"]
    assert sent.startswith("Announce our new Leeds office opening\n\nWrite it as TVD Driveways Limited, based in Winchester, Hampshire.")
    assert "About the company: TVD Driveways lays resin" in sent and "Website: https://tvd-driveways.example" in sent
    assert "Do not invent" in sent
    for private in ("Sam Private", "sam.private", "01962", "GB123456789"):
        assert private not in sent, private
    post = body["post"]
    assert post["prompt"] == "Announce our new Leeds office opening", "the user's own words are what is kept and shown"
    assert post["company_name"] == "TVD Driveways Limited" and post["profile_id"] == pid


def test_a_post_with_no_company_is_sent_as_written():
    use()
    c = client(USER_A)
    add_company(USER_A)
    code, body, _ = make_post(c, profile_id=None)
    assert code == 200 and only("POST", "/v1/social/posts")[0]["body"]["prompt"] == GOOD_PROMPT
    assert body["post"]["company_name"] is None and body["post"]["profile_id"] is None


def test_only_the_users_own_company_can_be_used():
    use()
    other = add_company(USER_B, name="Other Co", text="Other Co does other things for other people every single day.")
    code, body, _ = make_post(client(USER_A), profile_id=other)
    assert code == 404 and "not found" in body["error"] and only("POST", "/v1/social/posts") == []
    code, body, _ = make_post(client(USER_A), profile_id="abc")
    assert code == 400 and only("POST", "/v1/social/posts") == []


def test_the_company_list_shows_what_the_ai_would_be_told_and_what_to_add():
    use()
    c = client(USER_A)
    code, body, _ = call(c, "get", "/api/growth/social/company")
    assert code == 200 and body["profiles"] == [] and body["brief"] is None and body["profile_id"] is None
    assert "No company is set up" in body["hints"][0]
    first = add_company(USER_A)
    second = add_company(USER_A, name="Thin Co", text="", meta={}, default=False)
    code, body, _ = call(c, "get", "/api/growth/social/company")
    assert [p["name"] for p in body["profiles"]] == ["TVD Driveways Ltd", "Thin Co"]
    assert body["profile_id"] == first and body["brief"]["name"] == "TVD Driveways Limited" and body["hints"] == []
    assert body["profiles"][0]["has_description"] is True and body["profiles"][1]["has_description"] is False
    assert "sam.private" not in json.dumps(body) and "01962" not in json.dumps(body)
    code, body, _ = call(c, "get", f"/api/growth/social/company?profile_id={second}")
    assert body["brief"] == {"name": "Thin Co", "about": "", "where": "", "website": ""} and any("no description" in h for h in body["hints"])
    other = add_company(USER_B, name="Other Co")
    assert call(c, "get", f"/api/growth/social/company?profile_id={other}")[0] == 404
    assert call(client(USER_B), "get", "/api/growth/social/company")[1]["profiles"][0]["name"] == "Other Co"


def test_a_refresh_keeps_the_picture_and_how_the_post_reads_on_each_channel():
    use()
    c = client(USER_A)
    make_post(c)
    FAKE.handler.state["post_status"] = "needs_approval"
    p = call(c, "get", "/api/growth/social/posts?refresh=1")[1]["posts"][0]
    assert p["image_url"] == "https://outreach.example/img/office.png"
    assert p["variants"] == [{"channel": "linkedin", "text": "Long LinkedIn text"}, {"channel": "facebook", "text": "Short FB text"}]
    again = call(c, "get", "/api/growth/social/posts")[1]["posts"][0]
    assert again["image_url"] == p["image_url"] and len(again["variants"]) == 2, "kept for the next visit"


def test_a_post_time_can_be_changed_and_the_post_goes_back_for_approval():
    use(post_status="needs_approval")
    c = client(USER_A)
    pid = make_post(c)[1]["post"]["id"]
    when = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    code, body, _ = call(c, "patch", f"/api/growth/social/posts/{pid}", {"publish_at": when.isoformat(), "attempt": attempt()})
    assert code == 200, body
    sent = only("PATCH", "/v1/social/posts/v2_fake0001")
    assert len(sent) == 1 and sent[0]["body"] == {"publish_at": when.strftime("%Y-%m-%dT%H:%M:%SZ")}
    assert sent[0]["headers"]["Idempotency-Key"].startswith("tf-post-edit-")
    assert body["post"]["publish_at"] == when.strftime("%Y-%m-%dT%H:%M:%SZ") and body["post"]["status"] == "needs_approval"
    assert only("POST", "/decision") == [], "changing a time never approves"
    assert call(c, "get", "/api/growth/social/posts")[1]["posts"][0]["publish_at"] == body["post"]["publish_at"]


def test_the_ai_can_be_asked_to_write_the_picture_again():
    use(post_status="needs_approval")
    c = client(USER_A)
    pid = make_post(c)[1]["post"]["id"]
    code, body, _ = call(c, "patch", f"/api/growth/social/posts/{pid}", {"regenerate": "picture", "instruction": "A brighter office", "attempt": attempt()})
    assert code == 200 and body["post"]["status"] == "writing", body
    assert only("PATCH", "/v1/social/posts/v2_fake0001")[0]["body"] == {"regenerate": "picture", "instruction": "A brighter office"}
    assert only("POST", "/decision") == []


def test_a_bad_change_is_refused_before_outreach_is_asked():
    use(post_status="needs_approval")
    c = client(USER_A)
    pid = make_post(c)[1]["post"]["id"]
    url = f"/api/growth/social/posts/{pid}"
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    for over, word in (({}, "Choose a new time"), ({"regenerate": "poem"}, "text, the picture or both"), ({"publish_at": "2020-01-01T09:00:00Z"}, "at least 5 minutes"),
                       ({"publish_at": soon, "regenerate": "text"}, "two steps")):
        code, body, _ = call(c, "patch", url, {"attempt": attempt(), **over})
        assert code == 400 and word in body["error"], (over, body)
    assert call(c, "patch", url, {"regenerate": "text"})[0] == 400  # no attempt token
    assert only("PATCH", "/v1/social/posts/v2_fake0001") == []


def test_a_finished_post_cannot_be_changed_and_another_user_cannot_change_it():
    use(post_status="needs_approval")
    c = client(USER_A)
    pid = make_post(c)[1]["post"]["id"]
    FAKE.calls.clear()
    assert call(client(USER_B), "patch", f"/api/growth/social/posts/{pid}", {"regenerate": "text", "attempt": attempt()})[0] == 404
    assert only("PATCH", "/v1/social/posts/v2_fake0001") == []
    FAKE.handler.state["post_status"] = "published"
    code, body, _ = call(c, "patch", f"/api/growth/social/posts/{pid}", {"regenerate": "text", "attempt": attempt()})
    assert code == 409 and "already published" in body["error"] and only("PATCH", "/v1/social/posts/v2_fake0001") == []


PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="


def test_a_post_can_start_from_the_users_own_caption_used_as_written():
    use()
    c = client(USER_A)
    code, body, _ = call(c, "post", "/api/growth/social/posts", {"caption": "Our Leeds office is open.\nCome and say hello.", "channels": ["linkedin"],
                                                                "picture": {"mode": "none"}, "attempt": attempt()})
    assert code == 200, body
    sent = only("POST", "/v1/social/posts")[0]["body"]
    assert "exactly as written" in sent["prompt"] and sent["prompt"].endswith("Our Leeds office is open.\nCome and say hello.")
    assert "image" not in sent, "the user's own picture is set by hand afterwards; the AI draws nothing"
    assert only("POST", "/decision") == []
    assert body["post"]["prompt"] == "Our Leeds office is open.\nCome and say hello.", "the stored text is the user's own words"


def test_a_caption_can_be_polished_by_the_ai_and_a_company_is_still_named():
    use()
    c = client(USER_A)
    code, body, _ = call(c, "post", "/api/growth/social/posts", {"caption": "Our Leeds office is open.", "polish": True, "channels": ["linkedin"], "attempt": attempt()})
    assert code == 200, body
    sent = only("POST", "/v1/social/posts")[0]["body"]
    assert "Improve this draft post for linkedin" in sent["prompt"] and "Add no new facts" in sent["prompt"] and "Our Leeds office is open." in sent["prompt"]
    assert sent["image"] == {"mode": "ai"}


def test_a_bad_own_caption_is_refused_before_a_post_is_made():
    use()
    c = client(USER_A)
    for over, word in (({"caption": "hi"}, "few words"), ({"caption": "x" * 1600}, "too long"), ({"channels": []}, "at least one channel"),
                       ({"channels": ["instagram"]}, "Not connected in Outreach")):
        code, body, _ = call(c, "post", "/api/growth/social/posts", {"caption": "Our Leeds office is open.", "channels": ["linkedin"], "attempt": attempt(), **over})
        assert code == 400 and word in body["error"], (over, body)
    assert only("POST", "/v1/social/posts") == []


def test_the_users_own_caption_and_picture_are_set_by_hand_and_the_post_goes_back_for_approval():
    use(post_status="needs_approval")
    c = client(USER_A)
    pid = make_post(c)[1]["post"]["id"]
    code, body, _ = call(c, "patch", f"/api/growth/social/posts/{pid}", {"caption": "  Our Leeds office is open.  ", "picture_data": PNG, "attempt": attempt()})
    assert code == 200, body
    sent = only("PATCH", "/v1/social/posts/v2_fake0001")
    assert len(sent) == 1 and sent[0]["body"] == {"caption": "Our Leeds office is open.", "image_url": PNG}
    assert sent[0]["headers"]["Idempotency-Key"].startswith("tf-post-edit-")
    assert body["post"]["caption"] == "Our Leeds office is open.", "the post shows the user's words"
    assert body["post"]["status"] == "needs_approval"
    assert only("POST", "/decision") == [], "a hand edit never approves"
    assert call(c, "get", "/api/growth/social/posts")[1]["posts"][0]["caption"] == "Our Leeds office is open."


def test_a_bad_hand_edit_is_refused_before_outreach_is_asked():
    use(post_status="needs_approval")
    c = client(USER_A)
    pid = make_post(c)[1]["post"]["id"]
    url = f"/api/growth/social/posts/{pid}"
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    for over, word in (({"caption": "no"}, "few words"), ({"caption": "x" * 3100}, "too long"), ({"picture_data": "data:image/gif;base64,R0lGODlh"}, "PNG, JPG or WebP"),
                       ({"picture_data": "data:image/png;base64,PGh0bWw+PHNjcmlwdD4="}, "not really"), ({"picture_data": "javascript:alert(1)"}, "PNG, JPG or WebP"),
                       ({"caption": "New words here", "publish_at": soon}, "on its own"), ({"caption": "New words here", "regenerate": "text"}, "on its own")):
        code, body, _ = call(c, "patch", url, {"attempt": attempt(), **over})
        assert code == 400 and word in body["error"], (over, body)
    assert only("PATCH", "/v1/social/posts/v2_fake0001") == []


def test_a_post_the_ai_is_still_writing_cannot_be_edited_by_hand_but_can_be_scheduled():
    use(post_status="writing")
    c = client(USER_A)
    pid = make_post(c)[1]["post"]["id"]
    url = f"/api/growth/social/posts/{pid}"
    code, body, _ = call(c, "patch", url, {"caption": "New words here", "attempt": attempt()})
    assert code == 409 and "still writing" in body["error"] and only("PATCH", "/v1/social/posts/v2_fake0001") == []
    when = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    assert call(c, "patch", url, {"publish_at": when, "attempt": attempt()})[0] == 200


def test_another_user_cannot_edit_a_post_by_hand():
    use(post_status="needs_approval")
    pid = make_post(client(USER_A))[1]["post"]["id"]
    FAKE.calls.clear()
    assert call(client(USER_B), "patch", f"/api/growth/social/posts/{pid}", {"caption": "Hijacked caption", "attempt": attempt()})[0] == 404
    assert only("PATCH", "/v1/social/posts/v2_fake0001") == []


def test_an_approval_can_carry_a_new_time_and_a_bad_one_is_refused():
    use(post_status="needs_approval")
    c = client(USER_A)
    pid = make_post(c)[1]["post"]["id"]
    url = f"/api/growth/social/posts/{pid}/decision"
    code, body, _ = call(c, "post", url, {"decision": "approve", "confirm": True, "publish_at": "2020-01-01T09:00:00Z", "attempt": attempt()})
    assert code == 400 and "at least 5 minutes" in body["error"] and only("POST", "/decision") == []
    when = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    code, body, _ = call(c, "post", url, {"decision": "approve", "confirm": True, "publish_at": when.isoformat(), "attempt": attempt()})
    assert code == 200 and body["post"]["status"] == "approved", body
    assert only("POST", "/decision")[0]["body"] == {"decision": "approve", "publish_at": when.strftime("%Y-%m-%dT%H:%M:%SZ")}
    assert body["post"]["publish_at"] == when.strftime("%Y-%m-%dT%H:%M:%SZ")


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
