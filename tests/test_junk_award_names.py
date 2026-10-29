"""Page text read as a buyer or a winner is rejected (Tender search QA: "one.1) Name and addresses", "is PwC, with a committed").

Run: python -m pytest tests/test_junk_award_names.py
"""
from etenders_scraper.awards import is_junk_authority_name, is_junk_supplier_name
from tender_app.growth_studio import _renewal_text


def test_a_section_heading_is_not_a_buyer():
    assert is_junk_authority_name("one.1) Name and addressesI.1) Name and addresses")
    assert is_junk_authority_name("I.1) Name and addresses")
    assert is_junk_authority_name("1.1) Buyer")
    assert not is_junk_authority_name("Leeds City Council")
    assert not is_junk_authority_name("NHS England")


def test_a_sentence_fragment_is_not_a_winner():
    assert is_junk_supplier_name("is PwC, with a committed")
    assert is_junk_supplier_name("by EDF Energy Customers Ltd")
    assert not is_junk_supplier_name("with YOU"), "a real charity, stored in lower case"
    assert not is_junk_supplier_name("IS Group")
    assert not is_junk_supplier_name("Island Cars Ltd")
    assert not is_junk_supplier_name("Anderson and Sons")
    assert not is_junk_supplier_name("PwC")


def _entry(years):
    return {"framework": False, "title": "Data subscription", "days_left": 63, "ends": "2026-12-31", "suppliers": ["Moody's"],
            "supplier_count": 1, "value": 488263.0, "annual_value": 490000.0, "term_years": years}


def test_a_one_year_term_is_not_written_as_years():
    assert "over 1 year)" in _renewal_text(_entry(1))[1]
    assert "over 3 years)" in _renewal_text(_entry(3))[1]
