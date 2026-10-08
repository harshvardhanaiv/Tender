import os
import sys

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

from scripts.supplier_name_cleanup import classify_supplier, FORCED_JUNK_IDS, LOT_SCORE_IDS, MULTI_CO_IDS
from tender_app.db import get_db_connection
from etenders_scraper.awards import looks_like_description

def main():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT supplier_id, COUNT(*) FROM contract_awards WHERE supplier_id IS NOT NULL GROUP BY supplier_id;")
    award_counts = dict(cur.fetchall())

    cur.execute("SELECT id, name FROM suppliers WHERE name IS NOT NULL;")
    sup_rows = cur.fetchall()

    list_a = []
    for sid, sname in sup_rows:
        n_clean = sname.strip()
        is_flagged = (sid in FORCED_JUNK_IDS or sid in LOT_SCORE_IDS or sid in MULTI_CO_IDS or
                      len(n_clean) > 120 or looks_like_description(n_clean) or
                      ('see' in n_clean.lower() and 'attach' in n_clean.lower()) or
                      ('refer to' in n_clean.lower() and 'attach' in n_clean.lower()))
        if is_flagged:
            cat = classify_supplier(sid, n_clean)
            if cat == "A":
                cnt = award_counts.get(sid, 0)
                list_a.append((sid, n_clean, cnt))

    print(f"Total Category A items: {len(list_a)}\n")
    for idx, (sid, sname, cnt) in enumerate(list_a, 1):
        print(f"A{idx:02d}. [ID {sid}] ({cnt} awards) {repr(sname)}")

if __name__ == "__main__":
    main()
