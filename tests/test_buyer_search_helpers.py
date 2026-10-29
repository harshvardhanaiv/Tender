"""Buyer search helpers: day counts, name relevance, acronyms (Buyer intelligence QA B4, B5).

Run: python -m pytest tests/test_buyer_search_helpers.py
"""
from datetime import timedelta

from tender_app.blueprints import buyers_bp as bb


def test_days_are_whole_uk_days_not_a_clock_difference():
    today = bb._uk_today()
    assert bb._days_until(today.isoformat()) == 0
    assert bb._days_until((today + timedelta(days=1)).isoformat()) == 1
    assert bb._days_until((today + timedelta(days=3)).isoformat()) == 3, "'renews in 2 days' for a date 3 days away was the bug"
    assert bb._days_until((today - timedelta(days=1)).isoformat()) == -1
    assert bb._days_until("not a date") is None and bb._days_until(None) is None


def test_the_named_buyer_comes_before_one_that_merely_contains_the_word():
    names = ["Leeds Community Healthcare NHS Trust", "Leeds City Council", "Leeds", "The University Of Leeds", "Greater Leeds Partnership Ltd"]
    ranked = sorted(names, key=lambda n: bb._name_relevance(n, "leeds"))
    assert ranked[0] == "Leeds" and bb._name_relevance("Leeds City Council", "leeds") == 1
    assert bb._name_relevance("Transport For London", "transport for london") == 0
    assert bb._name_relevance("The Transport For London", "transport for london") == 0
    assert bb._name_relevance("Wetherby modern school", "mod") == 2, "'mod' inside 'modern' is not the word"


def test_acronyms_stand_for_the_long_name():
    assert bb.BUYER_ACRONYMS["tfl"] == "transport for london" and bb.BUYER_ACRONYMS["mod"] == "ministry of defence"
    assert "hmrc" in bb.BUYER_ACRONYMS and "dwp" in bb.BUYER_ACRONYMS


def test_a_cpv_division_label_is_used_when_a_notice_left_out_its_description():
    assert bb._bf_label("72212000") == "It services"
    assert bb._bf_label("") == "Not available" and bb._bf_label(None) == "Not available"
