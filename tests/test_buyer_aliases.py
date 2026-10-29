"""One public body, one buyer name (Buyer intelligence QA R12).

Run: python -m pytest tests/test_buyer_aliases.py
"""
from etenders_scraper.awards import canonical_authority_name as canon


def test_spellings_of_nhs_england_are_one_buyer():
    for name in ("NHS England", "NHS ENGLAND", "NHS COMMISSIONING BOARD", "The NHS Commissioning Board",
                 "The NHS Commissioning Board (operating under the name of NHS England)",
                 "The NHS Commissioning Board operating as NHS England", "NHS England (NHS Commissioning Board)"):
        assert canon(name) == "NHS England", name


def test_regional_nhs_england_teams_stay_separate():
    for name in ("NHS England - North West", "NHS England (North)", "NHS England North East and Yorkshire", "NHS England & NHS Improvement North East and Yorkshire"):
        assert canon(name) == name


def test_a_council_procurement_unit_is_the_council():
    assert canon("Leeds City Council - PACS") == "Leeds City Council"
    assert canon("Bristol City Council – PACS") == "Bristol City Council"
    assert canon("Leeds City Council") == "Leeds City Council"
    assert canon("Merseyside Police - Procurement Department") == "Merseyside Police - Procurement Department"
    assert canon("") == "" and canon(None) is None


def test_a_london_borough_is_one_body_however_it_is_written():
    for name in ("Camden Council", "London Borough Of Camden", "London Borough of Camden Council", "CAMDEN COUNCIL"):
        assert canon(name) == "London Borough of Camden", name
    assert canon("Hammersmith & Fulham Council") == "London Borough of Hammersmith and Fulham"
    assert canon("Camden Highline") == "Camden Highline" and canon("Camden") == "Camden", "no 'council' and no 'London Borough': left alone"
    assert canon("Leeds City Council - PACS") == "Leeds City Council"
