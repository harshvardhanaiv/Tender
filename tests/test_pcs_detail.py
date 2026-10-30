"""Re-test 3 TN1: opening a Public Contracts Scotland notice must not replace its title with "View Notice" or its
description with the site's cookie banner, and a label with no value must not take the next label as its value (T1).

Run: python tests/test_pcs_detail.py
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etenders_scraper.sources import bravo_search as bs  # noqa: E402

PAGE = """<html><head><title>View Notice</title></head><body>
<div>We use cookies to make the site simpler. Accept Cookie Settings</div><a>Skip to main content</a><a>Contact Us</a>
<h1>View Notice</h1>
<div>Published by:</div><div>Perth and Kinross Council</div>
<div>Deadline date:</div><div>Notice Type:</div><div>Contract Award Notice</div>
</body></html>"""

PAGE_WITH_ABSTRACT = PAGE.replace("<h1>View Notice</h1>", "<h1>Oracle Business Intelligence (BI) Software Licencing</h1>") \
    .replace("<div>Published by:</div>", "<div>Abstract</div><div>Licences and support for Oracle BI.</div><div>Published by:</div>")


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def _detail(page):
    with mock.patch.object(bs.requests, "get", return_value=_Resp(page)):
        return bs.fetch_bravo_details("pcs", "123", site_root="https://www.publiccontractsscotland.gov.uk")


def test_a_generic_page_title_is_not_used_as_the_title():
    d = _detail(PAGE)
    assert d["title"] == "", d["title"]


def test_the_description_is_never_the_top_of_the_page():
    d = _detail(PAGE)
    assert "cookie" not in d["description"].lower() and "skip to main content" not in d["description"].lower(), d["description"]
    assert "cookie" in d["full_text"].lower(), "the full text is still kept for the analysis"


def test_a_real_title_and_abstract_are_kept():
    d = _detail(PAGE_WITH_ABSTRACT)
    assert d["title"].startswith("Oracle Business Intelligence"), d["title"]
    assert "Licences and support" in d["description"], d["description"]


def test_a_deadline_label_with_no_value_is_empty_not_the_next_label():
    assert _detail(PAGE)["submission_deadline"] == ""
    row = {}
    bs._apply_label_text(row, "Deadline date:\nNotice Type:\nContract Award Notice")
    assert "submission_deadline" not in row, row
    row = {}
    bs._apply_label_text(row, "Deadline date:\n26/11/2026\nNotice Type:\nContract Award Notice")
    assert row.get("submission_deadline") == "26/11/2026" and row.get("notice_type") == "Contract Award Notice", row


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok   ", name)
    print(f"{n} passed")
