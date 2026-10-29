"""Who may plan a market engagement in a public body's name (Buyer workspace QA W1).

Run: python -m pytest tests/test_org_verify.py
"""
from tender_app import org_verify as ov


def test_a_public_sector_work_email_is_verified():
    for email in ("jo@camden.gov.uk", "a.b@nhs.net", "x@leedsth.nhs.uk", "p@met.police.uk", "r@exeter.ac.uk", "c@mod.gov.uk", "s@gov.scot".replace("@gov.scot", "@glasgow.gov.scot")):
        assert ov.status(email)["verified"], email
        assert ov.status(email)["via"] == "work email"


def test_other_addresses_are_not_verified_and_say_what_to_do():
    for email in ("harsh@gmail.com", "x@aivhub.com", "me@notgov.uk", "a@camden.gov.uk.evil.com", "", None):
        v = ov.status(email)
        assert not v["verified"] and "work email" in v["message"], email


def test_an_administrator_or_an_approved_account_is_verified():
    assert ov.status("x@gmail.com", is_admin=True)["via"] == "admin"
    assert ov.status("x@gmail.com", approved=True)["via"] == "approved"


def test_it_can_be_switched_off_for_a_site_that_does_not_want_it(monkeypatch):
    monkeypatch.setenv("ENFORCE_ORG_VERIFICATION", "0")
    assert ov.status("x@gmail.com")["verified"]
