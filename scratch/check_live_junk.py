"""Audit junk supplier rows on LIVE database."""
import psycopg2
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.supplier_name_cleanup import classify_supplier, LOT_SCORE_PATTERN_RE
from etenders_scraper.awards import looks_like_description

conn_live = psycopg2.connect(host='62.171.162.135', port=5440, dbname='postgres', user='postgres', password='root')
cur = conn_live.cursor()

cur.execute("SELECT id, name FROM suppliers WHERE name IS NOT NULL;")
rows = cur.fetchall()

flagged_a = 0
flagged_c = 0
flagged_l = 0

for sid, sname in rows:
    clean = sname.strip()
    cat = classify_supplier(sid, clean)
    if cat == "A":
        flagged_a += 1
    elif cat == "C":
        flagged_c += 1
    elif cat == "L":
        flagged_l += 1

print(f"LIVE suppliers examined: {len(rows):,}")
print(f"LIVE suppliers flagged as Category A (Junk to delete): {flagged_a}")
print(f"LIVE suppliers flagged as Category C (Multi-co lists): {flagged_c}")
print(f"LIVE suppliers flagged as Category L (Lot score rows): {flagged_l}")

conn_live.close()
