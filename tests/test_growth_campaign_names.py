"""Run: python tests/test_growth_campaign_names.py   (plain asserts, no database, no network)

Campaign auto-names are "<category>: <signal type>, <date>", so two campaigns made on the same day for the same category
shared one name. unique_campaign_name() adds " (2)", " (3)"... when the name is already taken (case and spacing ignored).
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tender_app import growth_studio as gs  # noqa: E402

BASE = "Housing repairs and gas: renewals, 13 Oct 2026"


def test_a_free_name_is_kept():
    assert gs.unique_campaign_name(BASE, []) == BASE
    assert gs.unique_campaign_name(BASE, ["Something else"]) == BASE


def test_a_taken_name_gets_a_numeric_suffix_and_the_next_free_number():
    assert gs.unique_campaign_name(BASE, [BASE]) == BASE + " (2)"
    assert gs.unique_campaign_name(BASE, [BASE, BASE + " (2)"]) == BASE + " (3)"
    assert gs.unique_campaign_name(BASE, [BASE, BASE + " (3)"]) == BASE + " (2)"  # fills the first gap


def test_case_and_spacing_do_not_make_a_name_different():
    assert gs.unique_campaign_name(BASE, ["  housing REPAIRS and gas:   renewals, 13 Oct 2026 "]) == BASE + " (2)"


def test_the_suffix_stays_inside_the_120_character_limit():
    long_base = "x" * 118
    out = gs.unique_campaign_name(long_base, [long_base])
    assert len(out) <= 120 and out.endswith(" (2)"), out
    assert gs.validate_campaign({"name": out})["name"] == out  # still a valid campaign name


def test_none_and_blank_existing_names_are_ignored():
    assert gs.unique_campaign_name(BASE, [None, "", "  "]) == BASE


def test_the_create_route_uses_it_for_automatic_names_only():
    src = (ROOT / "tender_app" / "blueprints" / "growth_bp.py").read_text(encoding="utf-8")
    assert "gs.unique_campaign_name(" in src
    assert 'name = body.get("name")' in src and "if not name:" in src, "a name the user typed must be kept as typed"


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
