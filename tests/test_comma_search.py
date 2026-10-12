"""Comma-separated keyword searches: "software, dashboard" = software OR dashboard."""
from tender_app.search_terms import split_terms
from etenders_scraper.sources import registry as reg


def test_split_terms():
    assert split_terms("software, dashboard") == ["software", "dashboard"]
    assert split_terms("  Software ,, software ; Cyber Security ") == ["software", "cyber security"]
    assert split_terms("supplier: acme, beta") == ["acme", "beta"]
    assert split_terms("software development") == ["software development"]
    assert split_terms("all") == [] and split_terms("") == [] and split_terms(None) == []
    assert len(split_terms("a1,a2,a3,a4,a5,a6,a7")) == 5


def test_merge_ranks_rows_found_by_more_terms_first_and_dedupes():
    a = {"resource_id": "1", "title": "Only software"}
    b = {"resource_id": "2", "title": "Both"}
    b2 = {"resource_id": "2", "title": "Both"}
    c = {"resource_id": "3", "title": "Only dashboard"}
    out = reg.merge_term_rows([("software", [a, b]), ("dashboard", [b2, c])])
    assert [r["resource_id"] for r in out] == ["2", "1", "3"]
    assert out[0]["matched_terms"] == ["software", "dashboard"]
    assert out[1]["matched_terms"] == ["software"]


def test_fan_out_only_for_several_terms_and_survives_one_failure():
    assert reg.fan_out_terms("software", lambda t: t) is None

    def one(term):
        if term == "bad":
            raise RuntimeError("down")
        return term.upper()

    res = reg.fan_out_terms("good, bad", one)
    assert res[0] == ("good", "GOOD") and isinstance(res[1][1], RuntimeError)
