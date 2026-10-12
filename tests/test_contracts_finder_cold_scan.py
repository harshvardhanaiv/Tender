"""A cold Contracts Finder search streams matches while the feed downloads in parallel windows
(it used to block until the whole feed was down, so the portal's time limit cut it off)."""
import time
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pytest

from etenders_scraper.sources import contracts_finder as cf

PAGES_PER_WINDOW = 4
PER_PAGE = 60  # 3 windows x 4 pages x 60 = 720 notices, over the 200 minimum for a cacheable scan


class FakeResp:
    status_code = 200
    headers: dict = {}

    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def fake_fetch(session, params):
    window = params.get("publishedFrom", "") + "|" + params.get("publishedTo", "")
    page = int(params.get("page", 0))
    releases = [
        {
            "ocid": f"ocds-{abs(hash(window)) % 10**6}-{page}-{i}",
            "tender": {"title": ("Software platform %d" % i) if i % 10 == 0 else ("Boiler works %d" % i), "description": ""},
            "buyer": {"name": "Council"},
            "date": "2026-01-01T00:00:00Z",
        }
        for i in range(PER_PAGE)
    ]
    links = {}
    if page + 1 < PAGES_PER_WINDOW:
        q = {**params, "page": page + 1, "cursor": "c"}
        links = {"next": "https://x/y?" + "&".join(f"{k}={v}" for k, v in q.items())}
    return FakeResp({"releases": releases, "links": links}), {"releases": releases, "links": links}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(cf, "_fetch_release_page", fake_fetch)
    monkeypatch.setattr(cf, "_PAGE_DELAY_SECS", 0.0)
    monkeypatch.setattr(cf, "_feed_cache", {"key": None, "at": 0.0, "entries": []})
    monkeypatch.setattr(cf, "_refresh_state", {"failures": 0, "next_allowed": 0.0})
    monkeypatch.setattr(cf, "_snapshot_store", None)


def test_cold_search_streams_and_fills_the_cache():
    seen_progress = []
    rows = cf.search_contracts_finder(
        keyword="software", source_id="contracts_finder", source_label="Contracts Finder (UK)",
        on_progress=lambda r: seen_progress.append(len(r)),
    )
    assert rows and all("Software" in r["title"] for r in rows)
    assert seen_progress and seen_progress == sorted(seen_progress)
    # the finished scan was cached, so the next search needs no download
    assert cf._feed_cache["entries"]
    t0 = time.time()
    again = cf.search_contracts_finder(keyword="software", source_id="contracts_finder", source_label="Contracts Finder (UK)")
    assert len(again) == len(rows) and time.time() - t0 < 1


def test_scan_uses_parallel_windows():
    windows = []
    orig = cf._iter_release_pages

    def spy(session, state, window=None):
        windows.append(window)
        return orig(session, state, window)

    cf._iter_release_pages = spy
    try:
        cf.search_contracts_finder(keyword="software", source_id="contracts_finder", source_label="Contracts Finder (UK)")
    finally:
        cf._iter_release_pages = orig
    assert len(windows) == cf._SCAN_WORKERS and all(w for w in windows)
