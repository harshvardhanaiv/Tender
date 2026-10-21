"""Run: python tests/test_growth_services.py   (plain asserts, no database)

Several services chosen together, user-made services, and the fit filter's validation."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tender_app import growth_services as gsv  # noqa: E402
from tender_app import growth_studio as gs  # noqa: E402
from tender_app import market_radar as mr  # noqa: E402


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc:
        return True
    return False


def test_catalogue_is_big_grouped_and_every_preset_resolves():
    assert len(mr.CATEGORY_PRESETS) >= 40
    assert len({p["id"] for p in mr.CATEGORY_PRESETS}) == len(mr.CATEGORY_PRESETS)
    for p in mr.CATEGORY_PRESETS:
        assert p["id"] in mr.PRESET_GROUP_OF, p["id"]
        c = mr.resolve_category(p["id"])
        where, params = mr.category_where(c)
        assert where.startswith("(") and params, p["id"]
        assert "%" not in where.replace("%s", ""), p["id"]


def test_several_services_make_one_category_that_matches_any_of_them():
    c = mr.resolve_category("cyber-security,housing-repairs-gas")
    assert len(c.parts) == 2 and c.key.startswith("multi:")
    assert mr.resolve_category("housing-repairs-gas,cyber-security").key == c.key, "order does not change the cache key"
    assert mr.resolve_category("housing-repairs-gas,housing-repairs-gas").key == "preset:housing-repairs-gas", "one service stays itself"
    assert mr.category_matches(c, "Gas safety servicing", "50721000")
    assert mr.category_matches(c, "Penetration test of the network", "72000000")
    assert not mr.category_matches(c, "School meals", "55500000")
    assert "CPV" in mr.match_reason(c, "x", "50721000") and "Housing repairs" in mr.match_reason(c, "x", "50721000")
    assert c.to_public()["preset"] == "cyber-security,housing-repairs-gas"
    assert mr.resolve_category(c.to_public()["preset"]).key == c.key, "the saved filter rebuilds the same category"
    where, params = mr.category_where(c)
    assert where.count(" OR ") >= 1 and len(params) >= 4
    assert raises(mr.CategoryError, mr.resolve_category, "no-such-service,cyber-security")
    many = ",".join(p["id"] for p in mr.CATEGORY_PRESETS[:30])
    assert len(mr.resolve_category(many).parts) == mr.MAX_SERVICES, "the number chosen together is capped"


def test_strength_names_the_service_and_signals_list_what_matched():
    c = mr.resolve_category("cyber-security,housing-repairs-gas")
    assert gs.category_strength(c, "Boiler servicing", "50721000") == "cpv"
    assert gs.category_strength(c, "Annual cyber assessment", None) == "keyword"
    assert gs.category_strength(c, "School meals", "55500000") is None
    part = gs.score_renewal({"days_left": 100, "title": "Boiler servicing", "cpv": "50721000"}, c, frozenset())["parts"][1]
    assert "Housing repairs & gas servicing" in part["note"] and "Cyber security" not in part["note"], part["note"]
    entries = [{"title": "Boiler servicing", "cpv": "50721000"}]
    assert gs.matched_services(c, entries) == ["Housing repairs & gas servicing"]
    assert gs.matched_services(mr.resolve_category("cyber-security"), entries) == []
    assert gs.build_strength(c) == "preset", "planning signals stay on when one chosen service builds or services buildings"
    assert gs.build_strength(mr.resolve_category("cyber-security,legal-services")) is None


def test_custom_services_are_validated_and_become_a_category():
    ok = gsv.clean_service({"name": "  Pool  maintenance ", "keywords": "swimming pool, leisure plant", "cpv": "5041", "share": True})
    assert ok == {"name": "Pool maintenance", "keywords": ["swimming pool", "leisure plant"], "cpv": ["5041"], "share": True}
    for bad in ({"name": "ab", "keywords": ["x1"]}, {"name": "Valid name"}, {"name": "Valid name", "keywords": ["<script>"]},
                {"name": "Valid name", "cpv": "abc"}, {"name": "Valid name", "keywords": [f"word{i}" for i in range(11)]},
                {"name": "x" * 61, "keywords": ["fine"]}):
        assert raises(gs.ValidationError, gsv.clean_service, bad), bad
    spec = gsv.spec_from_row(7, "Pool maintenance", "5041", "swimming pool|leisure plant")
    c = mr.resolve_category("service:7,cyber-security", services={"service:7": spec})
    assert [p.label for p in c.parts] == ["Cyber security", "Pool maintenance"] or {p.label for p in c.parts} == {"Cyber security", "Pool maintenance"}
    assert mr.category_matches(c, "Swimming pool plant room refurbishment", None)
    assert raises(mr.CategoryError, mr.resolve_category, "service:7")   # a service the caller may not use is unknown
    assert gsv.ids_in("cyber-security,service:7,service:9") == [7, 9] and gsv.ids_in(["service:3"]) == [3] and gsv.ids_in("x") == []


def test_cpv_search_finds_any_branch_by_its_description():
    labels = {"50": "Repair services", "507": "Repair of building installations", "5072": "Heating repair", "72": "IT services", "7221": "Software package services"}
    counts = {"507": 10, "5072": 40, "7221": 5}
    got = gsv.search_cpv(labels, counts, "repair")
    assert [r["cpv"] for r in got] == ["5072", "507"], "busiest first, divisions (2 digits) never offered"
    assert [r["cpv"] for r in gsv.search_cpv(labels, counts, "722")] == ["7221"]
    assert gsv.search_cpv(labels, counts, "x") == []


def test_cpv_search_offers_a_zero_padded_branch_once():
    # the awards data keeps 50000000 under 500, 5000, 50000, 500000 and 5000000; the user must see one row, the shortest
    labels = {c: "Repair and maintenance services" for c in ("500", "5000", "50000", "500000", "5000000")}
    labels["5041"] = "Repair of swimming pool plant"
    labels["504100"] = "Repair of swimming pool plant"
    counts = {c: 5638 for c in ("500", "5000", "50000", "500000", "5000000")}
    counts["5041"] = 12
    counts["504100"] = 12
    got = gsv.search_cpv(labels, counts, "repair")
    assert [r["cpv"] for r in got] == ["500", "5041"], got
    assert [r["cpv"] for r in gsv.search_cpv(labels, counts, "5041")] == ["5041"]
    assert [r["cpv"] for r in gsv.search_cpv(labels, counts, "500")] == ["500"], "a code typed in digits is not repeated either"


def test_min_fit_is_validated_and_round_trips():
    f = gs.filters_from({"category": "cyber-security,legal-services", "min_fit": "60"})
    assert f.min_fit == 60 and f.to_public()["min_fit"] == 60 and f.to_request()["min_fit"] == 60
    assert gs.filters_from({"preset": "cyber-security"}).min_fit == 0
    for bad in ("abc", "101", "-1"):
        assert raises(gs.ValidationError, gs.filters_from, {"preset": "cyber-security", "min_fit": bad}), bad
    f2 = gs.filters_from(f.to_request())
    assert f2.category.key == f.category.key and f2.min_fit == 60


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                failures += 1; print("FAIL", name, "->", repr(exc))
    print("failures:", failures)
    sys.exit(1 if failures else 0)
