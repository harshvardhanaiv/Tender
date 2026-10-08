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

LOT_SCORE_RE = re.compile(r'\s*(:\s*)?R\d+[a-z]?\s*:', re.IGNORECASE)

def clean_lot_score_name(name: str) -> str:
    if not name:
        return ""
    cleaned = re.sub(r'\s*(:\s*)?R\d+[a-z]?:.*$', '', name, flags=re.IGNORECASE).strip()
    return cleaned

def main():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, name FROM suppliers WHERE name IS NOT NULL;")
    sup_rows = cur.fetchall()

    matched_sups = []
    for sid, sname in sup_rows:
        if LOT_SCORE_RE.search(sname):
            cleaned = clean_lot_score_name(sname)
            matched_sups.append((sid, sname, cleaned))

    cur.execute("SELECT id, supplier_name FROM contract_awards WHERE supplier_name IS NOT NULL AND supplier_name ~* '\\s*(:\\s*)?R\\d+[a-z]?\\s*:';")
    matched_awards = cur.fetchall()

    print(f"Suppliers matching Lot-Score pattern: {len(matched_sups)}")
    print(f"Contract awards matching Lot-Score pattern: {len(matched_awards)}\n")

    print("=== SUPPLIERS MATCHING LOT-SCORE PATTERN ===")
    for idx, (sid, raw_name, clean_name) in enumerate(matched_sups, 1):
        print(f"{idx:2d}. [ID {sid}] Raw: {repr(raw_name)}\n    Clean: {repr(clean_name)}")

if __name__ == "__main__":
    main()
