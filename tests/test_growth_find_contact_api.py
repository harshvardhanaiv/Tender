"""Run: python tests/test_growth_find_contact_api.py   (plain asserts; needs the dev database, skips itself without one)

POST /api/growth/campaigns/<id>/targets/<tid>/find-contact through the real Flask app, as throwaway users
(zz_findcontact_*). The lookup itself is a fake (no network, nobody is emailed)."""
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
os.environ["ENABLE_SCHEDULERS"] = "0"
os.environ["ENABLE_GROWTH_STUDIO"] = "1"

RUN = uuid.uuid4().hex[:8]
USER_A = f"zz_findcontact_{RUN}_a"
USER_B = f"zz_findcontact_{RUN}_b"
CSRF = "test-csrf-token"
H = {"X-CSRF-Token": CSRF}
NOTICE = "https://www.find-tender.service.gov.uk/Notice/012345-2026"
_app = None


def app():
    global _app
    if _app is None:
        from server import create_app
        _app = create_app()
    return _app


def db():
    from tender_app.db import get_db_connection
    return get_db_connection()


def client(user):
    c = app().test_client()
    with c.session_transaction() as s:
        s.update(logged_in=True, username=user, email=f"{user}@test.invalid", last_activity=time.time(), csrf_token=CSRF)
    return c


def buyer(n):
    return f"Zz Findcontact {RUN} Borough {n}"


def make_campaign(user, targets):
    """targets: [(buyer name, email, status)] -> (campaign id, [target ids])"""
    snap = json.dumps({"type": "renewal", "date": "2026-12-01", "links": [{"label": "Award notice", "url": NOTICE}]})
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO growth_campaigns (username, name, category_label, channel, status, subject, body, last_activity_at)
                           VALUES (%s, %s, 'Repairs', 'email', 'draft', 's', 'b', NOW()) RETURNING id""", (user, f"findcontact {uuid.uuid4().hex[:6]}"))
            cid = cur.fetchone()[0]
            ids = []
            for i, (name, email, status) in enumerate(targets):
                cur.execute("""INSERT INTO growth_campaign_targets (campaign_id, buyer_key, buyer_name, signal_key, signal_type, snapshot_json, included, contact_email, status)
                               VALUES (%s, %s, %s, %s, 'renewal', %s, TRUE, %s, %s) RETURNING id""",
                            (cid, name.upper(), name, f"renewal:{name.lower()}:{i}", snap, email or None, status))
                ids.append(cur.fetchone()[0])
        conn.commit()
        return cid, ids
    finally:
        conn.close()


def cleanup():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM growth_campaigns WHERE username = ANY(%s)", ([USER_A, USER_B],))
            cur.execute("DELETE FROM buyer_contact_cache WHERE buyer_key LIKE %s", (f"ZZ FINDCONTACT {RUN}%",))
        conn.commit()
    finally:
        conn.close()


class Fake:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, name, urls, **kw):
        self.calls.append((name, list(urls)))
        return self.results[name]


def with_fake(fake, fn):
    from tender_app import buyer_contacts as bc
    real = bc.find_buyer_contact
    bc.find_buyer_contact = fake
    try:
        fn()
    finally:
        bc.find_buyer_contact = real


def found(email, role=True, source="notice"):
    return {"status": "found", "email": email, "source": source, "source_url": NOTICE, "website": None,
            "candidates": [{"email": email, "source": source, "source_url": NOTICE, "role": role}]}


def post(c, cid, tid, qs=""):
    r = c.post(f"/api/growth/campaigns/{cid}/targets/{tid}/find-contact{qs}", headers=H, json={})
    return r.status_code, (r.get_json(silent=True) or {})


def target(body, tid):
    return next(t for t in body["targets"] if t["id"] == tid)


def test_fills_an_empty_box_with_a_role_inbox_and_says_where_it_came_from():
    fake = Fake({buyer(1): found("procurement@one.example")})

    def run():
        c = client(USER_A)
        cid, (tid,) = make_campaign(USER_A, [(buyer(1), "", "not_sent")])
        code, body = post(c, cid, tid)
        assert code == 200, body
        t = target(body, tid)
        assert body["find_contact"]["status"] == "found" and t["contact_email"] == "procurement@one.example"
        assert t["contact_found"]["source"] == "notice" and t["contact_found"]["url"] == NOTICE
        assert fake.calls == [(buyer(1), [NOTICE])]                  # the stored notice link was given to the lookup
    with_fake(fake, run)


def test_an_address_the_user_already_has_is_never_replaced():
    fake = Fake({buyer(2): found("other@two.example")})

    def run():
        c = client(USER_A)
        cid, (tid,) = make_campaign(USER_A, [(buyer(2), "mine@two.example", "not_sent")])
        code, body = post(c, cid, tid)
        assert code == 200 and body["find_contact"]["status"] == "already"
        assert target(body, tid)["contact_email"] == "mine@two.example" and fake.calls == []
    with_fake(fake, run)


def test_a_named_persons_address_is_not_filled_in():
    fake = Fake({buyer(3): found("jane.smith@three.example", role=False)})

    def run():
        c = client(USER_A)
        cid, (tid,) = make_campaign(USER_A, [(buyer(3), "", "not_sent")])
        code, body = post(c, cid, tid)
        assert code == 200 and body["find_contact"]["status"] == "personal_only"
        assert not target(body, tid)["contact_email"]
    with_fake(fake, run)


def test_a_named_contact_is_offered_to_the_user_but_still_not_filled_in():
    fake = Fake({buyer(3): found("jane.smith@three.example", role=False, source="notice")})

    def run():
        c = client(USER_A)
        cid, (tid,) = make_campaign(USER_A, [(buyer(3), "", "not_sent")])
        code, body = post(c, cid, tid)
        assert code == 200 and body["find_contact"]["status"] == "personal_only"
        assert body["find_contact"]["suggestion"] == {"email": "jane.smith@three.example", "source": "notice", "url": NOTICE}
        assert not target(body, tid)["contact_email"], "offered, never saved until the user accepts"
        # accepting is an ordinary edit of the box
        r = c.patch(f"/api/growth/campaigns/{cid}/targets/{tid}", headers=H, json={"contact_email": body["find_contact"]["suggestion"]["email"]})
        assert r.status_code == 200 and target(r.get_json(), tid)["contact_email"] == "jane.smith@three.example"
    with_fake(fake, run)


def test_a_role_inbox_carries_no_suggestion():
    fake = Fake({buyer(8): found("procurement@eight.example")})

    def run():
        cid, (tid,) = make_campaign(USER_A, [(buyer(8), "", "not_sent")])
        body = post(client(USER_A), cid, tid)[1]
        assert body["find_contact"]["status"] == "found" and "suggestion" not in body["find_contact"]
    with_fake(fake, run)


def test_an_answer_from_the_older_weaker_lookup_is_looked_up_again():
    fake = Fake({buyer(9): found("procurement@nine.example")})

    def run():
        c = client(USER_A)
        conn = db()
        try:
            with conn.cursor() as cur:
                from tender_app.db_ext import create_buyer_contact_cache
                create_buyer_contact_cache(cur)
                cur.execute("""INSERT INTO buyer_contact_cache (buyer_key, status, email, candidates, source, source_url, website, checked_at, lookup_version)
                               VALUES (%s, 'not_found', NULL, '[]', NULL, NULL, NULL, (NOW() AT TIME ZONE 'utc'), 1)""", (buyer(9).upper(),))
            conn.commit()
        finally:
            conn.close()
        cid, (tid,) = make_campaign(USER_A, [(buyer(9), "", "not_sent")])
        code, body = post(c, cid, tid)
        assert body["find_contact"]["status"] == "found" and len(fake.calls) == 1, "a fresh version-1 'not found' must not be believed"
        cid2, (t2,) = make_campaign(USER_A, [(buyer(9), "", "not_sent")])
        post(c, cid2, t2)
        assert len(fake.calls) == 1, "the new answer is cached as usual"
    with_fake(fake, run)


def test_nothing_published_leaves_the_box_empty_and_is_remembered():
    fake = Fake({buyer(4): {"status": "not_found", "email": None, "candidates": [], "website": None}})

    def run():
        c = client(USER_A)
        cid, (t1,) = make_campaign(USER_A, [(buyer(4), "", "not_sent")])
        cid2, (t2,) = make_campaign(USER_A, [(buyer(4), "", "not_sent")])
        assert post(c, cid, t1)[1]["find_contact"]["status"] == "not_found"
        assert post(c, cid2, t2)[1]["find_contact"]["status"] == "not_found"
        assert len(fake.calls) == 1                                  # the same buyer is looked up once, not once per campaign
    with_fake(fake, run)


def test_a_cached_found_inbox_serves_another_campaign():
    fake = Fake({buyer(5): found("tenders@five.example")})

    def run():
        c = client(USER_A)
        cid, (t1,) = make_campaign(USER_A, [(buyer(5), "", "not_sent")])
        cid2, (t2,) = make_campaign(USER_A, [(buyer(5), "", "not_sent")])
        post(c, cid, t1)
        code, body = post(c, cid2, t2)
        assert target(body, t2)["contact_email"] == "tenders@five.example" and len(fake.calls) == 1
    with_fake(fake, run)


def test_a_buyer_who_opted_out_is_skipped():
    fake = Fake({buyer(6): found("procurement@six.example")})

    def run():
        c = client(USER_A)
        cid, (tid,) = make_campaign(USER_A, [(buyer(6), "", "opted_out")])
        code, body = post(c, cid, tid)
        assert code == 200 and body["find_contact"]["status"] == "skipped" and fake.calls == []
    with_fake(fake, run)


def test_another_users_campaign_is_not_reachable():
    fake = Fake({buyer(7): found("procurement@seven.example")})

    def run():
        cid, (tid,) = make_campaign(USER_A, [(buyer(7), "", "not_sent")])
        code, body = post(client(USER_B), cid, tid)
        assert code in (403, 404) and fake.calls == []
        assert client(USER_A).post(f"/api/growth/campaigns/{cid}/targets/999999999/find-contact", headers=H, json={}).status_code == 404
    with_fake(fake, run)


if __name__ == "__main__":
    try:
        db().close()
        app()
    except Exception as exc:  # noqa: BLE001
        print("SKIP (no dev database):", exc)
        sys.exit(0)
    cleanup()
    failures = 0
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                try:
                    fn()
                    print("PASS", name)
                except Exception as exc:  # noqa: BLE001
                    import traceback
                    traceback.print_exc()
                    failures += 1
                    print("FAIL", name, "->", repr(exc))
    finally:
        cleanup()
    print("failures:", failures)
    sys.exit(1 if failures else 0)
