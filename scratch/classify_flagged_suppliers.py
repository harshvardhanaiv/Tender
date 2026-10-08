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
from etenders_scraper.awards import looks_like_description

def classify_supplier(name: str) -> str:
    if not name:
        return "A"
    name_str = name.strip()
    n_lower = name_str.lower()

    # 1. Check for attachment phrases / URLs / clear prose descriptions -> Category A
    if 'http://' in n_lower or 'https://' in n_lower:
        return "A"
    if ('see' in n_lower and 'attach' in n_lower) or ('refer to' in n_lower and 'attach' in n_lower):
        return "A"
    if n_lower.startswith('see ') or n_lower.startswith('please see') or n_lower.startswith('see:') or n_lower.startswith('for individual contract awards'):
        return "A"

    prose_words = [' existing ', ' provider ', ' contract ', ' original ', ' satisfy ', ' proposed ',
                   ' shall provide ', ' routine servicing ', ' allow ', ' extended period ', ' allows ',
                   ' to lead on ', ' pilot period ', ' total value ', ' estimated ', ' call-off ',
                   ' information about ', ' section five ', ' date of conclusion ', ' number of tenders ',
                   ' using the framework ', ' direct-award process ', ' contract/lot ', ' capabilities and ',
                   ' who can provide ', ' bid submission ', ' rent reviews ', ' capability and capacity ',
                   ' end-to-end systems ', ' mistakenly published ', ' published as an opportunity ',
                   ' contract not awarded ', ' customer list attached ', ' log-in to the ',
                   ' view tender opportunities ', ' trained, customer-focussed ', ' footpath improvements ',
                   ' integrated business solution ', ' audience-led service ', ' chartering a ship ',
                   ' due diligence platform ', ' framework participants ']
    if any(pw in n_lower for pw in prose_words):
        return "A"

    # 2. Check for List C (comma/colon separated list of multiple companies)
    if ',' in name_str or (':' in name_str and 'http' not in n_lower):
        parts = [p.strip() for p in re.split(r'[,:]', name_str) if p.strip()]
        if len(parts) >= 2:
            comp_count = 0
            for p in parts:
                p_lower = p.lower()
                if any(kw in p_lower for kw in ['ltd', 'limited', 'inc', 'plc', 'llp', 'rentals', 'management', 'projects', 'engineering', 'services', 'r1a', 'r1b', 'r2', 'r3', 'r4', 'r5', 'r6', 'traffic']):
                    comp_count += 1
            if comp_count >= 2:
                return "C"
            if len(parts) >= 3 and not any(kw in n_lower for kw in ['t/a', 'trading as', 'part of', 'holding']):
                return "C"

    # 3. Category B (real-looking single company)
    single_co_indicators = ['t/a', 'trading as', 'part of', 'hosting', 'prev.', 'prev ', 'group', 'ltd', 'limited', 'inc', 'plc', 'llp', 'trust', 'service', 'services', 'consultants', 'consulting', 'hospital', 'institute', 'engineering']
    if any(ind in n_lower for ind in single_co_indicators) or (len(name_str) <= 120 and not looks_like_description(name_str)):
        return "B"

    words = name_str.split()
    if len(words) > 8 and not any(kw in n_lower for kw in ['ltd', 'limited', 'plc', 'inc', 'llp', 'trust', 'hospital', 'society', 'group']):
        return "A"

    return "B"

def main():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, name FROM suppliers WHERE name IS NOT NULL;")
    sup_rows = cur.fetchall()

    flagged = []
    for sid, sname in sup_rows:
        n_clean = sname.strip()
        if len(n_clean) > 120 or looks_like_description(n_clean) or ('see' in n_clean.lower() and 'attach' in n_clean.lower()) or ('refer to' in n_clean.lower() and 'attach' in n_clean.lower()):
            cat = classify_supplier(n_clean)
            flagged.append((sid, n_clean, cat))

    print("=== CATEGORY B (Real Single Companies - TO BE KEPT) ===")
    b_items = [f for f in flagged if f[2] == 'B']
    for idx, (sid, sname, _) in enumerate(b_items, 1):
        print(f"B{idx:02d}. [ID {sid}] {sname}")

    print("\n=== CATEGORY C (Comma/Colon Separated Company Lists - TO BE UNLINKED & DELETED) ===")
    c_items = [f for f in flagged if f[2] == 'C']
    for idx, (sid, sname, _) in enumerate(c_items, 1):
        parts = [p.strip() for p in re.split(r'[,:]', sname) if p.strip()]
        print(f"C{idx:02d}. [ID {sid}] Raw: {sname}")
        print(f"     Companies: {parts}\n")

if __name__ == "__main__":
    main()
