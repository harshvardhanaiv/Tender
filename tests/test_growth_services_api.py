"""Run: python tests/test_growth_services_api.py   (plain asserts; needs the dev database)

/api/growth/services and several services in /api/growth/signals, as throwaway users (zz_gsvc_*)."""
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
A, B = f"zz_gsvc_{RUN}_a", f"zz_gsvc_{RUN}_b"
H = {"X-CSRF-Token": "t"}


def client(app, user):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(logged_in=True, username=user, email=f"{user}@test.invalid", last_activity=time.time(), csrf_token="t")
    return c


def run():
    from server import create_app
    from tender_app.db import get_db_connection
    app = create_app()
    a, b = client(app, A), client(app, B)
    try:
        opts = a.get("/api/growth/options").get_json()
        assert len(opts["presets"]) >= 40 and all(p["group"] for p in opts["presets"]) and opts["max_services"] >= 5
        r = a.post("/api/growth/services", json={"name": "Pool maintenance", "keywords": ["swimming pool"], "cpv": "5041"}, headers=H)
        assert r.status_code == 201, r.get_json()
        svc = r.get_json()["service"]
        assert svc["id"].startswith("service:") and svc["visibility"] == "private" and svc["mine"]
        assert a.post("/api/growth/services", json={"name": "pool MAINTENANCE", "keywords": ["x1"]}, headers=H).status_code == 409
        assert a.post("/api/growth/services", json={"name": "Bad", "keywords": []}, headers=H).status_code == 400
        assert [s["id"] for s in a.get("/api/growth/services").get_json()["services"]] == [svc["id"]]
        assert b.get("/api/growth/services").get_json()["services"] == [], "private to its owner"
        # A can use it together with a built-in service; B cannot use A's private one
        q = f"/api/growth/signals?category=cyber-security,{svc['id']}"
        got = a.get(q)
        assert got.status_code == 200, got.get_json()
        f = got.get_json()["filters"]
        assert f["category"]["preset"] == f"cyber-security,{svc['id']}" and "Pool maintenance" in f["category"]["label"], f
        assert f["min_fit"] == 0
        assert b.get(q).status_code == 400, "an unknown / private service is rejected"
        hi = a.get(q + "&min_fit=100").get_json()
        assert hi["filters"]["min_fit"] == 100 and all(s["fit"] >= 100 for s in hi["signals"])
        assert a.get(q + "&min_fit=500").status_code == 400
        # offered to everyone: still private to others until an administrator approves
        a.post("/api/growth/services", json={"name": "Lido upkeep", "keywords": ["lido"], "share": True}, headers=H)
        assert not any(s["label"] == "Lido upkeep" for s in b.get("/api/growth/services").get_json()["services"])
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE growth_services SET visibility='shared' WHERE owner=%s AND name='Lido upkeep'", (A,))
        conn.commit(); conn.close()
        seen = [s for s in b.get("/api/growth/services").get_json()["services"] if s["label"] == "Lido upkeep"]
        assert seen and seen[0]["mine"] is False and seen[0]["group"] == "Shared by other users"
        assert b.delete(f"/api/growth/services/{svc['id'].split(':')[1]}", headers=H).status_code == 404, "only the owner deletes"
        assert a.delete(f"/api/growth/services/{svc['id'].split(':')[1]}", headers=H).status_code == 200
        assert a.get("/api/growth/services/search?q=repair").status_code == 200
        # non-admins cannot approve
        assert a.post("/api/admin/growth-services/1/visibility", json={"visibility": "shared"}, headers=H).status_code == 403
    finally:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM growth_services WHERE owner LIKE %s", (f"zz_gsvc_{RUN}%",))
        conn.commit(); conn.close()


if __name__ == "__main__":
    try:
        run()
        print("PASS api")
    except Exception as exc:  # noqa: BLE001
        if "connect" in str(exc).lower() and exc.__class__.__name__ in ("OperationalError", "RuntimeError"):
            print("SKIP api (no database)")
        else:
            raise
    print("failures: 0")
