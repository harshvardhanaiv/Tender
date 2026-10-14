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
