"""Run: python tests/test_charities.py   (plain asserts; the database half needs the db container).

Charity Commission register: the extract is parsed, registered charities are linked to suppliers by company
number or by a safe exact-name match, and a linked supplier is VCSE."""
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tender_app import charities, charity_import as ci  # noqa: E402
from tender_app.supplier_classifier import classify_supplier_real_data  # noqa: E402

HEADER = ["date_of_extract", "organisation_number", "registered_charity_number", "linked_charity_number", "charity_name",
          "charity_type", "charity_registration_status", "date_of_registration", "date_of_removal", "charity_reporting_status",
          "latest_acc_fin_period_start_date", "latest_acc_fin_period_end_date", "latest_income", "latest_expenditure",
          "charity_contact_address1", "charity_contact_address2", "charity_contact_address3", "charity_contact_address4",
          "charity_contact_address5", "charity_contact_postcode", "charity_contact_phone", "charity_contact_email",
          "charity_contact_web", "charity_company_registration_number", "charity_insolvent", "charity_in_administration",
          "charity_previously_excepted", "charity_is_cdf_or_cif", "charity_is_cio", "cio_is_dissolved",
          "date_cio_dissolution_notice", "charity_activities", "charity_gift_aid", "charity_has_land"]


def line(**kw):
    row = {h: "" for h in HEADER}
    row.update({"date_of_extract": "2026-10-22 00:00:00.0000000", "linked_charity_number": "0",
                "charity_registration_status": "Registered"}, **kw)
    return "\t".join(row[h] for h in HEADER)


ROWS = [
    line(registered_charity_number="990000001", charity_name="ZZ Hospice Care", charity_company_registration_number="1234567",
         latest_income="2500000.00", latest_acc_fin_period_end_date="2026-03-31 00:00:00.0000000",
         charity_contact_email="Info@ZZHospice.example", charity_contact_web="www.zzhospice.example", charity_is_cio="False",
         date_of_registration="1990-05-01 00:00:00.0000000", charity_activities="Palliative care at home"),
    line(registered_charity_number="990000002", charity_name='ZZ St John\'s "Home" Trust; Friends', charity_contact_postcode="LS1 1AA"),
    line(registered_charity_number="990000003", charity_name="ZZ Removed Charity", charity_registration_status="Removed"),
    line(registered_charity_number="990000004", linked_charity_number="2", charity_name="ZZ Subsidiary Charity"),
    line(registered_charity_number="990000005", charity_name="ZZ Shared Name"),
    line(registered_charity_number="990000006", charity_name="ZZ Shared Name"),
    line(registered_charity_number="990000007", charity_name="ZZ Unincorporated Aid"),
    line(registered_charity_number="990000008", charity_name=""),
]


def make_zip(directory):
    path = os.path.join(directory, "publicextract.charity.zip")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(ci.MEMBER, "﻿" + "\t".join(HEADER) + "\r\n" + "\r\n".join(ROWS) + "\r\n")
    return path


def test_company_numbers_are_normalised():
    assert ci.normalise_company_number("1234567") == "01234567"
    assert ci.normalise_company_number(" sc045439 ") == "SC045439"
    assert ci.normalise_company_number("") is None and ci.normalise_company_number("123456789") is None


def test_extract_is_parsed_without_quote_loss():
    with tempfile.TemporaryDirectory() as tmp:
        recs = {r["charity_number"]: r for r in ci.iter_charities(make_zip(tmp))}
    assert sorted(recs) == [990000001, 990000002, 990000005, 990000006, 990000007], "removed, linked and nameless rows are skipped"
    a = recs[990000001]
    assert a["company_number"] == "01234567" and a["income"] == 2500000.0 and a["year_end"] == "2026-03-31"
    assert a["email"] == "info@zzhospice.example" and a["registered_on"] == "1990-05-01" and a["name_key"] == "ZZHOSPICECARE"
    assert recs[990000002]["name"] == 'ZZ St John\'s "Home" Trust; Friends', "quotes are text, not quoting"
    assert recs[990000002]["income"] is None and recs[990000002]["postcode"] == "LS1 1AA"


def test_classifier_treats_a_registered_charity_as_vcse():
    assert classify_supplier_real_data("Zz Hospice Care", "01234567")[1] == "Non-VCSE"
    assert classify_supplier_real_data("Zz Hospice Care", "01234567", registered_charity=True)[1] == "VCSE"


def test_charity_public_shape():
    c = charities.public_charity((1083995, "METHODIST HOMES", 123.0, 100.0, "2026-03-31", "DE1 1AA", "01", "a@b.example",
                                  "www.mha.example", "Care homes", "company_number"))
    assert c["url"].endswith("/charity-details/1083995") and c["website"] == "https://www.mha.example" and c["income"] == 123.0


def test_database_linking():
    from tender_app.db import get_db_connection
    from tender_app.db_ext import create_charity_tables

    conn = get_db_connection()
    cur = conn.cursor()
    create_charity_tables(cur)
    conn.commit()
    nums = list(range(990000001, 990000009))
    cur.execute("DELETE FROM charity_register WHERE charity_number = ANY(%s)", (nums,))
    cur.execute("DELETE FROM suppliers WHERE company_number LIKE 'ZZCH%%' OR name LIKE 'ZZ %%'")
    ids = {}
    for key, name, cnum in [
        ("co", "ZZ Hospice Care Limited", "01234567"),     # linked by company number although the names differ
        ("name", "ZZ St John's Home Trust Friends", None),  # no Companies House number, exact name key, unique charity
        ("shared", "ZZ Shared Name", None),                 # two charities have this name: no link
        ("company-same-name", "ZZ Unincorporated Aid", "09876543"),  # a limited company that shares a charity's name
        ("other", "ZZ Plain Plumbing Ltd", "07654321"),
    ]:
        cur.execute("INSERT INTO suppliers (company_number, name, vcse_status) VALUES (%s, %s, 'Non-VCSE') RETURNING id",
                    (cnum or f"ZZCH{key}", name))
        ids[key] = cur.fetchone()[0]
    conn.commit()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            assert ci.load_register(conn, ci.iter_charities(make_zip(tmp)), log=lambda *_: None, prune=False) == 5
        # the supplier named "ZZ St John's Home Trust Friends" has the same name key as charity 990000002
        stats = ci.link_suppliers(conn, supplier_ids=list(ids.values()))
        assert stats["by_company_number"] == 1 and stats["by_name"] == 1 and stats["newly_marked_vcse"] == 2, stats
        got = charities.charities_for_suppliers(cur, ids.values())
        assert set(got) == {ids["co"], ids["name"]}, got
        assert got[ids["co"]]["charity_number"] == 990000001 and got[ids["co"]]["match"] == "company_number"
        assert got[ids["name"]]["match"] == "name"
        cur.execute("SELECT id, vcse_status FROM suppliers WHERE id = ANY(%s)", (list(ids.values()),))
        status = dict(cur.fetchall())
        assert status[ids["co"]] == "VCSE" and status[ids["name"]] == "VCSE"
        assert status[ids["shared"]] == "Non-VCSE" and status[ids["company-same-name"]] == "Non-VCSE" and status[ids["other"]] == "Non-VCSE"
        again = ci.link_suppliers(conn, supplier_ids=list(ids.values()))
        assert again["by_company_number"] == 1 and again["by_name"] == 1 and again["newly_marked_vcse"] == 0, "re-running changes nothing"
    finally:
        conn.rollback()
        cur.execute("DELETE FROM supplier_charity WHERE supplier_id = ANY(%s)", (list(ids.values()),))
        cur.execute("DELETE FROM suppliers WHERE id = ANY(%s)", (list(ids.values()),))
        cur.execute("DELETE FROM charity_register WHERE charity_number = ANY(%s)", (nums,))
        conn.commit()
        cur.close()
        conn.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
