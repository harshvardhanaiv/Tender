"""Re-test 6: reserved-matters applications do not inherit the whole scheme's homes (PN4); a financial adviser filed under systems consultancy
is not an IT renewal (Ofgem); the Beta label is on Growth Studio.

Run: python tests/test_retest6_planning_buyers.py"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etenders_scraper.planning.scoring import explain_lead_score, scheme_dwellings  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def test_a_reserved_matters_element_covers_none_of_the_schemes_homes():
    school = {"n_dwellings": 2750, "description": "Reserved Matters Application for School Substation"}
    assert scheme_dwellings(school) == 0
    homes = {"n_dwellings": 2750, "description": "Reserved matters for 120 dwellings and associated landscaping"}
    assert scheme_dwellings(homes) == 2750
    outline = {"n_dwellings": 2750, "description": "Outline application for a new community with a school"}
    assert scheme_dwellings(outline) == 2750, "only reserved matters are changed"


def test_the_lead_score_no_longer_counts_the_inherited_homes():
    base = {"app_size": "Large", "app_state": "Permitted", "app_type": "Full", "decided_date": date(2026, 10, 1)}
    inherited = explain_lead_score({**base, "n_dwellings": 2750, "description": "Reserved Matters Application for School Substation"}, date(2026, 11, 3))
    own = explain_lead_score({**base, "n_dwellings": None, "description": "Reserved Matters Application for School Substation"}, date(2026, 11, 3))
    assert inherited["dwellings"] == 0 == own["dwellings"], inherited


def test_the_renewal_list_leaves_out_financial_and_legal_advisers_filed_as_it():
    sql = read("tender_app", "stats.py")
    i = sql.index("renewals_by_division = t.r")
    assert "cpv_code LIKE '7222%%' AND tender_title ~*" in sql[i:i + 1800] and "financial|legal" in sql[i:i + 1800]
    assert "'722%%'" not in sql[i:i + 1800], "software for legal or audit work (CPV 7226) must stay an IT renewal"


def test_growth_studio_is_labelled_beta():
    assert 'id="growthStudioBeta"' in read("web", "index.html")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  ", name)
