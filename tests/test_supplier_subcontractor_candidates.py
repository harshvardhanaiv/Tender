"""Run: python tests/test_supplier_subcontractor_candidates.py   (plain asserts; needs the dev database with award data, skips itself without one)

"Invite subcontractors" in Supplier Intelligence: GET /api/suppliers/<id>/subcontractor-candidates, through the real Flask app.

  * suppliers that won in the same CPV divisions come back in two groups: with a real Companies House number and without,
  * the supplier itself is never in either group, and neither group holds a placeholder number (CF / UK / BR) as "registered",
  * an unknown supplier is a 404, a bad ?cpv= falls back to the supplier's own divisions, and nothing is written.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def client():
    from server import create_app
    c = create_app().test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["username"] = "zz_subcontractor_test"
        s["last_activity"] = time.time()
    return c


def a_supplier_with_cpv(c):
    from tender_app.blueprints import suppliers_bp as sb
    conn = sb._get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT supplier_id FROM contract_awards WHERE supplier_id IS NOT NULL AND cpv_code ~ '^[0-9]{4}'
                       GROUP BY supplier_id ORDER BY COUNT(*) DESC LIMIT 1""")
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_groups_split_on_companies_house_number():
    c = client()
    sid = a_supplier_with_cpv(c)
    assert sid, "no supplier with a CPV code in the dev database"
    r = c.get(f"/api/suppliers/{sid}/subcontractor-candidates")
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d["ok"] and d["divisions"] and len(d["divisions"]) <= 5
    from tender_app.ch_matcher import normalize_ch_company_number
    assert d["registered"] and all(normalize_ch_company_number(x["company_number"]) for x in d["registered"])
    assert all(not normalize_ch_company_number(x["company_number"]) and x["company_number"] == "" for x in d["unregistered"])
    everyone = d["registered"] + d["unregistered"]
    assert sid not in {x["supplier_id"] for x in everyone}, "the supplier is not its own subcontractor"
    assert len({x["supplier_id"] for x in everyone}) == len(everyone), "each supplier is listed once"
    assert len(d["registered"]) <= d["limit"] <= 100 and d["totals"]["registered"] >= len(d["registered"])
    divisions = {x["code"] for x in d["divisions"]}
    assert all(set(x["divisions"]) <= divisions and x["similar_awards"] >= 1 for x in everyone)


def test_unknown_supplier_and_bad_cpv():
    c = client()
    assert c.get("/api/suppliers/999999999/subcontractor-candidates").status_code == 404
    sid = a_supplier_with_cpv(c)
    own = c.get(f"/api/suppliers/{sid}/subcontractor-candidates").get_json()["divisions"]
    bad = c.get(f"/api/suppliers/{sid}/subcontractor-candidates?cpv=zz,%27;DROP").get_json()
    assert bad["ok"] and bad["divisions"] == own, "an invalid ?cpv= is ignored, not trusted"
    one = c.get(f"/api/suppliers/{sid}/subcontractor-candidates?cpv={own[0]['code']}xx").get_json()
    assert [x["code"] for x in one["divisions"]] == [own[0]["code"]]


if __name__ == "__main__":
    try:
        client().get("/api/me")
        from tender_app.blueprints import suppliers_bp as sb
        sb._get_connection().close()
    except Exception as exc:  # no database: nothing to test against
        print(f"SKIP  no database ({type(exc).__name__})")
        sys.exit(0)
    failures = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception:
            import traceback
            failures += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures} of {len(tests)} passed")
    sys.exit(1 if failures else 0)
