"""Funding search, award ranges, award clean-up and duplicates (no database needed)."""
from tender_app import funding


def rec(**kw):
    base = {"label": "a", "name": "Grant", "funder": "Defra", "summary": "", "eligibility": "", "min_award": None, "max_award": None,
            "total_award": None, "open_date": None, "close_date": "2026-12-01"}
    base.update(kw)
    return base


def hit(q, **kw):
    return funding.text_matches(funding._query_groups(q), rec(**kw))


def test_ai_is_a_whole_word():
    assert hit("AI", name="Frontier AI: SME Champions")
    assert hit("AI", summary="Uses artificial intelligence to help")
    assert not hit("AI", summary="Funding available for airports and trains")


def test_stems_hyphens_and_synonyms():
    assert hit("innovate", summary="A new innovation programme") and hit("innovation", summary="Innovate UK delivers it")
    assert hit("net-zero", summary="Support for net zero projects") and hit("net zero", summary="a net-zero scheme")
    assert hit("DSIT", funder="Department for Science, Innovation and Technology")
    assert hit("SME", summary="Open to small and medium sized enterprises")
    assert hit("r&d", summary="Research and development tax help")
    assert not hit("software", summary="Boiler upgrades")


def test_every_query_word_must_match():
    assert hit("heat pump", summary="Heat pump installers") and not hit("heat battery", summary="Heat pump installers")
    assert hit("", summary="anything")


def test_award_ranges_overlap():
    adopt = rec(min_award=2499.0, max_award=2500.0)
    assert not funding.overlaps(adopt, 10000, 100000)
    assert funding.overlaps(adopt, 0, 10000)
    assert funding.overlaps(rec(min_award=50000.0, max_award=400000.0), 10000, 100000)
    assert funding.overlaps(rec(min_award=50000.0, max_award=400000.0), 1000000, None) is False
    assert funding.overlaps(rec(), 1000000, None), "an unstated award is never ruled out"
    assert funding.overlaps(rec(min_award=1e6), 100000, 1000000) and funding.overlaps(rec(min_award=1e6, max_award=None), 5e6, None)


def test_award_clean_up():
    row = ("x", "Name", "F", "", 1, 300000000, 300000000, [], [], None, "2026-12-01", "2026-12-01T23:59", False, None, None, None)
    from datetime import date
    out = funding._public(row, date(2026, 10, 28))
    assert out["min_award"] is None and out["max_award"] is None and out["total_award"] == 300000000
    keep = funding._public(("x", "N", "F", "", 1000, 50000, 2000000, [], [], None, "2026-12-01", None, False, None, None, None), date(2026, 10, 28))
    assert keep["min_award"] == 1000 and keep["max_award"] == 50000


def test_duplicate_slugs_keep_the_newest():
    a = rec(label="youth-jobs-grant-1", name="Youth Jobs Grant")
    b = rec(label="youth-jobs-grant-2", name="Youth  Jobs grant")
    c = rec(label="other", name="Other grant")
    assert [r["label"] for r in funding.dedupe([a, b, c])] == ["youth-jobs-grant-2", "other"]
