"""Run: python tests/test_watchlist_api.py   (plain asserts; needs the dev database, skips itself without one)

/api/watchlist through the real Flask app, as throwaway users (zz_watchlist_*)."""
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
os.environ["ENABLE_SCHEDULERS"] = "0"

RUN = uuid.uuid4().hex[:8]
USER_A, USER_B = f"zz_watchlist_{RUN}_a", f"zz_watchlist_{RUN}_b"
CSRF = "test-csrf-token"
H = {"X-CSRF-Token": CSRF}


def client(app, user):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(logged_in=True, username=user, email=f"{user}@test.invalid", last_activity=time.time(), csrf_token=CSRF)
    return c


def test_pure_validation():
    from tender_app import entity_watchlist as ew
    assert ew.clean_entry({"kind": "buyer", "entity_key": "X", "name": " Leeds  City "})["name"] == "Leeds City"
    assert ew.clean_entry({"kind": "supplier", "entity_key": 12, "name": "A", "added_from": "nope"})["added_from"] == "other"
    for bad in (None, {}, {"kind": "tender", "entity_key": "1", "name": "n"}, {"kind": "buyer", "entity_key": "", "name": "n"}, {"kind": "buyer", "entity_key": "1", "name": ""}):
        assert ew.clean_entry(bad) is None


def run_api():
    from server import create_app
    from tender_app.db import get_db_connection
    app = create_app()
    a, b = client(app, USER_A), client(app, USER_B)
    try:
        assert app.test_client().get("/api/watchlist").status_code == 401
        r = a.post("/api/watchlist", json={"kind": "supplier", "entity_key": "42", "name": "Acme Ltd", "added_from": "supplier_intelligence"}, headers=H)
        assert r.status_code == 200 and r.get_json()["item"]["added_from_label"] == "Supplier Intelligence", r.get_json()
        a.post("/api/watchlist", json={"kind": "supplier", "entity_key": "42", "name": "Acme Ltd", "added_from": "growth_studio"}, headers=H)   # again: first source kept
        a.post("/api/watchlist", json={"kind": "buyer", "entity_key": "LEEDS CITY COUNCIL", "name": "Leeds City Council", "added_from": "buyer_intelligence"}, headers=H)
        items = a.get("/api/watchlist").get_json()["items"]
        assert len(items) == 2 and {i["added_from"] for i in items} == {"supplier_intelligence", "buyer_intelligence"}, items
        assert b.get("/api/watchlist").get_json()["items"] == []                              # not shared
        assert a.post("/api/watchlist", json={"kind": "tender", "entity_key": "1", "name": "n"}, headers=H).status_code == 400
        assert a.patch("/api/watchlist/supplier/42", json={"note": "call in Q1"}, headers=H).get_json()["changed"] == 1
        assert b.delete("/api/watchlist/supplier/42", headers=H).get_json()["changed"] == 0     # B cannot remove A's
        assert a.delete("/api/watchlist/supplier/42", headers=H).get_json()["changed"] == 1
        assert [i["entity_key"] for i in a.get("/api/watchlist").get_json()["items"]] == ["LEEDS CITY COUNCIL"]
        # the pipeline remembers where a tender was added from, and the first source sticks
        a.post("/api/pipeline", json={"tender_key": f"zz-{RUN}", "title": "T", "source": "FTS", "stage": "watching", "added_from": "open_opportunity"}, headers=H)
        a.post("/api/pipeline", json={"tender_key": f"zz-{RUN}", "title": "T", "source": "FTS", "stage": "bidding", "added_from": "tender_search"}, headers=H)
        it = [i for i in a.get("/api/pipeline").get_json()["items"] if i["tender_key"] == f"zz-{RUN}"][0]
        assert it["added_from"] == "open_opportunity" and it["stage"] == "bidding", it
    finally:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM entity_watchlist WHERE username LIKE %s", (f"zz_watchlist_{RUN}%",))
            cur.execute("DELETE FROM pipeline WHERE username LIKE %s", (f"zz_watchlist_{RUN}%",))
        conn.commit()
        conn.close()


if __name__ == "__main__":
    test_pure_validation()
    print("PASS test_pure_validation")
    try:
        run_api()
        print("PASS api")
    except Exception as exc:  # noqa: BLE001
        if exc.__class__.__name__ in ("OperationalError", "RuntimeError") and "connect" in str(exc).lower():
            print("SKIP api (no database)")
        else:
            raise
    print("failures: 0")
