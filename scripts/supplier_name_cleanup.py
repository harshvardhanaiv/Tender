"""Dry-run script for auditing and cleaning up supplier names and contract awards that hold free-text description content, junk, or lot scores.

Categories:
    A: Clearly junk / buyers / addresses (prose, URLs, attachment references, TBC, buyers as suppliers) -> Unlink awards & delete supplier row.
    B: Real-looking single company (long name, t/a, part of, hosting, prev., etc.) -> KEEP supplier row!
    C: Real multi-company lists -> Unlink awards & delete supplier row.
    L: Lot-score supplier rows (matches \\s*(:\\s*)?R\\d+[a-z]?\\s*:) -> Strip lot score tail, re-link awards to clean supplier or rename row.

Award-level cleanup:
    Nulls contract_awards.supplier_name and supplier_id for awards whose supplier_name is a placeholder, attachment text, or description (excluding Category B).

Usage:
    python scripts/supplier_name_cleanup.py          # dry-run mode (default)
    python scripts/supplier_name_cleanup.py --apply  # apply changes to database
"""
import sys
import os
import argparse
import re

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tender_app.db import get_db_connection
from etenders_scraper.awards import PLACEHOLDER_SUPPLIER_NAMES, _ATTACHMENT_PLACEHOLDER_RE, looks_like_description

# Explicit ID overrides based on manual review. IDs are LOCAL ids and may differ on live, so each
# rule maps id -> expected supplier name and only applies when the name found also matches.
# Expected names are compared after normalising case/whitespace/dashes, and may be a leading
# fragment of the full name (some were only recorded truncated during review).
# 229940 (prose row) has no recorded name, so its rule never applies; the prose rule covers it by name.
FORCED_JUNK_IDS = {
    756697: "To Be Confirmed",
    229940: None,
    165882: "Wirral University Teaching Hospital NHS Foundation Trust, Arrowe Park Hospital, Arrowe Park Road, Upton, Wirral, Merseyside, CH49 5PE",
    181621: "Hertfordshire Partnership NHS Foundation Trust (RWR) Principal and/or registered office address: The Colonnades Beaconsfield Close, Hatfield, Hertfordshire. AL10 8YE",
}
FORCED_KEEP_B_IDS = {
    152677: "HSO - Herts Schools Outreach (UK) CIC (HSO - Herts Schools Outreach (UK) CIC and NESHertfordhsireSie IN ED CIC",
}
MULTI_CO_IDS = {
    150909: "1st VALLEY FORESTRY, 2nd GARDINER FARM AND FOREST SERVICES",
    150910: "1st JF MCGOVERN CONTRACTS LIMITED, 2nd VALLEY FORESTRY, 3rd SEASONAL OUTDOOR SERVICES LIMITED, 4th ELM FORESTRY AND LANDSCAPING LTD, 5th GARDINER FARM AND FOREST SERVICES, 6th MCGRATH CONTRACTS LTD",
    150911: "1st SEASONAL OUTDOOR SERVICES LIMITED, 2nd VALLEY FORESTRY, 3rd GARDINER FARM AND FOREST SERVICES, 4th ELM FORESTRY AND LANDSCAPING LTD, 5th LANDSCAPING CENTRE LTD",
    164873: "Fox Building & Engineering Ltd, Adman Civil Projects Ltd, William & Henry Alexander (Civil Engineering) Limited, Lowry Building & Civil Engineering Ltd, Forrme Limited",
    212934: "Fox Building & Engineering Ltd, Adman Civil Projects Ltd, William & Henry Alexander",
    189383: "HW Martin, Amberon Ltd, Hooke Highwyas, Chevron Traffic Management, Sunbelt Rentals, Idverde Ltd, City Traffic Management, Quantum Traffic Management, Safemark Roadmarking",
    170292: "East Yorkshire Motor Services Limited, Procters Coaches (North Yorkshire) Limited, Esk Valley Coaches Limited, Compass Royston Travel Limted, W Cropper Limited T/A Fourway Coaches",
    142077: "Generali, Generali, Swiss Re, RSA, QBE, QBE, QBE, QBE, Zurich",
    156330: "1. Legal & General Affordable Homes Limited - CH no: 11223470 2. Legal & General",
    138451: "WORLDPAY (UK) LIMITED (Company No. 07316500), WORLDPAY LIMITED",
}


def _norm_name(name) -> str:
    """Normalise for ID-rule name comparison: unify dashes, collapse whitespace, casefold."""
    s = re.sub(r'[‐-―−]', '-', name or "")
    return re.sub(r'\s+', ' ', s).strip().casefold()


def id_rule_applies(sid, name: str, rules: dict, rule_label: str) -> bool:
    """True only if sid is in rules AND the supplier name matches the expected name.

    On an id hit with a different name (e.g. a live id that is a different supplier), the rule is
    skipped and a WARNING with the id and the name found is printed."""
    if not sid or sid not in rules:
        return False
    expected = rules[sid]
    found = _norm_name(name)
    if expected and found.startswith(_norm_name(expected)):
        return True
    print(f"WARNING: skipping {rule_label} rule for supplier id {sid}: name found {name!r} "
          f"does not match expected {expected!r}", file=sys.stderr)
    return False

LOT_SCORE_PATTERN_RE = re.compile(r'\s*(:\s*)?R\d+[a-z]?\s*:', re.IGNORECASE)

# Protected Category B names (must NOT have supplier_name nulled on contract_awards)
PROTECTED_B_NAMES = {
    'NHS North of England Commercial Procurement Collaborative, a department of  Leeds and York Partnership NHS Foundation Trust',
    'Sunbreeze Healthcare Limited Trading as: Ashlee Residential Care Home, Camden Care Home,  Elderflower House Nursing and Residential Care Home',
    'North of England Commercial Procurement Collaborative (NOE CPC) (hosted by and acting through Leeds and York Partnership NHS Foundation Trust.)',
    'HSO - Herts Schools Outreach (UK) CIC (HSO  Herts Schools Outreach (UK) CIC and NESHertfordhsireSie IN ED CIC  Consortium)',
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

def clean_lot_score_name(name: str) -> str:
    """Strips lot-score suffix from supplier name."""
    if not name:
        return ""
    cleaned = re.sub(r'\s*(:\s*)?R\d+[a-z]?:.*$', '', name, flags=re.IGNORECASE).strip()
    return cleaned

def classify_supplier(sid: int | str = 0, name: str = "") -> str:
    """Classifies a supplier row into 'A', 'B', 'C', or 'L'."""
    if isinstance(sid, str) and not name:
        name = sid
        sid = 0

    if not name:
        return "A"
    name_str = name.strip()
    n_lower = name_str.lower()

    if id_rule_applies(sid, name_str, FORCED_KEEP_B_IDS, "FORCED_KEEP_B"):
        return "B"
    if name and (name_str in PROTECTED_B_NAMES or any(b.lower() == n_lower for b in PROTECTED_B_NAMES)):
        return "B"
    if id_rule_applies(sid, name_str, FORCED_JUNK_IDS, "FORCED_JUNK"):
        return "A"
    if id_rule_applies(sid, name_str, MULTI_CO_IDS, "MULTI_CO"):
        return "C"

    if name and LOT_SCORE_PATTERN_RE.search(name):
        return "L"

    if not name:
        return "A"

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

    if looks_like_description(name_str):
        return "A"
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

    single_co_indicators = ['t/a', 'trading as', 'part of', 'hosting', 'prev.', 'prev ', 'group', 'ltd', 'limited', 'inc', 'plc', 'llp', 'trust', 'service', 'services', 'consultants', 'consulting', 'hospital', 'institute', 'engineering', 'cic']
    if any(ind in n_lower for ind in single_co_indicators):
        return "B"

    if len(name_str) <= 120 and not looks_like_description(name_str):
        return "B"

    words = name_str.split()
    if len(words) > 8 and not any(kw in n_lower for kw in ['ltd', 'limited', 'plc', 'inc', 'llp', 'trust', 'hospital', 'society', 'group', 'cic']):
        return "A"

    return "B"

def is_award_junk_supplier_name(name: str) -> bool:
    """True if contract_awards.supplier_name is a placeholder, attachment text, or description."""
    if not name:
        return False
    s_clean = name.strip()
    s_lower = s_clean.lower()

    if s_clean in PROTECTED_B_NAMES or any(b.lower() == s_lower for b in PROTECTED_B_NAMES):
        return False

    if s_lower in PLACEHOLDER_SUPPLIER_NAMES:
        return True

    if _ATTACHMENT_PLACEHOLDER_RE.search(s_clean):
        return True

    if looks_like_description(s_clean):
        return True

    return False

def main():
    parser = argparse.ArgumentParser(description="Audit and clean up supplier names matching description rules.")
    parser.add_argument("--apply", action="store_true", help="Apply changes to the database (default: dry-run)")
    parser.add_argument("--live", action="store_true", help="Target live database using LIVE_DB_* environment variables")
    parser.add_argument("--confirm-live", action="store_true", help="Explicit confirmation required to apply changes to live database")
    args = parser.parse_args()

    if args.live:
        required_live_vars = ["LIVE_DB_HOST", "LIVE_DB_PORT", "LIVE_DB_NAME", "LIVE_DB_USER", "LIVE_DB_PASSWORD"]
        missing = [v for v in required_live_vars if not os.environ.get(v)]
        if missing:
            sys.stderr.write(f"Error: --live requires all LIVE_DB_* environment variables to be set. Missing: {', '.join(missing)}\n")
            sys.exit(1)
        if args.apply and not args.confirm_live:
            sys.stderr.write("Error: Refusing to run --apply with --live unless --confirm-live is also passed.\n")
            sys.exit(1)

        db_host = os.environ["LIVE_DB_HOST"]
        db_port = os.environ["LIVE_DB_PORT"]
        db_name = os.environ["LIVE_DB_NAME"]
        db_user = os.environ["LIVE_DB_USER"]
        db_password = os.environ["LIVE_DB_PASSWORD"]
        target_label = "LIVE"
    else:
        db_host = os.environ.get("DB_HOST", "127.0.0.1")
        db_port = os.environ.get("DB_PORT", "5440")
        db_name = os.environ.get("DB_NAME", "postgres")
        db_user = os.environ.get("DB_USER", "postgres")
        db_password = os.environ.get("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))
        target_label = "LOCAL"

    # Set DB environment variables for downstream calls without exposing password in logs
    os.environ["DB_HOST"] = str(db_host)
    os.environ["DB_PORT"] = str(db_port)
    os.environ["DB_NAME"] = str(db_name)
    os.environ["DB_USER"] = str(db_user)
    os.environ["DB_PASSWORD"] = str(db_password)

    dry_run = not args.apply
    if dry_run:
        print(f"=== DRY RUN MODE ({target_label} DB: {db_host}:{db_port}/{db_name} as {db_user}) (Pass --apply to commit changes) ===\n")
    else:
        print(f"=== APPLY MODE ({target_label} DB: {db_host}:{db_port}/{db_name} as {db_user}) ===\n")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT supplier_id, COUNT(*) FROM contract_awards WHERE supplier_id IS NOT NULL GROUP BY supplier_id;")
    award_counts = dict(cur.fetchall())

    cur.execute("SELECT id, name FROM suppliers WHERE name IS NOT NULL")
    sup_rows = cur.fetchall()

    list_a = []
    list_b = []
    list_c = []
    list_l = []

    for sid, sname in sup_rows:
        n_clean = sname.strip()
        is_flagged = (sid in FORCED_JUNK_IDS or sid in FORCED_KEEP_B_IDS or sid in MULTI_CO_IDS or
                      LOT_SCORE_PATTERN_RE.search(n_clean) or
                      len(n_clean) > 120 or looks_like_description(n_clean) or
                      ('see' in n_clean.lower() and 'attach' in n_clean.lower()) or
                      ('refer to' in n_clean.lower() and 'attach' in n_clean.lower()))
        
        if is_flagged:
            cat = classify_supplier(sid, n_clean)
            cnt = award_counts.get(sid, 0)
            if cat == "A":
                list_a.append((sid, n_clean, cnt))
            elif cat == "B":
                list_b.append((sid, n_clean, cnt))
            elif cat == "C":
                list_c.append((sid, n_clean, cnt))
            elif cat == "L":
                cleaned_name = clean_lot_score_name(n_clean)
                list_l.append((sid, n_clean, cleaned_name, cnt))

    # Audit contract_awards table directly
    cur.execute("SELECT id, supplier_name, supplier_id FROM contract_awards WHERE supplier_name IS NOT NULL AND TRIM(supplier_name) != '';")
    awards_rows = cur.fetchall()

    flagged_junk_awards = []
    flagged_lot_score_awards = []

    for aid, sname, sid in awards_rows:
        s_clean = sname.strip()
        if is_award_junk_supplier_name(s_clean):
            flagged_junk_awards.append((aid, s_clean, sid))
        elif LOT_SCORE_PATTERN_RE.search(s_clean):
            cleaned = clean_lot_score_name(s_clean)
            flagged_lot_score_awards.append((aid, s_clean, cleaned))

    print(f"Total suppliers table rows examined: {len(sup_rows):,}")
    print(f"Total suppliers rows flagged: {len(list_a) + len(list_b) + len(list_c) + len(list_l)}")
    print(f"  - Category A (Junk / Buyers / Addresses - TO DELETE): {len(list_a)}")
    print(f"  - Category B (Real Single Companies - TO KEEP): {len(list_b)}")
    print(f"  - Category C (Multi-company Lists - TO DELETE & UNLINK): {len(list_c)}")
    print(f"  - Category L (Lot-Score Rows - STRIP TAIL & RENAME/RE-LINK): {len(list_l)}\n")

    print(f"Total contract_awards examined: {len(awards_rows):,}")
    print(f"Total contract_awards with junk/placeholder/description supplier_name (TO NULL): {len(flagged_junk_awards):,}")
    print(f"Total contract_awards with lot-score supplier_name (TO CLEAN): {len(flagged_lot_score_awards):,}\n")

    print("==========================================================================")
    print("CATEGORY A: JUNK / PROSE / BUYER ADDRESSES (Unlink awards & delete supplier row)")
    print("==========================================================================")
    for idx, (sid, sname, cnt) in enumerate(list_a, 1):
        print(f"  A{idx:02d}. [ID {sid}] ({cnt} awards) {repr(sname)}")

    print("\n==========================================================================")
    print("CATEGORY B: REAL-LOOKING SINGLE COMPANIES (KEEP SUPPLIER; DO NOT DELETE)")
    print("==========================================================================")
    for idx, (sid, sname, cnt) in enumerate(list_b, 1):
        print(f"  B{idx:02d}. [ID {sid}] ({cnt} awards) {repr(sname)}")

    print("\n==========================================================================")
    print("CATEGORY C: MULTI-COMPANY LISTS (Unlink awards & delete supplier row)")
    print("==========================================================================")
    for idx, (sid, sname, cnt) in enumerate(list_c, 1):
        print(f"  C{idx:02d}. [ID {sid}] ({cnt} awards) {repr(sname)}")

    print("\n==========================================================================")
    print("CATEGORY L: LOT-SCORE SUPPLIER ROWS (Strip tail & re-link/rename)")
    print("==========================================================================")
    for idx, (sid, raw_name, clean_name, cnt) in enumerate(list_l, 1):
        print(f"  L{idx:02d}. [ID {sid}] ({cnt} awards) {repr(raw_name)} -> {repr(clean_name)}")

    if not dry_run:
        print("\nApplying cleanup...")
        # 1. Delete Category A and C suppliers
        to_delete = list_a + list_c
        for sid, _, _ in to_delete:
            cur.execute("UPDATE contract_awards SET supplier_id = NULL, supplier_name = NULL WHERE supplier_id = %s", (sid,))
            cur.execute("DELETE FROM suppliers WHERE id = %s", (sid,))
        print(f"  [OK] Deleted {len(to_delete)} junk/multi-company supplier rows.")

        # 2. Process Lot-Score Category L suppliers
        for sid, raw_name, clean_name, cnt in list_l:
            cur.execute("SELECT id FROM suppliers WHERE name = %s AND id != %s LIMIT 1", (clean_name, sid))
            target = cur.fetchone()
            if target:
                target_id = target[0]
                cur.execute("UPDATE contract_awards SET supplier_id = %s, supplier_name = %s WHERE supplier_id = %s", (target_id, clean_name, sid))
                cur.execute("DELETE FROM suppliers WHERE id = %s", (sid,))
            else:
                cur.execute("UPDATE suppliers SET name = %s WHERE id = %s", (clean_name, sid))
                cur.execute("UPDATE contract_awards SET supplier_name = %s WHERE supplier_id = %s", (clean_name, sid))
        print(f"  [OK] Processed {len(list_l)} lot-score supplier rows.")

        # 3. Clean contract_awards with lot score tails
        for aid, raw_name, clean_name in flagged_lot_score_awards:
            cur.execute("UPDATE contract_awards SET supplier_name = %s WHERE id = %s", (clean_name, aid))
        print(f"  [OK] Cleaned {len(flagged_lot_score_awards)} contract_awards with lot-score supplier_name tails.")

        # 4. Null contract_awards with junk/placeholder/description supplier_name
        junk_award_ids = [aid for aid, _, _ in flagged_junk_awards]
        if junk_award_ids:
            cur.execute("UPDATE contract_awards SET supplier_name = NULL, supplier_id = NULL WHERE id = ANY(%s)", (junk_award_ids,))
        print(f"  [OK] Nulled supplier_name & supplier_id on {len(junk_award_ids)} junk/placeholder contract_awards.")

        conn.commit()
        print("Cleanup successfully committed to database.")

    conn.close()

if __name__ == "__main__":
    main()
