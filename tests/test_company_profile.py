"""Run: python tests/test_company_profile.py   (plain asserts; HTTP is faked, no network)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tender_app import company_profile as cp  # noqa: E402
from tender_app import config  # noqa: E402


def fake(routes):
    def get_json(url, headers, data=None):
        for frag, val in routes.items():
            if frag in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"unexpected url {url}")
    return get_json


def test_no_keys():
    cp._CACHE.clear()
    config.COMPANIES_HOUSE_API_KEY = config.GOOGLE_PLACES_API_KEY = ""
    out = cp.build_profile({"id": 1, "name": "Acme Ltd", "company_number": None, "region": None, "website": None})
    assert out["companies_house"]["status"] == out["google"]["status"] == "not_connected"


def test_companies_house_by_number():
    cp._CACHE.clear()
    config.COMPANIES_HOUSE_API_KEY = "k"
    cp._get_json = fake({
        "/filing-history": {"items": [{"date": "2025-09-01", "description": "accounts", "category": "accounts"}]},
        "/persons-with-significant-control": {"items": [{"name": "Jane Doe", "kind": "individual-person-with-significant-control", "natures_of_control": ["ownership-of-shares-25-to-50-percent"], "notified_on": "2020-01-01"}]},
        "/company/01234567": {"company_name": "ACME LTD", "company_status": "active", "date_of_creation": "2010-03-01", "type": "ltd",
                              "accounts": {"last_accounts": {"made_up_to": "2025-03-31"}, "next_due": "2026-12-31"},
                              "confirmation_statement": {"last_made_up_to": "2025-06-01"}},
    })
    out = cp.companies_house("Acme Ltd", "01234567")
    assert out["status"] == "ok" and out["matched_by"] == "number"
    assert out["last_accounts"] == "2025-03-31" and out["owners"][0]["name"] == "Jane Doe"
    assert out["owners"][0]["control"] == ["ownership of shares 25 to 50 percent"]
    assert out["filings"][0]["description"] == "accounts"


def test_name_match_needs_confidence():
    cp._CACHE.clear()
    config.COMPANIES_HOUSE_API_KEY = "k"
    cp._get_json = fake({"/search/companies": {"items": [{"title": "TOTALLY DIFFERENT SERVICES LIMITED", "company_number": "09999999"}]}})
    assert cp.companies_house("Acme Boilers Ltd", None)["status"] == "not_found"


def test_errors_not_cached_and_soft():
    cp._CACHE.clear()
    config.GOOGLE_PLACES_API_KEY = "k"
    cp._get_json = fake({"places:searchText": OSError("boom")})
    assert cp.google("Acme Ltd", None)["status"] == "error"
    assert not cp._CACHE, "a failed call must be retried, not remembered"


def test_google():
    cp._CACHE.clear()
    config.GOOGLE_PLACES_API_KEY = "k"
    cp._get_json = fake({
        "places:searchText": {"places": [{"displayName": {"text": "Other Co"}, "rating": 1.0, "userRatingCount": 3},
                                         {"displayName": {"text": "Acme Ltd"}, "rating": 4.4, "userRatingCount": 87, "googleMapsUri": "https://maps"}]},
    })
    g = cp.google("Acme Ltd", None)
    assert g["status"] == "ok" and g["rating"] == 4.4 and g["count"] == 87


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("ok   ", name)
            except Exception:
                import traceback; fails += 1; print("FAIL ", name); traceback.print_exc()
    sys.exit(1 if fails else 0)
