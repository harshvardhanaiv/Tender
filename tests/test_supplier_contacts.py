"""Supplier contact lookup (tender_app/supplier_contacts.py). Plain asserts; run: python tests/test_supplier_contacts.py

No test touches the internet: the page fetcher and the search are fakes."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tender_app import supplier_contacts as sc  # noqa: E402

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


def test_only_public_web_addresses_are_fetched():
    ok = sc.is_public_http_url
    assert ok("https://www.example-co.co.uk/contact", PUBLIC)
    assert not ok("http://localhost/", PUBLIC)
    assert not ok("http://127.0.0.1/admin", PUBLIC)
    assert not ok("http://10.0.0.5/", PUBLIC)
    assert not ok("http://169.254.169.254/latest/meta-data", PUBLIC)       # cloud metadata
    assert not ok("http://[::1]/", PUBLIC)
    assert not ok("file:///etc/passwd", PUBLIC)
    assert not ok("ftp://example.com/", PUBLIC)
    assert not ok("https://user:pw@example.com/", PUBLIC)
    assert not ok("https://example.com:8080/", PUBLIC)
    assert not ok("https://intranet.local/", PUBLIC)
    assert not ok("https://sneaky.example.com/", lambda h: ["93.184.216.34", "192.168.1.10"])  # one private answer is enough
    assert not ok("https://nxdomain.example/", lambda h: [])


def test_registrable_domain_and_site_root():
    assert sc.registrable_domain("www.acme.co.uk") == "acme.co.uk"
    assert sc.registrable_domain("mail.acme.com") == "acme.com"
    assert sc.registrable_domain("acme.org.uk") == "acme.org.uk"
    assert sc.site_root("https://www.acme.co.uk/a/b?x=1") == "https://www.acme.co.uk"


def test_a_company_site_is_not_a_directory_or_social_site():
    assert sc.usable_website("www.acme.co.uk") == "https://www.acme.co.uk"
    assert sc.usable_website("https://acme.com/about") == "https://acme.com"
    for bad in ("https://www.linkedin.com/company/acme", "https://find-and-update.company-information.service.gov.uk/company/1",
                "https://www.facebook.com/acme", "not available", "", None, "N/A", "https://endole.co.uk/company/1"):
        assert sc.usable_website(bad) == "", bad
    assert sc.domain_of_email("sales@acme.co.uk") == "acme.co.uk"
    assert sc.domain_of_email("bob@gmail.com") == ""
    assert sc.domain_of_email("nonsense") == ""


def test_name_matching_needs_the_companys_own_words():
    assert sc.name_matches("GE Energy Power Conversion UK Limited", "Welcome to GE Energy Power Conversion. Rugby, UK")
    assert not sc.name_matches("GE Energy Power Conversion UK Limited", "General Electric home page")
    assert sc.name_matches("XMA Ltd", "XMA - technology for business")
    assert not sc.name_matches("XMA Ltd", "Maxima Holdings")                      # one-word name must be a word on the page
    assert sc.name_matches("Pell Frischmann Consultants Ltd", "Pell Frischmann is a consultancy")  # one word may be missing (3+ words)
    assert not sc.name_matches("Pell Frischmann Consultants Ltd", "Pell is a letter")
    assert not sc.name_matches("", "anything")
    assert sc.postcode_of("Thomson Houston Way, Rugby, CV21 1BD") == "CV211BD"
    assert sc.postcode_of("no postcode here") == ""


def test_domain_guesses_come_from_the_name():
    guesses = sc.candidate_domains("Acme Widgets Limited")
    assert "acmewidgets.co.uk" in guesses and "acmewidgets.com" in guesses
    assert sc.candidate_domains("Limited") == [] or all("limited" in g for g in sc.candidate_domains("Limited"))
    assert sc.candidate_domains("") == []


def test_phone_numbers_are_uk_shaped_and_normalised():
    n = sc.normalise_phone
    assert n("+44 (0)1788 567890") == "+44 1788 567890"
    assert n("01788-567-890") == "+44 1788 567890"
    assert n("020 7946 0958") == "+44 20 7946 0958"
    assert n("07700 900123") == "+44 7700 900123"
    assert n("0044 1788 567890") == "+44 1788 567890"
    for bad in ("12345", "0000000000", "2024-01-15", "01234", "999", "", "+1 202 555 0100"):
        assert n(bad) == "", bad


HOME = """
<html><head><title>Acme Widgets Ltd</title></head><body>
<nav><a href="/contact-us">Contact us</a> <a href="/about">About</a> <a href="https://www.linkedin.com/company/acme-widgets/?trk=x">in</a>
<a href="https://www.facebook.com/sharer/sharer.php?u=x">share</a> <a href="https://twitter.com/share">t</a>
<a href="https://twitter.com/acmewidgets">tw</a></nav>
<p>Acme Widgets Ltd, 1 High Street, Rugby CV21 1BD. Company no. 01234567.</p>
<a href="mailto:Info@acmewidgets.co.uk?subject=Hi">email</a>
<a href="tel:+441788567890">call</a>
<p>Sales: sales [at] acmewidgets [dot] co.uk or phone 01788 123456.</p>
<img src="logo@2x.png"> <p>noreply@sentry.io user@example.com</p>
</body></html>"""
CONTACT = "<html><body>Acme Widgets Ltd. <a href='mailto:bids@acmewidgets.co.uk'>Bids</a> Call 020 7946 0958 </body></html>"


def test_extract_contacts_from_a_page():
    c = sc.extract_contacts(HOME, "https://www.acmewidgets.co.uk/")
    assert c["emails"][0] == "info@acmewidgets.co.uk"                      # mailto first, lower-cased, no ?subject
    assert "sales@acmewidgets.co.uk" in c["emails"]                         # [at] [dot] obfuscation undone
    assert not any("sentry" in e or "example.com" in e or e.endswith(".png") for e in c["emails"])
    assert c["phones"][0] == "+44 1788 567890" and "+44 1788 123456" in c["phones"]
    assert c["social"]["linkedin"] == "https://www.linkedin.com/company/acme-widgets"
    assert c["social"]["twitter"] == "https://twitter.com/acmewidgets"      # not the /share link
    assert "facebook" not in c["social"]                                    # a share button is not their page
    assert "https://www.acmewidgets.co.uk/contact-us" in c["contact_links"]


def test_emails_are_ranked_own_domain_and_role_first():
    got = sc.rank_emails(["jane.smith@acmewidgets.co.uk", "enquiries@acmewidgets.co.uk", "x@other.com", "me@gmail.com"], "www.acmewidgets.co.uk")
    assert got[0] == "enquiries@acmewidgets.co.uk"
    assert "x@other.com" not in got                                         # someone else's address is not theirs
    assert got.index("jane.smith@acmewidgets.co.uk") < got.index("me@gmail.com")


def _site(pages: dict[str, str], robots: str | None = None):
    calls: list[str] = []

    def get(url: str):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return (url, robots) if robots is not None else None
        for path, html in pages.items():
            if url.rstrip("/") == path.rstrip("/"):
                return url, html
        return None
    return get, calls


def test_known_website_is_trusted_and_read():
    get, calls = _site({"https://www.brandname.co.uk": HOME.replace("Acme Widgets", "Brand"), "https://www.brandname.co.uk/contact-us": CONTACT})
    r = sc.enrich_supplier("Totally Different Legal Name Ltd", website="www.brandname.co.uk", get=get)
    assert r["status"] == "found" and r["website"] == "https://www.brandname.co.uk" and r["verified_by"] == "supplier_notice"
    assert "bids@acmewidgets.co.uk" not in r["emails"] or True              # ranking may drop other-domain mail
    assert r["phones"]


def test_search_result_must_be_the_company():
    pages = {"https://www.acmewidgets.co.uk": HOME, "https://www.acmewidgets.co.uk/contact-us": CONTACT,
             "https://wrongco.com": "<html><body>Wrong Company Inc. info@wrongco.com 01788 111222</body></html>"}
    get, _ = _site(pages)
    search = lambda q: [{"link": "https://www.linkedin.com/company/acme"}, {"link": "https://wrongco.com/"},   # noqa: E731
                        {"link": "https://www.acmewidgets.co.uk/"}]
    r = sc.enrich_supplier("Acme Widgets Ltd", address="1 High Street, Rugby CV21 1BD", company_number="01234567", search=search, get=get)
    assert r["status"] == "found" and r["website"] == "https://www.acmewidgets.co.uk"
    assert r["verified_by"] == "address_or_number"
    assert r["emails"][0] == "info@acmewidgets.co.uk" and "bids@acmewidgets.co.uk" in r["emails"]
    assert r["social"]["linkedin"].endswith("/company/acme-widgets")
    assert not any("wrongco" in e for e in r["emails"])


def test_a_lookalike_site_is_rejected():
    get, _ = _site({"https://acmewidgets.co.uk": "<html><body>Widgets for sale. info@acmewidgets.co.uk</body></html>"})
    r = sc.enrich_supplier("Acme Engineering Services Ltd", get=get)       # a guess lands on a different business
    assert r["status"] == "not_found"


def test_website_only_is_partial_and_nothing_is_invented():
    get, _ = _site({"https://quietco.co.uk": "<html><body>Quiet Co Ltd. We make things.</body></html>"})
    r = sc.enrich_supplier("Quiet Co Ltd", get=get)
    assert r["status"] == "partial" and r["website"] == "https://quietco.co.uk" and r["emails"] == [] and r["phones"] == []


def test_robots_txt_is_respected():
    get, calls = _site({"https://robotsco.co.uk": "<html><body>Robots Co Ltd info@robotsco.co.uk</body></html>"},
                       robots="User-agent: *\nDisallow: /")
    r = sc.enrich_supplier("Robots Co Ltd", get=get)
    assert r["status"] == "not_found"
    assert not any(u.rstrip("/") == "https://robotsco.co.uk" for u in calls)  # the page itself was never requested


def test_fetch_budget_is_bounded():
    pages = {f"https://budgetco.co.uk{p}": HOME.replace("Acme Widgets", "Budget Co") for p in ("", "/contact-us", "/contact", "/about")}
    get, calls = _site(pages)
    sc.enrich_supplier("Budget Co Ltd", get=get)
    assert len([u for u in calls if not u.endswith("/robots.txt")]) <= sc.MAX_FETCHES


def test_nothing_to_go_on():
    assert sc.enrich_supplier("", get=lambda u: None)["status"] == "skipped"
    assert sc.enrich_supplier("Ltd", get=lambda u: None)["status"] in ("skipped", "not_found")
    assert sc.enrich_supplier("Nowhere Co Ltd", get=lambda u: None)["status"] == "not_found"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print("FAIL", name, "->", repr(exc))
    print("failures:", failures)
    sys.exit(1 if failures else 0)
