import os
import sys

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

from tender_app.db import get_db_connection
from etenders_scraper.awards import looks_like_description

def is_junk_supplier(name: str) -> bool:
    if not name:
        return False
    name_clean = name.strip()
    if len(name_clean) > 120:
        return True
    if looks_like_description(name_clean):
        return True
    n_lower = name_clean.lower()
    if ('see' in n_lower and 'attach' in n_lower) or ('refer to' in n_lower and 'attach' in n_lower):
        return True
    return False

def main():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, name FROM suppliers WHERE name IS NOT NULL;")
    sup_rows = cur.fetchall()

    flagged_suppliers = []
    for sid, sname in sup_rows:
        if is_junk_supplier(sname):
            flagged_suppliers.append((sid, sname))

    print(f"Total suppliers examined: {len(sup_rows)}")
    print(f"Total suppliers flagged for cleanup: {len(flagged_suppliers)}\n")

    print("=== ALL FLAGGED SUPPLIERS IN LOCAL DB ===")
    for idx, (sid, sname) in enumerate(flagged_suppliers, 1):
        reason = []
        if len(sname) > 120:
            reason.append(f"len={len(sname)} > 120")
        if looks_like_description(sname):
            reason.append("looks_like_description")
        if ('see' in sname.lower() and 'attach' in sname.lower()) or ('refer to' in sname.lower() and 'attach' in sname.lower()):
            reason.append("attach_phrase")
        
        reason_str = ", ".join(reason)
        print(f"{idx:3d}. [ID {sid}] [{reason_str}] {repr(sname)}")

    cur.execute("SELECT id, supplier_name, source_portal FROM contract_awards WHERE supplier_name IS NOT NULL;")
    award_rows = cur.fetchall()

    flagged_awards = []
    for aid, sname, portal in award_rows:
        if is_junk_supplier(sname):
            flagged_awards.append((aid, sname, portal))

    print(f"\nTotal contract_awards examined: {len(award_rows)}")
    print(f"Total contract_awards flagged for unlinking supplier_name: {len(flagged_awards)}")

if __name__ == "__main__":
    main()
