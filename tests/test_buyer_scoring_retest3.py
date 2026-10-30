"""Re-test 3 B1 + B2: the buyer feed's match scores spread, sector is scored on share of spend, and renewal points only come from a
contract in the company's own services within 12 months.

Run: python tests/test_buyer_scoring_retest3.py   (needs the dev database; zz_ fixtures are removed in `finally`)"""
import json
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("ENABLE_EMAIL_SCHEDULER", "0")
os.environ.setdefault("ENABLE_SCHEDULERS", "0")

PREFIX = "ZZ B1B2 Scoring"
USER = "zz_b1b2_scoring"


def _buyers():
    return [f"{PREFIX} Borough {i:02d} Council" for i in range(10)]


def test_scores_spread_and_renewals_are_scoped_to_the_companys_services():
    import unittest
    from server import create_app
    from tender_app.db import get_db_connection
    from tender_app.stats import refresh_buyer_stats
    try:
        conn = get_db_connection()
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"no database: {exc}")
    cur = conn.cursor()
    today = date.today()
    try:
        for i, name in enumerate(_buyers()):
            # buyer i spends i/10 of its money on IT (CPV 72) and the rest on construction (CPV 45); 12 awards each
            for n in range(12):
                it = n < round(12 * i / 10) or (i == 9 and True)
                cpv = "72000000" if it else "45000000"
                value = 100_000 * (1 + (i % 4)) if it else 100_000
                end = ""
                if n == 0 and i < 5:   # an IT contract renewing in 200 days (values differ by buyer)
                    end = (today + timedelta(days=200)).isoformat()
                    value = 250_000 * (i + 1)
                    cpv = "72000000"
                if n == 1 and i >= 5:  # a construction contract renewing in 200 days: not the company's service
                    end = (today + timedelta(days=200)).isoformat()
                    cpv = "45000000"
                cur.execute(
                    "INSERT INTO contract_awards (authority_name, supplier_name, tender_title, date_signed, contract_end_date, contract_value, cpv_code, "
                    "cpv_description, source_portal) VALUES (%s,%s,%s,%s,%s,%s,%s,'zzb1b2','Contracts Finder')",
                    (name, f"zz_b1b2_s{i}_{n}", f"ZZ contract {i}-{n}", "2025-01-01", end or None, value, cpv))
        cur.execute("DELETE FROM company_profiles WHERE username = %s", (USER,))
        cur.execute("INSERT INTO company_profiles (username, name, profile_text, meta_json, is_default) VALUES (%s,%s,%s,%s,TRUE)",
                    (USER, "ZZ Data Co", "We provide software, data analytics and business intelligence platforms.", json.dumps({})))
        conn.commit()
        refresh_buyer_stats(conn)
        c = create_app().test_client()
        with c.session_transaction() as s:
            s.update(logged_in=True, username=USER, email=USER + "@test.invalid", last_activity=time.time(), csrf_token="x")
        r = c.get("/api/buyers/opportunity-feed?q=" + PREFIX.replace(" ", "%20") + "&show_all=1")
        assert r.status_code == 200, r.get_json()
        cards = {x["authority_name"]: x for x in r.get_json()["cards"] if x["authority_name"].startswith(PREFIX)}
        assert len(cards) >= 9, sorted(cards)
        scored = sorted((x["match_score"] for x in cards.values()), reverse=True)
        assert scored[0] - scored[-1] > 20, ("scores should spread over more than 20 points", scored)
        for name, card in cards.items():
            i = int(name.split("Borough ")[1][:2])
            ren = next(f for f in card["fit_factors"] if f["key"] == "renewal")
            if i >= 5:   # its only renewal is construction, not an IT service
                assert ren["score"] == 0 and ren["note"] == "No renewals in your services in the next 12 months.", (i, ren)
                assert card["days_until_renewal"] is None
            elif i < 5:
                assert ren["score"] > 0 and f"ZZ contract {i}-0" in ren["note"], (i, ren)
        bigger = [next(f for f in cards[n]["fit_factors"] if f["key"] == "renewal")["score"] for n in _buyers()[:5] if n in cards]
        assert bigger == sorted(bigger) and bigger[-1] > bigger[0], ("a bigger renewing contract earns more", bigger)
        sector = next(f for f in cards[_buyers()[9]]["fit_factors"] if f["key"] == "sector")
        assert "of its spend" in sector["note"], sector
    finally:
        conn.rollback()
        cur.execute("DELETE FROM contract_awards WHERE authority_name LIKE %s", (PREFIX + "%",))
        cur.execute("DELETE FROM buyer_stats WHERE authority_name LIKE %s", (PREFIX + "%",))
        cur.execute("DELETE FROM company_profiles WHERE username = %s", (USER,))
        conn.commit()
        conn.close()


from tender_app.stats import refresh_award_values  # noqa: E402


def test_b9_the_radar_opens_on_your_services_and_60_to_365_days_and_cpv_less_rows_never_lead():
    import unittest
    from server import create_app
    from tender_app.db import get_db_connection
    try:
        conn = get_db_connection()
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"no database: {exc}")
    cur = conn.cursor()
    today = date.today()
    prefix, user = "ZZ B9 Radar", "zz_b9_radar"
    rows = [  # title, cpv, days until the contract ends
        ("zz it soon", "72000000", 30), ("zz it good", "72000000", 200), ("zz it far", "72000000", 500),
        ("zz build good", "45000000", 200), ("zz nocpv a", "", 100), ("zz nocpv b", None, 90),
    ]
    try:
        for i, (title, cpv, days) in enumerate(rows):
            cur.execute("INSERT INTO contract_awards (authority_name, supplier_name, tender_title, date_signed, contract_end_date, contract_value, cpv_code, "
                        "source_portal) VALUES (%s,%s,%s,'2025-01-01',%s,100000,%s,'Contracts Finder')",
                        (f"{prefix} Council", f"zz_b9_s{i}", title, (today + timedelta(days=days)).isoformat(), cpv))
        cur.execute("DELETE FROM company_profiles WHERE username = %s", (user,))
        cur.execute("INSERT INTO company_profiles (username, name, profile_text, meta_json, is_default) VALUES (%s,%s,%s,%s,TRUE)",
                    (user, "ZZ Data Co", "Software, data analytics and business intelligence platforms.", json.dumps({})))
        conn.commit()
        refresh_award_values(conn)   # the radar reads the de-duplicated awards table once it has been built
        conn.commit()
        c = create_app().test_client()
        with c.session_transaction() as s:
            s.update(logged_in=True, username=user, email=user + "@test.invalid", last_activity=time.time(), csrf_token="x")

        def titles(qs=""):
            r = c.get("/api/buyers/opportunity-radar?q=zz%20b9%20radar" + qs)
            assert r.status_code == 200, r.get_json()
            body = r.get_json()
            return [x["tender_title"] for x in body["renewals"]], body["scope"]

        got, scope = titles()
        assert got == ["zz it good"], got
        assert scope["min_days"] == 60 and scope["max_days"] == 365 and "72" in scope["services"] and not scope["all_categories"], scope
        assert titles("&ending_soon=1")[0] == ["zz it soon", "zz it good"]
        everything, scope = titles("&all_categories=1")
        assert set(everything) == {"zz it good", "zz build good", "zz nocpv a", "zz nocpv b"} and scope["all_categories"], everything
        assert everything[-2:] in (["zz nocpv b", "zz nocpv a"], ["zz nocpv a", "zz nocpv b"]), ("rows with no CPV last", everything)
        assert titles("&all_categories=1&ending_soon=1")[0][-2:] == everything[-2:]
        assert titles("&cpv=45&max_days=250&min_days=0")[0] == ["zz build good"], "an explicit cpv and window still win"
    finally:
        conn.rollback()
        cur.execute("DELETE FROM contract_awards WHERE authority_name LIKE %s", (prefix + "%",))
        cur.execute("DELETE FROM company_profiles WHERE username = %s", (user,))
        conn.commit()
        refresh_award_values(conn)
        conn.commit()
        conn.close()


if __name__ == "__main__":
    test_scores_spread_and_renewals_are_scoped_to_the_companys_services()
    test_b9_the_radar_opens_on_your_services_and_60_to_365_days_and_cpv_less_rows_never_lead()
    print("2 passed")
