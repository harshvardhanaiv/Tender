"""How long a search waits for each portal.

Public Contracts Scotland answers slowly: 2.5 to 3 minutes for a broad word on live ("construction", "balfour beatty"), so the 45 s / 150 s every portal
gets marked it "timed out" or kept the search waiting. It now has 3 minutes for its first page and 10 minutes to finish listing; the other portals
keep their limits.
Run: python tests/test_portal_time_budgets.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from etenders_scraper.sources.progressive_search import first_page_budget, full_list_budget


def test_public_contracts_scotland_gets_longer():
    assert first_page_budget("pcs") >= 180
    assert full_list_budget("pcs") >= 600
    print("ok    test_public_contracts_scotland_gets_longer")


def test_other_portals_keep_their_limits():
    assert first_page_budget("find_tender") == 45 and full_list_budget("find_tender") == 150
    assert first_page_budget("sell2wales") == 45 and full_list_budget("sell2wales") == 150
    assert first_page_budget("procontract") == 75 and first_page_budget("etenders_ni") == 100 and first_page_budget("contracts_finder") == 60
    print("ok    test_other_portals_keep_their_limits")


def test_listing_never_gets_less_time_than_the_first_page():
    for sid in ("pcs", "find_tender", "sell2wales", "procontract", "etenders_ni", "contracts_finder", "etenders_ie", "gca_agreements", "unknown_portal"):
        assert full_list_budget(sid) >= first_page_budget(sid), sid
    print("ok    test_listing_never_gets_less_time_than_the_first_page")


if __name__ == "__main__":
    test_public_contracts_scotland_gets_longer()
    test_other_portals_keep_their_limits()
    test_listing_never_gets_less_time_than_the_first_page()
    print("3 passed, 0 skipped, 0 failed")
