import os
import sys
import re

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

from tender_app.db import get_db_connection
from etenders_scraper.awards import PLACEHOLDER_SUPPLIER_NAMES, _ATTACHMENT_PLACEHOLDER_RE, looks_like_description

# Protected Category B names (must NOT have supplier_name nulled on awards)
PROTECTED_B_NAMES = {
    'NHS North of England Commercial Procurement Collaborative, a department of  Leeds and York Partnership NHS Foundation Trust',
    'Sunbreeze Healthcare Limited Trading as: Ashlee Residential Care Home, Camden Care Home,  Elderflower House Nursing and Residential Care Home',
    'North of England Commercial Procurement Collaborative (NOE CPC) (hosted by and acting through Leeds and York Partnership NHS Foundation Trust.)',
    'HSO - Herts Schools Outreach (UK) CIC (HSO  Herts Schools Outreach (UK) CIC and NESHertfordhsireSie IN ED CIC  Consortium)',
    'Balfour Beatty Civil Engineering Limited (registration number 04482405) acting as agent of Balfour Beatty Group Limited (registration number 00101073)',
    'Mosaic Healthcare Please note change from previous PIN: includes part of B-Connected Care (Bramblys Grange Medical Practice and Cedar Medical Limited (Beggarwood Surgery and Rooksdown Surgery).  Cedar Medical Limited was spilt on the 07/09/2019',
    "Imperial College Healthcare NHS Trust in partnership with London North West University Healthcare NHS Trust and St George's University Hospitals NHS Foundation Trust",
    'University College London Hospitals NHS Foundation Trust in partnership with Whittington Health NHS Trust and North Middlesex University Hospital NHS Trust',
    'Capita Business Services Ltd MoD Recruiting Partnering Project  71 Victoria Street London SW1H 0XA For the attention of Tony Page, RPP Managing Director',
    'Fado (Farrans Construction trading as a division of Northstone (NI) Limited and J.F & H Dowds Limited, together trading as Fado)',
    'Lynnfield Primary school - The Federation of Golden Flatts & Lynnfield Primary School (part of Lingfield Education Trust)',
    'University Hospital Coventry (UHCW) and Warwickshire NHS Trust hosting  Coventry and Warwickshire Pathology Service (CWPS)',
    'MA Cost Consulting Limited (t/a MAC Construction Consultants)MA Cost Consulting Limited (t/a MAC Construction Consultants)',
    'FRAUNHOFER-GESELLSCHAFT ZUR FORDERUNG DER ANGEWANDTEN FORSCHUNG E.V T/A Fraunhofer Institute for Integrated Circuits (Fraunhofer IIS)',
    'Purple Surgical (prev. Cory Bros)',
    'KWIK-FIT (GB) LIMITED (part of the European Tyre Enterprise Limited group which also includes Credential Environmental Limited)',
}

def is_award_junk_supplier_name(name: str) -> bool:
    if not name:
        return False
    s_clean = name.strip()
    s_lower = s_clean.lower()

    if s_clean in PROTECTED_B_NAMES:
        return False

    if s_lower in PLACEHOLDER_SUPPLIER_NAMES:
        return True

    if _ATTACHMENT_PLACEHOLDER_RE.search(s_clean):
        return True

    if looks_like_description(s_clean):
        return True

    return False

def main():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, supplier_name, supplier_id FROM contract_awards WHERE supplier_name IS NOT NULL AND TRIM(supplier_name) != '';")
    awards = cur.fetchall()

    flagged_awards = []
    for aid, sname, sid in awards:
        if is_award_junk_supplier_name(sname):
            flagged_awards.append((aid, sname, sid))

    print(f"Total contract_awards examined: {len(awards):,}")
    print(f"Total contract_awards with junk/placeholder/description supplier_name: {len(flagged_awards):,}")

    distinct_junk_names = set(r[1] for r in flagged_awards)
    print(f"Distinct junk supplier_names on awards: {len(distinct_junk_names)}")

    print("\nSample flagged award supplier_names:")
    for aid, sname, sid in flagged_awards[:25]:
        print(f"  [Award ID {aid} | sid={sid}] {repr(sname[:90])}")

if __name__ == "__main__":
    main()
