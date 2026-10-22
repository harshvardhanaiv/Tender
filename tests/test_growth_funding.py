"""Run: python tests/test_growth_funding.py   (plain asserts; the last test needs the db container).

The Growth Studio "Recently funded" signal: Innovate UK grants (grant_awards) matched to the supplier's
category by the words in the grant, one signal per company, public bodies left out, off unless asked for."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tender_app import growth_studio as gs  # noqa: E402
from tender_app import market_radar as mr  # noqa: E402

TODAY = date(2026, 10, 22)
CAT = mr.resolve_category("housing-repairs-gas", None, None)


def grant(**kw):
    row = {
        "supplier_name": "ACME HEATING LTD", "name_key": "ACMEHEATINGLTD", "title": "Smart boiler controls",
        "abstract": "A low-carbon boiler and heating maintenance platform.", "funder": "Innovate UK",
        "programme": "Investment Accelerator", "value_gbp": 249900, "start_date": "2026-09-01", "region": "South East",
        "website": None, "has_awards": False, "url": "https://gtr.ukri.org/projects?ref=1", "id": 1,
    }
    row.update(kw)
    return row


def test_off_unless_asked_for():
    assert "funding" in gs.SIGNAL_TYPES and "funding" not in gs.DEFAULT_SIGNAL_TYPES
    assert gs.filters_from({"preset": "housing-repairs-gas"}).types == gs.DEFAULT_SIGNAL_TYPES
    assert gs.filters_from({"preset": "housing-repairs-gas", "types": "funding"}).types == ("funding",)
    assert gs.filters_from({"preset": "housing-repairs-gas"}).public_bodies is False
    assert gs.filters_from({"preset": "housing-repairs-gas", "public_bodies": "1"}).public_bodies is True


def test_matching_uses_the_words_in_the_grant():
    hits, services = gs.funding_match(CAT, "A low-carbon boiler and heating maintenance platform")
    assert hits >= 2 and services == [CAT.label]
    assert gs.funding_match(CAT, "Marine sensors for fish farms") == (0, [])
    custom = mr.resolve_category(None, None, "swimming pool")
    assert gs.funding_match(custom, "Resin for concrete swimming pools")[0] == 2
    assert gs.funding_match(custom, "A pool of swimming data")[0] == 2, "word order does not matter, as in a custom search"
    crm = mr.resolve_category("crm-case-management", None, None)
    assert gs.funding_match(crm, "a UK/EU critical raw material (CRM) refinery")[0] == 0, "a lone acronym is not evidence"
    assert gs.funding_match(crm, "a CRM and case management system")[0] == 2
    cpv_only = mr.resolve_category(None, "5072", None)
    assert gs.has_funding_words(cpv_only) is False and gs.has_funding_words(CAT) is True


def test_one_signal_per_company_with_its_facts():
    rows = [grant(), grant(id=2, title="Heating retrofit pilot", value_gbp=100000, start_date="2026-07-01", programme="Smart")]
    signals, left_out = gs.funding_signals(rows, CAT, TODAY)
    assert left_out == 0 and len(signals) == 1
    s = signals[0]
    assert s["type"] == "funding" and s["key"] == "funding:ACMEHEATINGLTD" and s["buyer"] == "Acme Heating Ltd"
    assert "2 grants" in s["headline"] and "£349,900" in s["headline"] and "1 September 2026" in s["headline"]
    assert s["funding"]["grants"] == 2 and s["funding"]["value"] == 349900 and s["funding"]["start"] == "2026-09-01"
    assert s["target"]["targetable"] is True and s["target"]["name"] == "Acme Heating Ltd"
    assert s["days"] == 51 and s["links"][0]["url"].startswith("https://gtr.ukri.org/")
    assert sum(p["points"] for p in s["fit_parts"]) == s["fit"] and {p["key"] for p in s["fit_parts"]} == {
        "recency", "size", "category", "reachable"}


def test_single_grant_headline_names_the_project():
    s = gs.funding_signals([grant()], CAT, TODAY)[0][0]
    assert s["headline"].startswith("Acme Heating Ltd won £249,900 from Innovate UK for “Smart boiler controls”")


def test_score_parts():
    fresh = gs.score_funding(date(2026, 10, 10), 1_200_000, 3, True, True, TODAY)
    assert fresh["score"] == 30 + 25 + 30 + 15
    stale = gs.score_funding(date(2026, 1, 1), None, 1, False, False, TODAY)
    assert stale["score"] == 6 + 5 + 18 + 0
    assert [p["points"] for p in gs.score_funding(date(2026, 9, 1), 60_000, 2, False, True, TODAY)["parts"]] == [26, 14, 24, 5]


def test_public_bodies_are_left_out_unless_asked():
    rows = [grant(supplier_name="UNIVERSITY OF LEEDS", name_key="UNIVERSITYOFLEEDS"),
            grant(supplier_name="NHS NORFOLK & SUFFOLK ICB", name_key="NHSNORFOLKSUFFOLKICB"), grant(id=3)]
    signals, left_out = gs.funding_signals(rows, CAT, TODAY)
    assert [s["buyer"] for s in signals] == ["Acme Heating Ltd"] and left_out == 2
    signals, left_out = gs.funding_signals(rows, CAT, TODAY, public_bodies=True)
    assert len(signals) == 3 and left_out == 0
    assert gs.is_public_body("Imperial College London") and not gs.is_public_body("Collegiate Software Ltd")


def test_rows_outside_the_window_or_without_words_are_skipped():
    rows = [grant(start_date="2026-12-01"), grant(id=2, title="Marine sensors", abstract="Fish farms"),
            grant(id=3, supplier_name="", name_key="")]
    assert gs.funding_signals(rows, CAT, TODAY) == ([], 0)


def test_message_and_merge_fields():
    sender = {"company_name": "Boiler Co", "summary": "We service boilers.", "category": "Heating"}
    msg = gs.default_message("funding", sender)
    assert "{{grant_title}}" in msg["subject"] and "{{funder}}" in msg["subject"] and gs.OPT_OUT_LINE in msg["body"]
    names = {m["name"] for m in gs.MERGE_FIELDS if "funding" in m["types"]}
    assert {"grant_title", "grant_value", "grant_start", "funder", "buyer_name", "category"} <= names
    snap = {"type": "funding", "date": "2026-09-01",
            "funding": {"title": "Smart boiler controls", "value": 249900, "start": "2026-09-01", "funder": "Innovate UK"}}
    values = gs.fields_for_target({"buyer_name": "Acme Heating Ltd", "signal_type": "funding", "snapshot": snap}, sender)
    assert values["buyer_contact"] == "team" and values["grant_value"] == "£249,900"
    assert values["grant_start"] == "1 September 2026" and values["funder"] == "Innovate UK"
    text, empty, unknown = gs.render_template(msg["body"], values)
    assert not unknown and "Hello team," in text and "Acme Heating Ltd was awarded Innovate UK funding" in text
    system, user = gs.ai_prompt(sender, ["funding"], "Heating", [], "We service boilers.")
    assert "companies that have just won grant funding" in system and "company's team" in system
    assert "{{grant_title}}" in system and "{{contract_title}}" not in system


def test_database_signals():
    from tender_app.db import get_db_connection
    from tender_app.db_ext import create_grant_awards

    conn = get_db_connection()
    cur = conn.cursor()
    create_grant_awards(cur)
    conn.commit()
    ids = ("ZZ-GF-1", "ZZ-GF-2")
    cur.execute("DELETE FROM grant_awards WHERE gtr_project_id = ANY(%s)", (list(ids),))
    cur.execute(
        """INSERT INTO grant_awards (gtr_project_id, supplier_name, name_key, funder, title, abstract, value_gbp, start_date, url, region)
           VALUES ('ZZ-GF-1', 'ZZ FUNDED BOILERS LTD', 'ZZFUNDEDBOILERSLTD', 'Innovate UK', 'Boiler servicing robots',
                   'Heating and boiler maintenance', 180000, to_char(CURRENT_DATE - 20, 'YYYY-MM-DD'), 'https://gtr.ukri.org/x', 'North East'),
                  ('ZZ-GF-2', 'ZZ OLD BOILERS LTD', 'ZZOLDBOILERSLTD', 'Innovate UK', 'Boiler servicing', 'Heating boiler',
                   10000, to_char(CURRENT_DATE - 600, 'YYYY-MM-DD'), 'https://gtr.ukri.org/y', 'North East')"""
    )
    conn.commit()
    try:
        f = gs.filters_from({"preset": "housing-repairs-gas", "types": "funding", "where": "ZZ "})
        built = gs.build_signals(get_db_connection, f, frozenset(), "zz_funding_probe")
        names = [s["buyer"] for s in built["signals"]]
        assert names == ["Zz Funded Boilers Ltd"], names  # the 600-day-old grant is outside the window
        assert built["signals"][0]["authority"] == "North East · Funded by Innovate UK"
        other = gs.build_signals(get_db_connection, gs.filters_from({"preset": "housing-repairs-gas", "where": "ZZ "}),
                                 frozenset(), "zz_funding_probe")
        assert not any(s["type"] == "funding" for s in other["signals"]), "funding is not in the default types"
        cpv_only = gs.build_signals(get_db_connection, gs.filters_from({"cpv": "5072", "types": "funding"}), frozenset(), "zz_funding_probe")
        assert cpv_only["signals"] == [] and "CPV" in cpv_only["notes"]["funding"]
    finally:
        cur.execute("DELETE FROM grant_awards WHERE gtr_project_id = ANY(%s)", (list(ids),))
        conn.commit()
        cur.close()
        conn.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
