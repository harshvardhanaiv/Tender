#!/usr/bin/env python3
"""
COMPREHENSIVE SUPPLIER DATABASE AUDIT & CORRECTION SCRIPT
==========================================================

Performs full data quality audit on ALL tracked suppliers:
1. Company number validity checks
2. Name-to-company-number consistency verification via Companies House
3. Contact info provenance and confidence score validation  
4. Cross-domain sanity checks (industry vs SIC code)
5. Duplicate/conflicting record detection and merging

This is the COMPREHENSIVE audit that addresses data corruption at the SOURCE level,
not just at search time.

Usage:
  python scripts/run_supplier_audit.py                 # Full audit + apply fixes
  python scripts/run_supplier_audit.py --dry-run       # Show what would be fixed
  python scripts/run_supplier_audit.py --no-fix        # Only audit, don't fix
  python scripts/run_supplier_audit.py --quick         # Quick audit (old behavior)
"""

import sys
import os
import argparse

# Add repository root to path so we can import tender_app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def run_quick_audit(conn):
    """Run the quick contact auditor (original behavior)"""
    print("=" * 80)
    print("QUICK SUPPLIER CONTACT DATA QUALITY AUDITOR")
    print("=" * 80)
    print()
    print("Running quick audit - checks contact info domain matching only")
    print()
    
    # Get total supplier count before audit
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM suppliers;")
    total_suppliers = cursor.fetchone()[0]
    print(f"[OK] Found {total_suppliers} supplier records in database")
    print()
    
    # Run the quick auditor
    print("Running data quality audit...")
    from tender_app.supplier_data_auditor import audit_and_clean_supplier_contacts
    cleaned_count = audit_and_clean_supplier_contacts(conn)
    
    print()
    print("=" * 80)
    print("QUICK AUDIT COMPLETE")
    print("=" * 80)
    print(f"Total suppliers audited: {total_suppliers}")
    print(f"Cross-contaminated records cleaned: {cleaned_count}")
    
    if cleaned_count > 0:
        print()
        print(f"[!] {cleaned_count} supplier(s) had mismatched contact information that was")
        print("   cleared to 'Not available' to prevent cross-company data leakage.")
    else:
        print()
        print("[OK] All supplier contact records passed quick validation!")
    
    print()


def run_comprehensive_audit(conn, apply_fixes=True, dry_run=False):
    """Run the comprehensive full audit"""
    from tender_app.supplier_data_full_auditor import run_full_supplier_audit
    
    print()
    print("🔍 COMPREHENSIVE SUPPLIER DATABASE AUDIT")
    print()
    print("This will perform:")
    print("  ✓ Company number validity checks")
    print("  ✓ Name-to-company-number consistency (via Companies House API)")
    print("  ✓ Contact info confidence validation")
    print("  ✓ Industry/SIC code sanity checks")
    print("  ✓ Duplicate record detection")
    print()
    
    if os.environ.get("COMPANIES_HOUSE_API_KEY"):
        print("✅ Companies House API key detected - full validation enabled")
    else:
        print("⚠️  No COMPANIES_HOUSE_API_KEY environment variable found")
        print("   Some checks will be skipped. Set this for full validation:")
        print("   export COMPANIES_HOUSE_API_KEY=your_api_key")
    print()
    
    # Run the comprehensive audit
    report = run_full_supplier_audit(conn, apply_fixes=apply_fixes, dry_run=dry_run)
    
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Run supplier database audit and corrections",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--quick", action="store_true", 
                       help="Run quick audit (contact info only, original behavior)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be fixed without making changes")
    parser.add_argument("--no-fix", action="store_true",
                       help="Only audit, don't apply any fixes")
    parser.add_argument("--production", action="store_true",
                       help="Run against production database (.env.production)")
    
    args = parser.parse_args()
    
    try:
        # Load environment
        if args.production:
            print("🌐 Loading PRODUCTION environment...")
            from dotenv import load_dotenv
            load_dotenv(".env.production")
        
        # Import database connection logic (importing server loads .env)
        from server import get_db_connection

        conn = get_db_connection()
        print(f"✅ Connected to PostgreSQL database at {os.environ.get('DB_HOST', 'localhost')}:{os.environ.get('DB_PORT', '5432')}/{os.environ.get('DB_NAME', 'postgres')}")
        
        # Run appropriate audit
        if args.quick:
            run_quick_audit(conn)
        else:
            run_comprehensive_audit(conn, apply_fixes=not args.no_fix, dry_run=args.dry_run)
        
        conn.close()
        
        print()
        print("=" * 80)
        print("AUDIT SCRIPT COMPLETE")
        print("=" * 80)
        print()
        print("💡 TIP: The quick audit runs automatically after every 'Sync More Awards'.")
        print("   Run this comprehensive audit periodically to catch deeper issues.")
        print()
        
        return 0
        
    except Exception as ex:
        print()
        print("=" * 80)
        print("❌ ERROR")
        print("=" * 80)
        print(f"{ex}")
        print()
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
