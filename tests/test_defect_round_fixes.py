"""Pure-function checks for the D-series QA fixes: mojibake repair and generic ("View Notice") titles."""
from etenders_scraper.sources.bravo_search import _fix_generic_titles, _is_generic_title
from tender_app.text_fix import fix_mojibake, fix_row_text


def test_mojibake_repaired():
    assert fix_mojibake("Project 1283 â\u0080\u0093 Installation") == "Project 1283 – Installation"
    assert fix_mojibake("CafÃ© shop") == "Café shop"


def test_clean_text_untouched():
    assert fix_mojibake("Café – ok") == "Café – ok"
    assert fix_mojibake("plain") == "plain"
    assert fix_mojibake("Ã") == "Ã"


def test_row_text_fields_fixed():
    row = {"title": "A â\u0080\u0093 B", "value": 5}
    assert fix_row_text(row)["title"] == "A – B"


def test_view_notice_title_replaced_by_abstract():
    rows = _fix_generic_titles([
        {"title": "View Notice", "description": "Supply of boiler servicing for council housing"},
        {"title": "Real Title Here", "description": "x"},
    ])
    assert rows[0]["title"] == "Supply of boiler servicing for council housing"
    assert rows[1]["title"] == "Real Title Here"
    assert _is_generic_title("View Notice")
    assert not _is_generic_title("Boiler replacement")
