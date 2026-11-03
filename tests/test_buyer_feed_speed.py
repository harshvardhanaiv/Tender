"""Buyer intelligence speed (Re-test 4 B7): the feed and its searches must stay fast. Run with `python tests/test_buyer_feed_speed.py`
before every release (RELEASE_CHECKLIST.md). The limits are looser than the 1 s target because this runs against a small local copy
of the data and a cold process; live is checked with scripts/check_buyer_speed.py."""
import sys
import time

sys.path.insert(0, ".")
from server import create_app  # noqa: E402
from tender_app.blueprints import buyers_bp as b  # noqa: E402

FEED = "/api/buyers/opportunity-feed"
PROBE = "zz_speed_probe"


def _client():
    app = create_app()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["username"] = PROBE
        s["last_activity"] = time.time()
    return c


def _timed(c, url):
    t = time.time()
    r = c.get(url)
    assert r.status_code == 200, (url, r.status_code, r.get_data(as_text=True)[:200])
    return time.time() - t, r.get_json()


def test_a_repeated_feed_request_is_served_from_the_last_result():
    c = _client()
    _timed(c, FEED)
    again, data = _timed(c, FEED)
    assert data["ok"] and again < 0.3, again


def test_a_name_search_does_not_scan_every_award():
    c = _client()
    _timed(c, FEED)   # warms the buyer rows
    took, data = _timed(c, FEED + "?q=council")
    assert data["ok"] and took < 2.0, took
    took, data = _timed(c, FEED + "?q=MOD")
    assert took < 2.0, took


def test_top_suppliers_are_kept_between_requests():
    conn = b._get_connection()
    cur = conn.cursor()
    name = "ZZ Speed Probe Buyer"
    try:
        b._store_top_suppliers(cur, conn, {name: ["Acme Ltd", "Bolt plc"]})
        assert b._stored_top_suppliers(cur, conn, [name, "ZZ Nobody"]) == {name: ["Acme Ltd", "Bolt plc"]}
        b._store_top_suppliers(cur, conn, {name: ["Cara Ltd"]})
        assert b._stored_top_suppliers(cur, conn, [name])[name] == ["Cara Ltd"]
    finally:
        cur.execute("DELETE FROM buyer_top_suppliers WHERE authority_name = %s", (name,))
        conn.commit()
        conn.close()


def test_a_frameworks_only_search_returns_buyers_and_a_repeat_is_instant():
    """It used to de-duplicate every award, twice (over 40 s live). The buyers must still come back, with the real total."""
    c = _client()
    first, data = _timed(c, "/api/buyers/search?frameworks_only=1")
    assert data["ok"] and data["buyers"] and data["total"] >= len(data["buyers"]), data.get("total")
    assert first < 20, first
    again, data2 = _timed(c, "/api/buyers/search?frameworks_only=1")
    assert again < 0.5 and data2["total"] == data["total"], again
    _timed(c, "/api/buyers/search?date_range=2")


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok   ", name)
    print(f"{n} passed")
