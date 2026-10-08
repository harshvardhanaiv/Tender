"""Test for Workstream C: Market Radar category accuracy and £250m outlier handling.

Verifies:
1. Category matching requires word boundaries and keyword_cpv division alignment for keywords.
2. Single direct award > £250m is excluded from standard total_value and cost_table ranges, and counted as an outlier.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tender_app.market_radar import resolve_category, category_matches, prepare_row, cost_table, summarise
from etenders_scraper.awards import SINGLE_AWARD_STATS_CEILING_GBP

def test_single_award_ceiling_constant():
    assert SINGLE_AWARD_STATS_CEILING_GBP == 250_000_000.0, f"Expected 250M ceiling, got {SINGLE_AWARD_STATS_CEILING_GBP}"
    print("ok    test_single_award_ceiling_constant")

def test_category_keyword_word_boundary_and_cpv():
    crm_cat = resolve_category(preset="crm-case-management")
    
    # Matching CRM title with valid CPV (48445 or 72xxx or missing)
    assert category_matches(crm_cat, "Dynamics CRM Solution Specialist", "72000000")
    
    # Non-matching substring (CRMCAA transport)
    assert not category_matches(crm_cat, "CRMCAA - Ruskin Mill College to Newent", "60140000")
    
    # Non-matching CPV division (30213000 hardware tablets for waste)
    assert not category_matches(crm_cat, "Tablets for Waste In Cab CRM", "30213000")
    
    print("ok    test_category_keyword_word_boundary_and_cpv")

def test_250m_outlier_exclusion():
    # 5 normal awards £100k - £500k + 1 huge award £300m (non-framework)
    raw_rows = [
        {"id": 1, "authority_name": "Council A", "supplier_name": "Sup A", "contract_value": 100000, "is_framework": 0},
        {"id": 2, "authority_name": "Council B", "supplier_name": "Sup B", "contract_value": 200000, "is_framework": 0},
        {"id": 3, "authority_name": "Council C", "supplier_name": "Sup C", "contract_value": 300000, "is_framework": 0},
        {"id": 4, "authority_name": "Council D", "supplier_name": "Sup D", "contract_value": 400000, "is_framework": 0},
        {"id": 5, "authority_name": "Council E", "supplier_name": "Sup E", "contract_value": 500000, "is_framework": 0},
        {"id": 6, "authority_name": "Council F", "supplier_name": "Sup F", "contract_value": 300000000, "is_framework": 0},  # £300m outlier
    ]
    
    prepared = [prepare_row(r) for r in raw_rows]
    
    # Outlier row (ID 6) should have value_ok = None
    assert prepared[-1]["value_ok"] is None
    assert prepared[0]["value_ok"] == 100000
    
    s = summarise(prepared)
    assert s["total_value"] == 1500000, f"Expected 1.5M total, got {s['total_value']}"
    assert s["very_large_awards"]["count"] == 1
    assert s["very_large_awards"]["total_value"] == 300000000
    
    c = cost_table(prepared)
    assert c["excluded_outliers"] == 1
    
    print("ok    test_250m_outlier_exclusion")

def test_awards_say_why_they_match_and_when_their_value_is_a_share():
    from tender_app import market_radar as mr
    cat = mr.resolve_category("crm-case-management", None, None, {})
    assert mr.match_reason(cat, "Customer Care Services", "48445000") == "CPV 48445000"
    assert mr.match_reason(cat, "New CRM system", "72000000") == "title word “crm”"
    assert mr.match_reason(cat, "Tablets for Waste In Cab CRM", "30213000") is None, "a keyword outside the software CPV divisions is not a match"
    rows = [{"id": 1, "authority_name": "Council A", "supplier_name": "S1", "tender_title": "Customer Care Services", "cpv_code": "48445000",
             "contract_value": 3_000_000, "is_framework": 0, "date_signed": "2026-01-01", "shared_n": 3, "contract_value_notice": 9_000_000},
            {"id": 2, "authority_name": "Council B", "supplier_name": "S2", "tender_title": "New CRM system", "cpv_code": "72000000",
             "contract_value": 100_000, "is_framework": 0, "date_signed": "2026-01-02"}]
    analysis = mr.build_analysis(rows, False, cat)
    latest = {p["buyer"]: p["_awards"][0] for p in analysis["peers"]}
    assert latest["Council A"]["matched_by"] == "CPV 48445000" and latest["Council A"]["cpv"] == "48445000"
    assert latest["Council A"]["shared_with"] == 3 and latest["Council A"]["notice_value"] == 9_000_000
    assert latest["Council B"]["matched_by"].startswith("title word") and latest["Council B"]["shared_with"] == 1 and latest["Council B"]["notice_value"] is None
    print("ok    test_awards_say_why_they_match_and_when_their_value_is_a_share")


if __name__ == "__main__":
    test_single_award_ceiling_constant()
    test_category_keyword_word_boundary_and_cpv()
    test_250m_outlier_exclusion()
    test_awards_say_why_they_match_and_when_their_value_is_a_share()
    print("4 passed, 0 skipped, 0 failed")
