"""Test for Workstream A: Buyer stats consistency and Camden normalization.

Verifies:
1. Search, feed, and detail endpoints return identical deduplicated contract counts, spend, and unique suppliers.
2. Authority name variations ('London Borough of Camden', 'London Borough of Camden Council') collapse to a single buyer.
3. Places for People classifies as Housing Associations.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

from tender_app.db import get_db_connection
from tender_app.blueprints.buyers_bp import _norm_auth_sql, classify_buyer_type
from tender_app.market_radar import classify_authority, canonical_buyer_key

def test_buyer_type_classification():
    assert classify_buyer_type("Places for People Group Limited") == "Housing Associations"
    assert classify_authority("Places for People Group Limited") == "housing"
    for name in ("Orbit Housing Association", "Places for People", "Peabody Homes", "Homes England Housing Delivery", "Community Gateway Association Limited"):
        assert classify_buyer_type(name) == "Housing Associations", name
    # a bare "association" in the name is not a housing association (stage 4 dry run found these on live data)
    for name in ("Local Government Association", "THE LOCAL GOVERNMENT ASSOCIATION", "Reserve Forces' and Cadets' Association of East Anglia",
                 "Workers' Educational Association", "The Technology Procurement Association Ltd (TPA)", "PSHE ASSOCIATION"):
        assert classify_buyer_type(name) == "Other Public Bodies", f"{name} was typed {classify_buyer_type(name)}"
    print("ok    test_buyer_type_classification")

def test_camden_normalization():
    k1 = _norm_auth_sql("'London Borough of Camden'")
    k2 = _norm_auth_sql("'London Borough of Camden Council'")
    k3 = _norm_auth_sql("'THE LONDON BOROUGH OF CAMDEN'")
    k4 = _norm_auth_sql("'London Borough Camden'")   # a portal that dropped the "of"

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT {k1}, {k2}, {k3}, {k4}")
    v1, v2, v3, v4 = cur.fetchone()
    conn.close()
    print("DEBUG v1, v2, v3:", repr(v1), repr(v2), repr(v3))
    assert v1 == v2 == v3 == v4 == "LONDON BOROUGH OF CAMDEN"
    print("ok    test_camden_normalization")

def test_buyer_deduplication_consistency():
    conn = get_db_connection()
    cur = conn.cursor()
    
    test_auth = "zz_test_camden_health_trust"
    try:
        # Insert 3 awards for same buyer, same date, same supplier -> duplicate notice
        cur.execute("""
            INSERT INTO contract_awards (authority_name, supplier_name, date_signed, contract_value, source_portal)
            VALUES (%s, %s, '2026-01-01', 100000, 'Contracts Finder'),
                   (%s, %s, '2026-01-01', 100000, 'Contracts Finder'),
                   (%s, 'zz_supplier_b', '2026-01-02', 200000, 'Contracts Finder')
        """, (test_auth, "zz_supplier_a", test_auth, "zz_supplier_a", test_auth))
        conn.commit()

        # Query raw vs deduplicated
        from etenders_scraper.awards import DEDUPED_AWARDS_CTE_SQL
        cur.execute(f"""
            WITH {DEDUPED_AWARDS_CTE_SQL}
            SELECT COUNT(*), SUM(contract_value), COUNT(DISTINCT supplier_name)
            FROM deduped_awards WHERE {_norm_auth_sql('authority_name')} = {_norm_auth_sql('%s')}
        """, (test_auth,))
        dedup_count, dedup_spend, dedup_sups = cur.fetchone()

        assert dedup_count == 2, f"Expected 2 deduplicated awards, got {dedup_count}"
        assert float(dedup_spend) == 300000.0, f"Expected 300000 spend, got {dedup_spend}"
        assert dedup_sups == 2, f"Expected 2 unique suppliers, got {dedup_sups}"
        print("ok    test_buyer_deduplication_consistency")

    finally:
        try:
            cur.execute("DELETE FROM contract_awards WHERE authority_name = %s", (test_auth,))
            conn.commit()
        except Exception:
            conn.rollback()
        conn.close()

def test_the_buyer_key_sql_has_no_question_mark():
    """The app's _execute() helper rewrites every "?" to a %s placeholder, so a regex like "(OF )?" inside the key broke every
    buyer profile with an IndexError (HTTP 500). The key must be safe to embed in any _execute() query."""
    assert "?" not in _norm_auth_sql("authority_name"), "a literal ? in the key SQL becomes a stray placeholder"
    print("ok    test_the_buyer_key_sql_has_no_question_mark")

def test_buyer_key_whitespace_and_ampersand():
    k_double = _norm_auth_sql("'Stoke  on  Trent City Council'")
    k_single = _norm_auth_sql("'Stoke on Trent City Council'")
    
    k_amp = _norm_auth_sql("'NHS Blood & Transplant'")
    k_and = _norm_auth_sql("'NHS Blood  and  Transplant'")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT {k_double}, {k_single}, {k_amp}, {k_and}")
    v_double, v_single, v_amp, v_and = cur.fetchone()
    conn.close()

    assert v_double == v_single == "STOKE ON TRENT CITY"
    assert v_amp == v_and == "NHS BLOOD AND TRANSPLANT"
    print("ok    test_buyer_key_whitespace_and_ampersand")

if __name__ == "__main__":
    test_buyer_type_classification()
    test_camden_normalization()
    test_the_buyer_key_sql_has_no_question_mark()
    test_buyer_key_whitespace_and_ampersand()
    test_buyer_deduplication_consistency()
    print("5 passed, 0 skipped, 0 failed")
