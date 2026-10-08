"""Dry-run script for re-classifying buyer types on existing rows in contract_awards and buyer_stats.

Usage:
    python scripts/reclassify_buyer_types.py          # dry-run mode (default)
    python scripts/reclassify_buyer_types.py --apply  # apply changes to database
"""
import sys
import os
import argparse
from collections import Counter, defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tender_app.db import get_db_connection
from tender_app.blueprints.buyers_bp import classify_buyer_type

APPLY_CHUNK = 5000  # ids per UPDATE statement with --apply


def main():
    parser = argparse.ArgumentParser(description="Audit and reclassify buyer types.")
    parser.add_argument("--apply", action="store_true", help="Apply changes to the database (default: dry-run)")
    args = parser.parse_args()

    dry_run = not args.apply
    if dry_run:
        print("=== DRY RUN MODE (Pass --apply to commit changes) ===\n")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, authority_name, buyer_type FROM contract_awards WHERE authority_name IS NOT NULL AND TRIM(authority_name) != ''")
    rows = cur.fetchall()

    reclassified_count = 0
    type_changes = {}
    names_by_change = defaultdict(Counter)   # (old, new) -> distinct authority names with how many rows each
    ids_by_new_type = defaultdict(list)

    for row_id, auth_name, old_type in rows:
        new_type = classify_buyer_type(auth_name)
        if old_type != new_type:
            reclassified_count += 1
            change_key = (old_type or "NULL", new_type)
            type_changes[change_key] = type_changes.get(change_key, 0) + 1
            names_by_change[change_key][auth_name] += 1
            ids_by_new_type[new_type].append(row_id)
            if reclassified_count <= 20:
                print(f"[Row {row_id}] '{auth_name}': '{old_type}' -> '{new_type}'")

    print(f"\nTotal contract_awards examined: {len(rows)}")
    print(f"Total rows requiring reclassification: {reclassified_count}")
    print("\nSummary of type transitions:")
    for (old, new), count in sorted(type_changes.items(), key=lambda x: x[1], reverse=True):
        print(f"  {old} -> {new}: {count} rows")

    # Which names are behind each change (top 15 per transition), so a wrong rule is visible before --apply.
    print("\nNames behind each transition (top 15 by rows):")
    for (old, new), count in sorted(type_changes.items(), key=lambda x: x[1], reverse=True):
        print(f"  {old} -> {new}  ({len(names_by_change[(old, new)])} distinct names)")
        for name, n in names_by_change[(old, new)].most_common(15):
            print(f"      {n:6d}  {name}")

    if not dry_run and reclassified_count > 0:
        # One UPDATE per chunk of ids, not one per row: over a remote connection 44,000 single-row statements
        # take hours (the same trap as the planning dump).
        print("\nApplying updates to contract_awards...")
        for new_type, ids in ids_by_new_type.items():
            for start in range(0, len(ids), APPLY_CHUNK):
                cur.execute("UPDATE contract_awards SET buyer_type = %s WHERE id = ANY(%s)", (new_type, ids[start:start + APPLY_CHUNK]))
        conn.commit()
        print("Successfully updated contract_awards database rows.")

    conn.close()

if __name__ == "__main__":
    main()
