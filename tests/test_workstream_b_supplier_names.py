"""Test for Workstream B: Supplier-name validation and description text filtering.

Verifies:
1. Free-text description paragraphs are recognized by looks_like_description().
2. Legitimate company names (including those ending in Ltd., B.V., S.A.) pass looks_like_description().
3. prepare_row sets listable = False for description supplier names.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

from etenders_scraper.awards import looks_like_description, ingest_award_record
from tender_app.market_radar import prepare_row
from tender_app.db import get_db_connection
from tender_app.stats import _LISTABLE_SQL

def test_looks_like_description():
    # Negative cases (real company names including dotted abbreviations)
    assert not looks_like_description("The Borough Council Of Gateshead")
    assert not looks_like_description("Altered Images Ltd.")
    assert not looks_like_description("Elsevier B.V.")
    assert not looks_like_description("H. Malone & Sons Ltd")
    assert not looks_like_description("A.T. SERVICES LIMITED")
    assert not looks_like_description("Motor Oil (Hellas) Corinth Refineries S.A.")
    assert not looks_like_description("K.B. Refrigeration Wolverhampton LTD")
    assert not looks_like_description("John Wiley and Sons, Inc.")
    assert not looks_like_description("Penceat Medical Ltd.")
    assert not looks_like_description("Brown & Sons Limited.")
    assert not looks_like_description("S.L.U.")
    assert not looks_like_description("C.I.C.")
    assert not looks_like_description("N.I.")
    assert not looks_like_description("W.D.M.")

    # Positive cases (description text erroneously parsed as supplier name)
    assert looks_like_description("capabilities and the very latest technologies for the provision and implementation of an integrated solution")
    assert looks_like_description("framework per product line to a maximum of 3 Framework Participants for the Provision and Implementation of an Integrated Business Solution")
    assert looks_like_description("Full list of awards published: https://www.oxfordshire.gov.uk/council/about-your-council/council-tax-and-finance/spending-over-500")
    assert looks_like_description("Will provide comprehensive services under contract specification. Details attached.")
    
    print("ok    test_looks_like_description")

def test_sql_and_python_description_rules_agree():
    """Uses the REAL SQL constants. Read-only: it never modifies the database."""
    from tender_app.blueprints.suppliers_bp import SUPPLIER_NAME_NOT_PLACEHOLDER_SQL
    from etenders_scraper.awards import PLACEHOLDER_SUPPLIER_NAMES, ATTACHMENT_PLACEHOLDER_PATTERN

    assert ATTACHMENT_PLACEHOLDER_PATTERN in _LISTABLE_SQL and ATTACHMENT_PLACEHOLDER_PATTERN in SUPPLIER_NAME_NOT_PLACEHOLDER_SQL,         "the attachment pattern must be carried verbatim by both SQL filters"
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        extra = ["S.L.U.", "C.I.C.", "N.I.", "W.D.M.", "H. Malone & Sons Ltd", "Elsevier B.V.", "Brown & Sons Limited.",
                 "Secure & Confidential Documents Limited", "P&W Confidential Business Services Ltd", "Baxter Confidential", "CSO Confidential",
                 "Various Artists Ltd", "Multiple Choice Training Limited",
                 "Please see attachment", "PLEASE SEE ATTACHMENT FOR SUPPLIER DETAILS", "Please refer to attached list of successful supplier's",
                 "capabilities and the very latest technologies for the provision and implementation of an integrated solution"]
        cur.execute("SELECT DISTINCT name FROM suppliers WHERE name IS NOT NULL AND TRIM(name) != ''")
        names = [r[0] for r in cur.fetchall()] + extra + sorted(PLACEHOLDER_SUPPLIER_NAMES)
        cur.execute(f"SELECT s.name, {_LISTABLE_SQL}, {SUPPLIER_NAME_NOT_PLACEHOLDER_SQL} FROM (SELECT unnest(%s::text[]) AS name) s", (names,))
        rows = cur.fetchall()
        assert len(rows) == len(names)
        stats_vs_bp = [r[0] for r in rows if r[1] != r[2]]
        assert not stats_vs_bp, f"stats and suppliers_bp SQL disagree on {len(stats_vs_bp)} names: {stats_vs_bp[:5]}"
        listable_but_description = [r[0] for r in rows if r[1] and looks_like_description(r[0])]
        assert not listable_but_description, f"{len(listable_but_description)} names are listable in SQL but are descriptions in Python: {listable_but_description[:5]}"
        python_missing = [r[0] for r in rows if not r[1] and not looks_like_description(r[0])
                          and ("attach" in r[0].lower())]
        assert not python_missing, f"SQL hides attachment text that ingest would accept: {python_missing[:5]}"
        still_listable = [r[0] for r in rows if r[0].lower() in PLACEHOLDER_SUPPLIER_NAMES and r[1]]
        assert not still_listable, f"ingest placeholders still listable in SQL: {still_listable}"
    finally:
        conn.close()

    print("ok    test_sql_and_python_description_rules_agree")

def test_ingest_rejects_attachment_placeholders_without_a_supplier():
    conn = get_db_connection()
    cur = conn.cursor()
    url = "https://www.example.com/notice_test_attach_ingest_456"
    try:
        rec = {"supplier_name": "Please see attachment for supplier details", "authority_name": "Test Council Authority",
               "tender_title": "T", "contract_value": 50000.0, "notice_url": url, "date_signed": "2026-02-01", "source_portal": "Contracts Finder"}
        cur.execute("SELECT COUNT(*) FROM suppliers")
        before = cur.fetchone()[0]
        assert ingest_award_record(rec, conn) is True
        conn.commit()
        cur.execute("SELECT supplier_name, supplier_id FROM contract_awards WHERE notice_url = %s", (url,))
        assert cur.fetchone() == (None, None)
        cur.execute("SELECT COUNT(*) FROM suppliers")
        assert cur.fetchone()[0] == before, "a supplier row was created for attachment text"
        rec2 = dict(rec, supplier_name="Confidential", notice_url=url + "b")
        assert ingest_award_record(rec2, conn) is False, "exact placeholders are dropped, as before"
        print("ok    test_ingest_rejects_attachment_placeholders_without_a_supplier")
    finally:
        cur.execute("DELETE FROM contract_awards WHERE notice_url IN (%s, %s)", (url, url + "b"))
        conn.commit()
        conn.close()

def test_prepare_row_hides_description_suppliers():
    row_good = {"id": 1, "authority_name": "Test Council", "supplier_name": "Acme Ltd", "contract_value": 50000}
    row_bad = {"id": 2, "authority_name": "Test Council", "supplier_name": "capabilities and the very latest technologies for the provision of IT solutions", "contract_value": 50000}
    
    prep_good = prepare_row(row_good)
    prep_bad = prepare_row(row_bad)

    assert prep_good["listable"] is True
    assert prep_bad["listable"] is False
    print("ok    test_prepare_row_hides_description_suppliers")

def test_ingest_stores_description_without_supplier_link():
    conn = get_db_connection()
    cur = conn.cursor()
    desc_name = "capabilities and the very latest technologies for the provision and implementation of an integrated solution"
    test_url = "https://www.example.com/notice_test_desc_ingest_123"
    try:
        rec = {
            "supplier_name": desc_name,
            "authority_name": "Test Council Authority",
            "tender_title": "Test Title",
            "contract_value": 50000.0,
            "notice_url": test_url,
            "date_signed": "2026-02-01",
            "source_portal": "Contracts Finder"
        }
        res = ingest_award_record(rec, conn)
        assert res is True, "Expected ingest_award_record to return True for description award stored with no supplier link"
        conn.commit()

        cur.execute("SELECT supplier_name, supplier_id FROM contract_awards WHERE notice_url = %s", (test_url,))
        row = cur.fetchone()
        assert row is not None, "Expected contract_awards row to be present"
        stored_name, stored_id = row[0], row[1]
        assert stored_name is None, f"Expected supplier_name IS NULL, got '{stored_name}'"
        assert stored_id is None, f"Expected supplier_id IS NULL, got '{stored_id}'"
        
        cur.execute("SELECT COUNT(*) FROM suppliers WHERE name = %s", (desc_name,))
        assert cur.fetchone()[0] == 0, "Expected no supplier row created for description text"
        print("ok    test_ingest_stores_description_without_supplier_link")
    finally:
        cur.execute("DELETE FROM contract_awards WHERE notice_url = %s", (test_url,))
        cur.execute("DELETE FROM suppliers WHERE name = %s", (desc_name,))
        conn.commit()
        conn.close()

def test_category_b_names_are_listable():
    category_b_names = [
        "Will Rudd Davidson Ltd",
        "Will Build Contracting Limited",
        "To the Moon and Back Foster Care Ltd",
        "FOR THE LOVE OF THE NORTH LTD",
        "Will Taggart",
        "to the point limited",
        "Secure & Confidential Documents Limited",
        "P&W Confidential Business Services Ltd",
        "Baxter Confidential",
        "CSO Confidential",
        "Various Artists Ltd",
        "Multiple Choice Training Limited",
    ]
    for name in category_b_names:
        assert not looks_like_description(name), f"Name '{name}' should NOT be looks_like_description"

    assert looks_like_description("Will provide comprehensive services under contract specification. Details attached.") is True
    print("ok    test_category_b_names_are_listable")

def test_prose_placeholder_rules():
    prose_placeholders = [
        "As per Supplier Table",
        "As per address",
        "As per attachment",
        "Awarded supplier details as per attached list",
        "Confidential Information",
        "Confidential / Sensitive Information",
        "Information withheld as confidential",
        "Please See additional information",
        "Please see Contract Award Notice",
        "COMMERCIALLY CONFIDENTIAL - SUPPLIER NAME NOT TO BE PUBLISHED",
        "Please refer to spreadsheet",
        "All as per original awards",
        "All successful bidders as per original contract notice",
        "All successful bidders as per original contract notice ref 2021-041133",
        "Bolton General Practices - please refer to additional information",
        "Multiple Providers on FPS, Please see below comment for list of providers",
        "New Forest National Park Authority - please see separate Contrac",
        "Various - please refer to the Chest contract register",
        "Various - please see description box for details",
        "Various Please see additional Information.",
        "Various Providers",
        "Various Providers See Contract Award Notice",
        "Various Providers as per F03 Notice",
        "Various Providers as per the F03 Notice",
        "Various as per OJEU Notice - Link contained in Attachments secti",
        "Various firms - please see additional details for listing",
    ]
    for name in prose_placeholders:
        assert looks_like_description(name) is True, f"Prose placeholder '{name}' MUST return looks_like_description == True"

    print("ok    test_prose_placeholder_rules")

if __name__ == "__main__":
    test_looks_like_description()
    test_sql_and_python_description_rules_agree()
    test_category_b_names_are_listable()
    test_prose_placeholder_rules()
    test_prepare_row_hides_description_suppliers()
    test_ingest_stores_description_without_supplier_link()
    test_ingest_rejects_attachment_placeholders_without_a_supplier()
    print("7 passed, 0 skipped, 0 failed")
