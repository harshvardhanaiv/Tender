"""Run: python tests/test_growth_studio_core.py   (plain asserts, no database, no network)

The logic behind Growth Studio (tender_app/growth_studio.py), checked on hand-made rows so every rule
is pinned: what counts as a signal, how fit is scored, which values are never shown, how messages are
filled and exported, and how results are joined to Pipeline.
"""
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tender_app import growth_studio as gs  # noqa: E402
from tender_app import market_radar as mr  # noqa: E402

TODAY = date(2026, 10, 5)
HOUSING = mr.resolve_category("housing-repairs-gas")
CRM = mr.resolve_category("crm-case-management")
KEYWORDS = gs.profile_keywords("We service gas boilers and heating systems for housing providers.")


def row(**kw):
    """One prepared award row, as fetch_renewal_rows would hand it over."""
    ends = kw.pop("ends", TODAY + timedelta(days=91))
    raw = dict(
        id=kw.pop("id", 1), supplier_id=1, company_number="01234567", supplier_name="ABC Heating Ltd",
        authority_name="Hinckley & Bosworth Borough Council", tender_title="Gas servicing and boiler maintenance",
        cpv_code="50721000", contract_value=64000, date_signed=(ends - timedelta(days=1096)).isoformat(),
        procurement_type="Open", is_framework=0, buyer_type="Local Government / Council", notice_url="https://example.org/n/1",
        contract_start_date=(ends - timedelta(days=1096)).isoformat(), contract_end_date=ends.isoformat(),
        contract_duration=None, supplier_display="ABC Heating Ltd", supplier_cnum=None, listable=True,
    )
    raw.update(kw)
    prepared = mr.prepare_row(raw)
    prepared["cpv_description"] = raw.get("cpv_description", "")
    prepared["buyer_type"] = raw["buyer_type"]
    return prepared


def planning(**kw):
    base = dict(
        id="LBC/26/0001", uid="LBC/26/0001", authority="Charnwood", country="England", region="East Midlands",
        description="Outline planning application for up to 128 dwellings", address="Land at Mill Lane, Loughborough",
        postcode="LE11 1AA", app_size="Large", app_state="Permitted", app_type="Outline", n_dwellings=128,
        applicant_company="Acme Homes Ltd", agent_company="Pegasus Group", decided_date=TODAY - timedelta(days=42),
        start_date=TODAY - timedelta(days=300), lead_score=80, detail_url="https://example.org/p/1", planit_url=None, docs_url=None,
    )
    base.update(kw)
    return base


def engagement(**kw):
    base = dict(
        id=7, organisation="London Borough of Camden", title="Responsive repairs and gas servicing",
        category_json='{"preset": "housing-repairs-gas"}', category_label="Housing repairs & gas servicing",
        engagement_type="supplier_day", supplier_day_at="2026-11-10 10:00", supplier_day_place="Town Hall",
        response_deadline=TODAY + timedelta(days=20), published_url="https://example.org/notice/7",
    )
    base.update(kw)
    return base


def parts(result):
    return {p["key"]: p["points"] for p in result["parts"]}


def renewals(rows, cat=HOUSING, keywords=KEYWORDS, authority="all"):
    return gs.renewal_signals(rows, cat, keywords, TODAY, authority)


# ───────────────────────────────────── words and profile keywords ─────────────────────────────────

def test_stemming_and_profile_keywords():
    assert gs.stem("boilers") == gs.stem("boiler") == gs.stem("boiling") == "boil"
    assert gs.stem("servicing") == gs.stem("service") == gs.stem("services") == "servic"
    assert gs.stem("gas") == "gas" and gs.stem("heat") == "heat", "short words are left alone"
    assert gs.tokens("Gas servicing and boiler repairs for councils") == ["gas", "servic", "boil", "repair"]
    kw = gs.profile_keywords("We install boilers and gas heating.", {"business_type": "Plumbing contractor", "org_name": "Priority Plumbing Services Limited"})
    assert {"boil", "gas", "heat", "plumb", "priority"} <= kw
    assert not ({"servic", "services", "limit", "limited"} & kw), "boilerplate words are not keywords"
    assert gs.profile_keywords("", None) == frozenset() and gs.profile_keywords(None, {}) == frozenset()
    assert len(gs.profile_keywords("alpha " + " ".join(f"word{i}x" for i in range(300)), limit=80)) == 80


def test_keyword_overlap_counts_distinct_words_and_ignores_boilerplate():
    kw = gs.profile_keywords("gas boilers heating")
    assert gs.keyword_overlap(kw, "Boiler replacement and gas safety checks") == 2
    assert gs.keyword_overlap(kw, "Boiler boiler boilers") == 1, "distinct words, not repeats"
    assert gs.keyword_overlap(kw, "Grounds maintenance") == 0
    generic = gs.profile_keywords("General services provider for public sector contracts")
    assert gs.keyword_overlap(generic, "Services contract for the council") == 0, "words every notice has never match"
    assert gs.keyword_overlap(frozenset(), "boiler") == 0


def test_preset_suggestions_and_rounding():
    got = gs.suggest_presets(KEYWORDS)
    assert got[0]["preset"] == "housing-repairs-gas" and {"boil", "gas", "heat", "hous"} <= set(got[0]["matches"])
    assert len(got) <= 3 and all(s["matches"] for s in got)
    sizes = [len(s["matches"]) for s in got]
    assert sizes == sorted(sizes, reverse=True), "best match first"
    later = gs.suggest_presets(gs.profile_keywords("gas solar electricity fuel water"))
    assert [s["preset"] for s in later[:2]] == ["energy-utilities", "housing-repairs-gas"], "the stronger match leads even when a weaker one comes earlier in the list"
    assert gs.suggest_presets(frozenset()) == [] and gs.suggest_presets(gs.profile_keywords("zzz qqq")) == []
    assert gs.suggest_presets(gs.profile_keywords("Waste collection and recycling")) [0]["preset"] == "waste-recycling"
    assert (gs.round_sig(29993.2), gs.round_sig(21333), gs.round_sig(950), gs.round_sig(4567), gs.round_sig(1234567), gs.round_sig(0)) == (30000, 21300, 950, 4570, 1230000, 0)


# ─────────────────────────────────────────── renewal fit ──────────────────────────────────────────

def contract(days_left=91, title="Gas servicing and boiler maintenance", cpv="50721000", **kw):
    base = dict(days_left=days_left, title=title, cpv=cpv, cpv_description="", value=64000, suppliers=["ABC Heating Ltd"], term_years=3.0)
    base.update(kw)
    return base


def test_renewal_timing_points_by_days_left():
    expected = {0: 10, 29: 10, 30: 22, 59: 22, 60: 35, 179: 35, 180: 25, 269: 25, 270: 15, 700: 15, None: 0, -1: 0}
    for days, points in expected.items():
        got = parts(gs.score_renewal(contract(days_left=days), HOUSING, KEYWORDS))["timing"]
        assert got == points, f"{days} days left should score {points}, got {got}"
    note = lambda d: gs.score_renewal(contract(days_left=d), HOUSING, KEYWORDS)["parts"][0]["note"]
    assert note(1).startswith("Ends in 1 day:") and note(5).startswith("Ends in 5 days:") and note(None) == "End date unknown or already passed"


def test_renewal_category_points_depend_on_how_the_award_matches():
    by_cpv = gs.score_renewal(contract(), HOUSING, KEYWORDS)
    by_title = gs.score_renewal(contract(cpv="99999999"), HOUSING, KEYWORDS)
    nothing = gs.score_renewal(contract(cpv="99999999", title="Printing services"), HOUSING, KEYWORDS)
    assert (parts(by_cpv)["category"], parts(by_title)["category"], parts(nothing)["category"]) == (30, 20, 0)
    custom_cpv = mr.resolve_category(cpv="5072")
    assert parts(gs.score_renewal(contract(), custom_cpv, KEYWORDS))["category"] == 30
    custom_text = mr.resolve_category(q="boiler")
    assert parts(gs.score_renewal(contract(), custom_text, KEYWORDS))["category"] == 20
    assert gs.category_strength(HOUSING, "Boiler repairs", "", "") == "keyword"
    assert gs.category_strength(HOUSING, "Printing", "50721000", "") == "cpv"
    assert gs.category_strength(HOUSING, "Printing", "22000000", "") is None


def test_renewal_profile_points_and_evidence_points():
    def profile(words):
        return parts(gs.score_renewal(contract(title=f"{words} contract"), HOUSING, gs.profile_keywords("gas boilers heating plumbing")))["profile"]
    assert (profile("Grounds"), profile("Gas"), profile("Gas boiler"), profile("Gas boiler heating"), profile("Gas boiler heating plumbing")) == (0, 8, 14, 20, 20)
    notes = lambda title, kw: [p for p in gs.score_renewal(contract(title=title), HOUSING, kw)["parts"] if p["key"] == "profile"][0]["note"]
    assert notes("Grounds contract", gs.profile_keywords("gas boilers")) == "The contract's title and category description share no words with your company profile"
    assert notes("Gas boiler contract", gs.profile_keywords("gas boilers")) == "The contract's title and category description share 2 words with your company profile"
    assert notes("Gas contract", gs.profile_keywords("gas boilers")).endswith("share 1 word with your company profile")
    none = gs.score_renewal(contract(), HOUSING, frozenset())
    assert parts(none)["profile"] == 0 and "no description" in [p for p in none["parts"] if p["key"] == "profile"][0]["note"]
    full = gs.score_renewal(contract(), HOUSING, KEYWORDS)
    assert parts(full)["evidence"] == 15
    bare = gs.score_renewal(contract(value=None, suppliers=[], term_years=None), HOUSING, KEYWORDS)
    assert parts(bare)["evidence"] == 0
    assert parts(gs.score_renewal(contract(value=None, suppliers=[]), HOUSING, KEYWORDS))["evidence"] == 5


def test_renewal_score_is_the_sum_of_its_parts_and_never_above_100():
    perfect = gs.score_renewal(contract(title="Gas boiler heating plumbing"), HOUSING, gs.profile_keywords("gas boilers heating plumbing"))
    assert perfect["score"] == 100 == sum(p["points"] for p in perfect["parts"]) and perfect["band"] == "high"
    assert [p["max"] for p in perfect["parts"]] == [35, 30, 20, 15]
    assert gs.fit_band(75) == "high" and gs.fit_band(74) == "mid" and gs.fit_band(50) == "mid" and gs.fit_band(49) == "low"
    worst = gs.score_renewal(contract(days_left=None, cpv="9", title="x", value=None, suppliers=[], term_years=None), HOUSING, frozenset())
    assert worst["score"] == 0 and worst["band"] == "low"


# ───────────────────────────────────────── renewal signals ────────────────────────────────────────

def test_one_renewal_signal_per_buyer_headed_by_the_best_fit_contract():
    soon = row(id=1, ends=TODAY + timedelta(days=20), tender_title="Gas servicing contract", supplier_name="Soon Ltd", supplier_display="Soon Ltd")
    later = row(id=2, ends=TODAY + timedelta(days=120), tender_title="Boiler maintenance contract", supplier_name="Later Ltd", supplier_display="Later Ltd", date_signed="2024-06-01")
    other = row(id=3, authority_name="Blaby District Council", tender_title="Heating repairs")
    signals = renewals([soon, later, other])
    assert len(signals) == 2
    mine = next(s for s in signals if s["buyer"].startswith("Hinckley"))
    assert mine["key"] == "renewal:" + mr.canonical_buyer_key("Hinckley & Bosworth Borough Council", "Local Government / Council")
    assert mine["headline"].startswith("“Boiler maintenance contract” contract ends in 120 days"), "the better fit leads, not just the soonest"
    assert [c["title"] for c in mine["contracts"]] == ["Boiler maintenance contract", "Gas servicing contract"]
    assert mine["days"] == 120 and mine["date"] == (TODAY + timedelta(days=120)).isoformat()
    assert mine["fit"] == sum(p["points"] for p in mine["fit_parts"]) and mine["more_contracts"] == 0
    assert mine["target"] == {"name": mine["buyer"], "key": mine["key"].split(":", 1)[1], "targetable": True, "reason": None}
    assert mine["authority"] == "District & borough councils" and mine["links"] == [{"label": "Award notice", "url": "https://example.org/n/1"}]


def test_contracts_that_have_already_ended_are_not_signals():
    ended = row(ends=TODAY - timedelta(days=1))
    assert renewals([ended]) == []
    today_end = renewals([row(ends=TODAY)])
    assert len(today_end) == 1 and "ends today" in today_end[0]["headline"]
    assert "ends tomorrow" in renewals([row(ends=TODAY + timedelta(days=1))])[0]["headline"]


def test_a_frameworks_value_is_never_shown_and_its_suppliers_are_collected():
    rows = [
        row(id=1, is_framework=1, contract_value=5_000_000, supplier_name="Alpha Ltd", supplier_display="Alpha Ltd", supplier_id=1, tender_title="Heating framework"),
        row(id=2, is_framework=1, contract_value=5_000_000, supplier_name="Beta Ltd", supplier_display="Beta Ltd", supplier_id=2, tender_title="Heating framework"),
        row(id=3, is_framework=1, contract_value=5_000_000, supplier_name="Gamma Ltd", supplier_display="Gamma Ltd", supplier_id=3, tender_title="Heating framework"),
        row(id=4, is_framework=1, contract_value=5_000_000, supplier_name="Delta Ltd", supplier_display="Delta Ltd", supplier_id=4, tender_title="Heating framework"),
    ]
    [s] = renewals(rows)
    assert len(s["contracts"]) == 1, "one framework, however many suppliers are appointed to it"
    c = s["contracts"][0]
    assert c["framework"] and c["value"] is None and c["annual_value"] is None and c["supplier_count"] == 4
    assert "framework ends" in s["headline"] and "5,000,000" not in s["detail"] and "£" not in s["detail"]
    assert "Appointed suppliers on record: Alpha Ltd, Beta Ltd, Gamma Ltd and 1 other." in s["detail"]
    assert "shared by every appointed supplier" in s["detail"]


def test_a_frameworks_value_is_dropped_even_if_a_row_carries_one():
    r = row(is_framework=1, contract_value=5_000_000)
    r["value_ok"] = 5_000_000  # Market Radar already blanks it; this guard has to hold on its own
    [s] = renewals([r])
    assert s["contracts"][0]["value"] is None and s["contracts"][0]["annual_value"] is None and "£" not in s["detail"]


def test_the_highest_value_in_a_contract_is_the_one_shown():
    rows = [row(id=1, contract_value=50000), row(id=2, contract_value=90000, supplier_name="Other Ltd", supplier_display="Other Ltd", supplier_id=2)]
    [s] = renewals(rows)
    assert len(s["contracts"]) == 1 and s["contracts"][0]["value"] == 90000 and s["contracts"][0]["supplier_count"] == 2, "as Market Radar keeps the highest of a repeated notice"


def test_values_follow_market_radar_rules():
    [direct] = renewals([row(contract_value=90000)])
    assert direct["contracts"][0]["value"] == 90000 and direct["contracts"][0]["term_years"] == 3.0
    assert direct["contracts"][0]["annual_value"] == 30000
    assert "Last award on record: £90,000 (about £30,000 a year over 3 years)." in direct["detail"]
    [zero] = renewals([row(contract_value=0)])
    assert zero["contracts"][0]["value"] is None and "Last award" not in zero["detail"], "0 means not published, never £0"
    [huge] = renewals([row(contract_value=mr.SINGLE_AWARD_STATS_CEILING_GBP * 2)])
    assert huge["contracts"][0]["value"] is None, "a single award above the ceiling is source noise"
    [no_term] = renewals([row(contract_start_date=None, contract_duration=None)])
    assert no_term["contracts"][0]["annual_value"] is None and "a year" not in no_term["detail"]
    [placeholder] = renewals([row(supplier_name="N/A", supplier_display="N/A")])
    assert placeholder["contracts"][0]["suppliers"] == [] and "Incumbent" not in placeholder["detail"]
    [unlisted] = renewals([row(listable=False)])
    assert unlisted["contracts"][0]["suppliers"] == [], "a supplier hidden from Supplier Intelligence is not named here either"


def test_council_spellings_are_one_buyer_and_the_commonest_is_shown():
    rows = [
        row(id=3, authority_name="London Borough Camden Council", tender_title="Gas servicing C"),
        row(id=1, authority_name="London Borough of Camden", tender_title="Gas servicing A"),
        row(id=2, authority_name="London Borough of Camden", tender_title="Gas servicing B"),
    ]
    [s] = renewals(rows)
    assert s["buyer"] == "London Borough of Camden" and s["key"] == "renewal:CAMDEN"
    assert len(s["contracts"]) == 3


def test_authority_type_filter_and_more_contracts_counter():
    rows = [row(id=1, authority_name="London Borough of Camden"), row(id=2, authority_name="Blaby District Council")]
    assert [s["buyer"] for s in renewals(rows, authority="london-borough")] == ["London Borough of Camden"]
    assert len(renewals(rows, authority="all")) == 2 and len(renewals(rows, authority="local-government")) == 2
    many = [row(id=i, tender_title=f"Heating contract number {i}", supplier_id=i, supplier_name=f"S{i} Ltd", supplier_display=f"S{i} Ltd") for i in range(1, 13)]
    [s] = renewals(many)
    assert len(s["contracts"]) == gs.MAX_CONTRACTS_SHOWN == 8 and s["more_contracts"] == 4


# ──────────────────────────────────────── development signals ─────────────────────────────────────

def test_planning_approvals_only_matter_to_building_categories():
    assert gs.build_strength(mr.resolve_category("housing-repairs-gas")) == "preset"
    assert gs.build_strength(mr.resolve_category("construction-works")) == "preset"
    assert gs.build_strength(mr.resolve_category("grounds-tree-works")) == "preset"
    assert gs.build_strength(mr.resolve_category("crm-case-management")) is None
    assert gs.build_strength(mr.resolve_category("waste-recycling")) is None
    assert gs.build_strength(mr.resolve_category(cpv="45")) == "cpv" and gs.build_strength(mr.resolve_category(cpv="7110")) == "cpv"
    assert gs.build_strength(mr.resolve_category(cpv="72")) is None
    assert gs.build_strength(mr.resolve_category(q="plumbing")) == "keyword"
    assert gs.build_strength(mr.resolve_category(q="crm software")) is None
    assert gs.build_strength(mr.resolve_category(cpv="72", q="plumbing")) is None, "a non-building CPV code settles it"


def test_screening_opinions_are_not_approved_schemes():
    for text in ("EIA Screening Opinion - proposed development of 500 dwellings", "Request for a Scoping Opinion for a solar farm",
                 "Town and Country Planning (Environmental Impact Assessment) screening opinion", "eia scoping request"):
        assert gs.is_screening_opinion(text), text
    for text in ("Outline planning application for up to 128 dwellings", "Erection of a care home", "", None, "Screening to the car park"):
        assert not gs.is_screening_opinion(text), text
    assert gs.development_signals([planning(description="EIA Screening Opinion for 300 dwellings")], "preset", TODAY) == []


def test_development_signal_targets_the_developer_when_one_is_named():
    [s] = gs.development_signals([planning()], "preset", TODAY)
    assert s["key"] == "development:LBC/26/0001" and s["type"] == "development" and s["buyer"] == "Acme Homes Ltd"
    assert s["target"] == {"name": "Acme Homes Ltd", "key": "dev:ACME HOMES", "targetable": True, "reason": None}
    assert s["headline"] == "128 dwellings at Land at Mill Lane, Loughborough approved 6 weeks ago"
    assert "Planning agent: Pegasus Group." in s["detail"] and "no developer" not in s["detail"]
    assert s["authority"] == "Planning authority: Charnwood" and s["date"] == (TODAY - timedelta(days=42)).isoformat()
    assert "value" not in s and "est_value" not in s, "planning registers publish no value, so none is invented"
    assert s["scheme"]["dwellings"] == 128 and s["links"] == [{"label": "Planning record", "url": "https://example.org/p/1"}]


def test_development_signal_without_a_named_company_cannot_become_a_target():
    for applicant in (None, "", "  ", "Mr J Smith", "Mrs A Jones", "Dr P Patel", "Ms K Lee"):
        [s] = gs.development_signals([planning(applicant_company=applicant)], "preset", TODAY)
        assert s["buyer"] == "Developer not named" and s["target"]["targetable"] is False and s["target"]["key"] is None, applicant
        assert "nobody to add" in s["target"]["reason"] and "no developer" in s["detail"]
    for name in ("Misterton Developments Ltd", "Drake Homes Ltd", "Mr Fixit Ltd", "Sir Robert McAlpine Limited", "Mrs Smith's Bakery Group"):
        [company] = gs.development_signals([planning(applicant_company=name)], "preset", TODAY)
        assert company["target"]["targetable"], f"{name} is a company, not a person"
    [no_agent] = gs.development_signals([planning(agent_company=None)], "preset", TODAY)
    assert "Planning agent" not in no_agent["detail"]


def test_development_signals_skip_unusable_rows_and_describe_the_scheme():
    assert gs.development_signals([planning(decided_date=None)], "preset", TODAY) == []
    assert gs.development_signals([planning(decided_date=TODAY + timedelta(days=3))], "preset", TODAY) == [], "a future decision is a source error"
    [large] = gs.development_signals([planning(n_dwellings=None, address=None, app_size="Large")], "preset", TODAY)
    assert large["headline"] == "A major scheme in Charnwood approved 6 weeks ago"
    [one] = gs.development_signals([planning(n_dwellings=1, app_size="Medium")], "preset", TODAY)
    assert one["headline"].startswith("A scheme at ")
    [long] = gs.development_signals([planning(description="x" * 400)], "preset", TODAY)
    assert "x" * 240 + "…" in long["detail"] and "x" * 241 not in long["detail"]


def test_development_fit_parts():
    base = gs.score_development(planning(lead_score=100), "preset", TODAY)
    assert parts(base) == {"scheme": 35, "timing": 20, "category": 10, "approach": 35} and base["score"] == 100
    assert [p["max"] for p in base["parts"]] == [35, 20, 10, 35]
    assert parts(gs.score_development(planning(lead_score=80), "preset", TODAY))["scheme"] == 28
    for age, points in ((0, 20), (120, 20), (121, 14), (240, 14), (241, 8), (365, 8), (366, 0)):
        got = parts(gs.score_development(planning(decided_date=TODAY - timedelta(days=age)), "preset", TODAY))["timing"]
        assert got == points, f"{age} days since approval should score {points}, got {got}"
    assert parts(gs.score_development(planning(), "keyword", TODAY))["category"] == 7
    thin = gs.score_development(planning(applicant_company=None, n_dwellings=None, address=""), "preset", TODAY)
    assert parts(thin)["approach"] == 0
    named_person = gs.score_development(planning(applicant_company="Mr J Smith", n_dwellings=5, address="1 High St"), "preset", TODAY)
    assert parts(named_person)["approach"] == 5 and "but no developer to approach" in named_person["parts"][3]["note"]
    assert parts(gs.score_development(planning(), "preset", TODAY))["approach"] == 35 and "names the developer, a dwelling count, a site address" in gs.score_development(planning(), "preset", TODAY)["parts"][3]["note"]
    computed = gs.score_development(planning(lead_score=None), "preset", TODAY)
    assert 0 <= parts(computed)["scheme"] <= 35, "a row with no stored score is scored the way Planning Leads would"
    assert all(p["points"] <= p["max"] for p in base["parts"])


def test_a_scheme_with_a_developer_to_approach_outranks_a_bigger_one_without():
    small_named = gs.score_development(planning(lead_score=40, n_dwellings=12), "preset", TODAY)["score"]
    huge_unnamed = gs.score_development(planning(lead_score=100, applicant_company=None, n_dwellings=900), "preset", TODAY)["score"]
    assert small_named > huge_unnamed, "an outreach tool ranks who you can approach above how big the scheme is"
    assert huge_unnamed <= 70, "an unnamed scheme can never look like a top lead"
    assert gs.score_development(planning(lead_score=100), "preset", TODAY)["score"] > 90


# ───────────────────────────────────────── engagement signals ─────────────────────────────────────

def test_category_relation():
    housing, construction, highways = (mr.resolve_category(p) for p in ("housing-repairs-gas", "construction-works", "highways-maintenance"))
    assert gs.category_relation(housing, housing) == "same"
    assert gs.category_relation(housing, construction) == "related", "5xxx repairs and 45331 heating sit under construction 45"
    assert gs.category_relation(construction, highways) == "related"
    assert gs.category_relation(housing, CRM) is None
    assert gs.category_relation(mr.resolve_category(cpv="5072"), housing) == "related"
    assert gs.category_relation(mr.resolve_category(q="boiler"), housing) == "related"
    assert gs.category_relation(mr.resolve_category(q="boiler"), CRM) is None
    assert gs.category_relation(mr.resolve_category(q="gas heating"), mr.resolve_category(q="heating repairs")) == "related"


def test_engagement_signals():
    [s] = gs.engagement_signals([engagement()], HOUSING, TODAY)
    assert s["key"] == "engagement:7" and s["type"] == "engagement" and s["buyer"] == "London Borough of Camden"
    assert s["headline"].endswith("open until 25 October 2026") and "“Responsive repairs and gas servicing”" in s["headline"]
    assert "Supplier day: 2026-11-10 10:00 (Town Hall)." in s["detail"] and "Responses by 25 October 2026." in s["detail"]
    assert parts(gs.score_engagement(engagement(), "same", TODAY)) == {"timing": 40, "category": 40, "evidence": 20}
    assert s["fit"] == 100 and s["target"] == {"name": "London Borough of Camden", "key": "CAMDEN", "targetable": True, "reason": None}
    assert s["links"] == [{"label": "Published notice", "url": "https://example.org/notice/7"}]
    assert "contact" not in str(s).lower(), "the buyer's contact details are never passed on to suppliers"
    related = gs.engagement_signals([engagement()], mr.resolve_category("construction-works"), TODAY)
    assert parts({"parts": related[0]["fit_parts"]})["category"] == 25
    assert gs.engagement_signals([engagement()], CRM, TODAY) == [], "an unrelated category is not a signal"


def test_engagement_signals_skip_closed_nameless_and_unreadable_rows():
    assert gs.engagement_signals([engagement(response_deadline=TODAY - timedelta(days=1))], HOUSING, TODAY) == []
    assert len(gs.engagement_signals([engagement(response_deadline=TODAY)], HOUSING, TODAY)) == 1
    assert gs.engagement_signals([engagement(organisation="  ")], HOUSING, TODAY) == []
    assert gs.engagement_signals([engagement(organisation=None)], HOUSING, TODAY) == []
    for bad in ("not json", '{"preset": "no-such-preset"}', "[]", None):
        assert gs.engagement_signals([engagement(category_json=bad)], HOUSING, TODAY) == [], bad
    [open_ended] = gs.engagement_signals([engagement(response_deadline=None)], HOUSING, TODAY)
    assert open_ended["date"] is None and open_ended["days"] is None and "open until" not in open_ended["headline"]
    timing = lambda d: parts(gs.score_engagement(engagement(response_deadline=TODAY + timedelta(days=d)), "same", TODAY))["timing"]
    assert (timing(30), timing(14), timing(13), timing(7), timing(6), timing(1), timing(0)) == (40, 40, 30, 30, 20, 20, 10)
    assert parts(gs.score_engagement(engagement(response_deadline=None), "same", TODAY))["timing"] == 20
    assert gs.engagement_signals([engagement()], HOUSING, TODAY, authority="nhs") == []
    assert len(gs.engagement_signals([engagement()], HOUSING, TODAY, authority="london-borough")) == 1


def test_user_state_is_applied_without_changing_the_cached_signals():
    [s] = renewals([row()])
    original = {**s}
    out = gs.apply_user_state([s], {s["key"]}, set(), {s["target"]["key"]: [{"id": 3, "name": "Q1 renewals"}]})
    assert out[0]["state"] == {"dismissed": True, "opted_out": False, "campaigns": [{"id": 3, "name": "Q1 renewals"}]}
    assert "state" not in s and s == original, "the cached signal object is never mutated"
    blocked = gs.apply_user_state([s], set(), {s["target"]["key"]}, {})
    assert blocked[0]["target"]["targetable"] is False and "do-not-contact" in blocked[0]["target"]["reason"]
    assert blocked[0]["state"]["opted_out"] is True and s["target"]["targetable"] is True
    [dev] = gs.development_signals([planning(applicant_company=None)], "preset", TODAY)
    assert gs.apply_user_state([dev], set(), {""}, {})[0]["state"]["opted_out"] is False, "an unnamed developer is not 'opted out'"


# ─────────────────────────────────────────────── filters ──────────────────────────────────────────

def test_filters_from_validates_and_round_trips():
    f = gs.filters_from({"preset": "housing-repairs-gas"})
    assert (f.types, f.days, f.authority, f.where, f.frameworks) == (gs.SIGNAL_TYPES, 180, "all", "", True)
    f = gs.filters_from({"category": "housing-repairs-gas", "types": "engagement,renewal", "days": "90", "authority": "london-borough",
                         "where": "  Camden   Council ", "frameworks": "0"})
    assert f.types == ("renewal", "engagement"), "types come back in the fixed order"
    assert (f.days, f.authority, f.where, f.frameworks) == (90, "london-borough", "Camden Council", False)
    again = gs.filters_from(f.to_request())
    assert again == f, "what the page stores per profile rebuilds the same filters"
    assert gs.filters_from({"cpv": "5072", "q": "boiler", "types": ["renewal"], "days": 365}).category.key == "cpv:5072|q:boiler"
    assert f.to_public()["authority_label"] == "London boroughs" and f.to_public()["category"]["preset"] == "housing-repairs-gas"
    for bad, error in (({}, mr.CategoryError), ({"preset": "nope"}, mr.CategoryError),
                       ({"preset": "housing-repairs-gas", "types": "renewal,bogus"}, gs.ValidationError),
                       ({"preset": "housing-repairs-gas", "types": ","}, gs.ValidationError),
                       ({"preset": "housing-repairs-gas", "days": "abc"}, gs.ValidationError),
                       ({"preset": "housing-repairs-gas", "days": 29}, gs.ValidationError),
                       ({"preset": "housing-repairs-gas", "days": 731}, gs.ValidationError),
                       ({"preset": "housing-repairs-gas", "authority": "local-other"}, gs.ValidationError),
                       ({"preset": "housing-repairs-gas", "authority": "x"}, gs.ValidationError),
                       ({"preset": "housing-repairs-gas", "where": "a"}, gs.ValidationError),
                       ({"preset": "housing-repairs-gas", "where": "x" * 61}, gs.ValidationError)):
        try:
            gs.filters_from(bad)
        except error:
            continue
        raise AssertionError(f"{bad} should have been refused")


# ────────────────────────────────────────────── messages ──────────────────────────────────────────

FIELDS = {"buyer_name": "Camden", "buyer_contact": "procurement team", "category": "Gas servicing", "renewal_date": "31 January 2027"}


def test_render_template_fills_known_fields_and_reports_the_rest():
    text, missing, unknown = gs.render_template("Hi {{buyer_contact}}, {{ Buyer_Name }} ends {{renewal_date}}.", FIELDS)
    assert text == "Hi procurement team, Camden ends 31 January 2027." and missing == [] and unknown == []
    text, missing, unknown = gs.render_template("{{incumbent}} / {{incumbent}} / {{typo_field}} / {{TypoField}}", FIELDS)
    assert text == " /  / {{typo_field}} / {{TypoField}}", "an unknown name is left as typed so it can be fixed"
    assert missing == ["incumbent"] and unknown == ["typo_field", "TypoField"]
    assert gs.render_template("", FIELDS) == ("", [], []) and gs.render_template(None, FIELDS)[0] == ""
    assert gs.render_template("{{buyer_name}}", {"buyer_name": "  "})[1] == ["buyer_name"], "blank counts as missing"
    assert gs.render_template("100% {not a field} {{ }}", FIELDS)[0] == "100% {not a field} {{ }}"
    assert gs.template_names("{{Buyer_Name}} {{x}} {{buyer_name}}") == ["buyer_name", "x"]


def test_message_warnings():
    codes = lambda s, b: [w["code"] for w in gs.message_warnings(s, b)]
    assert codes("Hello", "Body " + gs.OPT_OUT_LINE) == []
    assert codes("", "Body " + gs.OPT_OUT_LINE) == ["no_subject"]
    assert codes("S", "") == ["no_body"]
    assert codes("S", "A body that ends without one") == ["no_opt_out"]
    assert codes("S {{nope}}", "Body {{also_nope}} " + gs.OPT_OUT_LINE) == ["unknown_fields"]
    assert "{{also_nope}}" in gs.message_warnings("S", "{{also_nope}} unsubscribe")[0]["text"]
    for line in ("You can opt out at any time.", "Unsubscribe here", 'Reply "stop" to end this', "Reply STOP", "we will not contact you again",
                 "If you do not want to receive these, say so"):
        assert gs.has_opt_out(line), line
    assert not gs.has_opt_out("Stop by our stand") and not gs.has_opt_out(None) and not gs.has_opt_out("")


def test_default_messages_use_only_merge_fields_and_carry_an_opt_out():
    sender = gs.sender_fields("Priority Plumbing", "We are Gas Safe registered. We cover the East of England.", {"contact_name": "Sam Ray", "phone": "01632 960001", "email": "sam@priority.example"}, "Housing repairs & gas servicing")
    assert sender["company_name"] == "Priority Plumbing" and sender["summary"] == "We are Gas Safe registered." and sender["category"] == "Housing repairs & gas servicing"
    for kind in gs.SIGNAL_TYPES:
        message = gs.default_message(kind, sender)
        assert gs.message_warnings(message["subject"], message["body"]) == [], kind
        assert gs.has_opt_out(message["body"]) and "{{buyer_contact}}" in message["body"], kind
        assert "We are Gas Safe registered." in message["body"] and "Sam Ray" in message["body"] and "01632 960001" in message["body"]
        names = set(gs.template_names(message["subject"]) + gs.template_names(message["body"]))
        if kind == "development":
            assert message["subject"] == "{{category}} for {{scheme_summary}}", "the scheme text already says what it is"
        allowed = {m["name"] for m in gs.MERGE_FIELDS if kind in m["types"]}
        assert names <= allowed, f"{kind} uses {names - allowed} which it cannot fill"
    bare = gs.default_message("renewal", gs.sender_fields(None, None, None, "Boilers"))
    assert "Sam" not in bare["body"] and "our company" not in bare["body"] and bare["body"].count("Kind regards,") == 1
    assert gs.sender_fields("Org", "x" * 400, None, "c")["summary"] == "x" * 260
    assert gs.sender_fields("Org", None, {"org_name": "Real Org Ltd"}, "c")["company_name"] == "Real Org Ltd"


def test_fields_for_target_per_signal_type():
    [renewal] = renewals([row(contract_value=90000)])
    sender = {"company_name": "Priority", "sender_name": "Sam", "category": "Gas servicing"}
    f = gs.fields_for_target({"buyer_name": "Hinckley", "signal_type": "renewal", "snapshot": renewal}, sender)
    assert f["renewal_date"] == gs.long_date(TODAY + timedelta(days=91)) and f["contract_title"] == "Gas servicing and boiler maintenance"
    assert f["incumbent"] == "ABC Heating Ltd" and f["last_award_value"] == "£90,000" and f["buyer_contact"] == "procurement team"
    assert (f["company_name"], f["sender_name"], f["category"]) == ("Priority", "Sam", "Gas servicing")
    framework = renewals([row(is_framework=1)])[0]
    ff = gs.fields_for_target({"buyer_name": "x", "signal_type": "renewal", "snapshot": framework}, sender)
    assert ff["incumbent"] == "" and ff["last_award_value"] == "", "nothing about a framework is passed off as a single incumbent's spend"
    [dev] = gs.development_signals([planning()], "preset", TODAY)
    d = gs.fields_for_target({"buyer_name": "Acme Homes Ltd", "signal_type": "development", "snapshot": dev}, sender)
    assert d["scheme_summary"] == "128 dwellings at Land at Mill Lane, Loughborough" and d["planning_authority"] == "Charnwood"
    assert d["approval_date"] == gs.long_date(TODAY - timedelta(days=42)) and d.get("renewal_date", "") == ""
    [eng] = gs.engagement_signals([engagement()], HOUSING, TODAY)
    e = gs.fields_for_target({"buyer_name": "Camden", "signal_type": "engagement", "snapshot": eng}, sender)
    assert e["engagement_title"] == "Responsive repairs and gas servicing" and e["response_deadline"] == "25 October 2026"
    bare = gs.fields_for_target({"buyer_name": "x", "signal_type": "renewal"}, {})
    assert bare["renewal_date"] == "" and bare["contract_title"] == "" and bare["buyer_name"] == "x", "a target with no snapshot still renders"


# ─────────────────────────────────────────── AI drafting ──────────────────────────────────────────

def test_clean_ai_message():
    ok = gs.clean_ai_message({"subject": "  Gas   servicing\ncover ", "body": "Hello {{buyer_contact}},\r\n\r\nWe can help.\r\n\r\n" + gs.OPT_OUT_LINE})
    assert ok["subject"] == "Gas servicing cover" and "\r" not in ok["body"] and ok["body"].endswith(gs.OPT_OUT_LINE)
    assert ok["warnings"] == [] and ok["body"].count(gs.OPT_OUT_LINE) == 1
    appended = gs.clean_ai_message({"subject": "S", "body": "Nothing at the end."})
    assert appended["body"] == "Nothing at the end.\n\n" + gs.OPT_OUT_LINE and appended["warnings"] == [], "the model cannot drop the opt-out line"
    unknown = gs.clean_ai_message({"subject": "S {{invented}}", "body": "Hi {{invented_too}} " + gs.OPT_OUT_LINE})
    assert [w["code"] for w in unknown["warnings"]] == ["unknown_fields"] and "{{invented}}" in unknown["warnings"][0]["text"]
    long = gs.clean_ai_message({"subject": "s" * 500, "body": "b" * 10000})
    assert len(long["subject"]) == gs.MAX_SUBJECT and len(long["body"]) <= gs.MAX_BODY and long["body"].endswith(gs.OPT_OUT_LINE)
    for bad in (None, [], "text", {}, {"subject": "S"}, {"body": "B"}, {"subject": 1, "body": "B"}, {"subject": "S", "body": None},
                {"subject": "  ", "body": "B"}, {"subject": "S", "body": "  "}):
        try:
            gs.clean_ai_message(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should have been refused")


def test_ai_prompt_is_grounded_and_limited():
    sender = {"company_name": "Priority Plumbing", "sender_name": "Sam Ray"}
    answers = [{"category": "Accreditations", "question": "Which are you registered with?", "answer": "Gas Safe, NICEIC. " + "x" * 600}]
    system, user = gs.ai_prompt(sender, ["renewal"], "Housing repairs & gas servicing", answers, "p" * 4000, "z" * 700)
    assert "{{contract_title}}" in system and "{{renewal_date}}" in system and "{{scheme_summary}}" not in system and "{{engagement_title}}" not in system
    assert "{{incumbent}}" not in system, "the model is told not to name the incumbent, so it is not offered the field"
    assert gs.OPT_OUT_LINE in system and "Never invent" in system and "never a named person" in system
    assert "Company: Priority Plumbing" in user and "Signed by: Sam Ray" in user and "Accreditations: Which are you registered with? Gas Safe, NICEIC." in user
    assert user.count("p") >= 2500 and "p" * 2501 not in user, "the profile is cut to 2,500 characters"
    assert "z" * 500 in user and "z" * 501 not in user and "x" * 600 not in user
    system2, user2 = gs.ai_prompt(sender, ["development", "engagement"], "Construction", [], "", "")
    assert "{{scheme_summary}}" in system2 and "{{engagement_title}}" in system2 and "{{renewal_date}}" not in system2
    assert "a planning approval for a new scheme and a market engagement the buyer has opened" in user2 and "Extra instructions" not in user2
    assert "a contract that is about to end" in gs.ai_prompt(sender, [], "c", [], "", "")[1], "no signal types falls back to a renewal"


# ──────────────────────────────────────────── validation ──────────────────────────────────────────

def test_campaign_validation():
    ok = gs.validate_campaign({"name": "  Social   housing  renewals ", "channel": "email", "status": "draft", "subject": "S", "body": "B"})
    assert ok == {"name": "Social housing renewals", "channel": "email", "status": "draft", "subject": "S", "body": "B"}
    assert gs.validate_campaign({"status": "paused"}, partial=True) == {"status": "paused"}
    for bad, partial in (({}, False), ({"name": "  "}, False), ({"name": "x" * 121}, False), ({"name": "n", "channel": "fax"}, False),
                         ({"name": "n", "status": "done"}, False), ({"name": "n", "subject": "s" * 201}, False),
                         ({"name": "n", "body": "b" * 6001}, False), ({"name": 5}, False), ({"subject": 5}, True), ({"name": ""}, True)):
        try:
            gs.validate_campaign(bad, partial)
        except gs.ValidationError:
            continue
        raise AssertionError(f"{bad} should have been refused")


def test_target_validation_and_email_advice():
    assert gs.validate_target_patch({"contact_email": " Procurement@Camden.GOV.uk "}) == {"contact_email": "procurement@camden.gov.uk"}
    assert gs.validate_target_patch({"contact_email": ""}) == {"contact_email": None} and gs.validate_target_patch({"contact_email": None}) == {"contact_email": None}
    assert gs.validate_target_patch({"included": False, "status": "sent", "note": " called "}) == {"included": False, "status": "sent", "note": "called"}
    assert gs.validate_target_patch({"note": ""}) == {"note": None} and gs.validate_target_patch({}) == {}
    for bad in ({"contact_email": "not an email"}, {"contact_email": "a@b"}, {"contact_email": "a" * 250 + "@b.co"}, {"included": "yes"},
                {"status": "won"}, {"note": "n" * 501}, {"contact_email": 5}):
        try:
            gs.validate_target_patch(bad)
        except gs.ValidationError:
            continue
        raise AssertionError(f"{bad} should have been refused")
    assert gs.valid_email("a.b+c@sub.example.co.uk") and not gs.valid_email("a@b") and not gs.valid_email("@b.co") and not gs.valid_email("a b@c.co")
    for personal in ("john.smith@council.gov.uk", "jane_doe@x.org", "al-ex@x.org"):
        assert gs.looks_personal_email(personal), personal
    for role in ("procurement@council.gov.uk", "contracts.team@x.org", "housing.repairs@x.org", "info@x.org", "jsmith@x.org", "a.b@x.org", "tenders.office@x.org"):
        assert not gs.looks_personal_email(role), role


def test_status_timestamps_stay_nested():
    now, earlier = datetime(2026, 10, 5, 12), datetime(2026, 10, 1, 9)
    blank = {}
    assert gs.status_timestamps("not_sent", {"sent_at": earlier}, now) == {"sent_at": None, "replied_at": None, "meeting_at": None}
    assert gs.status_timestamps("sent", blank, now) == {"sent_at": now, "replied_at": None, "meeting_at": None}
    assert gs.status_timestamps("sent", {"sent_at": earlier, "replied_at": now, "meeting_at": now}, now) == {"sent_at": earlier, "replied_at": None, "meeting_at": None}
    assert gs.status_timestamps("replied", blank, now) == {"sent_at": now, "replied_at": now, "meeting_at": None}, "a reply implies it was sent"
    assert gs.status_timestamps("meeting", blank, now) == {"sent_at": now, "replied_at": now, "meeting_at": now}
    assert gs.status_timestamps("meeting", {"sent_at": earlier, "replied_at": earlier}, now) == {"sent_at": earlier, "replied_at": earlier, "meeting_at": now}
    assert gs.status_timestamps("replied", {"sent_at": earlier, "replied_at": earlier, "meeting_at": now}, now)["meeting_at"] is None
    assert gs.status_timestamps("opted_out", {"sent_at": earlier, "replied_at": None, "meeting_at": None}, now) == {"sent_at": earlier, "replied_at": None, "meeting_at": None}
    assert gs.status_timestamps("opted_out", blank, now) == {"sent_at": None, "replied_at": None, "meeting_at": None}


# ─────────────────────────────────────────────── exports ──────────────────────────────────────────

def target(name="Hinckley & Bosworth Borough Council", **kw):
    [signal] = renewals([row(authority_name=name, contract_value=90000)])
    base = dict(buyer_name=name, buyer_key=signal["target"]["key"], signal_type="renewal", snapshot=signal, included=True,
                contact_email="procurement@hinckley.example", status="not_sent")
    base.update(kw)
    return base


SENDER = {"company_name": "Priority Plumbing", "sender_name": "Sam Ray", "category": "Gas servicing"}
BODY = "Hello {{buyer_contact}},\n\n{{buyer_name}}'s contract ends {{renewal_date}}. {{company_name}} / {{sender_name}}.\n\n" + gs.OPT_OUT_LINE
SUBJECT = "{{category}} for {{buyer_name}}"


def read_csv(text):
    import csv as _csv
    import io as _io
    assert text.startswith(chr(0xFEFF)), "a byte order mark so Excel reads £ correctly"
    return list(_csv.reader(_io.StringIO(text[1:])))


def test_csv_cells_cannot_run_as_formulas():
    for risky in ("=SUM(A1)", "+44 20", "-1+2", "@cmd", "\t=1", "\r=1"):
        assert gs.csv_cell(risky) == "'" + risky, risky
    for safe in ("Camden", "£90,000", "2027-01-31", "a=b", "", None, 5):
        assert gs.csv_cell(safe) == ("" if safe is None else str(safe)), safe


def test_csv_export_is_personalised_and_leaves_out_anyone_who_should_not_get_it():
    ts = [
        target(),
        target("Blaby District Council", included=False),
        target("Charnwood Borough Council", status="opted_out"),
        target("Melton Borough Council"),
        target("=cmd|' /C calc'!A0 Council", contact_email=None),
    ]
    suppressed = {ts[3]["buyer_key"]}
    rows = read_csv(gs.build_export(ts, SUBJECT, BODY, SENDER, "csv", suppressed))
    assert rows[0] == ["Buyer", "Signal", "Detail", "Date", "Contact email", "Subject", "Message", "Status", "Source link"]
    assert [r[0] for r in rows[1:]] == ["Hinckley & Bosworth Borough Council", "'=cmd|' /C calc'!A0 Council"], "not excluded, not opted out, not on the do-not-contact list"
    first = rows[1]
    assert first[1] == "Contract renewal" and first[4] == "procurement@hinckley.example" and first[5] == "Gas servicing for Hinckley & Bosworth Borough Council"
    assert "contract ends " + gs.long_date(TODAY + timedelta(days=91)) + ". Priority Plumbing / Sam Ray." in first[6]
    assert first[7] == "not_sent" and first[8] == "https://example.org/n/1"
    assert first[3] == (TODAY + timedelta(days=91)).isoformat() and first[2].startswith("“Gas servicing and boiler maintenance”")
    assert "{{" not in first[6] and "Hello procurement team," in first[6] and first[6].endswith(gs.OPT_OUT_LINE)
    assert read_csv(gs.build_export([], SUBJECT, BODY, SENDER, "csv", set()))[1:] == []


def test_mailchimp_export_and_message():
    ts = [target(), target("Blaby District Council", contact_email=None)]
    rows = read_csv(gs.build_export(ts, SUBJECT, BODY, SENDER, "mailchimp", set()))
    assert rows[0][:3] == ["Email Address", "Company", "Greeting"] and rows[0][-2:] == ["Signal", "Source Link"]
    header = {name: i for i, name in enumerate(rows[0])}
    assert rows[1][header["Email Address"]] == "procurement@hinckley.example" and rows[2][header["Email Address"]] == ""
    assert rows[1][header["Company"]] == "Hinckley & Bosworth Borough Council" and rows[1][header["Last Award Value"]] == "£90,000"
    assert rows[1][header["Renewal Date"]] == gs.long_date(TODAY + timedelta(days=91)) and rows[1][header["Signal"]] == "Contract renewal"
    assert rows[1][header["Scheme"]] == "" and rows[1][header["Source Link"]] == "https://example.org/n/1"
    message = gs.build_export(ts, SUBJECT, BODY, SENDER, "mailchimp-message", set())
    assert "*|GREETING|*" in message and "*|COMPANY|*'s contract ends *|RENEWDATE|*." in message
    assert "Priority Plumbing / Sam Ray." in message, "fields that never vary are written in, there is no column for them"
    assert "{{" not in message and gs.mailchimp_message("{{unknown}} {{buyer_name}}", {})  == "{{unknown}} *|COMPANY|*"
    for field in gs.MERGE_FIELDS:
        assert len(field["tag"]) <= 10 and field["tag"].isalnum() and field["tag"].isupper(), field
    assert len({m["tag"] for m in gs.MERGE_FIELDS}) == len(gs.MERGE_FIELDS)
    try:
        gs.build_export(ts, SUBJECT, BODY, SENDER, "xlsx", set())
    except gs.ValidationError:
        pass
    else:
        raise AssertionError("an unknown format should be refused")


# ────────────────────────────────────────────── performance ───────────────────────────────────────

def test_parse_money():
    table = {
        "£1,250,000": 1250000, "1,250,000": 1250000, "£450000": 450000, "GBP 450000": 450000, "450000": 450000, "£1.2m": 1200000,
        "1.25 million": 1250000, "£45k": 45000, "£2bn": 2e9, "approx £60,000 per year": 60000, "£0.5m": 500000, "  £7,500.50 ": 7500.5,
        "£100,000 - £200,000": None, "£100k to £200k": None, "€500,000": None, "500,000 EUR": None, "$1m": None, "TBC": None,
        "Not specified": None, "": None, None: None, "£0": None, "0": None, "12 months": None, "45": None, "£45": 45, "3 lots": None,
    }
    for text, expected in table.items():
        got = gs.parse_money(text)
        assert got == expected, f"{text!r}: expected {expected}, got {got}"


T0 = datetime(2026, 9, 1, 9)


def tgt(campaign, key, sent=None, replied=None, meeting=None, included=True):
    return dict(campaign_id=campaign, buyer_key=key, sent_at=sent, replied_at=replied, meeting_at=meeting, included=included)


def pipe(pid, authority, created, value="£100,000", stage="watching"):
    return dict(id=pid, contracting_authority=authority, created_at=created, estimated_value=value, stage=stage, title=f"Tender {pid}")


def test_pipeline_matches_credit_each_tender_once_to_the_first_campaign_that_reached_the_buyer():
    targets = [tgt(1, "CAMDEN", sent=T0), tgt(2, "CAMDEN", sent=T0 + timedelta(days=10)), tgt(1, "BLABY", sent=T0), tgt(3, "MELTON")]
    rows = [
        pipe(1, "London Borough of Camden", T0 + timedelta(days=30)),
        pipe(2, "London Borough Camden Council", T0 + timedelta(days=31)),
        pipe(3, "Blaby District Council", T0 - timedelta(days=1)),
        pipe(4, "Blaby District Council", T0),
        pipe(5, "Melton Borough Council", T0 + timedelta(days=5)),
        pipe(6, "Somewhere Else", T0 + timedelta(days=5)),
        pipe(7, "Blaby District Council", None),
    ]
    got = gs.pipeline_matches(targets, rows)
    assert [(m["pipeline_id"], m["campaign_id"]) for m in got] == [(1, 1), (2, 1), (4, 1), (7, 1)]
    assert got[0]["value"] == 100000 and got[0]["buyer_key"] == "CAMDEN" and got[0]["stage"] == "watching"
    assert len({m["pipeline_id"] for m in got}) == len(got), "no tender is counted twice"
    assert gs.pipeline_matches([], rows) == [] and gs.pipeline_matches(targets, []) == []


def test_performance_summary():
    c1 = dict(id=1, name="Renewals", status="active", last_activity_at=datetime(2026, 10, 1, 10))
    c2 = dict(id=2, name="Draft", status="draft", last_activity_at=None)
    targets = [
        tgt(1, "A", sent=datetime(2026, 9, 20), replied=datetime(2026, 9, 22), meeting=datetime(2026, 9, 25)),
        tgt(1, "B", sent=datetime(2026, 9, 20), replied=datetime(2026, 9, 23)),
        tgt(1, "C", sent=datetime(2026, 9, 21)),
        tgt(1, "D"),
        tgt(1, "E", included=False),
        tgt(2, "F", sent=datetime(2025, 2, 1)),
        tgt(1, "OLD", sent=datetime(2025, 1, 5), replied=datetime(2025, 1, 6)),
    ]
    matches = [dict(pipeline_id=1, campaign_id=1, value=200000.0), dict(pipeline_id=2, campaign_id=1, value=None), dict(pipeline_id=3, campaign_id=2, value=50000.0)]
    s = gs.performance_summary([c1, c2], targets, matches, 90, TODAY)
    t = s["tiles"]
    assert (t["campaigns_run"], t["buyers_reached"], t["sent"], t["replied"], t["meetings"]) == (1, 3, 3, 2, 1), "the 2025 outreach is outside the 90-day window"
    assert t["reply_rate"] == round(2 / 3, 3) and t["tenders_tracked"] == 2 and t["pipeline_value"] == 200000 and t["tenders_with_value"] == 1
    assert s["funnel"] == [{"label": "Sent", "value": 3}, {"label": "Replied", "value": 2}, {"label": "Meeting", "value": 1}]
    assert [r["value"] for r in s["funnel"]] == sorted((r["value"] for r in s["funnel"]), reverse=True), "each step is a subset of the one before"
    rows = {r["id"]: r for r in s["campaigns"]}
    assert rows[1] == {"id": 1, "name": "Renewals", "status": "active", "targets": 5, "sent": 4, "replied": 3, "meetings": 1, "tenders": 2, "last_activity": "2026-10-01T10:00:00"}, "campaign rows cover all time, not the window"
    assert (rows[2]["targets"], rows[2]["sent"], rows[2]["replied"], rows[2]["tenders"], rows[2]["last_activity"]) == (1, 1, 0, 1, None)
    everything = gs.performance_summary([c1, c2], targets, matches, None, TODAY)["tiles"]
    assert (everything["campaigns_run"], everything["sent"], everything["replied"], everything["tenders_tracked"]) == (2, 5, 3, 3)
    empty = gs.performance_summary([], [], [], 365, TODAY)
    assert empty["tiles"]["reply_rate"] is None and empty["tiles"]["pipeline_value"] is None and empty["tiles"]["sent"] == 0 and empty["campaigns"] == []


# ───────────────────────────────────────────── runner ─────────────────────────────────────────────

def main() -> None:
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    tests.sort(key=lambda item: item[1].__code__.co_firstlineno)
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception:  # noqa: BLE001 - report every failure, not just the first
            failed.append(name)
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - len(failed)} of {len(tests)} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
