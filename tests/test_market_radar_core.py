"""Run: python tests/test_market_radar_core.py   (plain asserts; no test framework and no database needed).

Covers the pure logic behind the buyer workspace (Market Radar and Market Engagement):

  * category resolution and the SQL predicate built from it (bound parameters only, no stray %),
  * authority-type classification and its SQL narrowing,
  * canonical buyer / supplier names, contract term and percentile maths,
  * the analysis itself (peers, suppliers, price bands, cost ranges) on a small hand-checked data set,
  * the query helpers, against a fake cursor that records the SQL it was given,
  * the analysis cache, and
  * the preliminary market engagement helpers (validation, notice text, steps, shortlist, hand-over).

Expected numbers were worked out by hand from the data sets below, not copied from the code's output.
"""
import sys
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etenders_scraper.awards import SINGLE_AWARD_STATS_CEILING_GBP, classify_buyer_type  # noqa: E402
from tender_app import market_engagement as me  # noqa: E402
from tender_app import market_radar as mr  # noqa: E402

LG = mr.LOCAL_GOVERNMENT
NHS = "NHS & Healthcare"
TODAY = date(2026, 10, 5)


def raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as e:
        return str(e)
    raise AssertionError(f"{fn.__name__} did not raise {exc.__name__}")


# ───────────────────────────────────────── fixtures ──────────────────────────────────────────────

def award(**kw):
    base = {
        "id": 1, "supplier_id": None, "company_number": None, "supplier_name": "Acme Repairs Ltd",
        "authority_name": "London Borough of Islington", "tender_title": "Responsive repairs",
        "cpv_code": "50721000", "contract_value": 100_000, "date_signed": "2025-01-15",
        "procurement_type": "Open procedure", "is_framework": 0, "buyer_type": LG, "notice_url": None,
        "contract_start_date": None, "contract_end_date": None, "contract_duration": None,
    }
    base.update(kw)
    return base


def scenario():
    """Eleven awards with every awkward case once. Hand-checked results are in the tests below."""
    return [
        award(id=1, supplier_name="Acme Repairs Ltd", authority_name="London Borough Of Camden", contract_value=100_000,
              date_signed="2025-03-01", contract_start_date="2025-03-01", contract_end_date="2027-03-01"),
        award(id=2, supplier_name="ACME REPAIRS LIMITED", authority_name="London Borough of Camden Council",
              contract_value=150_000, date_signed="2024-02-01", contract_duration="12 months"),
        award(id=3, supplier_name="Acme Repairs Ltd", authority_name="London Borough of Islington", contract_value=200_000,
              date_signed="2025-01-10", contract_start_date="2025-01-10", contract_end_date="2026-01-10"),
        award(id=4, supplier_name="Acme Repairs Ltd", authority_name="London Borough of Hackney", contract_value=300_000,
              date_signed="2024-06-01", contract_duration="12-36 months"),  # a placeholder, not a term
        award(id=5, supplier_name="Acme Repairs Ltd", authority_name="London Borough Of Camden", contract_value=5_000_000,
              date_signed="2023-05-01", is_framework=1, procurement_type=None),
        award(id=6, supplier_name="Beta Heating plc", authority_name="London Borough of Islington", contract_value=400_000,
              date_signed="2025-02-01"),
        award(id=7, supplier_name="Beta Heating plc", authority_name="Kent County Council", contract_value=600_000,
              date_signed="2024-11-01"),
        award(id=8, supplier_name="Gamma Gas Services Ltd", authority_name="Kent County Council",
              contract_value=SINGLE_AWARD_STATS_CEILING_GBP + 1, date_signed="2024-01-01"),
        award(id=9, supplier_name="N/A", authority_name="Kent County Council", contract_value=50_000,
              date_signed="2024-03-01", procurement_type=None),
        award(id=10, supplier_name="Delta Care Ltd", authority_name="NHS Kent and Medway", buyer_type=NHS,
              contract_value=0, date_signed="2025-04-01"),
        award(id=11, supplier_name="Delta Care Ltd", authority_name="NHS Kent and Medway", buyer_type=NHS,
              contract_value=80_000, date_signed="2025-05-01"),
    ]


class FakeCursor:
    """Records the last statement and plays back canned result sets, one per execute()."""

    def __init__(self, results, columns=None):
        self._results = list(results)
        self._columns = columns
        self.statements = []
        self._current = []
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        self._current = self._results.pop(0) if self._results else []
        if self._columns:
            self.description = [(c,) for c in self._columns]

    def fetchall(self):
        return list(self._current)

    def fetchone(self):
        return self._current[0] if self._current else None

    @property
    def sql(self):
        return self.statements[-1][0]

    @property
    def params(self):
        return self.statements[-1][1]


FETCH_COLUMNS = [
    "id", "supplier_id", "company_number", "supplier_name", "authority_name", "tender_title", "cpv_code",
    "contract_value", "date_signed", "procurement_type", "is_framework", "buyer_type", "notice_url",
    "contract_start_date", "contract_end_date", "contract_duration", "sup_key", "auth_key",
    "supplier_display", "supplier_cnum", "listable",
]


def db_tuple(raw):
    full = dict(raw)
    full.setdefault("sup_key", raw["supplier_name"])
    full.setdefault("auth_key", raw["authority_name"].upper())
    full.setdefault("supplier_display", None)
    full.setdefault("supplier_cnum", None)
    full.setdefault("listable", True)
    return tuple(full[c] for c in FETCH_COLUMNS)


def assert_sql_is_safe(sql, params):
    """psycopg2 treats every % that is not %s as a format character: none may sneak in, and every
    placeholder must have exactly one parameter."""
    assert sql.count("%") == sql.count("%s"), f"stray % in SQL: {sql}"
    assert sql.count("%s") == len(params), f"{sql.count('%s')} placeholders but {len(params)} params"


# ───────────────────────────────────────── categories ────────────────────────────────────────────

def test_resolve_preset():
    cat = mr.resolve_category(preset="housing-repairs-gas")
    assert cat.key == "preset:housing-repairs-gas" and cat.preset_id == "housing-repairs-gas"
    assert "5072" in cat.cpv and "boiler" in cat.keywords
    assert cat.to_public() == {"key": cat.key, "label": "Housing repairs & gas servicing", "preset": "housing-repairs-gas"}
    raises(mr.CategoryError, mr.resolve_category, preset="no-such-preset")


def test_resolve_cpv_variants():
    for raw in ("5072", "50720000", "50720000-8", " 5072 "):
        cat = mr.resolve_category(cpv=raw)
        assert cat.cpv == ("5072",), (raw, cat.cpv)
        assert cat.key == "cpv:5072"
    assert mr.resolve_category(cpv="09000000").cpv == ("09",), "a division code with all zeros keeps two digits"
    assert mr.resolve_category(cpv="45233120").cpv == ("4523312",), "trailing zeros mean 'the whole branch'"
    assert mr.resolve_category(cpv="45233121").cpv == ("45233121",)
    for bad in ("5", "1234567890123", "abc"):
        raises(mr.CategoryError, mr.resolve_category, cpv=bad)
    assert mr.resolve_category(cpv="5072").label == "CPV 5072"
    assert mr.resolve_category(cpv="5072", cpv_labels={"5072": "Repair services"}).label == "Repair services"


def test_resolve_text():
    cat = mr.resolve_category(q="CRM  System!")
    assert cat.text_tokens == ("crm", "system") and cat.key == "q:crm system" and cat.cpv == ()
    assert cat.to_public() == {"key": "q:crm system", "label": "“crm system” in award titles", "q": "crm system"}
    assert mr.resolve_category(q="a b crm").text_tokens == ("crm",), "one-letter words are dropped"
    assert len(mr.resolve_category(q="one two three four five six seven eight").text_tokens) == 6
    both = mr.resolve_category(cpv="72", q="hosting")
    assert both.key == "cpv:72|q:hosting" and both.cpv == ("72",) and both.text_tokens == ("hosting",)
    raises(mr.CategoryError, mr.resolve_category)
    raises(mr.CategoryError, mr.resolve_category, q="!!")
    raises(mr.CategoryError, mr.resolve_category, q="x" * 41)


def test_category_where_is_parameterised():
    for preset in mr.CATEGORY_PRESETS:
        sql, params = mr.category_where(mr.resolve_category(preset=preset["id"]))
        assert_sql_is_safe(sql, params)
        assert sql.count("cpv_code LIKE") >= len(preset["cpv"])
        assert any(isinstance(p, list) or (isinstance(p, str) and "\\y" in p) for p in params) == bool(preset["keywords"])
    sql, params = mr.category_where(mr.resolve_category(cpv="5072", q="gas"), alias="x")
    assert_sql_is_safe(sql, params)
    assert sql.count("x.") >= 3 and " a." not in sql and "(a." not in sql, "alias must be used everywhere"
    assert " AND " in sql and params[0] == "5072%"
    sql, params = mr.category_where(mr.resolve_category(q="a_b"))
    assert params[0] == [r"%a\_b%"], "LIKE wildcards in user text are escaped"


def test_category_matches_agrees_with_presets():
    housing = mr.resolve_category(preset="housing-repairs-gas")
    assert mr.category_matches(housing, "Anything", "50721000")
    assert mr.category_matches(housing, "Gas Servicing and boiler replacement", None)
    assert not mr.category_matches(housing, "Library books", "22110000")
    highways = mr.resolve_category(preset="highways-maintenance")
    assert mr.category_matches(highways, "Resurfacing", "45233141")
    assert not mr.category_matches(highways, "Wheelie bins", "34928480"), "waste bins are not highways"
    custom = mr.resolve_category(q="crm system")
    assert mr.category_matches(custom, "Supply of a CRM system", None)
    assert not mr.category_matches(custom, "CRM", None), "every word must appear"
    assert mr.category_matches(custom, "x", None, "crm system software")
    scoped = mr.resolve_category(cpv="72", q="hosting")
    assert mr.category_matches(scoped, "Cloud hosting", "72000000")
    assert not mr.category_matches(scoped, "Cloud hosting", "48000000")
    assert mr.category_matches(mr.resolve_category(cpv="72"), "Anything", "72200000")


# ─────────────────────────────────────── authority types ─────────────────────────────────────────

def test_classify_authority():
    cases = {
        "London Borough of Camden": "london-borough",
        "Royal Borough of Greenwich": "london-borough",
        "Westminster City Council": "london-borough",
        "Royal Borough of Windsor and Maidenhead": "district",
        "Kent County Council": "county",
        "Wigan Metropolitan Borough Council": "metropolitan",
        "Leeds City Council": "city",
        "City of Wolverhampton Council": "city",
        "Epping Forest District Council": "district",
        "Maidstone Borough Council": "district",
        "Oxted Parish Council": "parish",
        "Bath Town Council": "parish",
        "Sheffield Teaching Hospitals NHS Foundation Trust": "nhs",
        "Cabinet Office": "central-government",
    }
    for name, expected in cases.items():
        assert mr.classify_authority(name) == expected, (name, mr.classify_authority(name))
    assert mr.classify_authority("City of Westminster", LG) == "london-borough"
    assert mr.classify_authority("Some Unit", LG) == "local-other"
    assert mr.classify_authority("Anything", "Housing Associations") == "housing"
    assert mr.classify_authority("Anything", "Education & Academies") == "education"
    assert mr.classify_authority("Anything", "Police & Emergency Services") == "police-fire"
    assert mr.classify_authority("Anything", "Central Government & Agencies") == "central-government"
    assert mr.classify_authority("Anything", "Totally Unknown") == "other"


def test_authority_where():
    assert mr.authority_where("all") == ("TRUE", []) and mr.authority_where("") == ("TRUE", [])
    for local in ("local-government", "london-borough", "county", "metropolitan", "city", "district", "parish"):
        sql, params = mr.authority_where(local)
        assert sql == "a.buyer_type = %s" and params == [LG], local
    assert mr.authority_where("nhs") == ("a.buyer_type = %s", [NHS])
    sql, params = mr.authority_where("other", alias="b")
    assert "b.buyer_type IS NULL" in sql and params == ["Other Public Bodies"]
    assert_sql_is_safe(sql, params)
    raises(mr.CategoryError, mr.authority_where, "unicorns")
    raises(mr.CategoryError, mr.authority_where, "local-other")


def test_authority_matches_and_sql_are_consistent():
    names = ["London Borough of Camden", "Kent County Council", "Leeds City Council", "Oxted Parish Council",
             "Epping Forest District Council", "Wigan Metropolitan Borough Council", "Some Unit",
             "NHS Kent and Medway", "Camden Primary Academy", "Hackney Homes", "Cabinet Office"]
    for name in names:
        buyer_type = classify_buyer_type(name)  # what the importer stores in contract_awards.buyer_type
        for type_id, _ in mr.AUTHORITY_TYPES:
            matches = mr.authority_matches(type_id, name, buyer_type)
            sql, params = mr.authority_where(type_id)
            if sql == "TRUE":
                kept = True
            elif "IS NULL" in sql:
                kept = buyer_type in (None, "") or buyer_type == params[0]
            else:
                kept = buyer_type == params[0]
            assert not matches or kept, f"SQL would drop {name!r} for {type_id}, which the Python test accepts"
    assert mr.authority_matches("all", "Anything", None)
    assert mr.authority_matches("local-government", "Some Unit", LG)
    assert not mr.authority_matches("local-government", "NHS Kent and Medway", NHS)
    assert mr.authority_matches("county", "Kent County Council", LG)
    assert not mr.authority_matches("county", "Leeds City Council", LG)


# ──────────────────────────────────────── names and numbers ──────────────────────────────────────

def test_canonical_buyer_key():
    camden = {mr.canonical_buyer_key(n, LG) for n in (
        "London Borough Of Camden", "London Borough of Camden Council", "Camden Council", "LONDON BOROUGH OF CAMDEN")}
    assert camden == {"CAMDEN"}, camden
    assert mr.canonical_buyer_key("Kent County Council", LG) == "KENT"
    assert mr.canonical_buyer_key("Royal Borough of Greenwich", LG) == "GREENWICH"
    assert mr.canonical_buyer_key("NHS Kent & Medway", NHS) == "NHS KENT AND MEDWAY", "only councils lose their suffixes"
    assert mr.canonical_buyer_key("NHS Kent and Medway") == "NHS KENT AND MEDWAY"
    assert mr.canonical_buyer_key("Council", LG) == "COUNCIL", "never an empty key"
    assert mr.canonical_buyer_key(None, LG) == ""


def test_canonical_supplier_key():
    keys = {mr.canonical_supplier_key(n) for n in ("Bell Group Limited", "BELL GROUP LTD.", "Bell Group", "The Bell Group plc")}
    assert keys == {"BELL GROUP"}, keys
    assert mr.canonical_supplier_key("The Limited") == "THE LIMITED", "never an empty key"
    assert mr.canonical_supplier_key("Smith & Sons (Ltd)") == "SMITH AND SONS"
    assert mr.normalise_name("Camden & Islington  NHS!") == "CAMDEN AND ISLINGTON NHS"
    assert mr.normalise_name(None) == ""
    assert mr.place_token("London Borough of Camden") == "CAMDEN"
    assert mr.place_token("Epping Forest District Council") in ("EPPING", "FOREST")


def test_parse_iso_date_and_window_start():
    assert mr.parse_iso_date("2024-02-29") == date(2024, 2, 29)
    assert mr.parse_iso_date("2024-02-29T10:00:00Z") == date(2024, 2, 29)
    assert mr.parse_iso_date(date(2024, 1, 2)) == date(2024, 1, 2)
    for bad in (None, "", "yesterday", "2024-02-30", "29/02/2024", 20240229):
        assert mr.parse_iso_date(bad) is None, bad
    assert mr.window_start("1y", TODAY) == "2025-10-05"
    assert mr.window_start("3y", TODAY) == "2023-10-06"
    assert mr.window_start("5y", TODAY) == "2021-10-06"
    assert mr.window_start("all", TODAY) == "0000-01-01"
    raises(mr.CategoryError, mr.window_start, "10y", TODAY)


def test_term_days():
    assert mr.term_days("2024-01-01", "2026-01-01", None) == 731.0
    assert mr.term_days(None, None, "24 months") == 24 * 30.4375
    assert mr.term_days(None, None, "3 years") == 3 * 365.25
    assert mr.term_days(None, None, " 1 Year ") == 365.25
    assert mr.term_days("2024-01-01", "2024-12-31", "5 years") == 365.0, "published dates win over the text"
    for placeholder in ("12-36 months", "12 - 36 months", "ongoing", "", None, "6"):
        assert mr.term_days(None, None, placeholder) is None, placeholder
    assert mr.term_days("2024-01-01", "2024-01-10", None) is None, "under a month is a data error"
    assert mr.term_days("2000-01-01", "2030-01-01", None) is None, "over twenty years is a data error"
    assert mr.term_days("2026-01-01", "2024-01-01", None) is None, "end before start"
    assert mr.term_days("garbage", "2026-01-01", None) is None


def test_percentile():
    assert mr.percentile([10], 0.5) == 10.0
    assert mr.percentile([1, 2, 3, 4, 5], 0.5) == 3.0
    assert abs(mr.percentile([1, 2, 3, 4, 5], 0.1) - 1.4) < 1e-9
    assert mr.percentile([1, 2], 0.5) == 1.5
    assert mr.percentile([1, 2, 3], 0.0) == 1.0 and mr.percentile([1, 2, 3], 1.0) == 3.0
    raises(ValueError, mr.percentile, [], 0.5)


def test_route_label():
    assert mr.route_label("Open procedure", False) == "Open procedure"
    assert mr.route_label("  ", True) == "Framework / Call-off"
    assert mr.route_label(None, False) == "Not stated"


# ─────────────────────────────────────────── analysis ────────────────────────────────────────────

def test_prepare_row_value_rules():
    ceiling = SINGLE_AWARD_STATS_CEILING_GBP
    assert mr.prepare_row(award(contract_value=0))["value"] is None, "0 means no value, never £0"
    assert mr.prepare_row(award(contract_value=None))["value_ok"] is None
    assert mr.prepare_row(award(contract_value="abc"))["value"] is None
    fw = mr.prepare_row(award(contract_value=1_000_000, is_framework=1))
    assert fw["value"] == 1_000_000 and fw["value_ok"] is None and fw["framework"], "a ceiling is not spend"
    over = mr.prepare_row(award(contract_value=ceiling + 1))
    assert over["value"] == ceiling + 1 and over["value_ok"] is None
    assert mr.prepare_row(award(contract_value=ceiling))["value_ok"] is None, "the ceiling itself is excluded"
    assert mr.prepare_row(award(contract_value="250000.5"))["value_ok"] == 250000.5


def test_prepare_row_fields():
    row = mr.prepare_row(award(supplier_name="Raw Name", supplier_display="Proper Name Ltd", date_signed="2025-03-01",
                               notice_url="https://example.org/n/1"))
    assert row["supplier"] == "Proper Name Ltd", "the canonical supplier record wins over the raw notice text"
    assert row["supplier_key"] == "PROPER NAME" and row["listable"]
    assert row["started"] == date(2025, 3, 1), "start defaults to the signing date"
    assert row["url"] == "https://example.org/n/1" and row["type_id"] == "london-borough"
    for placeholder in ("N/A", "tbc", "Various", "Confidential", "see attached document", "Not Disclosed", ""):
        assert not mr.prepare_row(award(supplier_name=placeholder))["listable"], placeholder
    assert not mr.prepare_row(award(supplier_name="Real Co", listable=False))["listable"]
    assert mr.prepare_row(award(supplier_name="Attachment Services Ltd"))["listable"], "only placeholder text is hidden"


def test_summary():
    s = mr.build_analysis(scenario())["summary"]
    assert s["awards"] == 11 and s["buyers"] == 5 and s["suppliers"] == 4 and s["frameworks"] == 1
    assert s["total_value"] == 1_880_000, "framework ceiling, over-ceiling and zero values are all left out"
    assert s["coverage"] == {"with_value": 0.727, "with_term": 0.273, "with_route": 0.909}
    empty = mr.build_analysis([])
    assert empty["summary"]["awards"] == 0 and empty["summary"]["total_value"] is None
    assert empty["summary"]["coverage"]["with_value"] is None
    assert empty["peers"] == [] and empty["suppliers"] == [] and empty["cost"]["overall"]["total"] is None


def test_peers():
    peers = mr.build_analysis(scenario())["peers"]
    assert [p["key"] for p in peers] == ["NHS KENT AND MEDWAY", "CAMDEN", "ISLINGTON", "KENT", "HACKNEY"], \
        "newest activity first"
    by_key = {p["key"]: p for p in peers}

    camden = by_key["CAMDEN"]
    assert "Camden" in camden["buyer"] and camden["buyer"] != camden["buyer"].upper()
    assert (camden["awards"], camden["frameworks"], camden["suppliers"]) == (3, 1, 1)
    assert camden["total_value"] == 250_000, "the framework ceiling is not part of a buyer's spend"
    assert camden["type"] == "london-borough" and camden["type_label"] == "London boroughs"
    assert camden["main_supplier"] == "Acme Repairs Ltd", "two spellings of one supplier count as one, proper case shown"
    assert camden["latest"] == {"supplier": "Acme Repairs Ltd", "value": 100_000, "value_is_ceiling": False,
                                "started": "2025-03-01", "route": "Open procedure", "title": "Responsive repairs"}
    assert camden["latest_signed"] == "2025-03-01"
    assert [a["signed"] for a in camden["_awards"]] == ["2025-03-01", "2024-02-01", "2023-05-01"]
    assert camden["_awards"][2]["value_is_ceiling"] is True

    kent = by_key["KENT"]
    assert (kent["awards"], kent["suppliers"], kent["total_value"]) == (3, 2, 650_000)
    assert kent["main_supplier"] == "Beta Heating plc" and kent["type"] == "county"

    nhs = by_key["NHS KENT AND MEDWAY"]
    assert nhs["type"] == "nhs" and nhs["type_label"] == "NHS & healthcare"
    assert (nhs["awards"], nhs["total_value"]) == (2, 80_000)
    assert nhs["latest"]["value"] == 80_000 and nhs["latest"]["started"] == "2025-05-01"


def test_peers_main_supplier_prefers_spend_then_count():
    rows = [
        award(id=1, supplier_name="Small Co Ltd", contract_value=10_000, date_signed="2025-01-01"),
        award(id=2, supplier_name="Small Co Limited", contract_value=5_000, date_signed="2025-02-01"),
        award(id=3, supplier_name="Small Co Ltd", contract_value=5_000, date_signed="2025-03-01"),
        award(id=4, supplier_name="Big Co plc", contract_value=50_000, date_signed="2025-04-01"),
    ]
    assert mr.build_analysis(rows)["peers"][0]["main_supplier"] == "Big Co plc"
    rows[3]["contract_value"] = 15_000  # Small Co now totals 20,000 under two spellings, and "Ltd" is used twice
    assert mr.build_analysis(rows)["peers"][0]["main_supplier"] == "Small Co Ltd"


def test_suppliers():
    suppliers = mr.build_analysis(scenario())["suppliers"]
    assert [s["supplier"] for s in suppliers] == ["Acme Repairs Ltd", "Beta Heating plc", "Delta Care Ltd",
                                                  "Gamma Gas Services Ltd"], "widest adoption first"
    assert all(s["supplier"] != "N/A" for s in suppliers), "placeholder suppliers are not listed"
    by = {s["supplier"]: s for s in suppliers}

    acme = by["Acme Repairs Ltd"]
    assert (acme["contracts"], acme["framework_appointments"], acme["buyers"]) == (4, 1, 3)
    assert (acme["total_value"], acme["valued_contracts"], acme["avg_contract"], acme["median_contract"]) == \
        (750_000, 4, 187_500, 175_000)
    assert (acme["avg_term_months"], acme["terms_known"]) == (16.0, 3)
    assert acme["repeat_rate"] == 0.33, "Camden is the only one of three buyers to award twice"
    assert acme["latest_signed"] == "2025-03-01" and acme["price_band"] is None

    beta = by["Beta Heating plc"]
    assert (beta["contracts"], beta["buyers"], beta["total_value"], beta["avg_contract"]) == (2, 2, 1_000_000, 500_000)
    assert beta["repeat_rate"] is None, "needs three buyers before a repeat rate means anything"
    assert beta["avg_term_months"] is None and beta["terms_known"] == 0

    gamma = by["Gamma Gas Services Ltd"]
    assert gamma["contracts"] == 1 and gamma["total_value"] is None and gamma["avg_contract"] is None
    assert gamma["median_contract"] is None and gamma["valued_contracts"] == 0

    delta = by["Delta Care Ltd"]
    assert (delta["contracts"], delta["buyers"], delta["total_value"], delta["valued_contracts"]) == (2, 1, 80_000, 1)


def test_price_bands_are_relative_to_the_category():
    rows = []
    for i, value in enumerate([100_000, 200_000, 300_000, 400_000, 500_000, 600_000]):
        for place in ("A", "B"):
            rows.append(award(id=len(rows) + 1, supplier_name=f"Supplier {i}", authority_name=f"London Borough of {place}{i}",
                              contract_value=value))
    rows.append(award(id=99, supplier_name="Solo Ltd", authority_name="London Borough of Solo", contract_value=50_000))
    bands = {s["supplier"]: s["price_band"] for s in mr.build_analysis(rows)["suppliers"]}
    assert bands["Supplier 0"] == bands["Supplier 1"] == "£ Lower"
    assert bands["Supplier 2"] == bands["Supplier 3"] == "££ Mid"
    assert bands["Supplier 4"] == bands["Supplier 5"] == "£££ Higher"
    assert bands["Solo Ltd"] is None, "one valued contract is not enough to place a supplier"
    fewer = [r for r in rows if r["supplier_name"] in ("Supplier 0", "Supplier 1", "Supplier 2")]
    assert all(s["price_band"] is None for s in mr.build_analysis(fewer)["suppliers"]), "needs six suppliers to compare"


def test_cost_table():
    cost = mr.build_analysis(scenario())["cost"]
    assert cost["excluded_frameworks"] == 1
    assert cost["excluded_outliers"] == 1 and cost["excluded_no_value"] == 1, "the over-ceiling award and the zero-value award"
    assert [b["type"] for b in cost["by_type"]] == ["london-borough", "county", "nhs"]
    overall = cost["overall"]
    assert (overall["contracts"], overall["annualised"], overall["annual"]) == (8, 3, None)
    assert overall["total"] == {"p10": 71_000, "median": 175_000, "p90": 460_000, "min": 50_000, "max": 600_000}
    london = cost["by_type"][0]
    assert london["label"] == "London boroughs" and london["contracts"] == 5
    assert london["total"] == {"p10": 120_000, "median": 200_000, "p90": 360_000, "min": 100_000, "max": 400_000}
    assert cost["by_type"][1]["total"] is None and cost["by_type"][2]["total"] is None, "under five awards: no range"


def test_cost_table_annualises_by_term():
    values = [100_000, 200_000, 300_000, 400_000, 500_000, 600_000]
    terms = ["12 months"] * 3 + ["24 months"] * 3
    rows = [award(id=i + 1, supplier_name=f"Supplier {i}", authority_name=f"London Borough of Place{i}",
                  contract_value=v, contract_duration=t, date_signed=f"2025-0{i + 1}-01")
            for i, (v, t) in enumerate(zip(values, terms))]
    overall = mr.build_analysis(rows)["cost"]["overall"]
    assert overall["annualised"] == 6
    assert overall["annual"] == {"p10": 150_000, "median": 225_000, "p90": 300_000, "min": 100_000, "max": 300_000}
    assert overall["total"] == {"p10": 150_000, "median": 350_000, "p90": 550_000, "min": 100_000, "max": 600_000}


# ───────────────────────────────────────── query helpers ─────────────────────────────────────────

def test_fetch_award_rows_builds_safe_sql_and_filters_sub_types():
    rows = [
        award(id=1, authority_name="London Borough of Camden"),
        award(id=2, authority_name="Kent County Council"),
        award(id=3, authority_name="Epping Forest District Council"),
    ]
    cat = mr.resolve_category(preset="housing-repairs-gas")

    def run(authority, scope="all"):
        cur = FakeCursor([[db_tuple(r) for r in rows]], FETCH_COLUMNS)
        got, truncated = mr.fetch_award_rows(cur, cat, authority, "3y", today=TODAY, scope=scope)
        assert_sql_is_safe(cur.sql, cur.params)
        assert cur.params[0] == "2023-10-06" and cur.params[-1] == mr.MAX_ROWS + 1
        assert not truncated
        return [r["id"] for r in got], cur

    assert run("all")[0] == [1, 2, 3]
    assert run("local-government")[0] == [1, 2, 3], "SQL narrows to local government; no further filter"
    assert run("london-borough")[0] == [1]
    assert run("county")[0] == [2]
    assert run("district")[0] == [3]
    ids, cur = run("all", scope="direct")
    assert "COALESCE(a.is_framework, 0) = 0" in cur.sql and ids == [1, 2, 3]
    # the scope filter is on the awards (alias a); the shared-notice-value rule legitimately mentions is_framework on d
    assert "COALESCE(a.is_framework, 0) = 0" not in run("all")[1].sql
    raises(mr.CategoryError, run, "all", "sideways")
    raises(mr.CategoryError, run, "unicorns")


def test_fetch_award_rows_truncates():
    original = mr.MAX_ROWS
    mr.MAX_ROWS = 3
    try:
        cur = FakeCursor([[db_tuple(award(id=i)) for i in range(4)]], FETCH_COLUMNS)
        rows, truncated = mr.fetch_award_rows(cur, mr.resolve_category(cpv="5072"), today=TODAY)
        assert truncated and len(rows) == 3 and cur.params[-1] == 4
    finally:
        mr.MAX_ROWS = original


class FakeConnection:
    def __init__(self, cursor, log):
        self._cursor, self._log = cursor, log

    def cursor(self):
        return self._cursor

    def close(self):
        self._log.append("close")


def test_get_analysis_caches_per_view_and_validates_first():
    mr.ANALYSIS_CACHE.clear()
    log = []

    def connect():
        log.append("connect")
        return FakeConnection(FakeCursor([[db_tuple(r) for r in scenario()]], FETCH_COLUMNS), log)

    cat = mr.resolve_category(preset="housing-repairs-gas")
    try:
        first = mr.get_analysis(connect, cat, "all", "3y")
        assert log == ["connect", "close"], "the connection is always closed"
        assert first["summary"]["awards"] == 11 and first["computed_at"].endswith("Z") and first["truncated"] is False
        assert mr.get_analysis(connect, cat, "all", "3y") is first and log.count("connect") == 1
        mr.get_analysis(connect, cat, "all", "1y")
        mr.get_analysis(connect, cat, "all", "3y", scope="direct")
        assert log.count("connect") == 3, "window and scope are part of the cache key"
        raises(mr.CategoryError, mr.get_analysis, connect, cat, "unicorns", "3y")
        raises(mr.CategoryError, mr.get_analysis, connect, cat, "local-other", "3y")
        assert log.count("connect") == 3, "bad requests never reach the database"
    finally:
        mr.ANALYSIS_CACHE.clear()


def test_get_analysis_closes_connection_on_error():
    mr.ANALYSIS_CACHE.clear()
    log = []

    class Broken(FakeCursor):
        def execute(self, sql, params=None):
            raise RuntimeError("database went away")

    def connect():
        return FakeConnection(Broken([]), log)

    try:
        raises(RuntimeError, mr.get_analysis, connect, mr.resolve_category(cpv="5072"), "all", "3y")
        assert log == ["close"]
    finally:
        mr.ANALYSIS_CACHE.clear()


def test_organisation_search_and_lookup():
    rows = [("London Borough Of Camden", LG, 500), ("London Borough of Camden Council", LG, 120),
            ("Camden Primary Academy", "Education & Academies", 40)]
    cur = FakeCursor([rows])
    found = mr.search_organisations(cur, "Camden")
    assert cur.params == ["%Camden%"] and "ILIKE %s" in cur.sql
    assert [o["name"] for o in found] == ["London Borough Of Camden", "Camden Primary Academy"]
    assert found[0]["contracts"] == 620 and found[0]["type"] == "london-borough" and found[0]["key"] == "CAMDEN"
    assert found[1]["type"] == "education"
    cur = FakeCursor([[]])
    assert mr.search_organisations(cur, "a") == [] and cur.statements == [], "one letter never queries"
    cur = FakeCursor([[]])
    mr.search_organisations(cur, "100%_x")
    assert cur.params == [r"%100\%\_x%"], "wildcards in the search box are escaped"
    cur = FakeCursor([[("London Borough Of Camden", LG, 500)]])
    assert mr.lookup_organisation(cur, "London Borough Of Camden")["type_label"] == "London boroughs"
    assert mr.lookup_organisation(FakeCursor([[]]), "Nobody") is None


DASHBOARD_COLUMNS = FETCH_COLUMNS[:-1]


def test_fetch_org_renewals():
    org = mr.organisation_profile("London Borough of Camden", LG)
    rows = [
        award(id=1, authority_name="London Borough of Camden", supplier_name="Acme Repairs Ltd", date_signed="2023-12-01",
              contract_end_date="2026-12-01"),
        award(id=2, authority_name="London Borough Of Camden", supplier_name="Beta Heating plc", date_signed="2023-11-01",
              contract_end_date="2026-11-01"),
        award(id=3, authority_name="Camden Primary Academy", buyer_type="Education & Academies", date_signed="2023-11-15",
              contract_end_date="2026-11-15"),
        award(id=4, authority_name="London Borough of Camden Council", supplier_name="BETA HEATING LIMITED",
              date_signed="2023-11-01", contract_end_date="2026-11-01"),
    ]
    cur = FakeCursor([[db_tuple(r) for r in rows]], DASHBOARD_COLUMNS)
    out = mr.fetch_org_renewals(cur, org, today=TODAY)
    assert_sql_is_safe(cur.sql, cur.params)
    assert cur.params[:3] == ["%camden%", "2026-10-05", "2027-04-06"]
    assert [(r["supplier"], r["ends"], r["days_left"]) for r in out] == [
        ("Beta Heating plc", "2026-11-01", 27), ("Acme Repairs Ltd", "2026-12-01", 57)], \
        "another buyer with the same word in its name and a republished notice are both left out"
    assert out[0]["cpv"] == "50721000"


def test_fetch_peer_activity():
    org = mr.organisation_profile("London Borough of Camden", LG)
    cats = [mr.resolve_category(preset="housing-repairs-gas")]
    rows = [
        award(id=1, authority_name="London Borough of Islington", supplier_name="Acme Repairs Ltd",
              tender_title="Gas servicing", date_signed="2026-08-01"),
        award(id=2, authority_name="London Borough of Camden", date_signed="2026-08-02"),
        award(id=3, authority_name="Kent County Council", date_signed="2026-07-15"),
        award(id=4, authority_name="London Borough Of Islington", supplier_name="ACME REPAIRS LIMITED", date_signed="2026-08-01"),
        award(id=5, authority_name="London Borough of Hackney", supplier_name="Beta Heating plc",
              tender_title="Boiler replacement", date_signed="2026-07-01"),
    ]
    cur = FakeCursor([[db_tuple(r) for r in rows]], DASHBOARD_COLUMNS)
    out = mr.fetch_peer_activity(cur, cats, org, today=TODAY)
    assert_sql_is_safe(cur.sql, cur.params)
    assert cur.params[0] == "2026-04-05", "183 days (six months) back, the same length as the contracts-ending window looking forward"
    assert [(a["buyer"], a["supplier"]) for a in out] == [
        ("London Borough of Islington", "Acme Repairs Ltd"), ("London Borough of Hackney", "Beta Heating plc")], \
        "own awards, other kinds of buyer and republished notices are left out"
    assert out[0]["category"] == "Housing repairs & gas servicing" and out[0]["category_key"] == "preset:housing-repairs-gas"
    cur = FakeCursor([[db_tuple(r) for r in rows]], DASHBOARD_COLUMNS)
    assert len(mr.fetch_peer_activity(cur, cats, org, today=TODAY, limit=1)) == 1
    quiet = FakeCursor([])
    assert mr.fetch_peer_activity(quiet, [], org, today=TODAY) == [] and quiet.statements == [], "nothing followed, no query"
    assert mr.peer_scope("local-other") == "local-government" and mr.peer_scope("county") == "county"


# ──────────────────────────────────────────── CPV labels ─────────────────────────────────────────

CPV_DESCRIBED = [
    ("50721000", "Repair and maintenance services of heating equipment", 30),
    ("50721000", "Not available", 90),                       # junk text is never used as a label
    ("50720000", "Repair and maintenance services of central heating", 10),
    ("42000000", "Industrial machinery", 3),
    ("42160000", "Boiler installations", 5),
    ("42161000", "Boiler installations", 4),
]
CPV_TOTALS = [("50721000", 120), ("50720000", 10), ("42000000", 3), ("42160000", 5), ("42161000", 4)]


class FakeCpvConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor([CPV_DESCRIBED, CPV_TOTALS])
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


def _cpv_labels():
    mr._cpv_cache.update(at=0.0, labels={}, counts={})
    conn = FakeCpvConnection()
    labels, counts = mr.load_cpv_labels(lambda: conn, force=True)
    assert conn.closed
    return labels, counts


def test_load_cpv_labels():
    labels, counts = _cpv_labels()
    assert labels["5072"] == "Repair and maintenance services of central heating"
    assert labels["50721"] == "Repair and maintenance services of heating equipment"
    assert labels["507"] == labels["50721"], "a prefix nobody uses on its own borrows its busiest child's label"
    assert "Not available" not in labels.values()
    assert counts["5072"] == 130 and counts["50"] == 130 and counts["50721"] == 120 and counts["4216"] == 9
    assert mr._cpv_prefix("50721000") == "50721" and mr._cpv_prefix("50720000") == "5072"
    assert mr._cpv_prefix("00000000") == "00"


def test_suggest_categories():
    _cpv_labels()

    def connect():
        raise AssertionError("the labels were just loaded, so they must come from the cache")

    everything = mr.suggest_categories(connect, "")
    assert len(everything["presets"]) == len(mr.CATEGORY_PRESETS) and everything["cpv"] == []

    boiler = mr.suggest_categories(connect, "boiler")
    assert [p["preset"] for p in boiler["presets"]] == ["housing-repairs-gas"], "found through its keywords"
    assert [c["label"] for c in boiler["cpv"]] == ["Boiler installations"], "one suggestion per label"
    assert boiler["cpv"][0]["awards"] == 9

    by_code = mr.suggest_categories(connect, "5072")
    assert [c["cpv"] for c in by_code["cpv"]] == ["5072", "50721"], "busiest first"
    assert by_code["presets"] == []
    assert mr.suggest_categories(connect, "zzzz nothing")["cpv"] == []


# ──────────────────────────────────────────── the cache ──────────────────────────────────────────

def test_cache_ttl_and_lru():
    now = [1000.0]
    cache = mr.AnalysisCache(ttl=60, max_entries=2, clock=lambda: now[0])
    calls = []

    def compute(tag):
        def run():
            calls.append(tag)
            return {"tag": tag}
        return run

    assert cache.get_or_compute("a", compute("a1")) == {"tag": "a1"}
    assert cache.get_or_compute("a", compute("a2")) == {"tag": "a1"}
    now[0] += 61
    assert cache.get_or_compute("a", compute("a3")) == {"tag": "a3"}, "expired"
    cache.get_or_compute("b", compute("b1"))
    cache.get_or_compute("c", compute("c1"))           # over capacity: "a", the least recently used, goes
    cache.get_or_compute("a", compute("a4"))
    assert calls == ["a1", "a3", "b1", "c1", "a4"]
    cache.get_or_compute("c", compute("c2"))
    assert calls[-1] == "a4", "c is still cached"
    cache.clear()
    cache.get_or_compute("c", compute("c3"))
    assert calls[-1] == "c3"


def test_cache_does_not_keep_failures():
    cache = mr.AnalysisCache()
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("boom")
        return "ok"

    raises(RuntimeError, cache.get_or_compute, "k", flaky)
    assert cache.get_or_compute("k", flaky) == "ok" and len(attempts) == 2
    assert cache.get_or_compute("k", flaky) == "ok" and len(attempts) == 2


def test_cache_runs_one_scan_for_concurrent_requests():
    cache = mr.AnalysisCache()
    calls, results = [], []

    def slow():
        calls.append(1)
        time.sleep(0.2)
        return "value"

    threads = [threading.Thread(target=lambda: results.append(cache.get_or_compute("k", slow))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert results == ["value"] * 8 and len(calls) == 1, f"{len(calls)} scans for 8 simultaneous requests"


# ───────────────────────────────────── market engagement helpers ─────────────────────────────────

def test_validate_plan():
    ok = me.validate_plan({"title": "  Heat meter PME  "})
    assert ok["title"] == "Heat meter PME" and ok["engagement_type"] == "questionnaire"
    assert ok["est_value"] is None and ok["term_years"] is None and ok["organisation"] is None

    full = me.validate_plan({
        "title": "Heat meter PME", "organisation": "London Borough of Camden", "est_value": "250000", "term_years": 3,
        "engagement_type": "both", "objectives": "Test the market", "supplier_day_at": "12 Nov, 10:00",
        "supplier_day_place": "Town Hall", "contact_name": "Jane Doe", "contact_email": "jane@example.org",
        "response_deadline": "2026-11-30", "notice_text": "x", "published_url": "https://example.org/n/1",
    })
    assert full["est_value"] == 250000.0 and full["term_years"] == 3.0 and full["response_deadline"] == date(2026, 11, 30)
    assert full["published_url"] == "https://example.org/n/1" and full["contact_email"] == "jane@example.org"

    assert "status" not in me.validate_plan({"title": "Valid title", "status": "converted", "username": "someone"}), \
        "workflow fields cannot be set through an edit"
    assert me.validate_plan({"objectives": "x"}, partial=True) == {"objectives": "x"}
    assert me.validate_plan({}, partial=True) == {}
    assert me.validate_plan({"response_deadline": ""}, partial=True) == {"response_deadline": None}
    assert me.validate_plan({"contact_email": ""}, partial=True) == {"contact_email": None}

    bad = [{}, {"title": "ab"}, {"title": "   "}, {"title": 123}, {"title": "x" * 201},
           {"title": "Valid", "est_value": "abc"}, {"title": "Valid", "est_value": -1}, {"title": "Valid", "est_value": float("nan")},
           {"title": "Valid", "est_value": float("inf")}, {"title": "Valid", "term_years": 0}, {"title": "Valid", "term_years": 31},
           {"title": "Valid", "engagement_type": "carrier pigeon"}, {"title": "Valid", "contact_email": "not-an-email"},
           {"title": "Valid", "response_deadline": "2026-13-45"}, {"title": "Valid", "response_deadline": "30/11/2026"},
           {"title": "Valid", "published_url": "ftp://example.org"}, {"title": "Valid", "published_url": "javascript:alert(1)"},
           {"title": "Valid", "objectives": "x" * 4001}]
    for data in bad:
        raises(me.ValidationError, me.validate_plan, data)
    raises(me.ValidationError, me.validate_plan, {"title": None}, True)


def test_validate_supplier_update():
    assert me.validate_supplier_update({"included": False}) == {"included": False}
    assert me.validate_supplier_update({"status": "invited", "note": " called "}) == {"status": "invited", "note": "called"}
    assert me.validate_supplier_update({"note": ""}) == {"note": None}
    for bad in ({}, {"unknown": 1}, {"included": "yes"}, {"included": 1}, {"status": "ghosted"}, {"note": "x" * 1001}):
        raises(me.ValidationError, me.validate_supplier_update, bad)


def test_money():
    assert me.money(None) is None and me.money("") is None
    assert me.money(250000) == "£250,000" and me.money("999999") == "£999,999"
    assert me.money(1_000_000) == "£1m" and me.money(1_500_000) == "£1.5m" and me.money(2_300_000) == "£2.3m"


def test_notice_text_flags_what_is_missing():
    text, missing = me.build_notice_text({})
    assert missing == ["contracting authority", "indicative contract value", "indicative term", "response deadline",
                       "contact name and email"]
    assert text.startswith("PRELIMINARY MARKET ENGAGEMENT NOTICE (DRAFT)")
    assert "Procurement Act 2023" in text and "does not publish notices on your behalf" in text
    assert text.count(me.TODO) >= 5
    assert "Every supplier that takes part will receive the same information at the same time." in text


def test_notice_text_complete_plan():
    plan = {"organisation": "London Borough of Camden", "title": "Heat meters", "category_label": "Heat meters",
            "est_value": 250000, "term_years": 3, "engagement_type": "questionnaire",
            "response_deadline": date(2026, 11, 30), "contact_name": "Jane Doe", "contact_email": "jane@example.org"}
    text, missing = me.build_notice_text(plan)
    assert missing == [] and me.TODO not in text
    for expected in ("Contracting authority: London Borough of Camden", "Indicative contract value: £250,000 (an estimate, not a commitment)",
                     "Indicative term: 3 years", "return it by 30 November 2026", "Contact: Jane Doe <jane@example.org>",
                     "its needs for Heat meters"):
        assert expected in text, expected
    assert "Indicative term: 1 year\n" in me.build_notice_text({**plan, "term_years": 1})[0]
    assert "Indicative term: 2.5 years" in me.build_notice_text({**plan, "term_years": 2.5})[0]
    assert "30 November 2026" in me.build_notice_text({**plan, "response_deadline": "2026-11-30"})[0], "dates may arrive as text"
    custom = me.build_notice_text({**plan, "objectives": "Our own purpose statement."})[0]
    assert "Our own purpose statement." in custom and "is carrying out preliminary market engagement" not in custom


def test_notice_text_by_engagement_type():
    base = {"organisation": "Org", "title": "T", "category_label": "C", "est_value": 1, "term_years": 1,
            "contact_name": "A", "contact_email": "a@b.co"}
    meetings, missing = me.build_notice_text({**base, "engagement_type": "meetings"})
    assert "one-to-one meetings" in meetings and "response deadline" in missing and "supplier questionnaire" not in meetings
    day, missing = me.build_notice_text({**base, "engagement_type": "supplier_day"})
    assert "Supplier day: [to be confirmed], [to be confirmed]." in day
    assert missing == ["supplier day date and time", "supplier day place or online link", "response deadline"]
    day_done, missing = me.build_notice_text({**base, "engagement_type": "supplier_day", "supplier_day_at": "12 Nov, 10:00",
                                              "supplier_day_place": "Town Hall", "response_deadline": date(2026, 11, 5)})
    assert missing == [] and "Supplier day: 12 Nov, 10:00, Town Hall." in day_done
    assert "Please confirm your attendance by 5 November 2026." in day_done
    both = me.build_notice_text({**base, "engagement_type": "both"})[0]
    assert "supplier questionnaire" in both and "Supplier day:" in both


def test_step_states():
    def states(plan, suppliers=0):
        return [s["state"] for s in me.step_states(plan, suppliers)]

    ready = {
        "title": "T", "category_label": "C", "organisation": "Org",
        "est_value": 100000, "term_years": 1, "response_deadline": "2026-11-01",
        "contact_name": "Jane", "contact_email": "jane@example.com"
    }
    assert states(ready) == ["done", "active", "upcoming", "upcoming", "upcoming"]
    assert states({"title": "T", "category_label": "C"}) == ["active", "upcoming", "upcoming", "upcoming", "upcoming"]
    assert states(ready, 3) == ["done", "done", "active", "upcoming", "upcoming"]
    assert states({**ready, "status": "published"}, 3) == ["done", "done", "done", "active", "upcoming"]
    assert states({**ready, "status": "closed"}, 3) == ["done", "done", "done", "done", "active"]
    assert states({**ready, "status": "converted"}, 3) == ["done"] * 5
    assert [s["n"] for s in me.step_states(ready, 0)] == [1, 2, 3, 4, 5]
    assert set(me.TRANSITIONS) == set(me.STATUSES) and me.TRANSITIONS["converted"] == set()


def test_shortlist_suppliers():
    pool = [
        {"key": "A", "supplier": "A Ltd", "supplier_id": 1, "contracts": 5, "framework_appointments": 0, "buyers": 4,
         "total_value": 10_000_000, "avg_contract": 2_500_000, "latest_signed": "2025-01-01"},
        {"key": "B", "supplier": "B Ltd", "supplier_id": 2, "contracts": 2, "framework_appointments": 1, "buyers": 3,
         "total_value": 600_000, "avg_contract": 200_000, "latest_signed": "2025-02-01"},
        {"key": "C", "supplier": "C Ltd", "supplier_id": None, "contracts": 1, "framework_appointments": 0, "buyers": 3,
         "total_value": None, "avg_contract": None, "latest_signed": "2025-03-01"},
        {"key": "D", "supplier": "D Ltd", "supplier_id": 4, "contracts": 4, "framework_appointments": 0, "buyers": 3,
         "total_value": 1_500_000, "avg_contract": 500_000, "latest_signed": "2025-04-01"},
        {"key": "Z", "supplier": "Zero", "supplier_id": 5, "contracts": 0, "framework_appointments": 0, "buyers": 0,
         "total_value": None, "avg_contract": None, "latest_signed": None},
    ]
    widest = me.shortlist_suppliers(pool)
    assert [s["supplier_key"] for s in widest] == ["A", "D", "B", "C"], "buyers, then awards; empty rows dropped"
    assert [s["stats"]["awards"] for s in widest] == [5, 4, 3, 1], "framework appointments count as awards"
    assert [s["stats"]["size_hint"] for s in widest] == ["larger contracts", "mid-sized contracts", "smaller contracts", None]
    smaller = me.shortlist_suppliers(pool, smaller_first=True)
    assert [s["supplier_key"] for s in smaller] == ["B", "D", "A", "C"], "smallest typical contract first, unknown last"
    assert len(me.shortlist_suppliers(pool, limit=2)) == 2
    edge = me.shortlist_suppliers([{**pool[0], "avg_contract": 250_000}, {**pool[1], "avg_contract": 2_000_000}])
    assert {s["supplier_key"]: s["stats"]["size_hint"] for s in edge} == {"A": "mid-sized contracts", "B": "mid-sized contracts"}


def test_shortlist_uses_the_median_contract_not_the_average():
    pool = [{"key": "H", "supplier": "Heat Ltd", "supplier_id": 1, "contracts": 6, "framework_appointments": 0, "buyers": 4,
             "total_value": 50_000_000, "avg_contract": 8_000_000, "median_contract": 90_000, "latest_signed": "2025-01-01"}]
    out = me.shortlist_suppliers(pool)[0]["stats"]
    assert out["typical_contract"] == 90_000 and out["size_hint"] == "smaller contracts", out
    assert out["avg_contract"] == 8_000_000, "the average is still kept"
    assert me.shortlist_suppliers([{**pool[0], "median_contract": None}])[0]["stats"]["typical_contract"] == 8_000_000, "falls back to the average"


def test_handoff_markdown():
    plan = {"title": "Heat meters", "organisation": "Camden", "category_label": "Heat meters", "est_value": 250000,
            "term_years": 3, "engagement_type": "both", "status": "converted",
            "published_url": "https://example.org/n/1", "published_at": datetime(2026, 11, 2, 9, 30)}
    suppliers = [
        {"supplier_name": "Acme Ltd", "included": True, "status": "responded", "note": "Likes | pipes\nand more"},
        {"supplier_name": "Beta plc", "included": True, "status": "declined", "note": None},
        {"supplier_name": "Gamma Ltd", "included": True, "status": "responded", "note": ""},
        {"supplier_name": "Hidden Co", "included": False, "status": "invited", "note": "x"},
    ]
    log = [{"at": datetime(2026, 11, 2, 9, 30), "message": "Notice published"}, {"at": "2026-11-20T10:00:00", "message": "Closed"}]
    md = me.build_handoff_markdown(plan, suppliers, log)
    assert md.startswith("# Market engagement hand-over: Heat meters")
    assert "- **Indicative value:** £250,000" in md and "- **Indicative term:** 3 years" in md
    assert "Questionnaire and supplier day" in md and "https://example.org/n/1" in md and "2 November 2026" in md
    assert "| Acme Ltd | Responded | Likes / pipes and more |" in md, "pipes and line breaks cannot break the table"
    assert "Hidden Co" not in md, "suppliers taken off the list are not in the hand-over"
    assert "Summary: 1 declined, 2 responded" in md
    assert "- 02 Nov 2026 09:30: Notice published" in md and "- 2026-11-20T10:00: Closed" in md
    assert md.rstrip().endswith("_Generated by TenderFlow. Not legal advice._")
    bare = me.build_handoff_markdown({"title": "Bare", "status": "draft"}, [], [])
    assert "No suppliers were recorded." in bare and "No entries." in bare and me.TODO in bare


# ───────────────────────────────────────────── runner ────────────────────────────────────────────

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
