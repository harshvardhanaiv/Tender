"""Run: python tests/test_email_send.py   (plain asserts, no network, no database)

The rules for sending email through Outreach (tender_app/email_send.py): the routing function, the guards, who
is skipped and why, grouping by the text each person actually gets, the daily cap and batch cap, hashes that
identify a send, a status that never moves backwards, and the exact request bodies.
"""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_app import email_send as es  # noqa: E402
from tender_app import growth_studio as gs  # noqa: E402

OPT = gs.OPT_OUT_LINE
SUBJECT = "A short note"
BODY = "Hello there,\n\nWe help councils with repairs.\n\n" + OPT
PERSONAL_BODY = "Hello {{firstName}},\n\nWe help {{company}} with repairs.\n\n" + OPT


def people(*emails):
    return [{"email": e} for e in emails]


# ── the routing rule ────────────────────────────────────────────────────────────────────────────

def test_choose_route():
    assert es.choose_route(0, 0) == "none"
    assert es.choose_route(1, 1) == "single"
    assert es.choose_route(2, 1) == "campaign"
    assert es.choose_route(50, 1) == "campaign"
    assert es.choose_route(2, 2) == "per_recipient"
    assert es.choose_route(5, 3) == "per_recipient"


def test_one_recipient_is_a_single_send():
    plan = es.plan_send(SUBJECT, BODY, people("a@council.gov.uk"))
    assert plan["route"] == "single" and [i["email"] for i in plan["singles"]] == ["a@council.gov.uk"]
    assert plan["campaigns"] == [] and plan["skipped"] == []


def test_two_or_more_with_the_same_message_are_one_campaign_with_all_of_them():
    plan = es.plan_send(SUBJECT, BODY, people("a@x.co.uk", "b@x.co.uk", "c@x.co.uk"))
    assert plan["route"] == "campaign" and plan["singles"] == []
    assert len(plan["campaigns"]) == 1
    assert [i["email"] for i in plan["campaigns"][0]["items"]] == ["a@x.co.uk", "b@x.co.uk", "c@x.co.uk"]


def test_different_messages_are_sent_one_by_one_up_to_the_daily_cap():
    recs = [{"email": f"p{i}@x.co.uk", "first_name": f"Name{i}"} for i in range(5)]
    plan = es.plan_send(SUBJECT, PERSONAL_BODY.replace("{{company}}", "your team"), recs, daily_cap=25)
    assert plan["route"] == "per_recipient" and len(plan["singles"]) == 5 and plan["campaigns"] == []
    # only 3 left today: three go, two are reported and nothing is queued
    capped = es.plan_send(SUBJECT, PERSONAL_BODY.replace("{{company}}", "your team"), recs, sent_today=22, daily_cap=25)
    assert len(capped["singles"]) == 3 and len(capped["daily_limited"]) == 2
    assert [i["email"] for i in capped["daily_limited"]] == ["p3@x.co.uk", "p4@x.co.uk"]
    full = es.plan_send(SUBJECT, PERSONAL_BODY.replace("{{company}}", "your team"), recs, sent_today=25, daily_cap=25)
    assert full["singles"] == [] and len(full["daily_limited"]) == 5


def test_a_mixed_batch_makes_a_campaign_for_the_shared_text_and_sends_the_rest_singly():
    recs = [{"email": "a@x.co.uk", "first_name": "Sam"}, {"email": "b@x.co.uk", "first_name": "Sam"},
            {"email": "c@x.co.uk", "first_name": "Lee"}]
    plan = es.plan_send(SUBJECT, "Hello {{firstName}}\n" + OPT, recs)
    assert plan["route"] == "mixed"
    assert [i["email"] for i in plan["campaigns"][0]["items"]] == ["a@x.co.uk", "b@x.co.uk"]
    assert [i["email"] for i in plan["singles"]] == ["c@x.co.uk"]


def test_merge_fields_are_filled_per_person_before_grouping():
    plan = es.plan_send("Hi {{firstName}}", "Hello {{firstName}} at {{company}}\n" + OPT,
                        [{"email": "a@x.co.uk", "first_name": "Sam", "company": "Acme"}])
    item = plan["singles"][0]
    assert item["send_subject"] == "Hi Sam" and item["send_body"].startswith("Hello Sam at Acme")
    payload = es.single_payload(item)
    assert payload == {"to": "a@x.co.uk", "subject": "Hi Sam", "body": item["send_body"], "first_name": "Sam", "company": "Acme"}


def test_when_outreach_fills_merge_fields_people_are_grouped_by_template():
    recs = [{"email": "a@x.co.uk", "first_name": "Sam"}, {"email": "b@x.co.uk", "first_name": "Lee"}]
    plan = es.plan_send(SUBJECT, "Hello {{firstName}}\n" + OPT, recs, merge_in_campaign=True)
    assert plan["route"] == "campaign" and len(plan["campaigns"]) == 1
    group = plan["campaigns"][0]
    assert "{{firstName}}" in group["body"]  # sent unfilled
    payload = es.campaign_payload(group["subject"], group["body"], group["items"], with_first_name=True)
    assert [l.get("first_name") for l in payload["leads"]] == ["Sam", "Lee"]


# ── guards on the whole message ─────────────────────────────────────────────────────────────────

def test_the_whole_send_is_refused_for_a_bad_message():
    cases = [
        ("", BODY, "subject"),
        ("S", "", "message"),
        ("S", "Hello, we do repairs for councils.", "way to opt out"),
        ("S", "Hello {{firstname}} {{surname}}\n" + OPT, "{{surname}}"),
        ("S", "Hello {{ nope }}\n" + OPT, "{{nope}}"),
        ("x" * 201, BODY, "subject is longer"),
    ]
    for subject, body, word in cases:
        plan = es.plan_send(subject, body, people("a@x.co.uk"))
        assert plan["blockers"], (subject, body)
        assert word in " ".join(plan["blockers"]), (word, plan["blockers"])
        assert plan["singles"] == [] and plan["campaigns"] == []
    # known fields in any letter case are fine
    assert es.plan_send("Hi {{FirstName}}", "{{company}}\n" + OPT, [{"email": "a@x.co.uk", "first_name": "A", "company": "C"}])["blockers"] == []


# ── per-recipient skips never fail the batch ────────────────────────────────────────────────────

def test_recipients_are_skipped_with_a_reason_and_the_rest_still_go():
    recs = ["", "not-an-email", "dup@x.co.uk", "DUP@x.co.uk ", "gone@x.co.uk", "ok1@x.co.uk", "ok2@x.co.uk"]
    plan = es.plan_send(SUBJECT, BODY, recs, suppressed={"gone@x.co.uk"})
    reasons = {s["email"]: s["reason"] for s in plan["skipped"]}
    assert reasons[""] == "No email address."
    assert "valid" in reasons["not-an-email"]
    assert "more than once" in reasons["dup@x.co.uk"]
    assert "Opted out" in reasons["gone@x.co.uk"]
    sent_to = [i["email"] for g in plan["campaigns"] for i in g["items"]] + [i["email"] for i in plan["singles"]]
    assert sorted(sent_to) == ["dup@x.co.uk", "ok1@x.co.uk", "ok2@x.co.uk"]


def test_a_missing_merge_value_skips_only_that_person():
    recs = [{"email": "a@x.co.uk", "first_name": "Sam"}, {"email": "b@x.co.uk"}]
    plan = es.plan_send(SUBJECT, "Hello {{firstName}}\n" + OPT, recs)
    assert [i["email"] for i in plan["singles"]] == ["a@x.co.uk"]
    assert plan["skipped"] == [{"email": "b@x.co.uk", "reason": "No value for {{firstName}}."}]


def test_the_same_text_is_never_sent_to_the_same_address_twice():
    first = es.plan_send(SUBJECT, BODY, people("a@x.co.uk", "b@x.co.uk"))
    h = {i["email"]: i["hash"] for i in first["campaigns"][0]["items"]}
    for status in ("sent", "approved", "replied", "bounced"):
        again = es.plan_send(SUBJECT, BODY, people("a@x.co.uk", "b@x.co.uk"),
                             existing={h["a@x.co.uk"]: {"status": status, "mode": "single"}})
        assert [s["email"] for s in again["skipped"]] == ["a@x.co.uk"], status
        assert "already sent" in again["skipped"][0]["reason"]
    # failed and not_sent can be retried; an in-progress claim cannot, until it is stale
    for status in ("failed", "not_sent"):
        retry = es.plan_send(SUBJECT, BODY, people("a@x.co.uk"), existing={h["a@x.co.uk"]: {"status": status}})
        assert [i["email"] for i in retry["singles"]] == ["a@x.co.uk"]
    busy = es.plan_send(SUBJECT, BODY, people("a@x.co.uk"), existing={h["a@x.co.uk"]: {"status": "pending", "stale": False}})
    assert busy["singles"] == [] and "in progress" in busy["skipped"][0]["reason"]
    stale = es.plan_send(SUBJECT, BODY, people("a@x.co.uk"), existing={h["a@x.co.uk"]: {"status": "pending", "stale": True}})
    assert [i["email"] for i in stale["singles"]] == ["a@x.co.uk"]
    # different text to the same address is a different send
    other = es.plan_send("Another subject", BODY, people("a@x.co.uk"), existing={h["a@x.co.uk"]: {"status": "sent"}})
    assert [i["email"] for i in other["singles"]] == ["a@x.co.uk"]


def test_a_campaign_made_but_not_approved_is_resumed_not_recreated():
    first = es.plan_send(SUBJECT, BODY, people("a@x.co.uk", "b@x.co.uk"))
    existing = {i["hash"]: {"status": "created", "mode": "campaign", "outreach_id": "cp_7"} for i in first["campaigns"][0]["items"]}
    retry = es.plan_send(SUBJECT, BODY, people("a@x.co.uk", "b@x.co.uk"), existing=existing)
    assert retry["campaigns"] == [] and retry["singles"] == []
    assert len(retry["resume"]) == 1 and retry["resume"][0]["outreach_id"] == "cp_7"
    assert len(retry["resume"][0]["items"]) == 2 and retry["route"] == "campaign"


def test_test_and_live_sends_do_not_block_each_other():
    a = es.dedupe_hash("test", "a@x.co.uk", "S", "B")
    assert a != es.dedupe_hash("live", "a@x.co.uk", "S", "B")
    assert a == es.dedupe_hash("test", " A@X.co.uk ", "S", "B")
    assert a != es.dedupe_hash("test", "a@x.co.uk", "S2", "B") and a != es.dedupe_hash("test", "a@x.co.uk", "S", "B2")
    assert len(a) == 64


def test_the_batch_is_capped_and_the_overflow_is_reported():
    recs = people(*[f"r{i}@x.co.uk" for i in range(10)])
    plan = es.plan_send(SUBJECT, BODY, recs, max_batch=4)
    assert len(plan["campaigns"][0]["items"]) == 4
    over = [s for s in plan["skipped"] if "Over the limit of 4" in s["reason"]]
    assert len(over) == 6 and over[0]["email"] == "r4@x.co.uk"


def test_lookups_may_be_functions_and_are_called_once():
    calls = []
    plan = es.plan_send(SUBJECT, BODY, people("a@x.co.uk", "b@x.co.uk"),
                        suppressed=lambda emails: calls.append(("s", list(emails))) or {"b@x.co.uk"},
                        existing=lambda hashes: calls.append(("e", len(hashes))) or {})
    assert [c[0] for c in calls] == ["s", "e"] and calls[0][1] == ["a@x.co.uk", "b@x.co.uk"]
    assert [i["email"] for i in plan["singles"]] == ["a@x.co.uk"]


# ── status ──────────────────────────────────────────────────────────────────────────────────────

def test_status_never_moves_backwards():
    m = es.merge_status
    assert m("approved", "created") == "approved" and m("approved", "draft") == "approved"
    assert m("sent", "approved") == "sent" and m("sent", "pending") == "sent" and m("sent", "failed") == "sent"
    assert m("approved", "sent") == "sent" and m("sent", "replied") == "replied" and m("sent", "bounced") == "bounced"
    assert m("replied", "sent") == "replied"
    # unknown or empty answers change nothing
    assert m("approved", None) == "approved" and m("sent", "") == "sent" and m("sent", "queued_weirdly") == "sent"
    assert m(None, "sent") == "sent" and m("created", "approved") == "approved"


def test_labels_do_not_call_a_campaign_sent():
    assert "sending window" in es.status_label("approved", "campaign")
    assert es.status_label("approved", "campaign") != "Sent"
    assert "not approved" in es.status_label("created", "campaign")
    assert es.status_label("sent", "single") == "Sent"


# ── request bodies ──────────────────────────────────────────────────────────────────────────────

def test_campaign_payload_has_our_steps_and_never_personalise():
    plan = es.plan_send(SUBJECT, BODY, [{"email": "a@x.co.uk", "company": "Acme"}, {"email": "b@x.co.uk"}])
    group = plan["campaigns"][0]
    payload = es.campaign_payload(group["subject"], group["body"], group["items"])
    assert set(payload) == {"name", "goal", "audience", "emails", "steps", "leads"}
    assert "personalise" not in json.dumps(payload)
    assert payload["emails"] == 1
    assert payload["steps"] == [{"subject": SUBJECT, "body": BODY, "delay_days": 0}]
    assert payload["leads"] == [{"email": "a@x.co.uk", "company": "Acme"}, {"email": "b@x.co.uk"}]
    assert len(payload["name"]) <= 120 and len(payload["goal"]) <= 200


def test_idempotency_keys_are_stable_and_distinct():
    h1, h2 = es.dedupe_hash("test", "a@x.co.uk", "S", "B"), es.dedupe_hash("test", "b@x.co.uk", "S", "B")
    assert es.single_idempotency_key(h1) == es.single_idempotency_key(h1) != es.single_idempotency_key(h2)
    assert es.campaign_idempotency_key([h1, h2]) == es.campaign_idempotency_key([h2, h1])
    assert es.campaign_idempotency_key([h1, h2]) != es.campaign_idempotency_key([h1, h2], "approve")
    assert es.campaign_idempotency_key([h1]) != es.campaign_idempotency_key([h1, h2])
    assert max(len(es.single_idempotency_key(h1)), len(es.campaign_idempotency_key([h1, h2], "approve"))) <= 64


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
