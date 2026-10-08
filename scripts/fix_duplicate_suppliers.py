#!/usr/bin/env python3
"""
Quick script to find and remove duplicate supplier records.
Addresses the production issue where suppliers appear multiple times in the list.

Run this on production to fix duplicates immediately:
    python scripts/fix_duplicate_suppliers.py --production
"""

import sys
import os
from pathlib import Path

# Add repository root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def fix_duplicates(conn, dry_run=False):
    """Find and merge duplicate supplier records"""
    from tender_app.ch_matcher import normalize_ch_company_number, calculate_name_similarity, clean_name_for_matching
    
    cursor = conn.cursor()
    
    
    def _execute(sql, params=()):
        sql = sql.replace('?', '%s')
        cursor.execute(sql, params)
        return cursor
    
    print("\n" + "=" * 80)
    print("DUPLICATE SUPPLIER DETECTION & REMOVAL")
    print("=" * 80)
    
    # Get all suppliers
    _execute("SELECT id, name, company_number FROM suppliers ORDER BY id;")
    all_suppliers = []
    for row in cursor.fetchall():
        all_suppliers.append({
            'id': row[0],
            'name': row[1],
            'company_number': row[2]
        })
    
    print(f"\n📊 Total suppliers in database: {len(all_suppliers)}")
    
    # Find duplicates by company number
    cnum_groups = {}
    for s in all_suppliers:
        cnum = normalize_ch_company_number(s['company_number'])
        if cnum and cnum not in ('NOT AVAILABLE', 'PROCUREM'):
            if cnum not in cnum_groups:
                cnum_groups[cnum] = []
            cnum_groups[cnum].append(s)
    
    duplicates_by_cnum = {cnum: group for cnum, group in cnum_groups.items() if len(group) > 1}
    
    # Find duplicates by name similarity
    no_cnum_suppliers = [s for s in all_suppliers if not normalize_ch_company_number(s['company_number'])]
    name_duplicates = {}
    
    for i, s1 in enumerate(no_cnum_suppliers):
        for s2 in no_cnum_suppliers[i+1:]:
            similarity = calculate_name_similarity(s1['name'], s2['name'])
            if similarity >= 0.90:  # 90% similarity threshold
                key = clean_name_for_matching(s1['name'])
                if key not in name_duplicates:
                    name_duplicates[key] = []
                if s1 not in name_duplicates[key]:
                    name_duplicates[key].append(s1)
                if s2 not in name_duplicates[key]:
                    name_duplicates[key].append(s2)
    
    print(f"\n🔄 Found {len(duplicates_by_cnum)} duplicate groups by company number")
    print(f"🔄 Found {len(name_duplicates)} duplicate groups by name similarity")
    
    total_merged = 0
    
    # Merge duplicates by company number
    if duplicates_by_cnum:
        print(f"\n--- Duplicates by Company Number ---")
        for cnum, group in duplicates_by_cnum.items():
            master = group[0]  # Keep first (lowest ID)
            duplicates = group[1:]
            
            print(f"\n  Company Number: {cnum}")
            print(f"  Master: ID {master['id']} - {master['name']}")
            print(f"  Duplicates to merge:")
            for dup in duplicates:
                print(f"    - ID {dup['id']} - {dup['name']}")
            
            if not dry_run:
                # Move all awards to master
                for dup in duplicates:
                    _execute("UPDATE contract_awards SET supplier_id = ? WHERE supplier_id = ?;", 
                            (master['id'], dup['id']))
                    _execute("DELETE FROM suppliers WHERE id = ?;", (dup['id'],))
                    total_merged += 1
                conn.commit()
                print(f"    ✅ Merged into ID {master['id']}")
            else:
                print(f"    💡 Would merge {len(duplicates)} duplicates")
    
    # Merge duplicates by name
    if name_duplicates:
        print(f"\n--- Duplicates by Name Similarity ---")
        for clean_name, group in name_duplicates.items():
            if len(group) < 2:
                continue
            
            master = group[0]
            duplicates = group[1:]
            
            print(f"\n  Name Group: {clean_name}")
            print(f"  Master: ID {master['id']} - {master['name']}")
            print(f"  Duplicates to merge:")
            for dup in duplicates:
                print(f"    - ID {dup['id']} - {dup['name']}")
            
            if not dry_run:
                for dup in duplicates:
                    _execute("UPDATE contract_awards SET supplier_id = ? WHERE supplier_id = ?;", 
                            (master['id'], dup['id']))
                    _execute("DELETE FROM suppliers WHERE id = ?;", (dup['id'],))
                    total_merged += 1
                conn.commit()
                print(f"    ✅ Merged into ID {master['id']}")
            else:
                print(f"    💡 Would merge {len(duplicates)} duplicates")
    
    # Final count
    _execute("SELECT COUNT(*) FROM suppliers;")
    final_count = cursor.fetchone()[0]
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Before: {len(all_suppliers)} suppliers")
    print(f"After:  {final_count} suppliers")
    print(f"Merged: {total_merged} duplicate records")
    if dry_run:
        print("\n💡 This was a DRY RUN - no changes were made")
        print("   Run without --dry-run to apply fixes")
    else:
        print("\n✅ Duplicates successfully merged!")
    print()
    
    return total_merged


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Fix duplicate supplier records")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fixed without making changes")
    parser.add_argument("--production", action="store_true", help="Run against production database")
    
    args = parser.parse_args()
    
    # Load environment
    if args.production:
        print("🌐 Loading PRODUCTION environment...")
        from dotenv import load_dotenv
        load_dotenv(".env.production")
    
    # Connect to database (importing server loads .env)
    from server import get_db_connection

    conn = get_db_connection()
    print(f"✅ Connected to PostgreSQL at {os.environ.get('DB_HOST', 'localhost')}:{os.environ.get('DB_PORT', '5432')}/{os.environ.get('DB_NAME', 'postgres')}")
    
    try:
        fix_duplicates(conn, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
