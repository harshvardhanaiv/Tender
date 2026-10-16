"""Buyer contact lookup (tender_app/buyer_contacts.py). Plain asserts; run: python tests/test_buyer_contacts.py
The network is a fake: nothing here touches the internet."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tender_app import buyer_contacts as bc  # noqa: E402

BUYER = "Royal Borough of Greenwich"
NOTICE = "https://www.find-tender.service.gov.uk/Notice/012345-2026"


def test_only_notice_portal_links_are_fetched():
    assert bc.safe_notice_url(NOTICE) == NOTICE
    assert bc.safe_notice_url("https://www.contractsfinder.service.gov.uk/notice/abc") != ""
    for bad in ("http://localhost/x", "https://evil.example/Notice/1", "file:///etc/passwd", "", None,
                "https://www.find-tender.service.gov.uk.evil.example/Notice/1", "https://169.254.169.254/"):
        assert bc.safe_notice_url(bad) == "", bad


def test_notice_addresses_must_look_like_the_public_body():
    ok = lambda e, site="": bc.public_body_email(e, BUYER, site)  # noqa: E731
    assert ok("procurement@royalgreenwich.gov.uk")
    assert ok("tenders@greenwich-council.example", "https://www.greenwich-council.example")     # the buyer's own site domain
    assert not ok("info@cabinetoffice.gov.uk")                  # the portal's helpdesk is on every notice
    assert not ok("sales@winning-supplier.co.uk")               # an award notice also names the winner
    assert not ok("noreply@royalgreenwich.gov.uk")
    assert not ok("foi@royalgreenwich.gov.uk")
    assert not ok("not an email")
    assert ok("buying@greenwichhealth.nhs.uk")                  # public-body suffix


def test_procurement_inbox_beats_other_role_beats_named_person():
    cands = [{"email": "jane.smith@x.gov.uk", "source": "notice", "source_url": "u"},
             {"email": "info@x.gov.uk", "source": "website", "source_url": "u"},
             {"email": "Procurement@x.gov.uk", "source": "website", "source_url": "u"},
             {"email": "procurement@x.gov.uk", "source": "notice", "source_url": "u"}]
    out = bc.pick_best(cands)
    assert [c["email"] for c in out] == ["procurement@x.gov.uk", "info@x.gov.uk", "jane.smith@x.gov.uk"]
    assert out[0]["source"] == "website" or out[0]["source"] == "notice"
    assert out[0]["role"] and out[1]["role"] and not out[2]["role"]


def _site(pages):
    calls = []

    def get(url):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return None
        for path, html in pages.items():
            if url.rstrip("/") == path.rstrip("/"):
                return url, html
        return None
    return get, calls


NOTICE_HTML = """<html><body><h1>Contract notice</h1>
<p>Section I: Contracting authority Royal Borough of Greenwich. Contact: <a href="mailto:procurement@royalgreenwich.gov.uk">procurement@royalgreenwich.gov.uk</a>.
Helpdesk: <a href="mailto:info@cabinetoffice.gov.uk">info@cabinetoffice.gov.uk</a></p>
<p>Awarded to Smith Repairs Ltd, <a href="mailto:bids@smithrepairs.co.uk">bids@smithrepairs.co.uk</a></p></body></html>"""


def test_the_notice_gives_the_buyers_inbox_and_not_the_winner_or_helpdesk():
    get, calls = _site({NOTICE: NOTICE_HTML})
    r = bc.find_buyer_contact(BUYER, [NOTICE], get=get)
    assert r["status"] == "found" and r["email"] == "procurement@royalgreenwich.gov.uk" and r["source"] == "notice"
    emails = [c["email"] for c in r["candidates"]]
    assert "bids@smithrepairs.co.uk" not in emails and "info@cabinetoffice.gov.uk" not in emails
    assert len(calls) <= 2                                       # a good notice address means no website crawl


def test_falls_back_to_the_buyers_own_website():
    pages = {
        "https://www.royalgreenwich.gov.uk": '<html><body>Royal Borough of Greenwich. <a href="/procurement">Procurement</a></body></html>',
        "https://www.royalgreenwich.gov.uk/procurement": "<html><body>Royal Borough of Greenwich procurement team: "
                                                         '<a href="mailto:procurement.team@royalgreenwich.gov.uk">email us</a> or info@royalgreenwich.gov.uk</body></html>',
        "https://www.find-tender.service.gov.uk/Notice/000": "<html><body>Contracting authority. No contact given.</body></html>",
    }
    get, calls = _site(pages)
    search = lambda q: [{"link": "https://www.gov.uk/government"}, {"link": "https://www.royalgreenwich.gov.uk/info/procurement"}]  # noqa: E731
    r = bc.find_buyer_contact(BUYER, ["https://www.find-tender.service.gov.uk/Notice/000"], search=search, get=get)
    assert r["status"] == "found" and r["source"] == "website"
    assert r["email"] == "procurement.team@royalgreenwich.gov.uk"
    assert "info@royalgreenwich.gov.uk" in [c["email"] for c in r["candidates"]]
    assert not any("gov.uk/government" in u for u in calls if u.startswith("https://www.gov.uk"))   # GOV.UK itself is not the buyer


def test_nothing_published_means_not_found():
    get, _ = _site({})
    r = bc.find_buyer_contact("Some Obscure Body", [NOTICE], search=lambda q: [], get=get)
    assert r["status"] == "not_found" and r["email"] is None and r["candidates"] == []


def test_a_named_persons_address_is_returned_but_ranked_last():
    html = '<html><body>Contracting authority Royal Borough of Greenwich <a href="mailto:jane.smith@royalgreenwich.gov.uk">Jane</a></body></html>'
    get, _ = _site({NOTICE: html})
    r = bc.find_buyer_contact(BUYER, [NOTICE], search=lambda q: [], get=get)
    assert r["status"] == "found" and r["candidates"][0]["role"] is False


SWALE = "Swale Borough Council"
SWALE_NOTICE = "https://www.contractsfinder.service.gov.uk/Notice/be4b07fd-bf3c-4999-ba7a-1f2f1078e226"
# the shape of a real Contracts Finder award notice: a named contact, the buyer's website, a tendering portal and GOV.UK links
SWALE_NOTICE_HTML = """<html><body><h2>Contact</h2><p>Contact name Charlotte Knowles Address Swale Borough Council Swale House Sittingbourne
Email charlotteknowles@swale.gov.uk Website <a href="http://www.swale.gov.uk/">http://www.swale.gov.uk/</a></p>
<a href="http://procontract.due-north.com">portal</a> <a href="http://gov.uk">GOV.UK</a>
<a href="http://www.nationalarchives.gov.uk/doc/open-government-licence/version/3">OGL</a>
<a href="https://www.contractsfinder.service.gov.uk/Home/Contact">Contact us</a>
<a href="https://www.acme-groundcare.co.uk">Winning supplier</a></body></html>"""


def test_a_public_bodys_usual_website_is_guessed_from_its_name():
    d = bc.public_body_domains
    assert d("Swale Borough Council")[0] == "www.swale.gov.uk"
    assert d("London Borough of Camden")[0] == "www.camden.gov.uk"
    assert "www.royalgreenwich.gov.uk" in d("Royal Borough of Greenwich")
    assert "www.cheshirewestandchester.gov.uk" in d("Cheshire West & Chester Council")
    assert "www.aberdeencity.gov.uk" in d("Aberdeen City Council")
    assert d("University of Leeds") == ["www.leeds.ac.uk"]
    assert d("Kent Police") == ["www.kent.police.uk"]
    assert d("Kent Community Health NHS Foundation Trust")[0].endswith(".nhs.uk")
    assert d("Acme Groundcare Ltd") == [] and d("") == [] and d(None) == []
    assert len(d("Swale Borough Council", limit=2)) == 2


def test_the_notices_website_is_the_buyers_and_not_a_portal_or_a_supplier():
    sites = bc.notice_websites(SWALE_NOTICE_HTML, SWALE_NOTICE, SWALE)
    assert sites == ["http://www.swale.gov.uk"], sites
    assert bc.notice_websites("", SWALE_NOTICE, SWALE) == []
    html = '<a href="https://www.swale.gov.uk/x">a</a><a href="mailto:a@b.gov.uk">m</a><a href="javascript:alert(1)">j</a><a href="/relative">r</a>'
    assert bc.notice_websites(html, SWALE_NOTICE, SWALE) == ["https://www.swale.gov.uk"]


def test_a_named_contact_on_the_notice_leads_to_the_councils_own_procurement_inbox():
    pages = {
        SWALE_NOTICE: SWALE_NOTICE_HTML,
        "http://www.swale.gov.uk": '<html><body>Swale Borough Council <a href="/business/procurement">Procurement</a></body></html>',
        "http://www.swale.gov.uk/business/procurement": "<html><body>Swale Borough Council procurement: procurement@swale.gov.uk</body></html>",
    }
    get, calls = _site(pages)
    r = bc.find_buyer_contact(SWALE, [SWALE_NOTICE], search=lambda q: [], get=get)
    assert r["status"] == "found" and r["email"] == "procurement@swale.gov.uk" and r["source"] == "website", r
    assert r["candidates"][0]["role"] is True
    assert "charlotteknowles@swale.gov.uk" in [c["email"] for c in r["candidates"]], "the named contact stays as a lower candidate"
    assert not any("acme-groundcare" in u or "due-north" in u for u in calls), "a supplier or a tendering portal is never read"


def test_with_no_website_on_the_notice_the_councils_usual_domain_is_tried_before_any_search():
    pages = {
        "https://www.swale.gov.uk": '<html><body>Swale Borough Council <a href="/procurement">Procurement</a></body></html>',
        "https://www.swale.gov.uk/procurement": "<html><body>Swale Borough Council. Email procurement@swale.gov.uk</body></html>",
    }
    get, calls = _site(pages)
    searched = []
    r = bc.find_buyer_contact(SWALE, [], search=lambda q: searched.append(q) or [], get=get)
    assert r["status"] == "found" and r["email"] == "procurement@swale.gov.uk", r
    assert searched == [], "the guessed council domain worked, so no web search was needed"


def test_a_guessed_domain_that_is_not_the_buyer_is_not_believed():
    pages = {"https://www.swale.gov.uk": "<html><body>Welcome to a quite different organisation. contact: hello@swale.gov.uk</body></html>"}
    get, _ = _site(pages)
    r = bc.find_buyer_contact(SWALE, [], search=lambda q: [], get=get)
    assert r["status"] == "not_found"


def test_a_site_that_blocks_us_leaves_the_named_contact_to_offer_and_nothing_guessed():
    get, _ = _site({SWALE_NOTICE: SWALE_NOTICE_HTML})  # the council's own site answers nothing
    r = bc.find_buyer_contact(SWALE, [SWALE_NOTICE], search=lambda q: [], get=get)
    assert r["status"] == "found" and r["email"] == "charlotteknowles@swale.gov.uk"
    assert r["candidates"][0]["role"] is False and r["source"] == "notice"
    assert not any(c["email"].startswith(("procurement@", "info@")) for c in r["candidates"])


def test_the_notice_portals_are_read_as_a_browser_and_council_sites_as_the_honest_bot():
    seen = []

    def fake_fetch(url, *, timeout=4.0, user_agent=None):
        seen.append((url, user_agent))
        if url == SWALE_NOTICE:
            return url, SWALE_NOTICE_HTML
        return None

    real = bc.sc.fetch
    bc.sc.fetch = fake_fetch
    try:
        bc.find_buyer_contact(SWALE, [SWALE_NOTICE], search=lambda q: [])
    finally:
        bc.sc.fetch = real
    notice_calls = [ua for u, ua in seen if u.startswith("https://www.contractsfinder.service.gov.uk/Notice/")]
    assert notice_calls and all(ua == bc.sc.BROWSER_USER_AGENT for ua in notice_calls)
    council_calls = [ua for u, ua in seen if "swale.gov.uk" in u]
    assert council_calls and all(ua is None for ua in council_calls), "a council's own site is never fetched with a browser's identity"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                failures += 1
                print("FAIL", name, "->", repr(exc))
    print("failures:", failures)
    sys.exit(1 if failures else 0)
