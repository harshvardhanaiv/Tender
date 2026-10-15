from tender_app.search_terms import parse_operators, split_terms


def test_or_becomes_comma():
    assert parse_operators("software OR cloud") == ("software, cloud", [])
    assert split_terms("software OR cloud") == ["software", "cloud"]


def test_not_and_minus_are_excludes():
    assert parse_operators("software NOT cloud") == ("software", ["cloud"])
    assert parse_operators('software -"legacy kit"') == ("software", ["legacy kit"])


def test_hyphen_inside_word_and_plain_or_untouched():
    assert parse_operators("e-tender") == ("e-tender", [])
    assert parse_operators("a or b") == ("a or b", [])


def test_quoted_phrases_are_searched_without_their_quote_marks():
    assert parse_operators('"fire safety"') == ("fire safety", [])
    assert parse_operators('\u201cfire safety\u201d') == ("fire safety", [])
    assert parse_operators('"fire safety" -"fire alarm"') == ("fire safety", ["fire alarm"])
    assert parse_operators('"fire safety" OR "gas servicing" NOT legacy') == ("fire safety, gas servicing", ["legacy"])
    assert split_terms('"fire safety"') == ["fire safety"]
    assert split_terms('"fire safety" OR roofing') == ["fire safety", "roofing"]
    assert parse_operators('""') == ("", [])


def test_major_towns_resolve_without_the_place_index():
    from tender_app.town_coords import TOWN_COORDS
    for town in ("hull", "kingston upon hull", "bradford", "stoke-on-trent", "stoke on trent", "leicester", "york"):
        assert town in TOWN_COORDS, town
    lat, lon, name = TOWN_COORDS["hull"]
    assert name == "Kingston upon Hull" and 53.7 < lat < 53.8 and -0.4 < lon < -0.3
