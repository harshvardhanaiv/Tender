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
