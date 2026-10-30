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


def test_a_council_published_under_several_spellings_is_one_buyer():
    """Report-6 follow-up: spelling variants of one council (case, '&', 'The', MBC, Borough/City/County Council) go to one name."""
    for name in ("Bradford Metropolitan District Council", "CITY OF BRADFORD METROPOLITAN DISTRICT COUNCIL", "The City of Bradford Metropolitan District Council"):
        assert canon(name) == "Bradford Metropolitan District Council", name
    for name in ("Milton Keynes Council", "Milton Keynes City Council", "Milton Keynes City Council "):
        assert canon(name) == "Milton Keynes Council", name
    assert canon("SANDWELL MBC") == canon("Sandwell Metropolitan Borough Council") == "Sandwell Metropolitan Borough Council"
    assert canon("Telford and Wrekin Council") == canon("TELFORD & WREKIN COUNCIL") == "Telford & Wrekin Council"
    assert canon("Tower Hamlets") == canon("LONDON BOROUGH OF TOWER HAMLETS") == "London Borough of Tower Hamlets"
    assert canon("Islington") == "London Borough of Islington"


def test_successor_councils_and_non_councils_stay_separate():
    assert canon("North Yorkshire County Council") == "North Yorkshire County Council"
    assert canon("North Yorkshire Council") != canon("North Yorkshire County Council")
    assert canon("Buckinghamshire County Council") != canon("Buckinghamshire Council")
    assert canon("Dorset County Council") != canon("Dorset Council")
    assert canon("British Council") == "British Council"
    assert canon("Kent") == "Kent" and canon("Sutton") == "Sutton"
    assert canon("Kent County Council") == "Kent County Council"
