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


def test_retest4_b5_variants_of_one_body_are_one_buyer():
    for name in ("BRITISH COUNCIL(THE)", "British Council", "British Council ", "The British Council"):
        assert canon(name.strip()) == "British Council", name
    for name in ("Kent County Council (t/a KCS)", "Kent County Council (T/A Procurement Services)", "Kent County Council (t/a LASER Energy)",
                 "Kent County Council (t/a KCS Procurement Services)", "COMMERCIAL SERVICES KENT LIMITED", "Commercial Services Kent Limited"):
        assert canon(name) == "Kent County Council", name
    for name in ("Ministry of Defence", "MINISTRY OF DEFENCE", "Ministry of Defence, C&C, C&C", "Ministry of Defence : Ministry of Defence",
                 "Ministry of Defence, DIO, Defence Infrastructure Organisation (DIO)"):
        assert canon(name) == "Ministry of Defence", name
    # bodies that only share a word stay apart
    assert canon("Leicestershire County Council, trading as ESPO") == "Leicestershire County Council, trading as ESPO", "ESPO is a purchasing body in its own right"
    assert canon("Police and Crime Commissioner for Kent") == "Police and Crime Commissioner for Kent"
    assert canon("Defence Equipment and Support") == "Defence Equipment and Support"


def test_retest5_bn5_numbers_addresses_and_tender_references_are_not_buyers():
    from etenders_scraper.awards import is_junk_buyer_name
    for junk in ("2307564.27", "2nd Floor", "019-SN-24 Leisure Services Software Mana", "159-SNBD-23 South Norfolk District Counc"):
        assert is_junk_buyer_name(junk), junk
    for real in ("2Gether Support Solutions Limited", "7 Force- Commercial Services", "Leeds City Council", "2 Sisters Food Group"):
        assert not is_junk_buyer_name(real), real
    assert canon("Ministry of Justice.") == "Ministry of Justice"


def test_retest6_bn5_seven_forces_and_a2dominion_variants_merge():
    for name in ("7 Forces Procurement", "7 Forces Commercial Services", "7 Force Commercial Services", "7 Force- Commercial Services"):
        assert canon(name) == "7 Forces Procurement", name
    for name in ("A2Dominion", "A2Dominion Housing Group Ltd", "A2 Dominion Housing Group Ltd.", "A2Dominion Group", "A2Dominion Housing Group ltd",
                 "A2DOMINION HOUSING GROUP LIMITED", "A2Dominion Group Limited", "A2Dominion Housing Group Ltd (A2D)"):
        assert canon(name) == "A2Dominion", name
    assert canon("7 Sisters Housing") == "7 Sisters Housing"
