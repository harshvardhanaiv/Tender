"""Run: python tests/test_sme_implausible_total.py   (plain asserts; needs the dev database)

A supplier flagged "SME" whose awards add up to more than GBP 100,000,000 is not an SME (the turnover cap is
about EUR 50m): the list card (/api/suppliers/search) and the profile (/api/suppliers/<id>) must both say
"Non-SME Enterprise" for it, and a small flagged SME must still read "SME" in both. Uses throwaway rows
(zz_sme_*) that are removed afterwards. Never touches the network.
"""
import os
import sys
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
os.environ["ENABLE_SCHEDULERS"] = "0"

RUN = uuid.uuid4().hex[:8]
USER = f"zz_sme_{RUN}"
BIG = {"number": f"ZZSMEB{RUN}".upper()[:16], "name": f"ZZ SME Big Probe {RUN}", "value": 250_000_000}
SMALL = {"number": f"ZZSMES{RUN}".upper()[:16], "name": f"ZZ SME Small Probe {RUN}", "value": 400_000}
IDS = {}
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


def client():
    c = app().test_client()
    with c.session_transaction() as s:
        s.update(logged_in=True, username=USER, email=f"{USER}@test.invalid", last_activity=time.time())
    return c


def cleanup():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM supplier_stats WHERE supplier_id IN (SELECT id FROM suppliers WHERE company_number = ANY(%s))",
                        ([BIG["number"], SMALL["number"]],))
            cur.execute("DELETE FROM contract_awards WHERE company_number = ANY(%s)", ([BIG["number"], SMALL["number"]],))
            cur.execute("DELETE FROM suppliers WHERE company_number = ANY(%s)", ([BIG["number"], SMALL["number"]],))
            cur.execute("DELETE FROM award_values WHERE company_number = ANY(%s)", ([BIG["number"], SMALL["number"]],))
        conn.commit()
    finally:
        conn.close()


def make_fixtures():
    conn = db()
    try:
        with conn.cursor() as cur:
            for key, s in (("big", BIG), ("small", SMALL)):
                cur.execute(
                    "INSERT INTO suppliers (company_number, name, sme_status) VALUES (%s, %s, 'SME') RETURNING id",
                    (s["number"], s["name"]),
                )
                IDS[key] = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO contract_awards (supplier_id, company_number, supplier_name, authority_name, tender_title, "
                    "contract_value, currency, date_signed, source_portal, notice_url) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'GBP', '2025-01-15', 'Contracts Finder', %s)",
                    (IDS[key], s["number"], s["name"], f"ZZ SME Authority {RUN}", f"ZZ SME award {RUN}", s["value"],
                     f"https://example.invalid/zz-sme/{RUN}/{key}"),
                )
                # the list reads supplier_stats (listable rows only), which a refresh would fill for these two
                cur.execute(
                    "INSERT INTO supplier_stats (supplier_id, listable, total_awards, total_value, avg_value, "
                    "framework_total_ceiling, framework_appointments_count, latest_award_date) "
                    "VALUES (%s, TRUE, 1, %s, %s, 0, 0, '2025-01-15')",
                    (IDS[key], s["value"], s["value"]),
                )
        conn.commit()
        # a typed search and the profile read the award_values table, rebuilt from the awards; put the two probe awards in it
        from tender_app import stats
        stats.refresh_award_values(conn)
    finally:
        conn.close()


def list_status(c, supplier):
    r = c.get("/api/suppliers/search", query_string={"q": supplier["name"], "per_page": 10})
    assert r.status_code == 200, r.status_code
    rows = (r.get_json() or {}).get("suppliers") or []
    hit = [x for x in rows if x.get("company_number") == supplier["number"] or x.get("name") == supplier["name"]]
    assert hit, f"the probe supplier is not in the list: {[x.get('name') for x in rows][:5]}"
    return hit[0].get("sme_status")


def profile_status(c, key):
    r = c.get(f"/api/suppliers/{IDS[key]}")
    assert r.status_code == 200, r.status_code
    body = r.get_json() or {}
    info = body.get("supplier") or body.get("info") or body
    status = info.get("sme_status")
    assert status is not None, f"no sme_status in the profile response: {list(body)[:10]}"
    return status


def test_a_flagged_sme_with_hundreds_of_millions_of_awards_is_non_sme_in_the_list():
    assert list_status(client(), BIG) == "Non-SME Enterprise"


def test_the_same_supplier_is_non_sme_in_the_profile():
    assert profile_status(client(), "big") == "Non-SME Enterprise"


def test_a_small_flagged_sme_stays_an_sme_in_both():
    c = client()
    assert list_status(c, SMALL) == "SME"
    assert profile_status(c, "small") == "SME"


def setup_module(module=None):
    import unittest
    try:
        cleanup()
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"no database: {exc}")
    make_fixtures()


def teardown_module(module=None):
    cleanup()


def main():
    cleanup()
    make_fixtures()
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    try:
        for name, fn in tests:
            try:
                fn()
                print(f"ok    {name}")
            except Exception:
                failed += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
    finally:
        cleanup()
    print(f"\n{len(tests) - failed} of {len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
