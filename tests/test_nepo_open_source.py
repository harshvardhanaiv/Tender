"""Run: python tests/test_nepo_open_source.py   (plain asserts; parsing is offline, one live check at the end).

NEPO Open publishes one public JSON list; the source filters it by keyword itself."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etenders_scraper.sources import nepo_open  # noqa: E402
from etenders_scraper.sources.registry import SOURCES, UK_IE_SOURCE_IDS  # noqa: E402

ITEM = {
    "opportunityID": "9b6e981a-8420-44a8-8796-a86ded51addf",
    "opportunityName": "A028PA Direct Payment Support Services",
    "description": "Essex County Council is seeking input from the market for direct payments.",
    "projectReference": "OPEN-PR-20102026131648816",
    "eventName": "PME",
    "estimatedValue": "4081327.63",
    "createOn": "2026-10-20T14:46:37.356Z",
    "submissionEndDate": "2026-11-04T17:00:00.000Z",
    "account": {"companyName": "Essex County Council"},
}


def test_registered_as_a_uk_portal():
    assert SOURCES["nepo_open"]["type"] == "nepo_open"
    assert "nepo_open" in UK_IE_SOURCE_IDS, "county filter would drop every NEPO Open row"


def test_row_fields():
    r = nepo_open._row(ITEM)
    assert r["resource_id"] == ITEM["opportunityID"]
    assert r["contracting_authority"] == "Essex County Council" and r["reference"] == "OPEN-PR-20102026131648816"
    assert r["estimated_value_eur"] == "£4,081,327.63"
    assert r["date_published"] == "20/10/2026"
    assert r["submission_deadline"] == "04/11/2026 17:00", r["submission_deadline"]  # November: London is UTC
    assert r["detail_url"].endswith("/opportunities/overview/public/" + ITEM["opportunityID"])


def test_summer_deadlines_use_london_time():
    r = nepo_open._row({**ITEM, "submissionEndDate": "2026-07-01T11:00:00.000Z"})
    assert r["submission_deadline"] == "01/07/2026 12:00", r["submission_deadline"]


def test_missing_value_and_bad_rows():
    assert "estimated_value_eur" not in nepo_open._row({**ITEM, "estimatedValue": "0"})
    assert "estimated_value_eur" not in nepo_open._row({**ITEM, "estimatedValue": None})
    assert nepo_open._row({"opportunityName": "no id"}) is None


def test_keyword_rules():
    r = nepo_open._row(ITEM)
    assert nepo_open._matches(r, nepo_open._terms("direct payment"))
    assert nepo_open._matches(r, nepo_open._terms("essex"))
    assert not nepo_open._matches(r, nepo_open._terms("direct roofing"))
    assert nepo_open._matches(r, nepo_open._terms('"direct payment"'))
    assert not nepo_open._matches(r, nepo_open._terms('"payment direct"'))
    assert nepo_open._terms("") == []


def test_live_search():
    everything = nepo_open.search_nepo_open(keyword="")
    assert everything and all(r["source"] == "nepo_open" and r["resource_id"] for r in everything)
    assert nepo_open.search_nepo_open(keyword="zzzzqq") == []
    some = nepo_open.search_nepo_open(keyword="services")
    assert 0 < len(some) <= len(everything)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
