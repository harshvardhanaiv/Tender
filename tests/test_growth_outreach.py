"""Run: python tests/test_growth_outreach.py   (plain asserts, no network, no database)

Which Growth Studio buyers become Outreach drafts and what each draft says (tender_app/growth_outreach.py).
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_app import growth_outreach as go  # noqa: E402
from tender_app import growth_studio as gs  # noqa: E402

SENDER = {"company_name": "Acme Heating Ltd", "sender_name": "Sam", "category": "Housing repairs & gas servicing"}
SUBJECT = "{{category}} support ahead of your contract renewal"
BODY = ("Hello {{buyer_contact}},\n\nWe noticed that {{buyer_name}}'s contract “{{contract_title}}” is due to end on "
        "{{renewal_date}}.\n\nKind regards,\n{{sender_name}}\n\n" + gs.OPT_OUT_LINE)


def campaign(subject=SUBJECT, body=BODY, name="Gas renewals"):
    return {"name": name, "subject": subject, "body": body}


def target(tid, buyer="Alpha Borough Council", email="procurement@alpha.example", **over):
    t = {
        "id": tid, "buyer_key": buyer.upper(), "buyer_name": buyer, "signal_type": "renewal", "included": True,
        "status": "not_sent", "contact_email": email,
        "snapshot": {"type": "renewal", "date": "2026-12-01",
                     "contracts": [{"title": "Gas servicing", "ends": "2026-12-01", "value": 250000, "suppliers": ["Old Co"]}]},
    }
    t.update(over)
    return t


def plan(targets, camp=None, opted=None, pushed=None, **kw):
    return go.plan_push(camp or campaign(), targets, opted or set(), SENDER, pushed or set(), **kw)


def reasons(p):
    return {s["target_id"]: s["reason"] for s in p["skipped"]}


def test_a_ready_buyer_becomes_one_filled_in_draft():
    p = plan([target(1)])
    assert not p["blockers"] and not p["skipped"] and p["deferred"] == 0
    (item,) = p["items"]
    pay = item["payload"]
    assert item["target_id"] == 1 and item["email"] == "procurement@alpha.example"
    assert pay["name"] == "Gas renewals - Alpha Borough Council"
    assert pay["emails"] == 1 and pay["leads"] == [{"email": "procurement@alpha.example", "company": "Alpha Borough Council"}]
    (step,) = pay["steps"]
    assert step["delay_days"] == 0
    assert step["subject"] == "Housing repairs & gas servicing support ahead of your contract renewal"
    assert "Alpha Borough Council's contract" in step["body"] and "Gas servicing" in step["body"]
    assert "{{" not in step["body"] and "{{" not in step["subject"]
    assert gs.OPT_OUT_LINE in step["body"]
    assert pay["goal"] == step["subject"]


def test_two_buyers_get_their_own_text():
    a = target(1, "Alpha Borough Council", "procurement@alpha.example")
    b = target(2, "Beta District Council", "contracts@beta.example")
    b["snapshot"]["contracts"][0]["title"] = "Boiler replacement"
    items = plan([a, b])["items"]
    assert [i["target_id"] for i in items] == [1, 2]
    assert "Gas servicing" in items[0]["payload"]["steps"][0]["body"]
    assert "Boiler replacement" in items[1]["payload"]["steps"][0]["body"]


def test_whole_push_is_refused_for_a_message_that_cannot_go():
    p = plan([target(1)], campaign(subject="", body=BODY))
    assert p["items"] == [] and "subject and the message" in p["blockers"][0]
    no_opt = plan([target(1)], campaign(body="Hello {{buyer_contact}}, we can help."))
    assert no_opt["items"] == [] and any("opt out" in b for b in no_opt["blockers"])
    unknown = plan([target(1)], campaign(body=BODY + " {{favourite_colour}}"))
    assert unknown["items"] == [] and any("favourite_colour" in b for b in unknown["blockers"])


def test_buyers_that_must_not_be_contacted_are_left_out_with_a_reason():
    ts = [
        target(1, included=False),
        target(2, status="opted_out"),
        target(3, buyer="Gamma Trust"),   # on the do-not-contact list below
        target(4, status="sent"),
        target(5, email=""),
        target(6, email="not-an-email"),
        target(7, email="jo.bloggs@delta.example"),
        target(8),
    ]
    p = plan(ts, opted={"GAMMA TRUST"})
    r = reasons(p)
    assert r[1].startswith("Switched off")
    assert "Opted out" in r[2] and "Opted out" in r[3]
    assert "contacted" in r[4]
    assert "No contact email" in r[5] and "not a valid" in r[6]
    assert "named person" in r[7]
    assert [i["target_id"] for i in p["items"]] == [8]


def test_a_named_person_address_goes_only_when_the_user_says_so():
    t = target(1, email="jo.bloggs@delta.example")
    assert plan([t])["items"] == []
    assert [i["target_id"] for i in plan([t], allow_personal=True)["items"]] == [1]


def test_a_buyer_already_sent_is_not_sent_twice():
    p = plan([target(1), target(2, "Beta", "contracts@beta.example")], pushed={1})
    assert [i["target_id"] for i in p["items"]] == [2]
    assert "Already sent through Outreach" in reasons(p)[1]


def test_a_buyer_with_a_value_missing_is_skipped_not_sent_blank():
    t = target(1)
    t["snapshot"]["contracts"][0]["ends"] = None
    t["snapshot"]["date"] = None
    p = plan([t])
    assert p["items"] == [] and "renewal_date" in reasons(p)[1]


def test_only_the_chosen_buyers():
    p = plan([target(1), target(2, "Beta", "contracts@beta.example"), target(3, "Gamma", "team@gamma.example")], only_ids=[2])
    assert [i["target_id"] for i in p["items"]] == [2] and p["skipped"] == []


def test_a_push_is_capped_and_says_how_many_wait():
    ts = [target(i, f"Buyer {i}", f"team{i}@b{i}.example") for i in range(1, 31)]
    p = plan(ts)
    assert len(p["items"]) == go.MAX_PER_PUSH == 25 and p["deferred"] == 5
    assert plan(ts, limit=3)["deferred"] == 27


def test_the_message_hash_follows_the_exact_text():
    a = go.message_hash("Subject", "Body")
    assert a == go.message_hash("Subject", "Body") and len(a) == 16
    assert a != go.message_hash("Subject", "Body ") and a != go.message_hash("Subject ", "Body")
    assert go.message_hash("a", "bc") != go.message_hash("ab", "c"), "the split between subject and body counts"
    item = plan([target(1)])["items"][0]
    step = item["payload"]["steps"][0]
    assert item["hash"] == go.message_hash(step["subject"], step["body"])
    edited = plan([target(1)], campaign(body=BODY + " Thank you."))["items"][0]
    assert edited["hash"] != item["hash"]


def test_a_tag_makes_a_different_idempotency_key():
    base = go.idempotency_key("alice@example.com", 7, 42)
    assert go.idempotency_key("alice@example.com", 7, 42, "abc12345") == base + "-abc12345"
    assert go.idempotency_key("alice@example.com", 7, 42, "approve-abc12345") != go.idempotency_key("alice@example.com", 7, 42, "abc12345")


def test_idempotency_key_is_stable_and_does_not_show_the_username():
    k = go.idempotency_key("alice@example.com", 7, 42)
    assert k == go.idempotency_key("alice@example.com", 7, 42)
    assert k != go.idempotency_key("bob@example.com", 7, 42) and k != go.idempotency_key("alice@example.com", 7, 43)
    assert "alice" not in k and k.startswith("tf-growth-") and k.endswith("-7-42")


def test_long_names_are_trimmed():
    p = plan([target(1, "B" * 300)], campaign(name="N" * 300))
    pay = p["items"][0]["payload"]
    assert len(pay["name"]) <= 120 and len(pay["audience"]) <= 120 and len(pay["leads"][0]["company"]) <= 120


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
