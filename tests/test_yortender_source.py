"""Run: python tests/test_yortender_source.py   (plain asserts; parsing is offline, one live check at the end).

YORtender is a Mercell CTM (eu-supply) public list. The parser reads the table rows; the search
posts the page's own form with the keyword in the Name filter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etenders_scraper.sources import eu_supply_ctm as ctm  # noqa: E402
from etenders_scraper.sources.registry import SOURCES, UK_IE_SOURCE_IDS  # noqa: E402

ROW = """<table><tr>
<td><div>100115292</div></td><td><div>REF-1</div></td>
<td><a href="/app/rfq/rwlentrance_s.asp?PID=110273&amp;PP=ctm/Supplier/publictenders&amp;B=YORTENDER&amp;PS=1">Equipment Loans Service</a></td>
<td><div>20/10/2026</div></td><td><div>20/11/2026 12:00</div></td><td><div>PA23 - Open Procedure</div></td>
<td><div>Sheffield City Council</div></td><td><div>United Kingdom</div></td></tr>
<tr><td>1</td><td>2</td><td><a href="/other">not a tender</a></td></tr></table>"""

FORM = """<form id="searchForm" method="post"><input name="SearchFilter.ShortDescription" type="hidden" value="">
<input name="SearchFilter.TenderId" type="hidden" value="0">
<select name="SearchFilter.PublishType"><option value="2">Awarded</option><option value="1" selected>Issued</option></select>
<select name="SearchFilter.BrandingCode"><option value="">All</option><option value="YORTENDER" selected>Local</option></select>
<input type="checkbox" name="SearchFilter.ShowExpiredRft" value="true"><input name="Branding" type="hidden" value="YORTENDER"></form>"""


def test_registered_as_a_uk_portal():
    cfg = SOURCES["yortender"]
    assert cfg["type"] == "eu_supply_ctm" and cfg["branding"] == "YORTENDER"
    assert "yortender" in UK_IE_SOURCE_IDS, "county filter would drop every YORtender row"


def test_rows_are_parsed():
    rows = ctm._parse_rows(ROW, "https://yortender.eu-supply.com")
    assert len(rows) == 1, rows
    r = rows[0]
    assert r["resource_id"] == "110273" and r["title"] == "Equipment Loans Service"
    assert r["contracting_authority"] == "Sheffield City Council"
    assert r["date_published"] == "20/10/2026" and r["submission_deadline"] == "20/11/2026 12:00"
    assert r["detail_url"].startswith("https://yortender.eu-supply.com/app/rfq/rwlentrance_s.asp?PID=110273")


def test_form_defaults_keep_selected_options_and_skip_checkboxes():
    d = ctm._form_defaults(FORM)
    assert d["SearchFilter.PublishType"] == "1" and d["SearchFilter.BrandingCode"] == "YORTENDER"
    assert "SearchFilter.ShowExpiredRft" not in d and d["Branding"] == "YORTENDER"


def test_duplicates_are_dropped_and_labelled():
    raw = [{"resource_id": "1"}, {"resource_id": "1"}, {"resource_id": "2"}, {"title": "no id"}]
    out = ctm._finalize_rows(raw, source_id="yortender", source_label="YORtender", max_results=None)
    assert [r["resource_id"] for r in out] == ["1", "2"] and out[0]["source"] == "yortender"


def test_live_keyword_search():
    from etenders_scraper.sources import search_single_source
    rows, _ = search_single_source("zzzzqq", "yortender")
    assert rows == []
    rows, _ = search_single_source("a", "yortender")
    assert rows and all(r["source"] == "yortender" and r["resource_id"] for r in rows)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
