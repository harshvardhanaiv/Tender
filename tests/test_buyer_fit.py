"""Run: python tests/test_buyer_fit.py   (plain asserts, no database)"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tender_app import buyer_fit as bf  # noqa: E402

KW = dict(tier=1, cpv_match=True, dist_km=12.0, days_until_renewal=90, next_renewal="2027-01-20", repeat_display="20.0%", total_contracts=40)


def test_weights_add_to_one_and_factors_cover_every_component():
    assert abs(sum(bf.WEIGHTS.values()) - 1.0) < 1e-9
    f = bf.factors(cpv_score=100, prox_score=94, renew_score=100, openness_score=80, **KW)
    assert [x["key"] for x in f] == ["sector", "proximity", "renewal", "openness"]
    assert [x["weight"] for x in f] == [35, 25, 25, 15]
    assert all(x["band"] == "strong" and x["note"] for x in f)
    assert "12 km" in f[1]["note"] and "90 days" in f[2]["note"] and "20.0%" in f[3]["note"]


def test_weak_and_unknown_inputs_say_so_plainly():
    f = bf.factors(cpv_score=40, prox_score=60, renew_score=30, openness_score=60, tier=1, cpv_match=False, dist_km=None,
                   days_until_renewal=None, next_renewal="", repeat_display="Not enough data yet", total_contracts=2)
    by = {x["key"]: x for x in f}
    assert by["sector"]["band"] == "weak" and "No overlap" in by["sector"]["note"]
    assert "not known" in by["proximity"]["note"]
    assert "No renewal" in by["renewal"]["note"] and by["renewal"]["band"] == "weak"
    assert "Too few" in by["openness"]["note"]
    n = bf.factors(cpv_score=70, prox_score=60, renew_score=30, openness_score=60, **{**KW, "tier": 0})[0]["note"]
    assert "company profile" in n


def test_engagement_is_bounded_ordered_and_honest():
    hi = bf.engagement(cpv_score=100, prox_score=95, renew_score=100, openness_score=90, total_contracts=60)
    lo = bf.engagement(cpv_score=40, prox_score=20, renew_score=30, openness_score=10, total_contracts=2)
    assert hi["level"] == "high" and lo["level"] == "low" and hi["percent"] > lo["percent"]
    assert 5 <= lo["percent"] <= hi["percent"] <= 95
    assert "does not predict" in hi["note"]
    mid = bf.engagement(cpv_score=70, prox_score=60, renew_score=70, openness_score=60, total_contracts=20)
    assert mid["level"] == "medium", mid
    # more contracts let can only raise the estimate
    assert bf.engagement(cpv_score=70, prox_score=60, renew_score=70, openness_score=60, total_contracts=50)["percent"] >= mid["percent"]


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                failures += 1; print("FAIL", name, "->", repr(exc))
    print("failures:", failures)
    sys.exit(1 if failures else 0)
