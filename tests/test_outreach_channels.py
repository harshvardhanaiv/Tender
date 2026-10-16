"""Run: python tests/test_outreach_channels.py   (plain asserts, no network, no database)

The rules for Outreach social posts and phone calls (tender_app/outreach_channels.py): which numbers may be called,
that a call needs the user's confirmations, what a post request looks like, and when a post can be decided.
"""
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_app import outreach_channels as och  # noqa: E402

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def call(**over):
    base = dict(number="020 7946 0000", buyer="Alpha Borough Council", goal="Ask who handles gas servicing", script_id=None,
                instructions="", questions=[], business_line=True, tps_checked=True, suppressed=False)
    base.update(over)
    return och.call_problems(**base)


def test_uk_landlines_become_e164():
    for raw in ("020 7946 0000", "(020) 7946 0000", "+44 20 7946 0000", "+44 (0)20 7946 0000", "0044 20 7946 0000", "02079460000"):
        assert och.normalize_uk_landline(raw) == ("+442079460000", None), raw
    assert och.normalize_uk_landline("0161 496 0000") == ("+441614960000", None)
    assert och.normalize_uk_landline("0300 123 4567") == ("+443001234567", None)


def test_mobiles_premium_freephone_and_foreign_numbers_are_refused():
    for raw, word in (("07911 123456", "mobile"), ("+44 7911 123456", "mobile"), ("0800 123 4567", "01, 02 or 03"),
                      ("0900 123 4567", "01, 02 or 03"), ("0560 123 4567", "01, 02 or 03"), ("+1 202 555 0100", "UK numbers"),
                      ("+33 1 23 45 67 89", "UK numbers"), ("7946 0000", "area code"), ("", "Enter"), ("call me", "phone number"),
                      ("020 794", "wrong length"), ("020 7946 0000 123", "wrong length")):
        number, problem = och.normalize_uk_landline(raw)
        assert number is None and word in problem, (raw, number, problem)


def test_a_call_needs_both_confirmations_and_says_which_is_missing():
    problems, fields = call()
    assert problems == [] and fields["number"] == "+442079460000" and fields["buyer"] == "Alpha Borough Council"
    problems, _ = call(business_line=False)
    assert problems == ["Confirm that this is an organisation's line, not an individual's."]
    problems, _ = call(tps_checked=None)
    assert problems == ["Confirm that you have checked the number against the TPS / CTPS."]
    problems, _ = call(business_line="true", tps_checked=1)  # only a real true counts
    assert len(problems) == 2


def test_a_buyer_on_the_do_not_contact_list_cannot_be_called():
    problems, _ = call(suppressed=True)
    assert problems == ["This buyer is on your do-not-contact list."]


def test_a_call_needs_a_goal_or_a_script_and_a_buyer():
    problems, _ = call(goal="", script_id=None)
    assert problems == ["Say what the call is for, or choose a saved script."]
    problems, fields = call(goal="", script_id="script_1234")
    assert problems == [] and fields["script_id"] == "script_1234"
    problems, _ = call(goal="x", script_id="no good!")
    assert "That is not a saved script." in problems
    problems, _ = call(buyer="   ")
    assert problems == ["Say who you are calling (the buyer's name)."]


def test_call_text_is_cleaned_and_limited():
    problems, fields = call(goal="  Ask   about\n gas  ", instructions="Be brief.\r\n\x07Polite.", questions=["  How many?  ", "", "Which system?"])
    assert problems == [] and fields["goal"] == "Ask about gas"
    assert fields["instructions"] == "Be brief.\nPolite." and fields["questions"] == ["How many?", "Which system?"]
    problems, _ = call(goal="x" * 401)
    assert any("goal is too long" in p for p in problems)
    problems, _ = call(questions=[f"q{i}" for i in range(9)])
    assert any("at most 8 questions" in p for p in problems)


def test_the_call_request_has_only_what_was_chosen():
    _, fields = call(goal="Ask about gas", questions=["How many staff?"], instructions="Be brief.")
    assert och.build_call_payload(fields) == {
        "to": "+442079460000", "lead_name": "Alpha Borough Council", "variables": {"company": "Alpha Borough Council"},
        "goal": "Ask about gas", "instructions": "Be brief.", "questions": ["How many staff?"]}
    _, fields = call(goal="", script_id="script_1234")
    body = och.build_call_payload(fields)
    assert body["script_id"] == "script_1234" and "goal" not in body and "questions" not in body and "instructions" not in body


def test_a_number_is_masked_for_display():
    assert och.mask_number("+442079460000") == "+4420****0000"
    assert och.mask_number(None) == "" and och.mask_number("+44") == ""


def test_publish_time_is_checked_and_normalised_to_utc():
    assert och.parse_publish_at("", NOW) == (None, None) and och.parse_publish_at(None, NOW) == (None, None)
    when, problem = och.parse_publish_at("2026-10-20T09:00:00+01:00", NOW)
    assert (when, problem) == ("2026-10-20T08:00:00Z", None)
    assert och.parse_publish_at("2026-10-20T09:00:00Z", NOW)[0] == "2026-10-20T09:00:00Z"
    assert "time zone" in och.parse_publish_at("2026-10-20T09:00:00", NOW)[1]
    assert "valid date" in och.parse_publish_at("soon", NOW)[1]
    assert "at least 5 minutes" in och.parse_publish_at("2026-10-16T12:02:00Z", NOW)[1]
    assert "at least 5 minutes" in och.parse_publish_at("2026-10-01T12:00:00Z", NOW)[1]
    far = (NOW + timedelta(days=91)).isoformat()
    assert "within the next 90 days" in och.parse_publish_at(far, NOW)[1]
    assert och.parse_publish_at((NOW + timedelta(days=90)).isoformat(), NOW)[1] is None


def test_a_post_needs_a_prompt_and_a_connected_channel():
    problems, channels, prompt = och.post_problems("Announce our new Leeds office opening", ["LinkedIn", "linkedin", "facebook"], ["linkedin", "facebook"])
    assert problems == [] and channels == ["linkedin", "facebook"] and prompt.startswith("Announce")
    problems, _, _ = och.post_problems("short", ["linkedin"], None)
    assert any("at least a sentence" in p for p in problems)
    problems, _, _ = och.post_problems("A long enough prompt here", [], None)
    assert problems == ["Choose at least one channel to post to."]
    problems, _, _ = och.post_problems("A long enough prompt here", ["linkedin", "instagram"], ["linkedin"])
    assert problems == ["Not connected in Outreach: instagram. Connect it there, or choose another channel."]
    problems, channels, _ = och.post_problems("A long enough prompt here", ["linkedin; drop"], None)
    assert channels == [] and problems == ["Choose at least one channel to post to."]  # not a channel name at all
    problems, _, prompt = och.post_problems("x" * 1600, ["linkedin"], None)
    assert len(prompt) == 1500 and any("too long" in p for p in problems)


def test_with_no_list_of_connected_channels_the_usual_ones_are_accepted():
    assert och.post_problems("A long enough prompt here", ["x"], [])[0] == []
    assert "Not connected" in och.post_problems("A long enough prompt here", ["tiktok"], [])[0][0]


def test_a_change_is_a_new_time_or_a_rewrite_never_both_and_never_nothing():
    assert och.change_payload("2026-10-20T09:00:00+01:00", "", "", NOW) == ([], {"publish_at": "2026-10-20T08:00:00Z"})
    assert och.change_payload("", "picture", "  A  brighter   office ", NOW) == ([], {"regenerate": "picture", "instruction": "A brighter office"})
    assert och.change_payload(None, "TEXT", "", NOW) == ([], {"regenerate": "text"})
    assert och.change_payload("", "both", "x" * 400, NOW)[1]["instruction"] == "x" * 300
    for args, word in ((("", "", ""), "Choose a new time"), (("", "poem", ""), "text, the picture or both"),
                       (("2020-01-01T09:00:00Z", "", ""), "at least 5 minutes"), (("tomorrow", "", ""), "valid date"),
                       (("2026-10-20T09:00:00Z", "text", ""), "two steps")):
        problems, body = och.change_payload(*args, NOW)
        assert any(word in p for p in problems), (args, problems)
    assert och.change_payload("", "text", "note", NOW)[1].get("publish_at") is None


def _data_url(mime, raw):
    import base64
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
WEBP_BYTES = b"RIFF\x10\x00\x00\x00WEBP" + b"\x00" * 8


def test_the_users_own_caption_is_checked():
    assert och.caption_problem("  Our Leeds office\nis open.  ")[0] is None
    assert och.caption_problem("  Our Leeds office\r\nis open.  ")[1] == "Our Leeds office\nis open."
    assert "few words" in och.caption_problem("  hi ")[0] and "few words" in och.caption_problem(None)[0]
    problem, text = och.caption_problem("x" * 3100)
    assert "too long" in problem and len(text) == och.MAX_CAPTION
    assert och.caption_problem("x" * 1600, och.MAX_DRAFT_CAPTION)[0] and not och.caption_problem("x" * 1500, och.MAX_DRAFT_CAPTION)[0]
    assert och.caption_problem("a\x00b\x07 caption")[1] == "ab caption"


def test_the_users_own_picture_must_really_be_a_png_jpg_or_webp_within_the_limit():
    for mime, raw in (("image/png", PNG_BYTES), ("image/jpeg", JPG_BYTES), ("image/webp", WEBP_BYTES)):
        problem, clean = och.picture_problem(_data_url(mime, raw))
        assert problem is None and clean == _data_url(mime, raw), (mime, problem)
    assert och.picture_problem("  " + _data_url("image/png", PNG_BYTES).replace(",", ",\n") + " ")[0] is None, "wrapped base64 is tidied"
    for bad, word in ((_data_url("image/gif", b"GIF89a....."), "PNG, JPG or WebP"), ("javascript:alert(1)", "PNG, JPG or WebP"),
                      ("https://x.example/a.png", "PNG, JPG or WebP"), ("", "PNG, JPG or WebP"), (None, "PNG, JPG or WebP"),
                      ("data:text/html;base64,PGI+", "PNG, JPG or WebP"), ("data:image/png;base64,not base64!", "PNG, JPG or WebP"),
                      ("data:image/png;base64,@@@@", "PNG, JPG or WebP"),
                      (_data_url("image/png", b"<html><script>alert(1)</script>"), "not really"),
                      (_data_url("image/jpeg", PNG_BYTES), "not really"), (_data_url("image/webp", b"RIFF....WAVE" + b"\x00" * 8), "not really"),
                      ("data:image/png;base64,A", "could not be read")):
        problem, clean = och.picture_problem(bad)
        assert problem and word in problem and clean is None, (bad, problem)
    big = _data_url("image/png", PNG_BYTES + b"\x00" * och.MAX_PICTURE_BYTES)
    assert "over 5 MB" in och.picture_problem(big)[0]
    assert och.picture_problem(_data_url("image/png", PNG_BYTES + b"\x00" * (och.MAX_PICTURE_BYTES - len(PNG_BYTES))))[0] is None


def test_a_hand_change_is_the_users_caption_and_or_picture_and_nothing_else():
    png = _data_url("image/png", PNG_BYTES)
    assert och.change_payload("", "", "", NOW, caption=" New words here ") == ([], {och.HAND_CAPTION_FIELD: "New words here"})
    assert och.change_payload(None, None, None, NOW, picture=png) == ([], {och.HAND_PICTURE_FIELD: png})
    assert och.change_payload("", "", "", NOW, caption="New words", picture=png)[1] == {och.HAND_CAPTION_FIELD: "New words", och.HAND_PICTURE_FIELD: png}
    for kw, word in (({"caption": "no"}, "few words"), ({"caption": ""}, "few words"), ({"picture": "data:image/gif;base64,AAAA"}, "PNG, JPG or WebP"),
                     ({"caption": "x" * 3100}, "too long")):
        problems, body = och.change_payload("", "", "", NOW, **kw)
        assert any(word in p for p in problems), (kw, problems)
    for args in (("2026-10-20T09:00:00Z", "", ""), ("", "text", "")):
        problems, _ = och.change_payload(*args, NOW, caption="New words here")
        assert any("on its own" in p for p in problems), (args, problems)
    assert och.change_payload("", "", "", NOW)[0], "nothing at all is still refused"
    assert och.HAND_CAPTION_FIELD == "caption" and och.HAND_PICTURE_FIELD == "image_url"


def test_a_post_started_from_the_users_caption_asks_the_ai_to_polish_or_to_keep_it():
    polish = och.polish_prompt("Our Leeds office is open.", ["linkedin", "x"])
    assert "linkedin, x" in polish and "Add no new facts" in polish and polish.endswith("Draft:\nOur Leeds office is open.")
    assert "social media" in och.polish_prompt("Hello there", [])
    exact = och.exact_prompt("Our Leeds office is open.")
    assert "exactly as written" in exact and exact.endswith("Post:\nOur Leeds office is open.")
    assert och.channel_problems(["linkedin", "LinkedIn"], ["linkedin"]) == ([], ["linkedin"])
    assert "at least one channel" in och.channel_problems([], None)[0][0]
    assert "Not connected" in och.channel_problems(["tiktok"], ["linkedin"])[0][0]


def test_a_post_cannot_be_edited_by_hand_while_the_ai_is_writing_it():
    assert "overwrite" in och.can_decide("writing", "edit")
    assert och.can_decide("needs_approval", "edit") is None and och.can_decide("writing", "change") is None
    assert "already published" in och.can_decide("published", "edit")


PROFILE = {"id": 4, "name": "TVD Driveways Ltd", "is_default": True,
           "text": "TVD Driveways is a family firm that lays resin and block-paved driveways for councils and housing associations across Hampshire. We hold Constructionline Gold.",
           "meta": {"org_name": "TVD Driveways Limited", "city": "Winchester", "county": "Hampshire", "website": "www.tvd-driveways.example",
                    "contact_name": "Sam Private", "email": "sam@tvd.example", "phone": "01962 000000", "vat_number": "GB123", "turnover": "2.5", "duns_number": "99"}}


def test_the_company_brief_is_what_the_profile_says_and_nothing_private():
    b = och.company_brief(PROFILE)
    assert b["name"] == "TVD Driveways Limited" and b["where"] == "Winchester, Hampshire"
    assert b["website"] == "https://www.tvd-driveways.example" and b["about"].startswith("TVD Driveways is a family firm")
    flat = str(b)
    for private in ("Sam Private", "sam@tvd.example", "01962", "GB123", "2.5", "99"):
        assert private not in flat, private
    assert och.company_brief(None) is None and och.company_brief({}) is None
    assert och.company_brief({"name": "  ", "meta": {}}) is None
    plain = och.company_brief({"name": "Acme", "text": "", "meta": {"website": "not a site; drop", "city": ""}})
    assert plain == {"name": "Acme", "about": "", "where": "", "website": ""}
    assert och.company_brief({"name": "Acme", "text": "x", "meta": {"website": "https://ok.example/path"}})["website"] == "https://ok.example/path"


def test_a_long_description_is_cut_at_a_sentence():
    text = "We do one thing well. " * 60
    about = och.company_brief({"name": "Acme", "text": text, "meta": {}})["about"]
    assert len(about) <= 600 and about.endswith("well.") and "…" not in about
    run_on = och.company_brief({"name": "Acme", "text": "word " * 400, "meta": {}})["about"]
    assert len(run_on) <= 601 and run_on.endswith("…")


def test_the_prompt_names_the_company_gives_its_facts_and_forbids_inventing_more():
    sent = och.compose_post_prompt("Announce our new Leeds office", och.company_brief(PROFILE))
    assert sent.startswith("Announce our new Leeds office\n\nWrite it as TVD Driveways Limited, based in Winchester, Hampshire.")
    assert "About the company: TVD Driveways is a family firm" in sent and "Website: https://www.tvd-driveways.example" in sent
    assert sent.rstrip().endswith("Do not invent contracts, awards, figures, people, clients or claims.")
    assert len(sent) < 2400
    assert och.compose_post_prompt("Just this", None) == "Just this"
    bare = och.compose_post_prompt("Idea", {"name": "Acme", "about": "", "where": "", "website": ""})
    assert "About the company" not in bare and "Website" not in bare and "Write it as Acme." in bare


def test_the_hints_say_what_to_add_to_the_profile():
    assert "No company is set up" in och.brief_hints([], None)[0]
    full = och.company_brief(PROFILE)
    assert och.brief_hints([PROFILE], full) == []
    thin = och.company_brief({"name": "Acme", "text": "", "meta": {}})
    hints = och.brief_hints([{"name": "Acme"}], thin)
    assert any("no description" in h for h in hints) and any("website" in h for h in hints)


def test_the_post_request_asks_for_an_ai_picture_unless_told_not_to():
    assert och.build_post_payload("Announce it", ["linkedin"], "2026-10-20T09:00:00Z", {"instruction": " A bright   office "}) == {
        "prompt": "Announce it", "channels": ["linkedin"], "publish_at": "2026-10-20T09:00:00Z",
        "image": {"mode": "ai", "instruction": "A bright office"}}
    assert och.build_post_payload("Announce it", ["linkedin"], None, None) == {
        "prompt": "Announce it", "channels": ["linkedin"], "image": {"mode": "ai"}}
    assert och.build_post_payload("Announce it", ["linkedin"], None, {"mode": "none"}) == {"prompt": "Announce it", "channels": ["linkedin"]}


def test_a_finished_post_cannot_be_decided_and_a_post_being_written_cannot_be_approved():
    for s in ("published", "rejected", "cancelled", "failed", "expired"):
        assert och.can_decide(s, "approve") and och.can_decide(s, "cancel"), s
    assert och.can_decide("writing", "approve") and "still writing" in och.can_decide("writing", "approve")
    assert och.can_decide("writing", "cancel") is None  # a post being written can still be cancelled
    assert och.can_decide("needs_approval", "approve") is None and och.can_decide("needs_approval", "reject") is None
    assert och.can_decide(None, "approve") is None


def test_an_attempt_token_makes_a_double_click_one_request():
    assert och.attempt_token("abc12345") == "abc12345" and och.attempt_token("short") is None
    assert och.attempt_token("bad token!") is None and och.attempt_token(None) is None
    a = och.attempt_key("sam", "call", "abc12345")
    assert a == och.attempt_key("sam", "call", "abc12345") and a != och.attempt_key("sam", "call", "abc12346")
    assert a != och.attempt_key("alex", "call", "abc12345") and a != och.attempt_key("sam", "post", "abc12345")
    assert och.attempt_key("sam", "post-decide", "abc12345", "x").endswith("-x")
    assert "sam" not in a


def test_call_states():
    assert och.call_is_live("ringing") and och.call_is_live("In_Progress") and not och.call_is_live("completed")
    assert not och.call_is_live(None)


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
