"""Run: python tests/test_find_tender_corrigendum.py   (plain asserts; no network, no database).

A Find a Tender corrigendum (F14) changes an earlier notice. Its page names that notice under
"VI.6) Original notice reference". These tests use a saved copy of a real corrigendum page
(tests/fixtures/find_tender_corrigendum_027372-2024.html, which corrects 025108-2024) and check that:

  * the original notice id is read from the page, and a page that names none gives "",
  * a corrigendum's page is fetched once, however many searches show it,
  * a page that could not be read is tried again later, but not on every search,
  * rows get corrects_notice_id / corrects_url, and only corrigendum rows from Find a Tender do,
  * no more than max_lookups new pages are read in one call.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etenders_scraper.sources import find_tender as ft  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "find_tender_corrigendum_027372-2024.html"
HTML = FIXTURE.read_text(encoding="utf-8")

ft._sleep = lambda s: None
ft._MIN_GAP_SEC = 0.0


class FakeResponse:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text
        self.headers: dict[str, str] = {}


def install(pages: dict[str, FakeResponse]):
    """Replace the page fetch; returns the list of notice ids that were requested."""
    asked: list[str] = []

    def fake_request(session, method, url, **kwargs):
        rid = url.rsplit("/", 1)[-1]
        asked.append(rid)
        return pages.get(rid, FakeResponse(404))

    ft._request = fake_request
    ft._make_session = lambda: object()
    ft._CORRECTS_CACHE.clear()
    return asked


def corrigendum_row(rid: str, **extra):
    row = {
        "source": "find_tender",
        "resource_id": rid,
        "notice_type": "F14: Corrigendum notice for changes or additional information",
        "status": "Corrigendum",
        "title": "A corrigendum",
    }
    row.update(extra)
    return row


def test_the_original_notice_is_read_from_the_page():
    assert ft.parse_original_notice_id(HTML, "027372-2024") == "025108-2024"


def test_a_page_that_names_no_original_gives_nothing():
    plain = "<html><body><h2>Contract notice</h2><a href='/Notice/011111-2024'>another notice</a></body></html>"
    assert ft.parse_original_notice_id(plain, "027372-2024") == ""
    assert ft.parse_original_notice_id("", "027372-2024") == ""


def test_a_link_to_its_own_page_is_not_the_original():
    own = '<div><h3>VI.6) Original notice reference</h3><a href="/Notice/027372-2024">itself</a></div>'
    assert ft.parse_original_notice_id(own, "027372-2024") == ""


def test_rows_get_the_link_fields():
    asked = install({"027372-2024": FakeResponse(200, HTML)})
    rows = [corrigendum_row("027372-2024")]
    assert ft.attach_corrigendum_links(rows) == 1
    assert rows[0]["corrects_notice_id"] == "025108-2024"
    assert rows[0]["corrects_url"] == "https://www.find-tender.service.gov.uk/Notice/025108-2024"
    assert asked == ["027372-2024"]


def test_each_corrigendum_is_fetched_once():
    asked = install({"027372-2024": FakeResponse(200, HTML)})
    for _ in range(3):
        rows = [corrigendum_row("027372-2024")]
        ft.attach_corrigendum_links(rows)
        assert rows[0]["corrects_notice_id"] == "025108-2024"
    assert asked == ["027372-2024"], asked


def test_a_page_with_no_original_is_not_fetched_again():
    asked = install({"027372-2024": FakeResponse(200, "<html><body>nothing here</body></html>")})
    for _ in range(3):
        rows = [corrigendum_row("027372-2024")]
        assert ft.attach_corrigendum_links(rows) == 0
        assert "corrects_notice_id" not in rows[0]
    assert asked == ["027372-2024"], asked


def test_a_failed_read_is_retried_only_after_the_wait():
    asked = install({})  # every page answers 404
    rows = [corrigendum_row("027372-2024")]
    ft.attach_corrigendum_links(rows)
    ft.attach_corrigendum_links(rows)
    assert asked == ["027372-2024"], "a failed read is not repeated on the very next search"
    # age the failed entry past the wait, and the page is tried again
    found, ok, _ = ft._CORRECTS_CACHE["027372-2024"]
    assert ok is False
    ft._CORRECTS_CACHE["027372-2024"] = (found, ok, time.monotonic() - ft._CORRECTS_MISS_TTL - 1)
    ft._request = lambda *a, **k: FakeResponse(200, HTML)
    assert ft.attach_corrigendum_links(rows) == 1
    assert rows[0]["corrects_notice_id"] == "025108-2024"


def test_only_find_a_tender_corrigenda_are_looked_up():
    asked = install({"027372-2024": FakeResponse(200, HTML)})
    rows = [
        corrigendum_row("027372-2024", source="contracts_finder"),
        {"source": "find_tender", "resource_id": "011111-2024", "notice_type": "F02: Contract notice", "status": "Active"},
        {"source": "find_tender", "resource_id": "not-an-id", "notice_type": "F14: Corrigendum notice", "status": "Active"},
    ]
    assert ft.attach_corrigendum_links(rows) == 0
    assert asked == [], asked


def test_one_call_reads_at_most_max_lookups_new_pages():
    pages = {f"0000{n:02d}-2024": FakeResponse(200, HTML) for n in range(10)}
    asked = install(pages)
    rows = [corrigendum_row(rid) for rid in pages]
    assert ft.attach_corrigendum_links(rows, max_lookups=4) == 4
    assert len(asked) == 4
    # the next call carries on with the ones not read yet; those already known cost nothing
    assert ft.attach_corrigendum_links(rows, max_lookups=4) == 8
    assert len(asked) == 8


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception:  # noqa: BLE001
            import traceback
            failed.append(name)
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - len(failed)} of {len(tests)} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
