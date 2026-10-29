"""Eligibility guide for funding cards: rule outcomes, region from postcode, and the where filter."""
from tender_app import funding_fit as ff

SOFTWARE = ff.applicant_from_profile(
    {"company_type": "private", "enterprise_type": "small", "business_type": "J", "postcode": "RG2", "turnover": "£1.8m"},
    "We build software and data products using AI", "AIVHUB LTD",
)


def grant(**kw):
    base = {"name": "A grant", "summary": "", "eligibility": "", "locations": ["National"], "applicants": ["Private Sector"], "matches": ["data"], "band": "low"}
    base.update(kw)
    return base


def test_region_from_postcode():
    assert SOFTWARE["region"] == "South East England"
    assert ff.region_of({"postcode": "EH1 1AA"}) == "Scotland"
    assert ff.region_of({"postcode": "BT7"}) == "Northern Ireland"
    assert ff.region_of({"postcode": "19958", "country": "US"}) == ""
    assert ff.region_of({"postcode": ""}) == ""


def test_parse_money():
    assert ff.parse_money("€1.5M") == 1.5e6 * 0.85
    assert ff.parse_money("£1,800,000") == 1.8e6
    assert ff.parse_money("") is None


def test_likely_when_everything_lines_up():
    out = ff.assess(grant(name="Frontier AI: SME Champions", eligibility="UK wide. Open to SMEs."), SOFTWARE, has_profile=True)
    assert out["verdict"] == "likely" and "SME (under 250 staff)" in out["why"] and "UK wide" in out["why"]


def test_wrong_nation_is_a_blocker():
    out = ff.assess(grant(locations=["Scotland"]), SOFTWARE, has_profile=True)
    assert out == {"verdict": "unlikely", "blocker": "Only open in Scotland; you're based in South East England"}


def test_applicant_type_blocker_uses_the_page_filter():
    out = ff.assess(grant(applicants=["Public Sector", "Non-profit"]), SOFTWARE, kind="Private Sector", has_profile=True)
    assert out["verdict"] == "unlikely" and "applying as a private company" in out["blocker"]


def test_sector_mismatch_and_farming():
    bio = ff.assess(grant(name="Engineering Biology Collaborative R&D"), SOFTWARE, has_profile=True)
    assert bio == {"verdict": "unlikely", "blocker": "Sector mismatch: engineering biology"}
    farm = ff.assess(grant(name="ADOPT Facilitator Support Grant", summary="For farming, growing or forestry businesses.", locations=["England"]), SOFTWARE, has_profile=True)
    assert farm["blocker"] == "Must be a farming, growing or forestry business"


def test_project_cost_floor_vs_turnover():
    out = ff.assess(grant(eligibility="Needs a project costing £100m or more."), SOFTWARE, has_profile=True)
    assert out["blocker"] == "Needs a project costing £100m or more: your turnover is £1.8m"


def test_checks():
    growth = ff.assess(grant(eligibility="You need 20% annual growth over 3 years."), SOFTWARE, has_profile=True)
    assert growth["verdict"] == "check" and "20%+ annual growth" in growth["check"]
    research = ff.assess(grant(eligibility="Projects are led by a research organisation."), SOFTWARE, has_profile=True)
    assert research["verdict"] == "check" and "research organisation" in research["check"]
    no_profile = ff.assess(grant(), ff.applicant_from_profile({}), has_profile=False)
    assert no_profile["verdict"] == "check"


def test_covers():
    assert ff.covers(["National"], "Scotland") and not ff.covers(["National"], "Scotland", include_uk_wide=False)
    assert ff.covers(["England"], "South East England") and not ff.covers(["Scotland"], "England")
    assert ff.covers(["Midlands"], "England") and not ff.covers(["Midlands"], "South East England")
    assert ff.covers(["Wales"], "")


def test_likely_needs_real_affinity():
    weak = ff.assess(grant(name="Music Export Growth Scheme", matches=["based", "business", "deliver"]), SOFTWARE, has_profile=True)
    assert weak["verdict"] == "check" and "rules it out" in weak["check"]
    words = ff.assess(grant(name="Analytics platforms and pipeline grant", matches=["pipeline", "analytics", "platforms"]), SOFTWARE, has_profile=True)
    assert words["verdict"] == "check", "shared profile words alone are not evidence"
    sector = ff.assess(grant(name="Frontier AI grant"), SOFTWARE, has_profile=True)
    assert sector["verdict"] == "likely" and sector["why"].startswith("Fits your sector (information and communication)")
    builder = ff.applicant_from_profile({"company_type": "private", "enterprise_type": "micro", "business_type": "F", "postcode": "SO23"}, "driveways", "TVD")
    assert ff.assess(grant(name="Boiler Upgrade Scheme", locations=["England"]), builder, has_profile=True)["verdict"] == "likely"
    assert ff.assess(grant(name="International Travel Award", summary="Supports capacity building overseas"), builder, has_profile=True)["verdict"] == "check"
