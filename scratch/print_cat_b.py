import os
import sys

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

from scratch.classify_flagged_suppliers import classify_supplier
from tender_app.db import get_db_connection
from etenders_scraper.awards import looks_like_description

def main():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, name FROM suppliers WHERE name IS NOT NULL;")
    sup_rows = cur.fetchall()

    b_items = []
    for sid, sname in sup_rows:
        n_clean = sname.strip()
        if len(n_clean) > 120 or looks_like_description(n_clean) or ('see' in n_clean.lower() and 'attach' in n_clean.lower()) or ('refer to' in n_clean.lower() and 'attach' in n_clean.lower()):
            cat = classify_supplier(n_clean)
            if cat == 'B':
                b_items.append((sid, n_clean))

    print(f"Total Category B items: {len(b_items)}\n")
    for idx, (sid, sname) in enumerate(b_items, 1):
        print(f"B{idx:02d}. [ID {sid}] {sname}")

if __name__ == "__main__":
    main()
