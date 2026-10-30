"""Re-test 3 W1: an account that is not verified for a public body cannot change, publish or delete a market engagement.
The server answers 403 "verification_required" to every write, whatever the page shows; reading stays open.

Run: python tests/test_engagement_verification.py  (needs the dev database)
"""
import os
import sys
import time
import unittest

os.environ["ENABLE_SCHEDULERS"] = "0"
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import create_app  # noqa: E402


def _client(email):
    try:
        c = create_app().test_client()
    except (Exception, SystemExit) as exc:
        raise unittest.SkipTest(f"no database: {exc}")
    with c.session_transaction() as s:
        s.update(logged_in=True, username=email, email=email, last_activity=time.time(), csrf_token="tok")
    return c


def test_every_write_is_refused_for_an_unverified_account():
    c = _client("someone@gmail.com")
    h = {"X-CSRF-Token": "tok"}
    calls = [
        ("post", "/api/market-engagement", {"title": "x"}),
        ("patch", "/api/market-engagement/1", {"title": "y"}),
        ("post", "/api/market-engagement/1/publish", {}),
        ("post", "/api/market-engagement/1/suppliers", {"name": "z"}),
        ("delete", "/api/market-engagement/1", None),
    ]
    for method, url, body in calls:
        r = getattr(c, method)(url, json=body, headers=h) if body is not None else getattr(c, method)(url, headers=h)
        assert r.status_code == 403, (method, url, r.status_code, r.get_json())
        assert (r.get_json() or {}).get("verification_required") is True, (method, url, r.get_json())


def test_reading_stays_open():
    c = _client("someone@gmail.com")
    r = c.get("/api/market-engagement")
    assert r.status_code == 200, (r.status_code, r.get_json())


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok   ", name)
    print(f"{n} passed")
