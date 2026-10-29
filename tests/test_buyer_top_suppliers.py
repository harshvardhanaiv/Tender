"""A buyer's "top suppliers": one row per company, and other public bodies are not winners (Buyer intelligence QA).

Leeds City Council listed two NHS trusts as incumbent suppliers and "Mears Limited" and "MEARS LIMITED" as two companies.
Run: python -m pytest tests/test_buyer_top_suppliers.py
"""
from tender_app.blueprints.buyers_bp import _tidy_top_suppliers


def _row(name, wins, value, last="2026-01-01", fw=0.0):
    return {"supplier_name": name, "contracts_won": wins, "total_value": value, "framework_total_ceiling": fw,
            "framework_appointments": 0, "last_award_date": last}


def test_other_public_bodies_are_not_listed_as_winners():
    rows = [_row("Harrogate and District NHS Foundation Trust", 1, 139_000_000), _row("Leeds Community Healthcare NHS Trust", 6, 87_000_000),
            _row("Balfour Beatty Civil Engineering Ltd", 3, 125_000_000)]
    assert [r["supplier_name"] for r in _tidy_top_suppliers(rows)] == ["Balfour Beatty Civil Engineering Ltd"]


def test_spellings_of_one_company_are_added_together():
    rows = [_row("Mears Limited", 6, 48_900_000, "2026-03-01"), _row("MEARS LIMITED", 5, 44_700_000, "2026-05-01"), _row("Kedleston Schools", 4, 39_000_000)]
    out = _tidy_top_suppliers(rows)
    mears = out[0]
    assert mears["supplier_name"] == "Mears Limited" and mears["contracts_won"] == 11
    assert round(mears["total_value"]) == 93_600_000 and mears["last_award_date"] == "2026-05-01"
    assert [r["supplier_name"] for r in out] == ["Mears Limited", "Kedleston Schools"]


def test_valued_companies_come_before_those_without_a_value_and_only_ten_are_kept():
    rows = [_row(f"Company {i} Ltd", 1, 1000 - i) for i in range(15)] + [_row("No Value Ltd", 9, 0)]
    out = _tidy_top_suppliers(rows)
    assert len(out) == 10 and out[0]["supplier_name"] == "Company 0 Ltd"
