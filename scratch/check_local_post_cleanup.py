import psycopg2
import os
import re
import sys

sys.path.insert(0, os.path.abspath("."))
from etenders_scraper.awards import (
    PLACEHOLDER_SUPPLIER_NAMES,
    _ATTACHMENT_PLACEHOLDER_RE,
    looks_like_description,
)
from scripts.supplier_name_cleanup import PROTECTED_B_NAMES

db_pass = os.getenv("LOCAL_DB_PASSWORD", "postgres")
conn = psycopg2.connect(host="127.0.0.1", port=5440, dbname="postgres", user="postgres", password=db_pass)
cur = conn.cursor()

print("=== VERIFYING POST-CLEANUP DB STATE ===")

# 1. Check lot score pattern on suppliers.name
cur.execute("SELECT count(*) FROM suppliers WHERE name ~* '\\\\s*(:\\\\s*)?R\\\\d+[a-z]?\\\\s*:'")
lot_suppliers_count = cur.fetchone()[0]
print(f"1. Suppliers matching lot-score pattern: {lot_suppliers_count} (Expected: 0)")

# 2. Check orphan supplier_ids in contract_awards
cur.execute("""
    SELECT count(*) 
    FROM contract_awards ca 
    LEFT JOIN suppliers s ON ca.supplier_id = s.id 
    WHERE ca.supplier_id IS NOT NULL AND s.id IS NULL
""")
orphan_count = cur.fetchone()[0]
print(f"2. Orphan supplier_id in contract_awards: {orphan_count} (Expected: 0)")

# 3. Check total contract_awards
cur.execute("SELECT count(*) FROM contract_awards")
total_awards = cur.fetchone()[0]
print(f"3. Total contract_awards: {total_awards} (Expected: 501,882)")

# 4. Check awards with placeholder/attachment/description supplier_name (excluding Category B)
cur.execute("SELECT id, supplier_name, supplier_id FROM contract_awards WHERE supplier_name IS NOT NULL")
rows = cur.fetchall()

junk_awards_count = 0
for aid, sname, sid in rows:
    if sname in PROTECTED_B_NAMES:
        continue
    # Check placeholder
    if sname in PLACEHOLDER_SUPPLIER_NAMES or _ATTACHMENT_PLACEHOLDER_RE.match(sname):
        junk_awards_count += 1
        print(f"  Flagged award ID {aid}, sid={sid}: '{sname[:80]}'")
    elif looks_like_description(sname):
        junk_awards_count += 1
        print(f"  Flagged award ID {aid}, sid={sid}: '{sname[:80]}'")

print(f"4. Non-Category-B awards with placeholder/attachment/description supplier_name: {junk_awards_count} (Expected: 0)")

conn.close()

if lot_suppliers_count == 0 and orphan_count == 0 and total_awards == 501882 and junk_awards_count == 0:
    print("\nALL POST-CLEANUP CHECKS PASSED!")
else:
    print("\nSOME CHECKS FAILED! Check output above.")
