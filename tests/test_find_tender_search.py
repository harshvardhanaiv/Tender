"""Run: python tests/test_find_tender_search.py   (plain asserts; no network, no database).

Find a Tender's search form hands out a SINGLE-USE token. The scraper used to cache one token for 30
minutes and reuse it for every search, so after the first search every later one was answered with the
site's "Something went wrong" page, which the scraper read as "0 results" without any error (Round 27:
"Find a Tender returned 0 in every run, with no warning"). These tests run the scraper against a
stand-in for the site that behaves the same way (single-use tokens, the search kept in the session,
pages by GET, an error page that still answers HTTP 200) and check that:

  * every search works, however many ran before it, and a long result list is read page by page,
  * a real "0 notices" is an empty result, but an error page / rate limit / unreadable page is raised
    as FindTenderError and never returned as an empty list,
  * the rows carry what the results page shows (organisation, notice type, value, dates, location).
"""
import sys
import time
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etenders_scraper.sources import find_tender as ft  # noqa: E402

SLEEPS: list[float] = []
ft._sleep = SLEEPS.append  # nothing in these tests waits
ft._MIN_GAP_SEC = 0.0


def raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as e:
        return str(e)
    raise AssertionError(f"{fn.__name__} did not raise {exc.__name__}")


# ── a stand-in for find-tender.service.gov.uk ────────────────────────────────────────────────

def notice(n, title="A notice", authority="Some Council", kind="F02: Contract notice", **extra):
    return {"id": f"{n:06d}-2026", "title": title, "authority": authority, "kind": kind, **extra}


def results_page(notices, total, token):
    blocks = ""
    for n in notices:
        entries = [("Notice type", n["kind"])]
        for label in ("Total value excluding VAT", "Contract location", "Suppliers", "Submission deadline", "Publication date"):
            key = label.lower().replace(" ", "_")
            if n.get(key):
                entries.append((label, n[key]))
        dl = "".join(f'<div class="search-result-entry"><dt><strong>{escape(k)}</strong></dt><dd>{escape(v)}</dd></div>' for k, v in entries)
        blocks += f"""
        <div class="search-result">
          <div class="search-result-header" title="{escape(n['title'])}"><h2 id="{n['id']}-heading">
            <a class="govuk-link search-result-rwh break-word" href="https://www.find-tender.service.gov.uk/Notice/{n['id']}?origin=SearchResults&amp;p=1">{escape(n['title'])}</a></h2></div>
          <div class="search-result-sub-header wrap-text">{escape(n['authority'])}</div>
          <div class="wrap-text" id="{n['id']}-description"> ...the <span class="search-keyword-match">keyword</span> in context... </div>
          <dl aria-labelledby="{n['id']}-heading">{dl}</dl>
        </div>"""
    found = f"<p>We've found {total:,} notices</p>"
    return (f"<html><head><title>Search results - Find a Tender</title></head><body><h1>Search results</h1>{found}"
            f'<form><input type="hidden" name="form_token" value="{token}"></form>{blocks}</body></html>')


ERROR_PAGE = "<html><head><title>Something went wrong - Find a Tender</title></head><body><h1>Something went wrong</h1></body></html>"
FORM_PAGE = '<html><head><title>Search results - Find a Tender</title></head><body><form><input type="hidden" name="form_token" value="{token}"></form></body></html>'


class Response:
    def __init__(self, status=200, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}


class Site:
    """Tokens are single use; the search is kept in the session; the error page still answers 200."""

    def __init__(self, catalogue):
        self.catalogue = catalogue          # keyword -> list of notices
        self.valid_tokens = set()
        self.issued = 0
        self.log = []                       # (method, params) of every request
        self.fail_posts = 0                 # answer this many searches with the error page
        self.form_statuses = []             # statuses to answer the next form GETs with (then 200)
        self.post_status = None
        self.page2_status = None
        self.unreadable = False

    def token(self):
        self.issued += 1
        tok = f"token-{self.issued}"
        self.valid_tokens.add(tok)
        return tok

    def new_session(self):
        return Session(self)


class Session:
    def __init__(self, site):
        self.site, self.keyword, self.headers = site, None, {}

    def request(self, method, url, timeout=None, params=None, data=None, **kw):
        site = self.site
        site.log.append((method, dict(params or {})))
        if method == "GET" and not params:
            if site.form_statuses:
                status = site.form_statuses.pop(0)
                if status != 200:
                    return Response(status, "", {"Retry-After": "2"} if status == 429 else {})
            return Response(200, FORM_PAGE.format(token=site.token()))
        if method == "POST":
            if site.post_status:
                return Response(site.post_status, "")
            tok = (data or {}).get("form_token")
            if site.fail_posts:
                site.fail_posts -= 1
                return Response(200, ERROR_PAGE)
            if tok not in site.valid_tokens:
                return Response(200, ERROR_PAGE)
            site.valid_tokens.discard(tok)           # single use
            self.keyword = data["keywords"]
            if site.unreadable:
                return Response(200, "<html><head><title>Search results - Find a Tender</title></head><body>hello</body></html>")
            found = site.catalogue.get(self.keyword, [])
            return Response(200, results_page(found[:20], len(found), site.token()))
        if method == "GET":                           # a later results page
            if self.keyword is None:
                return Response(200, ERROR_PAGE)
            if site.page2_status:
                return Response(site.page2_status, "")
            page = int(params["page"])
            found = site.catalogue.get(self.keyword, [])
            return Response(200, results_page(found[(page - 1) * 20: page * 20], len(found), site.token()))
        raise AssertionError(f"unexpected request {method} {url}")


def run(site, keyword="construction", **kw):
    ft._make_session = site.new_session
    info = {}
    rows = ft.search_find_tender(keyword=keyword, source_id="find_tender", source_label="Find a Tender (UK)", info=info, **kw)
    return rows, info


# ── tests ────────────────────────────────────────────────────────────────────────────────────

def test_every_search_works_not_just_the_first():
    site = Site({"construction": [notice(i) for i in range(1, 6)], "plumbing": [notice(i) for i in range(50, 53)]})
    for keyword, expected in (("construction", 5), ("plumbing", 3), ("construction", 5), ("plumbing", 3)):
        rows, info = run(site, keyword)
        assert len(rows) == expected and info["total"] == expected, (keyword, len(rows))
    assert site.issued == 8 and len([m for m, _ in site.log if m == "POST"]) == 4, "one fresh token per search"


def test_a_long_result_list_is_read_page_by_page():
    site = Site({"construction": [notice(i) for i in range(1, 46)]})
    rows, info = run(site, max_pages=6)
    assert len(rows) == 45 and info["total"] == 45 and "note" not in info
    assert len({r["resource_id"] for r in rows}) == 45
    pages = [p for m, p in site.log if m == "GET" and p]
    assert pages == [{"page": 2}, {"page": 3}], "pages 2 and 3 are GETs in the same session, never repeat POSTs"
    assert len([1 for m, _ in site.log if m == "POST"]) == 1
    rows, _ = run(Site({"construction": [notice(i) for i in range(1, 200)]}), max_pages=3)
    assert len(rows) == 60, "max_pages is honoured"


def test_first_page_stops_at_max_results_without_asking_for_more():
    site = Site({"construction": [notice(i) for i in range(1, 100)]})
    rows, info = run(site, max_results=20, max_pages=2)
    assert len(rows) == 20 and info["total"] == 99
    assert [p for m, p in site.log if m == "GET" and p] == [], "the first page is enough"
    ft._make_session = Site({"construction": [notice(1)]}).new_session
    assert len(ft.search_find_tender_first_page(keyword="construction", source_id="find_tender", source_label="FT")) == 1


def test_a_real_zero_is_empty_but_an_error_page_is_not():
    site = Site({})
    rows, info = run(site, "zzqxv")
    assert rows == [] and info["total"] == 0, "'We've found 0 notices' is a genuine, empty answer"

    site = Site({"construction": [notice(1)]})
    site.fail_posts = 1
    rows, _ = run(site)
    assert len(rows) == 1, "one error page is retried from a fresh session"
    assert len([1 for m, p in site.log if m == "GET" and not p]) == 2, "the retry fetched a new form (and token)"

    site = Site({"construction": [notice(1)]})
    site.fail_posts = 2
    message = raises(ft.FindTenderError, run, site)
    assert "Something went wrong" in message, "two in a row is a failure, never an empty list"


def test_unreadable_page_and_http_errors_are_failures():
    site = Site({"construction": [notice(1)]})
    site.unreadable = True
    assert "could not read" in raises(ft.FindTenderError, run, site)
    site = Site({})
    site.post_status = 500
    assert "HTTP 500" in raises(ft.FindTenderError, run, site)
    site = Site({})
    site.post_status = 429
    assert "429" in raises(ft.FindTenderError, run, site)


def test_rate_limiting_is_waited_out_then_reported():
    SLEEPS.clear()
    site = Site({"construction": [notice(1)]})
    site.form_statuses = [429]
    rows, _ = run(site)
    assert len(rows) == 1 and SLEEPS and max(SLEEPS) <= 10, "one 429 is waited out (honouring Retry-After)"
    site = Site({"construction": [notice(1)]})
    site.form_statuses = [429, 429, 429]
    assert "429" in raises(ft.FindTenderError, run, site), "a portal that keeps refusing is reported, not hidden"


def test_a_failed_later_page_keeps_the_pages_already_read():
    site = Site({"construction": [notice(i) for i in range(1, 46)]})
    site.page2_status = 500
    rows, info = run(site)
    assert len(rows) == 20 and "results page 2" in info["note"]


def test_rows_carry_what_the_results_page_shows():
    site = Site({"construction": [
        notice(77, title="TC621 - NORTH WEST CONSTRUCTION HUB", authority="Manchester City Council",
               kind="F03: Contract award notice", total_value_excluding_vat="£250,000,000 - £1,500,000,000",
               contract_location="UKD - North West (England)", suppliers="ISG Construction Ltd; Kier Construction Limited",
               publication_date="3 January 2023, 3:29pm"),
        notice(78, title="Combustibility testing", authority="Department for Business and Trade", kind="F02: Contract notice",
               total_value_excluding_vat="£810,000", contract_location="UKI - London",
               submission_deadline="5 July 2023, 4:00pm", publication_date="24 May 2023, 1:18pm"),
        notice(79, title="Nothing else stated", authority="Tiny Parish Council", kind="UK6: Contract details notice"),
    ]})
    rows, _ = run(site)
    award, tender, bare = rows
    assert award["contracting_authority"] == "Manchester City Council" and award["title"] == "TC621 - NORTH WEST CONSTRUCTION HUB"
    assert award["status"] == "Awarded" and award["notice_type"] == "F03: Contract award notice"
    assert award["estimated_value_eur"] == "£1,500,000,000", "a range reads as its ceiling"
    assert award["location"] == "UKD - North West (England)" and award["supplier_name"].startswith("ISG Construction Ltd; Kier")
    assert award["date_published"] == "03/01/2023 15:29:00" and award["submission_deadline"] == ""
    assert award["detail_url"] == "https://www.find-tender.service.gov.uk/Notice/000077-2026"
    assert award["source"] == "find_tender" and award["source_label"] == "Find a Tender (UK)"
    assert tender["status"] == "Active" and tender["estimated_value_eur"] == "£810,000"
    assert tender["submission_deadline"] == "05/07/2023 16:00:00" and tender["location"] == "UKI - London"
    assert bare["status"] == "Awarded", "a contract details notice is post-award"
    assert bare["estimated_value_eur"] == "" and bare["date_published"] == "" and bare["location"] == ""
    assert "in context" in award["description"] and "keyword" in award["description"]


def test_page_classification():
    kind, total, title, rows = ft._parse_page(ERROR_PAGE, "find_tender", "FT")
    assert (kind, rows) == ("error", []) and "Something went wrong" in title
    kind, total, _, rows = ft._parse_page(results_page([], 0, "t"), "find_tender", "FT")
    assert (kind, total, rows) == ("empty", 0, [])
    kind, total, _, rows = ft._parse_page(results_page([], 12, "t"), "find_tender", "FT")
    assert (kind, total) == ("unknown", 12), "notices are claimed but none can be read: the layout changed"
    kind, total, _, rows = ft._parse_page(results_page([notice(1), notice(1)], 2, "t"), "find_tender", "FT")
    assert kind == "results" and len(rows) == 1, "the same notice twice on a page is listed once"
    assert ft._fat_datetime("3 January 2023, 3:29pm") == "03/01/2023 15:29:00"
    assert ft._fat_datetime("19 June 2026") == "19/06/2026 00:00:00"
    assert ft._fat_datetime("sometime soon") == "sometime soon"
    assert ft._best_value({"Lot values including VAT": "£1,000; £250,000.60"}) == "£250,001"
    assert ft._best_value({}) == ""


def test_requests_are_spaced_out_across_threads():
    SLEEPS.clear()
    ft._MIN_GAP_SEC = 1.0
    ft._next_slot = 0.0
    try:
        for _ in range(3):
            ft._throttle()
    finally:
        ft._MIN_GAP_SEC = 0.0
    assert len(SLEEPS) == 2 and SLEEPS[0] > 0.9 and SLEEPS[1] > 1.9, SLEEPS


def main() -> None:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    tests.sort(key=lambda item: item[1].__code__.co_firstlineno)
    failed = []
    for name, fn in tests:
        try:
            ft._next_slot = 0.0
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
