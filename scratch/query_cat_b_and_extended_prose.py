import psycopg2
import os
import sys

sys.path.insert(0, os.path.abspath("."))
from scripts.supplier_name_cleanup import PROTECTED_B_NAMES, FORCED_KEEP_B_IDS

db_pass = os.getenv("LOCAL_DB_PASSWORD", "postgres")
conn = psycopg2.connect(host="127.0.0.1", port=5440, dbname="postgres", user="postgres", password=db_pass)
cur = conn.cursor()

print("=== TASK 1: THE 16 CATEGORY B PROTECTED SUPPLIERS (ID + NAME) ===")
# Find suppliers matching FORCED_KEEP_B_IDS or PROTECTED_B_NAMES in DB
cur.execute("""
    SELECT id, name 
    FROM suppliers 
    WHERE id = ANY(%s) OR name = ANY(%s)
    ORDER BY id
""", (list(FORCED_KEEP_B_IDS), list(PROTECTED_B_NAMES)))

cat_b_rows = cur.fetchall()
print(f"Total Category B suppliers in DB: {len(cat_b_rows)}")
for sid, sname in cat_b_rows:
    safe_name = sname.encode("ascii", errors="replace").decode("ascii")
    print(f"  [ID {sid}] {safe_name}")


print("\n=== TASK 2: SUPPLIERS MATCHING EXTENDED PROSE REGEX PATTERN ===")
pattern = r'(please see|please refer|as per (the |original |attached )?(notice|award|contract|list|f03|ojeu)|various (providers|firms|suppliers)|all successful bidders|all as per)'

cur.execute("""
    SELECT s.id, s.name, COUNT(ca.id) AS award_count
    FROM suppliers s
    LEFT JOIN contract_awards ca ON ca.supplier_id = s.id
    WHERE s.name ~* %s
    GROUP BY s.id, s.name
    ORDER BY s.name
""", (pattern,))

matching_rows = cur.fetchall()
print(f"Total suppliers matching extended pattern: {len(matching_rows)}")
for sid, sname, cnt in matching_rows:
    safe_name = sname.encode("ascii", errors="replace").decode("ascii")
    print(f"  [ID {sid}] ({cnt} awards): {safe_name}")

conn.close()
