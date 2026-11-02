"""Re-test 4 R9: a Sell2Wales Contract Results run reads every page it is asked for, not just the first (84 awards held).
Run: python tests/test_sell2wales_pages.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etenders_scraper.sources import bravo_search as bs  # noqa: E402


def page(n, total, ids):
    cards = "".join(f'<div class="search-result"><a class="notice-title" href="/Notice/NoticeDisplay.aspx?ID=ZZ{i}">Contract award {i} title</a></div>' for i in ids)
    return f'<html><body><form id="aspnetForm"><input name="__VIEWSTATE" id="__VIEWSTATE" value="vs{n}"/>Page {n} of {total}{cards}</form></body></html>'


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, pages, fail_on=None):
        self.pages, self.fail_on, self.sent = pages, fail_on, []

    def post(self, url, data=None, timeout=None):
        self.sent.append(data)
        n = int(data["__EVENTARGUMENT"])
        if n == self.fail_on:
            raise RuntimeError("portal down")
        return FakeResponse(self.pages[n])


def test_pages_after_the_first_are_read_in_turn_with_the_notice_type_kept():
    pages = {2: page(2, 4, [11, 12]), 3: page(3, 4, [21, 22]), 4: page(4, 4, [31])}
    s = FakeSession(pages)
    rows = bs._wales_contract_result_pages(s, "https://x/Search", page(1, 4, [1, 2]), "https://x", 30, 4, pause=0)
    assert [r["detail_url"][-4:] for r in rows] == ["ZZ11", "ZZ12", "ZZ21", "ZZ22", "ZZ31"], rows
    assert [d["__EVENTARGUMENT"] for d in s.sent] == ["2", "3", "4"]
    assert all(d[bs._WALES_CONTRACT_RESULTS_CHECKBOX] == "on" and d["__EVENTTARGET"] == bs._WALES_PAGER for d in s.sent)
    assert s.sent[1]["__VIEWSTATE"] == "vs2", "each request carries the form state of the page before it"


def test_only_the_pages_asked_for_are_read_and_a_failing_page_ends_the_read_with_what_was_found():
    pages = {2: page(2, 9, [11]), 3: page(3, 9, [21])}
    assert len(bs._wales_contract_result_pages(FakeSession(pages), "u", page(1, 9, [1]), "x", 30, 3, pause=0)) == 2
    assert bs._wales_contract_result_pages(FakeSession(pages), "u", page(1, 9, [1]), "x", 30, 1, pause=0) == []
    got = bs._wales_contract_result_pages(FakeSession(pages, fail_on=3), "u", page(1, 9, [1]), "x", 30, 5, pause=0)
    assert len(got) == 1


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok   ", name)
    print(f"{n} passed")
