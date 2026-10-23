"""Run: python tests/test_growth_rich_email_api.py   (plain asserts; needs the dev database, skips itself without one)

A campaign's company, its formatted (HTML) message and its attached files, through the real Flask app and its test client, as
throwaway users. The mail server and Outreach are fakes: nothing is ever sent.

  * the company a campaign speaks for can be changed (own profiles only) and fills {{company_name}} in the preview,
  * a formatted message is cleaned, its plain-text twin becomes the body, and the preview shows it filled in for the buyer,
  * files can be attached and removed, bad ones are refused one by one, and nobody else can touch them,
  * Send mail from the user's own account carries the HTML and the files; Outreach refuses files and plain-texts the formatting.
"""
import io
import sys
import traceback

import test_growth_email_account_api as ea
import test_growth_outreach_api as base
from test_growth_outreach_api import FAKE, OPT_OUT, USER_A, USER_B, call, client, db, make_campaign

HTML = ("<p>Hello {{buyer_contact}},</p><p><b>{{buyer_name}}</b> ends on {{renewal_date}}. Write to {{company_name}}.</p>"
        "<p>" + OPT_OUT + "</p>")


def profile(user, name, text="We fit boilers."):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO company_profiles (username, name, profile_text, meta_json, is_default) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (username, name) DO UPDATE SET profile_text = EXCLUDED.profile_text RETURNING id",
                        (user, name, text, "{}", False))
            pid = cur.fetchone()[0]
        conn.commit()
        return pid
    finally:
        conn.close()


def cleanup():
    ea.cleanup()
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM company_profiles WHERE username = ANY(%s)", (base.USERS,))
        conn.commit()
    finally:
        conn.close()


def company_campaign(c, user=USER_A):
    """A campaign that speaks for a company, so {{company_name}} has a value."""
    cid, ids = make_campaign(user)
    assert call(c, "patch", f"/api/growth/campaigns/{cid}", {"profile_id": profile(user, "Acme Heating Ltd")})[0] == 200
    return cid, ids


def upload(c, cid, *files):
    data = {"files": [(io.BytesIO(body), name) for name, body in files]}
    r = c.post(f"/api/growth/campaigns/{cid}/attachments", data=data, headers=base.H, content_type="multipart/form-data")
    return r.status_code, (r.get_json(silent=True) or {})


def test_the_company_of_a_campaign_can_be_changed_to_one_of_the_users_own():
    ea.setup()
    c = client(USER_A)
    cid, _ = make_campaign(USER_A)
    acme, other = profile(USER_A, "Acme Heating Ltd"), profile(USER_B, "Someone Else Ltd")
    code, body, _ = call(c, "patch", f"/api/growth/campaigns/{cid}", {"profile_id": acme})
    assert code == 200 and body["campaign"]["profile_id"] == acme and body["campaign"]["profile_name"] == "Acme Heating Ltd"
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/preview", {"subject": "S {{company_name}}", "body": "Hi from {{company_name}}"})
    assert code == 200 and body["subject"] == "S Acme Heating Ltd" and body["body"] == "Hi from Acme Heating Ltd"
    assert call(c, "patch", f"/api/growth/campaigns/{cid}", {"profile_id": other})[0] == 404, "another user's company is not offered"
    code, body, _ = call(c, "patch", f"/api/growth/campaigns/{cid}", {"profile_id": None})
    assert code == 200 and body["campaign"]["profile_id"] is None


def test_a_formatted_message_is_cleaned_saved_with_its_text_twin_and_previewed_for_the_buyer():
    ea.setup()
    c = client(USER_A)
    cid, ids = make_campaign(USER_A)
    dirty = HTML + '<script>alert(1)</script><img src=x onerror=alert(2)>'
    code, body, _ = call(c, "patch", f"/api/growth/campaigns/{cid}", {"body_html": dirty})
    assert code == 200, body
    camp = body["campaign"]
    assert "<script" not in camp["body_html"] and "<img" not in camp["body_html"] and "<b>{{buyer_name}}</b>" in camp["body_html"]
    assert "alert" not in camp["body"] and "{{buyer_name}} ends on" in camp["body"] and "<" not in camp["body"]
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/preview", {"target_id": ids[0]})
    assert code == 200 and "<b>Alpha Borough Council</b>" in body["html"] and "{{" not in body["html"]
    # writing plain text again drops the formatted version
    code, body, _ = call(c, "patch", f"/api/growth/campaigns/{cid}", {"body": "Plain " + OPT_OUT})
    assert code == 200 and body["campaign"]["body_html"] == ""
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/preview", {"target_id": ids[0]})
    assert body["html"] is None
    # an unsaved formatted draft can be previewed without saving it
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/preview", {"target_id": ids[0], "body_html": "<p><i>Draft</i> {{buyer_name}}</p>"})
    assert body["html"] == "<p><i>Draft</i> Alpha Borough Council</p>" and body["body"] == "Draft Alpha Borough Council"


def test_files_are_attached_removed_and_checked_one_by_one():
    ea.setup()
    c = client(USER_A)
    cid, _ = make_campaign(USER_A)
    code, body = upload(c, cid, ("Case study.pdf", b"%PDF-1.4 hello"), ("setup.exe", b"MZ"), ("empty.txt", b""))
    assert code == 200
    assert [a["name"] for a in body["attachments"]] == ["Case study.pdf"] and body["attachments"][0]["size"] == len(b"%PDF-1.4 hello")
    assert body["attachments"][0]["type"] == "application/pdf"
    assert len(body["rejected"]) == 2 and any("setup.exe" in r and "block" in r for r in body["rejected"]) and any("empty" in r for r in body["rejected"])
    assert call(c, "get", f"/api/growth/campaigns/{cid}")[1]["attachments"][0]["name"] == "Case study.pdf"
    assert upload(c, cid)[0] == 400, "no file chosen"
    fid = body["attachments"][0]["id"]
    assert call(client(USER_B), "delete", f"/api/growth/campaigns/{cid}/attachments/{fid}")[0] == 404, "another user cannot remove it"
    code, body, _ = call(c, "delete", f"/api/growth/campaigns/{cid}/attachments/{fid}")
    assert code == 200 and body["attachments"] == []
    assert call(c, "delete", f"/api/growth/campaigns/{cid}/attachments/{fid}")[0] == 404


def test_a_campaign_carries_at_most_ten_files_and_ten_megabytes():
    ea.setup()
    c = client(USER_A)
    cid, _ = make_campaign(USER_A)
    for i in range(10):
        assert upload(c, cid, (f"f{i}.txt", b"x"))[0] == 200
    code, body = upload(c, cid, ("one-more.txt", b"x"))
    assert code == 200 and len(body["attachments"]) == 10 and "at most 10" in body["rejected"][0]
    cid2, _ = make_campaign(USER_A)
    assert upload(c, cid2, ("a.bin.pdf", b"x" * (6 * 1024 * 1024)))[1]["attachments"][0]["size"] == 6 * 1024 * 1024
    code, body = upload(c, cid2, ("b.pdf", b"x" * (5 * 1024 * 1024)))
    assert body["attachments"] and len(body["attachments"]) == 1 and "together" in body["rejected"][0]


def test_another_user_cannot_attach_to_my_campaign():
    ea.setup()
    cid, _ = make_campaign(USER_A)
    assert upload(client(USER_B), cid, ("a.pdf", b"x"))[0] == 404


def test_sending_from_the_own_account_carries_the_html_and_the_files():
    ea.setup()
    c = client(USER_A)
    ea.save(c)
    cid, ids = company_campaign(c)
    assert call(c, "patch", f"/api/growth/campaigns/{cid}", {"body_html": HTML})[0] == 200
    assert upload(c, cid, ("Case study.pdf", b"%PDF-1.4 hello"))[0] == 200
    ea.SMTP.sent.clear()
    code, body, _ = ea.send(c, cid)
    assert code == 200 and len(body["sent"]) == 2, body
    first = ea.SMTP.sent[0]
    html = first.get_body(preferencelist=("html",)).get_content()
    assert "<b>Alpha Borough Council</b>" in html and "{{" not in html
    assert "Alpha Borough Council ends on" in first.get_body(preferencelist=("plain",)).get_content()
    assert [(a.get_filename(), a.get_content()) for a in first.iter_attachments()] == [("Case study.pdf", b"%PDF-1.4 hello")]


def test_changing_the_formatting_or_the_files_makes_a_new_message_for_the_same_buyer():
    ea.setup()
    c = client(USER_A)
    ea.save(c)
    cid, _ = company_campaign(c)
    call(c, "patch", f"/api/growth/campaigns/{cid}", {"body_html": HTML})
    from tender_app import growth_outreach as go
    conn = db()
    try:
        from tender_app.blueprints import growth_bp
        camp, targets = growth_bp._load_campaign(conn, USER_A, cid)
        sender = growth_bp._sender_for(conn, USER_A, camp)
        a = go.plan_push(camp, targets, set(), sender, set())["items"][0]["hash"]
        b = go.plan_push(camp, targets, set(), sender, set(), files=[{"id": 1, "size_bytes": 5}])["items"][0]["hash"]
    finally:
        conn.close()
    assert a != b


def test_outreach_refuses_files_and_sends_formatting_as_plain_text_with_a_warning():
    ea.setup(outreach_key=base.TEST_KEY)
    c = client(USER_A)
    cid, _ = company_campaign(c)
    assert call(c, "patch", f"/api/growth/campaigns/{cid}", {"body_html": HTML})[0] == 200
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid}/outreach", {})
    assert code == 200, body
    assert any("plain text" in w for w in body["warnings"]), body["warnings"]
    sent_bodies = [x for x in FAKE.calls if "/email/" in str(x)]
    assert sent_bodies and all("<b>" not in str(x) for x in sent_bodies)
    cid2, _ = make_campaign(USER_A)
    assert upload(c, cid2, ("a.pdf", b"x"))[0] == 200
    code, body, _ = call(c, "post", f"/api/growth/campaigns/{cid2}/outreach", {})
    assert code == 400 and body["code"] == "attachments_need_own_account" and "own email account" in body["error"]


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
