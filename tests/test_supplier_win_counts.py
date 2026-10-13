"""Run: python tests/test_supplier_win_counts.py   (plain asserts, no database, no network)

D21: a supplier read 33 wins on the list card and 31 in the profile. The card counts every non-framework award
(total_awards - framework_appointments_count); the profile tile counted only those inside the single-award value ceiling.
supplier_win_counts() is the one definition both now use, with the ceiling-limited number kept for the average.
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etenders_scraper.awards import SINGLE_AWARD_STATS_CEILING_GBP as CEILING  # noqa: E402
from tender_app.blueprints.suppliers_bp import supplier_win_counts  # noqa: E402


def award(value, framework=False):
    return {"contract_value": value, "is_framework": framework}


def test_every_non_framework_award_is_a_win_even_above_the_value_ceiling():
    awards = [award(100_000)] * 30 + [award(CEILING * 2)] * 2 + [award(5_000_000, framework=True)] * 4
    c = supplier_win_counts(awards)
    assert c["total_awards"] == 36
    assert c["framework_appointments"] == 4
    assert c["direct_awards"] == 32, c          # 30 + the 2 above the ceiling
    assert c["valued_direct_awards"] == 30, c   # what the average is divided by


def test_it_matches_the_list_card_definition_total_minus_frameworks():
    awards = [award(10)] * 7 + [award(CEILING + 1)] * 3 + [award(1, framework=True)] * 2
    c = supplier_win_counts(awards)
    assert c["direct_awards"] == c["total_awards"] - c["framework_appointments"]


def test_no_awards():
    assert supplier_win_counts([]) == {"total_awards": 0, "direct_awards": 0, "framework_appointments": 0, "valued_direct_awards": 0}


def test_missing_values_count_as_trusted_zero_value_wins():
    c = supplier_win_counts([award(None), award(0), award(250)])
    assert c["direct_awards"] == 3 and c["valued_direct_awards"] == 3


def test_the_profile_and_summary_routes_use_it():
    src = (ROOT / "tender_app" / "blueprints" / "suppliers_bp.py").read_text(encoding="utf-8")
    assert src.count("supplier_win_counts(awards)") >= 2
    assert '"direct_awards": win_counts["direct_awards"]' in src


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
