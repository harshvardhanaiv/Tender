from tender_app.entity_merge import entity_name_key, merge_duplicate_suppliers


def _s(i, name, num="Not available", awards=1, value=100.0):
    return {"id": i, "name": name, "company_number": num, "total_awards": awards, "direct_awards": awards,
            "framework_appointments_count": 0, "total_value": value, "avg_value": value / awards,
            "framework_total_ceiling": 0.0}


def test_key_folds_case_suffix_and_ampersand():
    assert entity_name_key("XMA LIMITED") == entity_name_key("Xma Ltd.")
    assert entity_name_key("Smith & Sons Ltd") == entity_name_key("SMITH AND SONS LIMITED")


def test_variants_merge_and_sum():
    out = merge_duplicate_suppliers([_s(1, "XMA LIMITED", "01234567", 2, 200.0), _s(2, "XMA Ltd", awards=3, value=300.0), _s(3, "Other plc")])
    assert len(out) == 2
    xma = out[0]
    assert xma["total_awards"] == 5 and xma["total_value"] == 500.0 and xma["company_number"] == "01234567"
    assert sorted(xma["merged_ids"]) == [1, 2]


def test_different_real_numbers_stay_apart():
    out = merge_duplicate_suppliers([_s(1, "Acme Ltd", "01111111"), _s(2, "ACME LIMITED", "02222222")])
    assert len(out) == 2


def test_blank_name_untouched():
    assert len(merge_duplicate_suppliers([_s(1, ""), _s(2, "")])) == 2
