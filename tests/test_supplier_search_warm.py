"""Re-test 4 R8: common supplier searches are answered from a warm cache. Run: python tests/test_supplier_search_warm.py (dev database, read only)."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENABLE_SCHEDULERS", "0")
os.environ.setdefault("ENABLE_EMAIL_SCHEDULER", "0")

from server import create_app  # noqa: E402
from tender_app.blueprints import suppliers_bp as sb  # noqa: E402

app = create_app()


def test_the_cache_key_ignores_argument_order_and_how_a_space_is_written():
    keys = []
    for qs in ("q=Capita%20plc&page=1&per_page=25", "per_page=25&page=1&q=Capita+plc", "q=Capita+plc&page=1&per_page=25&sort="):
        with app.test_request_context("/api/suppliers/search?" + qs):
            keys.append(sb._search_cache_key())
    assert len(set(keys)) == 1 and "q=Capita plc" in keys[0], keys


def test_warming_keeps_the_first_page_and_the_next_request_is_served_from_it():
    sb._supplier_search_cache.clear()
    assert sb.warm_supplier_search_once(app, terms=("software",), pause=0) == 2
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(logged_in=True, username="zz_warm_probe", last_activity=time.time())
    t = time.time()
    r = c.get("/api/suppliers/search?q=software&page=1&per_page=25")
    assert r.status_code == 200 and r.get_json()["ok"] and time.time() - t < 0.15, time.time() - t


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok   ", name)
    print(f"{n} passed")
