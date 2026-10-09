"""Run: python tests/test_buyer_insights.py   (plain asserts; no database, no network).

Market Radar's two row actions:
  * "View similar suppliers": CPV-overlap ranking, built from the cached analysis (hand-checked data below),
  * "Insights": the facts handed to the model, the thin-data rule and the guard that rejects any reply
    quoting a figure that is not in those facts.
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tender_app import buyer_insights as bi  # noqa: E402
from tender_app import market_radar as mr  # noqa: E402

LG = mr.LOCAL_GOVERNMENT


def award(**kw):
    base = {
        "id": 1, "supplier_id": None, "company_number": None, "supplier_name": "Acme Boilers Ltd",
        "authority_name": "Colchester City Council", "tender_title": "Boiler replacement",
        "cpv_code": "50721000", "contract_value": 100_000, "date_signed": "2025-01-15",
        "procurement_type": "Open procedure", "is_framework": 0, "buyer_type": LG, "notice_url": None,
        "contract_start_date": None, "contract_end_date": None, "contract_duration": None,
    }
    base.update(kw)
    return base


def analysis():
    rows = [
        award(id=1, supplier_name="Acme Boilers Ltd", authority_name="Colchester City Council", contract_value=100_000),
        award(id=2, supplier_name="ACME BOILERS LIMITED", authority_name="Wigan Council", contract_value=50_000, date_signed="2025-02-01"),
        award(id=3, supplier_name="Acme Boilers Ltd", authority_name="Kent County Council", contract_value=30_000, date_signed="2025-03-01"),
        award(id=4, supplier_name="Beta Heating plc", authority_name="Wigan Council", contract_value=200_000, date_signed="2025-04-01"),
        award(id=5, supplier_name="Beta Heating plc", authority_name="Kent County Council", contract_value=5_000_000,
              is_framework=1, date_signed="2025-05-01", cpv_code="50721100"),   # same class, framework: counts, no spend
        award(id=6, supplier_name="Beta Heating plc", authority_name="Kent County Council", contract_value=10_000,
              date_signed="2025-06-01", cpv_code="50531000"),                   # a class Acme does not hold
        award(id=7, supplier_name="Gamma Lifts Ltd", authority_name="Wigan Council", contract_value=70_000,
              date_signed="2025-07-01", cpv_code="42416100"),                   # no overlap with Acme
        award(id=8, supplier_name="Delta Services Ltd", authority_name="Wigan Council", contract_value=20_000,
              date_signed="2025-08-01", cpv_code=""),                           # no CPV at all
    ]
    return mr.build_analysis(rows)


def test_cpv_class_is_the_first_five_digits():
    assert mr.cpv_class("50721000-5") == "50721"
    assert mr.cpv_class("50721100") == "50721"
    assert mr.cpv_class("") == "" and mr.cpv_class(None) == "" and mr.cpv_class("7") == ""


def test_peers_expose_the_main_supplier_key_the_button_sends():
    a = analysis()
    wigan = next(p for p in a["peers"] if p["buyer"].startswith("Wigan"))
    assert wigan["main_supplier"] == "Beta Heating plc"      # 200k beats 50k, 70k, 20k
    assert wigan["main_supplier_key"] in a["_supplier_cpv"]
    assert a["_supplier_cpv"][wigan["main_supplier_key"]]["name"] == "Beta Heating plc"


def test_similar_suppliers_by_cpv_overlap():
    a = analysis()
    acme = next(k for k, v in a["_supplier_cpv"].items() if v["name"].lower().startswith("acme"))
    r = mr.similar_suppliers(a["_supplier_cpv"], acme)
    assert r["basis"] == "cpv" and r["cpv"] == ["50721"]
    names = [x["supplier"] for x in r["rows"]]
    assert names == ["Beta Heating plc"], names              # Gamma (42416) and Delta (no CPV) do not overlap
    beta = r["rows"][0]
    assert beta["shared_awards"] == 2                        # ids 4 and 5; id 6 is another class
    assert beta["shared_value"] == 200_000                   # the framework ceiling is never value
    assert beta["awards"] == 3 and beta["buyers"] == 2 and beta["shared_cpv"] == ["50721"]
    assert r["total"] == 1


def test_similar_suppliers_excludes_the_reference_and_merges_spellings():
    a = analysis()
    idx = a["_supplier_cpv"]
    assert len([v for v in idx.values() if v["name"].lower().startswith("acme")]) == 1   # LTD / LIMITED are one supplier
    beta = next(k for k, v in idx.items() if v["name"].startswith("Beta"))
    r = mr.similar_suppliers(idx, beta)
    assert r["basis"] == "cpv" and r["cpv"][0] == "50721"
    assert [x["supplier"] for x in r["rows"]] == ["Acme Boilers Ltd"] and r["rows"][0]["shared_awards"] == 3
    assert all(x["key"] != beta for x in r["rows"])


def test_similar_suppliers_without_a_cpv_falls_back_to_the_category():
    a = analysis()
    idx = a["_supplier_cpv"]
    delta = next(k for k, v in idx.items() if v["name"].startswith("Delta"))
    r = mr.similar_suppliers(idx, delta)
    assert r["basis"] == "category" and r["cpv"] == []
    assert len(r["rows"]) == 3 and r["rows"][0]["supplier"] in ("Acme Boilers Ltd", "Beta Heating plc")
    assert r["rows"][0]["shared_awards"] == 3


def test_similar_suppliers_unknown_supplier():
    assert mr.similar_suppliers(analysis()["_supplier_cpv"], "NOBODY") is None


def test_money_matches_the_front_end():
    cases = {132_000: "£132k", 28_000: "£28k", 1_400_000: "£1.4m", 365_000_000: "£365m", 950: "£950", 9_999: "£9,999", 999_600: "£1m"}
    for value, text in cases.items():
        assert bi.money(value) == text, (value, bi.money(value))
    assert bi.money(None) == "not published"


PROFILE = {
    "buyer_type": "Local Government / Council",
    "stats": {"total_contracts": 6, "total_spend": 480_000.0, "avg_contract_value": 96_000.0, "framework_appointments": 1,
              "earliest_award": "2022-01-10", "latest_award": "2025-01-10", "direct_awards": 1, "competitive_awards": 5,
              "unique_suppliers": 3, "repeat_supplier_pct": 50.0},
    "top_suppliers": [{"supplier_name": "Acme Boilers Ltd", "contracts_won": 3, "total_value": 300_000.0}],
    "cpv_breakdown": [{"cpv_code": "50721000", "cpv_description": "Boiler maintenance", "count": 4},
                      {"cpv_code": "uncategorized", "cpv_description": "Uncategorized", "count": 2}],
    "contract_history": [{"cpv_code": "50721000", "date_signed": d} for d in ("2022-01-10", "2023-01-10", "2025-01-10")],
}
CAT_AWARDS = [{"value": 100_000, "value_is_ceiling": False, "route": "Open competition"},
              {"value": 120_000, "value_is_ceiling": False, "route": "Open competition"},
              {"value": 110_000, "value_is_ceiling": False, "route": "Open competition"},
              {"value": 5_000_000, "value_is_ceiling": True, "route": "Framework / Call-off"}]


def test_facts_come_only_from_the_data():
    facts = bi.build_facts("Colchester City Council", PROFILE, "Housing repairs: gas", CAT_AWARDS, 4)
    text = "\n".join(facts)
    for expected in ("6 contract awards", "£480k", "£96k", "5 awards were competitive and 1 were direct", "50.0%",
                     "Acme Boilers Ltd (3 contracts, £300k", "Boiler maintenance (4 awards)", "about 2.0 awards a year",
                     "median gap between awards was about 18 months", "Routes in that category: Open competition 3, Framework / Call-off 1",
                     "Median published value of its awards in that category: £110k"):
        assert expected in text, (expected, text)
    assert "Uncategorized" not in text and "£5m" not in text    # the ceiling never becomes a figure


def test_facts_without_a_profile_use_only_the_category_slice():
    facts = bi.build_facts("Wigan Council", None, "Housing repairs: gas", CAT_AWARDS[:1], 1)
    assert facts[0] == "Buyer: Wigan Council." and not any("contract awards on record" in f for f in facts)
    assert not any("Median published value" in f for f in facts)   # fewer than three valued awards


def test_guard_rejects_invented_figures():
    facts = bi.build_facts("Colchester City Council", PROFILE, "Housing repairs: gas", CAT_AWARDS, 4)
    assert bi.numbers_are_grounded("It has 6 awards worth £480k, most of them competitive.", facts)
    assert bi.numbers_are_grounded("It rarely re-tenders.", facts)                 # no numbers, nothing to check
    assert not bi.numbers_are_grounded("It spends about £2m a year.", facts)
    assert not bi.numbers_are_grounded("It made 7 awards.", facts)
    assert not bi.numbers_are_grounded("Roughly 83% went to one supplier.", facts)
    assert bi.numbers_are_grounded("About 2 awards a year and 50% repeat wins.", facts)   # 2.0 / 50.0% are the same figures
    assert not bi.numbers_are_grounded("It re-tenders every 2 years, at £480m.", facts)


def test_prompt_numbers_every_fact():
    p = bi.build_prompt(["A.", "B."])
    assert "1. A." in p and "2. B." in p


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    tests.sort(key=lambda t: t[1].__code__.co_firstlineno)
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception:  # noqa: BLE001
            failed.append(name)
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - len(failed)} of {len(tests)} passed")
    if failed:
        sys.exit(1)


def test_not_awarded_helpers():
    cat = mr.resolve_category("housing-repairs-gas", None, None, None)
    assert mr.related_cpv_groups(cat) == ["453", "507"]
    terms = mr.category_name_terms(cat, {"5072": "Repair and maintenance services of central heating"})
    assert {"gas", "boiler", "heating"} <= set(terms) and "services" not in terms and "repair" not in terms
    custom = mr.resolve_category(None, None, "tree surgery", None)
    assert mr.related_cpv_groups(custom) == [] and mr.category_name_terms(custom) == ["tree", "surgery"]


if __name__ == "__main__":
    main()
