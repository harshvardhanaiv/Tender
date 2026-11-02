"""Re-test 3 Growth Studio: GS1 (trade and size), GS3 (email copy), GS4 (shared inbox), GS5 (nobody to approach).

Run: python tests/test_growth_fit_retest3.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tender_app import growth_studio as gs  # noqa: E402
from tender_app import market_radar as mr  # noqa: E402
from tender_app import buyer_contacts as bc  # noqa: E402
from test_growth_studio_core import contract, planning, HOUSING, TODAY  # noqa: E402

PLUMBER = gs.profile_keywords("Plumbing and heating contractor: gas boilers, central heating, pipework.")


def test_a_plumber_is_not_a_fit_for_electrical_testing():
    """GS1: a plumbing SME scored 94% for electrical testing (EICR)."""
    plumbing = gs.score_renewal(contract(title="Boiler servicing and heating repairs"), HOUSING, PLUMBER)
    eicr = gs.score_renewal(contract(title="Electrical Installation Condition Reports (EICR) testing"), HOUSING, PLUMBER)
    assert plumbing["score"] > 60, plumbing
    assert eicr["score"] <= gs.OVERSIZE_CAP and eicr["band"] == "low", eicr
    assert any("electrical" in p["note"] for p in eicr["parts"]), eicr["parts"]


def test_a_contract_far_above_the_firms_size_is_capped_as_a_subcontract_lead():
    """GS1: a plumbing SME got 100% for a £48m mechanical and electrical maintenance contract."""
    big = contract(title="Planned and reactive heating and plumbing maintenance", value=48_000_000)
    alone = gs.score_renewal(big, HOUSING, PLUMBER)
    sized = gs.score_renewal(big, HOUSING, PLUMBER, ceiling=gs.bid_ceiling({"turnover": "£1.2m"}))
    assert alone["score"] > gs.OVERSIZE_CAP, "with no size on the profile nothing is capped"
    assert sized["score"] == gs.OVERSIZE_CAP and sized["parts"][-1]["key"] == "limit" and "subcontract" in sized["parts"][-1]["note"].lower()
    small = gs.score_renewal(contract(value=250_000), HOUSING, PLUMBER, ceiling=12e6)
    assert small["score"] > gs.OVERSIZE_CAP, "a contract the firm can take is not capped"


def test_the_bid_ceiling_comes_from_turnover_then_size_band():
    assert gs.bid_ceiling({"turnover": "£1.2m"}) == 12_000_000
    assert gs.bid_ceiling({"enterprise_type": "small"}) == 5_000_000
    assert gs.bid_ceiling({}) is None and gs.bid_ceiling(None) is None


def test_a_scheme_with_no_developer_is_research_not_a_high_fit():
    """GS5: a scheme with nobody to add to a campaign scored 70%."""
    today = TODAY
    r = planning(applicant_company=None, lead_score=100, n_dwellings=500)
    fit = gs.score_development(r, "preset", today)
    assert fit["score"] <= gs.NO_DEVELOPER_CAP and fit["band"] == "low", fit
    named = gs.score_development(planning(applicant_company="Chestnut Homes Ltd", lead_score=100, n_dwellings=500), "preset", today)
    assert named["score"] > fit["score"]


def test_a_councils_own_scheme_is_not_a_developer_to_pitch_to():
    """GS2: a council was listed as a development target."""
    out = gs.development_signals([planning(applicant_company="Bromsgrove District Council")], "preset", TODAY)
    assert out and out[0]["target"]["targetable"] is False and "council" in out[0]["target"]["reason"].lower(), out[0]["target"]


def test_notice_titles_lose_their_award_tail_and_reference_and_summaries_are_cut_at_a_word():
    """GS3: '... Work Services - AWARD' and 'Milton Key' (a word cut in two)."""
    assert gs.clean_notice_title("Mechanical and Electrical Work Services - AWARD") == "Mechanical and Electrical Work Services"
    assert gs.clean_notice_title("PPCS/2024/12 - Planned Maintenance - Award Notice") == "Planned Maintenance"
    assert gs.clean_notice_title("Boiler servicing") == "Boiler servicing"
    cut = gs.cut_at_word("We install and maintain heating systems across Milton Keynes and Bedfordshire for housing providers", 60)
    assert cut.endswith("…") and not cut[:-1].endswith("Mil") and len(cut) <= 61, cut
    assert gs.cut_at_word("short", 60) == "short"


def test_a_team_mailbox_of_initials_is_a_role_inbox_not_a_named_person():
    """GS4: ppcs@parliament.uk was labelled a named person."""
    assert bc.pick_best([{"email": "ppcs@parliament.uk", "source": "notice"}])[0]["role"] is True
    assert bc.pick_best([{"email": "jane.smith@parliament.uk", "source": "notice"}])[0]["role"] is False
    assert bc.pick_best([{"email": "tim@parliament.uk", "source": "notice"}])[0]["role"] is False


def test_a_development_never_ranks_with_a_named_contract_and_a_council_scheme_ranks_low():
    """Re-test 4 GS2: developments at 95-97%, councils listed as targets."""
    private = gs.score_development(planning(applicant_company="Chestnut Homes Ltd", lead_score=100, n_dwellings=500), "preset", TODAY)
    assert private["score"] <= gs.DEVELOPMENT_CAP, private
    council = gs.score_development(planning(applicant_company="Bromsgrove District Council", lead_score=100, n_dwellings=500), "preset", TODAY, public_body=True)
    assert council["score"] <= gs.COUNCIL_SCHEME_CAP, council


def test_a_contract_that_names_none_of_the_firms_trades_earns_no_category_or_profile_points():
    """Re-test 4 GS1: a plumber at 88% for 'Radiology Ventilation Replacement Works'."""
    cat = gs.mr.resolve_category("housing-repairs-gas", None, None)
    kw = frozenset({"plumbing", "heating", "works", "maintenance"})
    other = {"title": "Radiology Ventilation Replacement Works", "cpv": "45331200", "cpv_description": "Ventilation installation work",
             "days_left": 120, "value": 400000, "suppliers": ["X"], "term_years": 1}
    own = dict(other, title="Boiler replacement works", cpv="45331100")
    low = gs.score_renewal(other, cat, kw)
    high = gs.score_renewal(own, cat, kw)
    assert low["score"] < 60 and high["score"] > low["score"], (low["score"], high["score"])


def test_a_long_first_sentence_of_the_profile_ends_at_a_word_with_a_full_stop():
    """Re-test 4 GS3: the description was cut mid-word ('Milton Key')."""
    text = ("Priority Plumbing Services provides domestic and commercial plumbing, heating and gas servicing to housing associations, "
            "councils and private landlords across Leighton Buzzard, Milton Keynes, Bedfordshire and Buckinghamshire and Hertfordshire, Oxfordshire and Northamptonshire without any break whatsoever in service")
    out = gs.sender_fields("Priority", text, None, "Boilers")["summary"]
    assert out.endswith(".") and len(out) <= 262 and not out.endswith("Key."), out


def test_the_subject_is_written_from_the_firms_trades():
    """Re-test 4 GS3: the subject was the category name."""
    plumber = gs.sender_fields("Priority Plumbing", "We install boilers and heating systems and plumbing for housing providers.", None, "Housing repairs & gas servicing")
    assert plumber["services"] == "Plumbing and heating"
    dev = gs.default_message("development", plumber)
    assert dev["subject"] == "Plumbing and heating for {{scheme_summary}}", dev["subject"]
    assert gs.default_message("renewal", plumber)["subject"] == "Plumbing and heating support ahead of your contract renewal"
    nameless = gs.sender_fields("Acme", "We make things.", None, "Boilers")
    assert gs.default_message("renewal", nameless)["subject"] == "{{category}} support ahead of your contract renewal", "no trade named: the merge field stays"


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok   ", name)
    print(f"{n} passed")
