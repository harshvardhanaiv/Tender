"""Run: python tests/test_planning_consultation_cap.py   (plain asserts, no database, no network)

D23 (remaining part): a consultation / screening request is capped at a lead score of 25, but the detail breakdown
still listed the uncapped parts with no cap line, so the lines did not add up to the score shown. The cap now appears
as a negative "cap" line (like the other caps) and the page labels it.
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tender_app.blueprints.planning_bp import _mark_consultation  # noqa: E402

PARTS = {"size": 30, "stage": 25, "status": 20, "recency": 10, "type": 5}  # sums to 90


def row(**kw):
    base = {"description": "Request for an EIA screening opinion for a solar farm", "lead_score": 90,
            "app_state": "Permitted", "lead_score_breakdown": dict(PARTS)}
    base.update(kw)
    return base


def test_a_consultation_is_capped_and_the_breakdown_adds_up_to_the_capped_score():
    r = _mark_consultation(row())
    b = r["lead_score_breakdown"]
    assert r["lead_score"] == 25 and r["is_consultation"] is True
    assert b["cap"] == -65.0, b
    assert sum(v for k, v in b.items()) == r["lead_score"], b
    assert r["low_value_reason"] == "consultation"


def test_an_existing_cap_is_added_to_not_replaced():
    parts = dict(PARTS, cap=-10)  # an earlier cap already took 10 points: the score was 80
    r = _mark_consultation(row(lead_score=80, lead_score_breakdown=parts))
    assert r["lead_score"] == 25
    assert sum(r["lead_score_breakdown"].values()) == 25, r["lead_score_breakdown"]


def test_a_low_score_is_left_alone():
    r = _mark_consultation(row(lead_score=20, lead_score_breakdown=dict(PARTS, cap=-70)))
    assert r["lead_score"] == 20 and r["lead_score_breakdown"]["cap"] == -70
    assert "low_value_reason" not in r


def test_a_normal_application_is_untouched():
    r = _mark_consultation(row(description="Erection of 40 dwellings"))
    assert r["lead_score"] == 90 and "is_consultation" not in r and "cap" not in r["lead_score_breakdown"]


def test_the_page_labels_the_consultation_cap():
    js = (ROOT / "web" / "planning.js").read_text(encoding="utf-8")
    assert '"consultation" ? "Consultation / screening cap"' in js


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"{len(tests) - failed} passed, 0 skipped, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
