"""Run: python tests/test_bd_playbook_seller_scope.py   (plain asserts; needs the dev database, skips itself without one)

The BD Playbook describes the seller from a company profile. It used to read
`SELECT ... FROM company_profiles ORDER BY is_default DESC, id DESC LIMIT 1` with no username, so every
user got whichever user's default profile had the highest id (another company's text and metadata leaked
into the AI prompt). These tests drive the real route as throwaway users (zz_bdpb_*) against a throwaway
supplier and check the prompt that would be sent to the AI:

  * a user gets their own profile, never one with a higher id that belongs to someone else,
  * no profile at all means the generic defaults, and so does no username in the session,
  * no default profile means the user's first profile,
  * an explicit profile_id works from the query string and from the JSON body,
  * another user's profile_id is ignored: nothing of it reaches the prompt.

It never calls an AI provider and never touches the network.
"""
import json
import os
import sys
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# create_app() starts the email scheduler, which really sends mail to the addresses in the dev database.
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
os.environ["ENABLE_SCHEDULERS"] = "0"

RUN = uuid.uuid4().hex[:8]
USER_A = f"zz_bdpb_{RUN}_a"   # default profile + a second one
USER_B = f"zz_bdpb_{RUN}_b"   # created last: the highest profile id, and a default (what the old query returned for everyone)
USER_C = f"zz_bdpb_{RUN}_c"   # no profile
USER_D = f"zz_bdpb_{RUN}_d"   # two profiles, neither is the default
USERS = [USER_A, USER_B, USER_C, USER_D]
SUPPLIER_NUMBER = f"ZZBDPB{RUN}"
SUPPLIER_NAME = f"ZZ BDPB Target Supplier {RUN}"
CSRF = "test-csrf-token"
H = {"X-CSRF-Token": CSRF}
GENERIC = "Civenta Tender & Services Group"

NAMES = {
    "A1": f"ZZBDPB Alpha Default {RUN}",
    "A2": f"ZZBDPB Alpha Second {RUN}",
    "B1": f"ZZBDPB Bravo Secret {RUN}",
    "D1": f"ZZBDPB Delta First {RUN}",
    "D2": f"ZZBDPB Delta Later {RUN}",
}
TEXTS = {k: f"{v} is a company that does {k.lower()} work." for k, v in NAMES.items()}
SERVICES = {"B1": f"ZZBDPB bravo-only service {RUN}"}
IDS = {}
CAPTURED = []


class Skip(Exception):
    pass


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


def client(user=None):
    c = app().test_client()
    if user:
        with c.session_transaction() as s:
            s.update(logged_in=True, username=user, email=f"{user}@test.invalid", last_activity=time.time(), csrf_token=CSRF)
    return c


def cleanup():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM company_profiles WHERE username = ANY(%s)", (USERS,))
            cur.execute("DELETE FROM suppliers WHERE company_number = %s", (SUPPLIER_NUMBER,))
        conn.commit()
    finally:
        conn.close()


def make_fixtures():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO suppliers (company_number, name) VALUES (%s, %s)", (SUPPLIER_NUMBER, SUPPLIER_NAME))

            def profile(key, user, default):
                cur.execute(
                    "INSERT INTO company_profiles (username, name, profile_text, meta_json, is_default) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (user, NAMES[key], TEXTS[key], json.dumps({"services": [SERVICES[key]]} if key in SERVICES else {}), default),
                )
                IDS[key] = cur.fetchone()[0]

            profile("A1", USER_A, True)
            profile("A2", USER_A, False)
            profile("D1", USER_D, False)
            profile("D2", USER_D, False)
            profile("B1", USER_B, True)   # last, so the highest id of all four users
        conn.commit()
    finally:
        conn.close()
    assert IDS["B1"] > max(IDS["A1"], IDS["A2"], IDS["D1"], IDS["D2"])


def playbook_prompt(user, query="", body=None):
    """GET/POST the real route as `user` with the AI and registry calls replaced; return the prompt sent to the AI."""
    import server
    from tender_app.blueprints import suppliers_bp

    c = client(user)
    original_chat, original_ch = server.call_chat_api, suppliers_bp._fetch_ch_filings_and_officers
    CAPTURED.clear()

    def fake_chat(**kwargs):
        CAPTURED.append(kwargs)
        return None  # the route falls back to its sanitiser

    server.call_chat_api = fake_chat
    suppliers_bp._fetch_ch_filings_and_officers = lambda number, name: {"officers": [], "sic_codes": []}
    try:
        url = f"/api/suppliers/{SUPPLIER_NUMBER}/bd-playbook" + (f"?{query}" if query else "")
        r = c.post(url, json=body, headers=H) if body is not None else c.get(url, headers=H)
    finally:
        server.call_chat_api, suppliers_bp._fetch_ch_filings_and_officers = original_chat, original_ch
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:300])
    assert len(CAPTURED) == 1, "the AI should have been asked exactly once"
    return CAPTURED[0]["user"], r.get_json()


def assert_only(prompt, keep, drop=()):
    assert NAMES[keep] in prompt, f"{keep} missing from the prompt"
    for key in NAMES:
        if key != keep:
            assert NAMES[key] not in prompt, f"{key} leaked into the prompt of a user who does not own it"
            assert TEXTS[key] not in prompt


# ─────────────────────────────────────────────── tests ───────────────────────────────────────────────

def test_a_user_gets_their_own_default_profile_not_a_higher_id_one_from_someone_else():
    prompt, _ = playbook_prompt(USER_A)
    assert_only(prompt, "A1")
    assert TEXTS["A1"] in prompt
    assert SERVICES["B1"] not in prompt


def test_the_other_user_still_gets_theirs():
    prompt, data = playbook_prompt(USER_B)
    assert_only(prompt, "B1")
    assert SERVICES["B1"] in data["seller_services"]


def test_a_user_with_no_profile_gets_the_generic_defaults():
    prompt, data = playbook_prompt(USER_C)
    assert GENERIC in prompt
    for key in NAMES:
        assert NAMES[key] not in prompt, f"{key} leaked to a user with no profile"
    assert SERVICES["B1"] not in data["seller_services"]
    assert "Public Sector Subcontracting & Delivery Surge Support" in data["seller_services"]


def test_no_username_in_the_session_gets_the_generic_defaults():
    from flask import session
    from tender_app.blueprints import suppliers_bp

    conn = db()
    try:
        for logged_in_as in (None, ""):
            with app().test_request_context("/"):
                if logged_in_as is not None:
                    session["username"] = logged_in_as
                context, services = suppliers_bp._resolve_seller_services(conn.cursor(), conn, {}, {})
            assert context.startswith(GENERIC), context
            assert all(NAMES[k] not in context for k in NAMES)
            assert SERVICES["B1"] not in services
    finally:
        conn.close()


def test_a_user_without_a_default_profile_gets_their_first_profile():
    prompt, _ = playbook_prompt(USER_D)
    assert_only(prompt, "D1")


def test_profile_id_in_the_query_string_is_honoured():
    prompt, _ = playbook_prompt(USER_A, f"profile_id={IDS['A2']}")
    assert_only(prompt, "A2")


def test_profile_id_in_the_json_body_is_honoured():
    prompt, _ = playbook_prompt(USER_A, body={"profile_id": IDS["A2"]})
    assert_only(prompt, "A2")
    prompt, _ = playbook_prompt(USER_D, body={"profile_id": str(IDS["D2"])})
    assert_only(prompt, "D2")


def test_another_users_profile_id_is_ignored_in_the_query_string():
    prompt, data = playbook_prompt(USER_A, f"profile_id={IDS['B1']}")
    assert_only(prompt, "A1")  # falls back to A's own default, B's profile is never read
    assert SERVICES["B1"] not in data["seller_services"]
    prompt, _ = playbook_prompt(USER_C, f"profile_id={IDS['B1']}")
    assert GENERIC in prompt and NAMES["B1"] not in prompt


def test_another_users_profile_id_is_ignored_in_the_json_body():
    prompt, data = playbook_prompt(USER_A, body={"profile_id": IDS["B1"]})
    assert_only(prompt, "A1")
    assert SERVICES["B1"] not in data["seller_services"]
    prompt, _ = playbook_prompt(USER_D, body={"profile_id": IDS["B1"]})
    assert_only(prompt, "D1")


def test_a_nonsense_profile_id_does_not_break_the_playbook():
    for query in ("profile_id=abc", "profile_id=", "profile_id=-1", "profile_id=99999999999"):
        prompt, _ = playbook_prompt(USER_A, query)
        assert_only(prompt, "A1")


# ─────────────────────────────────────────────── runner ──────────────────────────────────────────────

def main() -> None:
    try:
        db().close()
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP: the database is not reachable from here ({type(exc).__name__}: {exc})")
        return
    app()  # a failure to build the app is a real failure, not a reason to skip
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    tests.sort(key=lambda item: item[1].__code__.co_firstlineno)
    failed, skipped = [], []
    cleanup()  # whatever a crashed earlier run left behind
    try:
        make_fixtures()
        for name, fn in tests:
            started = time.time()
            try:
                fn()
                print(f"ok    {name} ({time.time() - started:.1f}s)")
            except Skip as why:
                skipped.append(name)
                print(f"skip  {name}: {why}")
            except Exception:  # noqa: BLE001 - report every failure, not just the first
                failed.append(name)
                print(f"FAIL  {name}")
                traceback.print_exc()
    finally:
        cleanup()
    print(f"\n{len(tests) - len(failed) - len(skipped)} passed, {len(skipped)} skipped, {len(failed)} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
