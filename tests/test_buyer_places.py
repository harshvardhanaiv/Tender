"""Re-test 4 B6: a buyer with no stored location is placed from the place named in its own name (the centre of that planning area).
Run: python tests/test_buyer_places.py (the database half uses the dev database, read only)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_app import buyer_places as bp  # noqa: E402

INDEX = {"reading": (51.4556, -0.9741, "Reading"), "west berkshire": (51.43, -1.2, "West Berkshire"), "bath north east somerset": (51.38, -2.36, "Bath and North East Somerset")}


def test_the_longest_place_named_wins_and_other_words_are_ignored():
    assert bp.locate_by_place("University of Reading", INDEX)[2] == "Reading"
    assert bp.locate_by_place("Royal West Berkshire Hospital NHS Trust", INDEX)[2] == "West Berkshire"
    assert bp.locate_by_place("Reading Borough Council", INDEX)[2] == "Reading"
    assert bp.locate_by_place("Bath North East Somerset Council", INDEX)[2] == "Bath and North East Somerset"
    assert bp.locate_by_place("Ministry of Defence", INDEX) is None
    assert bp.locate_by_place("", INDEX) is None and bp.locate_by_place("Reading", {}) is None


def test_place_keys_drop_the_words_that_decorate_a_council_name():
    assert bp.place_key("Reading Borough Council") == "reading"
    assert bp.place_key("London Borough of Camden") == "camden"


def test_the_index_is_read_from_the_planning_areas_and_places_a_known_buyer():
    from tender_app.db import get_db_connection
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        index = bp.place_index(cur)
        assert len(index) > 100, len(index)
        assert not set(index) & bp.TOO_COMMON, "a word like 'north' places nothing"
        lat, lon, area = bp.locate_by_place("University of Reading", index)
        assert area == "Reading" and 51.3 < lat < 51.6 and -1.2 < lon < -0.8, (lat, lon)
    finally:
        conn.close()


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok   ", name)
    print(f"{n} passed")
