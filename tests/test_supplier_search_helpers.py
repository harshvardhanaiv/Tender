"""Supplier Intelligence: name keys, mistyped company numbers, regions, ranking and filters (no database needed).

Run: python -m pytest tests/test_supplier_search_helpers.py
"""
from tender_app.blueprints import suppliers_bp as sb
from tender_app.entity_merge import entity_name_key, merge_duplicate_suppliers, numbers_compatible
from tender_app.regions import REGIONS, brand_key, itl1_region


def test_name_key_ignores_what_notices_add_to_a_company_name():
    assert entity_name_key("DarntonB3 Limited (appointed to Lot 4)") == entity_name_key("DarntonB3 Ltd")
    assert entity_name_key("Lot 1 (Gas): Gazprom Energy") == entity_name_key("Gazprom Energy")
    assert entity_name_key("Bunzl UK Ltd T/A Bunzl Cleaning") == entity_name_key("Bunzl UK Limited")
    assert entity_name_key("Capita Business Services Ltd (Trading as NAFIC)") == entity_name_key("Capita Business Services Limited")
    assert entity_name_key("IBM United Kingdom Ltd") == entity_name_key("IBM UK Limited")
    assert entity_name_key("Sainsbury's Supermarkets Ltd") == entity_name_key("sainsburys supermarkets limited")
    assert entity_name_key("Serco Ltd") == entity_name_key("SERCO LIMITED") == "SERCO"
    assert entity_name_key("Smith & Sons") == entity_name_key("Smith and Sons")


def test_mistyped_company_numbers_can_be_one_company():
    assert numbers_compatible("00741598", "00741958"), "two digits swapped (IBM United Kingdom)"
    assert not numbers_compatible("00741598", "00741590"), "one different digit is another company: numbers are issued in sequence"
    assert numbers_compatible("00741598", "00741598")
    assert not numbers_compatible("00741598", "01503908"), "a different company"
    assert not numbers_compatible("00741598", "CF35730"), "a placeholder is not a number to compare"


def test_records_with_a_mistyped_number_merge_and_keep_both_numbers():
    rows = [
        {"id": 1, "name": "IBM UNITED KINGDOM LTD", "company_number": "00741598", "total_awards": 103, "total_value": 300.0, "direct_awards": 100, "framework_appointments_count": 3, "framework_total_ceiling": 0, "avg_value": 3.0},
        {"id": 2, "name": "IBM United Kingdom Limited", "company_number": "00741958", "total_awards": 57, "total_value": 100.0, "direct_awards": 55, "framework_appointments_count": 2, "framework_total_ceiling": 0, "avg_value": 2.0},
        {"id": 3, "name": "IBM UK Limited", "company_number": "Not available", "total_awards": 30, "total_value": 40.0, "direct_awards": 30, "framework_appointments_count": 0, "framework_total_ceiling": 0, "avg_value": 1.0},
        {"id": 4, "name": "IBM Limited", "company_number": "01503908", "total_awards": 24, "total_value": 10.0, "direct_awards": 24, "framework_appointments_count": 0, "framework_total_ceiling": 0, "avg_value": 1.0},
    ]
    merged = merge_duplicate_suppliers(rows)
    main = next(r for r in merged if r["id"] == 1)
    assert main["total_awards"] == 190 and main["merged_ids"] == [1, 2, 3]
    assert main["merged_numbers"] == ["00741598", "00741958"], "the possible typo is kept and visible"
    assert any(r["id"] == 4 for r in merged), "IBM Limited is another company"


def test_regions_are_one_name_each():
    assert itl1_region("UKI - London", "") == "London"
    assert itl1_region("London", "") == "London"
    assert itl1_region("South East England", "") == "South East"
    assert itl1_region("UK", "1 Main Street, Glasgow G2 4QE") == "Scotland", "a postcode beats a bare 'UK'"
    assert itl1_region("", "Unit 4, Reading RG2 7TB") == "South East"
    assert itl1_region("UK", "Cork, Ireland") == "Ireland"
    assert itl1_region("UK", "") == "UK"
    assert itl1_region("North West England", "") in REGIONS


def test_related_records_share_a_leading_name():
    assert brand_key("Serco Limited") == brand_key("Serco Leisure Operating Limited") == brand_key("The Serco Group plc") == "SERCO"
    assert brand_key("NHS England") == "" and brand_key("University of Exeter") == "", "generic first words relate nothing"
    assert brand_key("IBM United Kingdom") == "IBM"


def test_a_typed_search_ranks_the_named_supplier_then_winners_then_the_chosen_sort():
    order, params = sb._order_clause({"sort": "value"}, "cleaning", "CLEANING", "st.total_value", "st.total_awards", "st.latest_award_date", "st.entity_key")
    assert order.startswith("CASE WHEN LOWER(s.name) = LOWER(?) OR st.entity_key = ? THEN 0 ELSE 1 END, (st.total_awards = 0), st.total_value DESC")
    assert params == ["cleaning", "CLEANING"], "no 'starts with' tier: a name beginning with the word proves nothing"
    plain, none = sb._order_clause({"sort": "name"}, None, "", "st.total_value", "st.total_awards", "st.latest_award_date", "st.entity_key")
    assert plain.startswith("LOWER(s.name) ASC") and none == []
    assert "latest_award_date DESC NULLS LAST" in sb._order_clause({"sort": "recent"}, None, "", "v", "w", "st.latest_award_date", None)[0]


def test_extra_filters_become_conditions():
    conds, vals = sb._refine_conditions({"region": "Scotland", "min_value": 1e6, "max_value": 1e7, "buyer": "leeds city", "cpv": "90"}, "st.total_value")
    assert vals == ["Scotland", 1e6, 1e7, "%LEEDS CITY%", "90%"]
    assert any("region_norm" in c for c in conds) and any("total_value >= ?" in c for c in conds) and len(conds) == 5
    assert sb._refine_conditions(None, "x") == ([], []) and sb._refine_conditions({}, "x") == ([], [])


def test_buyer_names_that_differ_in_case_are_one_buyer():
    assert sb._nicer_name("Ministry of Defence", "MINISTRY OF DEFENCE") is True
    assert sb._nicer_name("MINISTRY OF DEFENCE", "Ministry of Defence") is False


def test_a_three_letter_search_matches_word_starts_only():
    """QA N4: 'IBM' also returned Gibmaroc and LEIB Medical."""
    rows = [{"name": "IBM United Kingdom Limited"}, {"name": "Gibmaroc Ltd"}, {"name": "LEIB MEDICAL LTD"}, {"name": "UK IBM Services"}]
    assert [r["name"] for r in sb._drop_inside_word_matches(rows, "IBM")] == ["IBM United Kingdom Limited", "UK IBM Services"]
    assert sb._drop_inside_word_matches(rows, "ibm ltd") == rows and sb._drop_inside_word_matches(rows, "") == rows


def test_merged_cards_are_ordered_by_the_figures_they_show():
    """QA N2: 'sort by wins' put 694 awards above 1,628 because the order was set before records were merged."""
    cards = [{"name": "Specialist Computer Centres PLC", "total_awards": 694, "total_value": 5e8}, {"name": "Softcat PLC", "total_awards": 1628, "total_value": 9e8},
             {"name": "WSP UK Limited", "total_awards": 594, "total_value": 4e8}]
    assert [c["total_awards"] for c in sb._order_merged(cards, {"sort": "wins"})] == [1628, 694, 594]
    assert [c["name"] for c in sb._order_merged(cards, {"sort": "value"})][0] == "Softcat PLC"
    assert [c["name"] for c in sb._order_merged(cards, {"sort": "name"})][0] == "Softcat PLC"
    named = sb._order_merged(cards, {"sort": "wins"}, q_key="WSPUK", q="WSP UK")
    assert named[0]["name"] == "WSP UK Limited", "the supplier actually named stays first"
