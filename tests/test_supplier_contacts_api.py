"""Run: python tests/test_supplier_contacts_api.py   (plain asserts; needs the dev database, skips itself without one)

POST /api/suppliers/<id>/contacts/enrich and the found_contacts block of GET /api/suppliers/<id>, through the real
Flask app. The website lookup itself is a fake (no network); a throwaway supplier (zz_contacts_*) is used and removed."""
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
USER = f"zz_contacts_{RUN}"
CSRF = "test-csrf-token"
H = {"X-CSRF-Token": CSRF}
_app = None


class Skip(Exception):
    pass


def app():
    global _app
    if _app is None:
        from server import create_app
        _app = create_app()
    return _app


def db():
    from tender_app.db import get_db_connection
    return get_db_connection()


def client():
    c = app().test_client()
    with c.session_transaction() as s:
        s.update(logged_in=True, username=USER, email=f"{USER}@test.invalid", last_activity=time.time(), csrf_token=CSRF)
    return c


def make_supplier():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO suppliers (company_number, name, address) VALUES (%s, %s, %s) RETURNING id",
                        (f"ZZC{RUN}", f"Zz Contacts Test {RUN} Ltd", "1 High Street, Rugby CV21 1BD"))
            sid = cur.fetchone()[0]
        conn.commit()
        return sid
    finally:
        conn.close()


def cleanup(sid):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM suppliers WHERE id = %s", (sid,))   # the enrichment row goes with it (ON DELETE CASCADE)
        conn.commit()
    finally:
        conn.close()


def test_enrich_stores_and_serves_found_contacts():
    from tender_app import supplier_contacts as sc
    from tender_app.blueprints import suppliers_bp as sb
    calls = []

    def fake_enrich(name, **kw):
        calls.append((name, kw.get("address"), kw.get("company_number")))
        return {"status": "found", "website": "https://zzcontacts.example", "emails": ["info@zzcontacts.example"],
                "phones": ["+44 1788 567890"], "social": {"linkedin": "https://www.linkedin.com/company/zz"},
                "source_url": "https://zzcontacts.example/", "verified_by": "name"}
    real = sc.enrich_supplier
    sc.enrich_supplier = fake_enrich
    sid = make_supplier()
    try:
        c = client()
        r = c.post(f"/api/suppliers/{sid}/contacts/enrich", headers=H)
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        fc = body["found_contacts"]
        assert body["cached"] is False and fc["status"] == "found" and fc["emails"] == ["info@zzcontacts.example"]
        assert fc["phones"] == ["+44 1788 567890"] and fc["social"]["linkedin"].endswith("/company/zz")
        assert len(calls) == 1 and "CV21 1BD" in calls[0][1]

        again = c.post(f"/api/suppliers/{sid}/contacts/enrich", headers=H).get_json()
        assert again["cached"] is True and len(calls) == 1                      # fresh result: no second lookup

        detail = c.get(f"/api/suppliers/{sid}?enrich=0").get_json()
        assert detail["supplier"]["found_contacts"]["website"] == "https://zzcontacts.example"
        assert detail["supplier"]["email"] == ""                                # the notice's own fields are never overwritten

        forced = c.post(f"/api/suppliers/{sid}/contacts/enrich?force=1", headers=H).get_json()
        assert forced["cached"] is False and len(calls) == 2
        assert c.post("/api/suppliers/999999999/contacts/enrich", headers=H).status_code == 404
    finally:
        sc.enrich_supplier = real
        cleanup(sid)


def test_nothing_found_is_remembered_so_it_is_not_retried_at_once():
    from tender_app import supplier_contacts as sc
    calls = []
    real = sc.enrich_supplier
    sc.enrich_supplier = lambda name, **kw: (calls.append(1) or {"status": "not_found"})
    sid = make_supplier()
    try:
        c = client()
        first = c.post(f"/api/suppliers/{sid}/contacts/enrich", headers=H).get_json()
        assert first["found_contacts"]["status"] == "not_found" and first["found_contacts"]["emails"] == []
        assert c.post(f"/api/suppliers/{sid}/contacts/enrich", headers=H).get_json()["cached"] is True
        assert len(calls) == 1
    finally:
        sc.enrich_supplier = real
        cleanup(sid)


if __name__ == "__main__":
    try:
        db().close()
        app()
    except Exception as exc:  # noqa: BLE001
        print("SKIP (no dev database):", exc)
        sys.exit(0)
    failures = 0
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
    print("failures:", failures)
    sys.exit(1 if failures else 0)
