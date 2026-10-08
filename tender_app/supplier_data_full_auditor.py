#!/usr/bin/env python3
"""
COMPREHENSIVE SUPPLIER DATABASE AUDIT & CORRECTION SYSTEM
==========================================================

Performs full data quality audit on ALL tracked suppliers:
1. Company number validity checks
2. Name-to-company-number consistency verification via Companies House
3. Contact info provenance and confidence score validation
4. Cross-domain sanity checks (industry vs SIC code)
5. Duplicate/conflicting record detection and merging

Generates detailed reports before/after correction.
Applies fixes at the DATABASE SOURCE LEVEL, not search-time patches.
"""

import re
import os
import sys
import requests
import difflib
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from pathlib import Path

# Add repository root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import existing matching utilities
try:
    from tender_app.ch_matcher import (
        clean_name_for_matching,
        normalize_ch_company_number,
        calculate_name_similarity,
        is_valid_ch_company_number
    )
except ImportError:
    # Fallback implementations if imports fail
    def clean_name_for_matching(name: str) -> str:
        if not name:
            return ""
        name = re.sub(r'\b(ltd|limited|plc|llp|dac|inc|corp|co|uk)\b', '', name, flags=re.IGNORECASE)
        name = re.sub(r'[^\w\s]', ' ', name)
        return ' '.join(name.lower().split())
    
    def normalize_ch_company_number(cnum: str) -> Optional[str]:
        if not cnum:
            return None
        cnum = str(cnum).strip().upper()
        if cnum in ('NOT AVAILABLE', 'PROCUREM', 'NONE', 'N/A', '-', '—') or cnum.startswith('NO_REG_'):
            return None
        # Remove non-alphanumeric
        cnum = re.sub(r'[^A-Z0-9]', '', cnum)
        if not cnum:
            return None
        # Pad numeric-only to 8 digits
        if cnum.isdigit():
            return cnum.zfill(8)
        return cnum
    
    def calculate_name_similarity(name1: str, name2: str) -> float:
        clean1 = clean_name_for_matching(name1)
        clean2 = clean_name_for_matching(name2)
        return difflib.SequenceMatcher(None, clean1, clean2).ratio()
    
    def is_valid_ch_company_number(cnum: str) -> bool:
        norm = normalize_ch_company_number(cnum)
        if not norm:
            return False
        # 8 digits or 2 letters + 6 digits
        return bool(re.match(r'^(\d{8}|[A-Z]{2}\d{6})$', norm))


class SupplierAuditReport:
    """Holds audit findings and statistics"""
    
    def __init__(self):
        self.total_suppliers = 0
        self.invalid_company_numbers = []
        self.name_number_mismatches = []
        self.low_confidence_contacts = []
        self.industry_sic_mismatches = []
        self.duplicate_records = []
        self.records_corrected = 0
        self.records_blanked = 0
        self.records_merged = 0
        self.ch_api_calls = 0
        self.ch_api_failures = []
        
    def add_invalid_company_number(self, sup_id: int, name: str, cnum: str, reason: str):
        self.invalid_company_numbers.append({
            'id': sup_id,
            'name': name,
            'company_number': cnum,
            'reason': reason
        })
    
    def add_name_number_mismatch(self, sup_id: int, stored_name: str, stored_cnum: str, 
                                  ch_name: str, similarity: float):
        self.name_number_mismatches.append({
            'id': sup_id,
            'stored_name': stored_name,
            'stored_company_number': stored_cnum,
            'companies_house_name': ch_name,
            'name_similarity': similarity
        })
    
    def add_low_confidence_contact(self, sup_id: int, name: str, cnum: str, 
                                   field: str, value: str, confidence_issue: str):
        self.low_confidence_contacts.append({
            'id': sup_id,
            'name': name,
            'company_number': cnum,
            'field': field,
            'value': value,
            'confidence_issue': confidence_issue
        })
    
    def add_industry_sic_mismatch(self, sup_id: int, name: str, cnum: str, 
                                  name_industry: str, sic_description: str):
        self.industry_sic_mismatches.append({
            'id': sup_id,
            'name': name,
            'company_number': cnum,
            'name_suggests_industry': name_industry,
            'sic_description': sic_description
        })
    
    def add_duplicate_record(self, master_id: int, master_name: str, 
                            duplicate_ids: List[int], duplicate_names: List[str]):
        self.duplicate_records.append({
            'master_id': master_id,
            'master_name': master_name,
            'duplicate_ids': duplicate_ids,
            'duplicate_names': duplicate_names
        })
    
    def print_summary(self):
        """Print comprehensive audit summary"""
        print("\n" + "=" * 80)
        print("SUPPLIER DATABASE AUDIT REPORT")
        print("=" * 80)
        print(f"\nTotal Suppliers Audited: {self.total_suppliers}")
        print(f"Companies House API Calls: {self.ch_api_calls}")
        print(f"\n--- AUDIT FINDINGS ---")
        print(f"❌ Invalid Company Numbers: {len(self.invalid_company_numbers)}")
        print(f"❌ Name-to-Number Mismatches: {len(self.name_number_mismatches)}")
        print(f"⚠️  Low Confidence Contact Info: {len(self.low_confidence_contacts)}")
        print(f"⚠️  Industry/SIC Code Mismatches: {len(self.industry_sic_mismatches)}")
        print(f"🔄 Duplicate Records Found: {len(self.duplicate_records)}")
        print(f"\n--- CORRECTIONS APPLIED ---")
        print(f"✅ Records Corrected: {self.records_corrected}")
        print(f"🧹 Records Blanked (Not Available): {self.records_blanked}")
        print(f"🔀 Records Merged: {self.records_merged}")
        
        if self.ch_api_failures:
            print(f"\n⚠️  Companies House API Failures: {len(self.ch_api_failures)}")
    
    def print_detailed_findings(self):
        """Print detailed breakdown of all findings"""
        
        if self.invalid_company_numbers:
            print("\n" + "-" * 80)
            print("INVALID COMPANY NUMBERS")
            print("-" * 80)
            for item in self.invalid_company_numbers[:20]:  # Show first 20
                print(f"  ID {item['id']}: {item['name']}")
                print(f"    Company Number: {item['company_number']}")
                print(f"    Reason: {item['reason']}\n")
            if len(self.invalid_company_numbers) > 20:
                print(f"  ... and {len(self.invalid_company_numbers) - 20} more\n")
        
        if self.name_number_mismatches:
            print("\n" + "-" * 80)
            print("NAME-TO-COMPANY-NUMBER MISMATCHES")
            print("-" * 80)
            for item in self.name_number_mismatches[:20]:
                print(f"  ID {item['id']}: {item['stored_name']}")
                print(f"    Stored Company Number: {item['stored_company_number']}")
                print(f"    Companies House Name: {item['companies_house_name']}")
                print(f"    Name Similarity: {item['name_similarity']:.1%}\n")
            if len(self.name_number_mismatches) > 20:
                print(f"  ... and {len(self.name_number_mismatches) - 20} more\n")
        
        if self.low_confidence_contacts:
            print("\n" + "-" * 80)
            print("LOW CONFIDENCE CONTACT INFORMATION")
            print("-" * 80)
            for item in self.low_confidence_contacts[:20]:
                print(f"  ID {item['id']}: {item['name']} ({item['company_number']})")
                print(f"    Field: {item['field']} = {item['value']}")
                print(f"    Issue: {item['confidence_issue']}\n")
            if len(self.low_confidence_contacts) > 20:
                print(f"  ... and {len(self.low_confidence_contacts) - 20} more\n")
        
        if self.industry_sic_mismatches:
            print("\n" + "-" * 80)
            print("INDUSTRY/SIC CODE MISMATCHES")
            print("-" * 80)
            for item in self.industry_sic_mismatches[:20]:
                print(f"  ID {item['id']}: {item['name']} ({item['company_number']})")
                print(f"    Name suggests: {item['name_suggests_industry']}")
                print(f"    SIC description: {item['sic_description']}\n")
            if len(self.industry_sic_mismatches) > 20:
                print(f"  ... and {len(self.industry_sic_mismatches) - 20} more\n")
        
        if self.duplicate_records:
            print("\n" + "-" * 80)
            print("DUPLICATE SUPPLIER RECORDS")
            print("-" * 80)
            for item in self.duplicate_records[:20]:
                print(f"  Master: ID {item['master_id']} - {item['master_name']}")
                print(f"  Duplicates: {', '.join([f'ID {did}' for did in item['duplicate_ids']])}")
                print(f"    {', '.join(item['duplicate_names'])}\n")
            if len(self.duplicate_records) > 20:
                print(f"  ... and {len(self.duplicate_records) - 20} more\n")


class CompaniesHouseClient:
    """Client for Companies House API lookups"""
    
    BASE_URL = "https://api.company-information.service.gov.uk"
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("COMPANIES_HOUSE_API_KEY")
        self.session = requests.Session()
        if self.api_key:
            self.session.auth = (self.api_key, '')
    
    def get_company_profile(self, company_number: str) -> Optional[Dict]:
        """Fetch company profile from Companies House"""
        if not self.api_key:
            return None
        
        try:
            norm_cnum = normalize_ch_company_number(company_number)
            if not norm_cnum:
                return None
            
            url = f"{self.BASE_URL}/company/{norm_cnum}"
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                return None
            else:
                return None
        except Exception as e:
            return None
    
    def get_company_name(self, company_number: str) -> Optional[str]:
        """Get company name from Companies House"""
        profile = self.get_company_profile(company_number)
        if profile:
            return profile.get('company_name')
        return None
    
    def get_sic_codes(self, company_number: str) -> List[str]:
        """Get SIC codes from Companies House"""
        profile = self.get_company_profile(company_number)
        if profile:
            return profile.get('sic_codes', [])
        return []


class SupplierDatabaseAuditor:
    """Main auditor class"""
    
    # Industry keywords for sanity checking
    INDUSTRY_KEYWORDS = {
        'taxi': ['taxis', 'taxi', 'cabs', 'minicabs', 'transport'],
        'pest_control': ['pest', 'vermin', 'exterminator', 'fumigation'],
        'construction': ['construction', 'builders', 'building', 'tarmac', 'asphalt', 'concrete'],
        'cleaning': ['cleaning', 'cleaners', 'janitorial', 'hygiene'],
        'catering': ['catering', 'food', 'hospitality', 'restaurant'],
        'it': ['software', 'computing', 'technology', 'digital', 'systems'],
        'security': ['security', 'guarding', 'surveillance'],
        'healthcare': ['medical', 'health', 'care', 'hospital', 'clinical'],
        'education': ['education', 'training', 'learning', 'school'],
        'facilities': ['facilities', 'maintenance', 'estate', 'property']
    }
    
    # SIC code descriptions (simplified mapping)
    SIC_DESCRIPTIONS = {
        '49': 'Land transport and transport via pipelines',
        '81': 'Services to buildings and landscape activities',
        '62': 'Computer programming, consultancy',
        '80': 'Security and investigation activities',
        '86': 'Human health activities',
        '85': 'Education',
        '56': 'Food and beverage service activities',
        '41': 'Construction of buildings',
        '42': 'Civil engineering',
        '43': 'Specialised construction activities'
    }
    
    def __init__(self, conn, ch_client: Optional[CompaniesHouseClient] = None):
        self.conn = conn
        self.cursor = conn.cursor()
        self.ch_client = ch_client or CompaniesHouseClient()
        self.report = SupplierAuditReport()
        
    def _execute(self, sql: str, params: tuple = ()):
        """Execute SQL with automatic parameter placeholder conversion"""
        sql = sql.replace('?', '%s')
        self.cursor.execute(sql, params)
        return self.cursor
    
    def _row_to_dict(self, row) -> Dict[str, Any]:
        """Convert row to dictionary"""
        if row is None:
            return {}
        if hasattr(row, 'keys'):
            return dict(row)
        if self.cursor.description:
            return dict(zip([col[0] for col in self.cursor.description], row))
        return {}
    
    def check_company_number_validity(self, supplier: Dict) -> bool:
        """CHECK 1: Validate company number format"""
        sup_id = supplier['id']
        name = supplier['name']
        cnum = supplier['company_number']
        
        if not cnum or str(cnum).strip() == '':
            self.report.add_invalid_company_number(sup_id, name, cnum, "Empty/missing company number")
            return False
        
        cnum_str = str(cnum).strip()
        
        # Check for placeholder values
        if cnum_str.upper() in ('NOT AVAILABLE', 'PROCUREM', 'NONE', 'N/A', '-', '—'):
            self.report.add_invalid_company_number(sup_id, name, cnum, "Placeholder value")
            return False
        
        if cnum_str.startswith('NO_REG_'):
            self.report.add_invalid_company_number(sup_id, name, cnum, "Auto-generated unregistered ID")
            return False
        
        # Validate format
        if not is_valid_ch_company_number(cnum_str):
            self.report.add_invalid_company_number(sup_id, name, cnum, "Invalid format (not 8 digits or 2 letters + 6 digits)")
            return False
        
        return True
    
    def check_name_to_number_consistency(self, supplier: Dict) -> bool:
        """CHECK 2: Verify stored name matches Companies House record"""
        if not self.ch_client.api_key:
            return True  # Skip if no API key
        
        sup_id = supplier['id']
        stored_name = supplier['name']
        cnum = supplier['company_number']
        
        # Skip if company number is invalid
        if not is_valid_ch_company_number(cnum):
            return True
        
        # Fetch from Companies House
        self.report.ch_api_calls += 1
        ch_name = self.ch_client.get_company_name(cnum)
        
        if ch_name is None:
            self.report.ch_api_failures.append({
                'id': sup_id,
                'name': stored_name,
                'company_number': cnum,
                'reason': 'Company not found in Companies House'
            })
            return True  # Don't flag as mismatch if API fails
        
        # Calculate similarity
        similarity = calculate_name_similarity(stored_name, ch_name)
        
        # Flag if similarity is below 85%
        if similarity < 0.85:
            self.report.add_name_number_mismatch(sup_id, stored_name, cnum, ch_name, similarity)
            return False
        
        return True
    
    def check_contact_info_confidence(self, supplier: Dict) -> bool:
        """CHECK 3: Validate contact info against company name"""
        sup_id = supplier['id']
        name = supplier['name']
        cnum = supplier['company_number']
        email = supplier.get('email')
        website = supplier.get('website')
        phone = supplier.get('phone')
        
        has_issues = False
        
        # Extract domain from email or website
        domain = None
        if email and '@' in email and email.lower() not in ('not available', 'none', 'n/a'):
            domain = email.split('@')[-1].lower().strip()
            domain_source = 'email'
        elif website and website.lower() not in ('not available', 'none', 'n/a', ''):
            domain = website.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0].lower().strip()
            domain_source = 'website'
        
        if domain:
            # Skip generic domains
            generic_domains = {'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'btconnect.com', 'live.co.uk'}
            if domain in generic_domains:
                return True
            
            # Extract domain base (e.g., 'skylinetaxis' from 'skylinetaxis.co.uk')
            domain_base = domain.split('.')[0]
            
            # Clean company name and extract words
            clean_name = clean_name_for_matching(name)
            name_words = [w for w in clean_name.split() if len(w) > 2]
            
            # Check if any significant word from company name appears in domain
            matched = any(word in domain_base or domain_base in word for word in name_words)
            
            if not matched:
                confidence_issue = f"Domain '{domain}' doesn't match company name '{name}'"
                if email and domain_source == 'email':
                    self.report.add_low_confidence_contact(sup_id, name, cnum, 'email', email, confidence_issue)
                    has_issues = True
                if website and domain_source == 'website':
                    self.report.add_low_confidence_contact(sup_id, name, cnum, 'website', website, confidence_issue)
                    has_issues = True
        
        return not has_issues
    
    def check_industry_sic_consistency(self, supplier: Dict) -> bool:
        """CHECK 4: Cross-domain sanity check - industry vs SIC code"""
        if not self.ch_client.api_key:
            return True  # Skip if no API key
        
        sup_id = supplier['id']
        name = supplier['name']
        cnum = supplier['company_number']
        
        # Skip if company number is invalid
        if not is_valid_ch_company_number(cnum):
            return True
        
        # Detect industry from name
        name_lower = name.lower()
        detected_industries = []
        
        for industry, keywords in self.INDUSTRY_KEYWORDS.items():
            if any(keyword in name_lower for keyword in keywords):
                detected_industries.append(industry)
        
        if not detected_industries:
            return True  # Can't infer industry from name
        
        # Fetch SIC codes from Companies House
        sic_codes = self.ch_client.get_sic_codes(cnum)
        if not sic_codes:
            return True  # No SIC data to compare
        
        # Get SIC descriptions
        sic_descriptions = []
        for sic in sic_codes:
            sic_prefix = sic[:2]  # First 2 digits
            if sic_prefix in self.SIC_DESCRIPTIONS:
                sic_descriptions.append(self.SIC_DESCRIPTIONS[sic_prefix])
        
        # Simple heuristic: check for major mismatches
        # Example: "Taxi" in name but SIC code is "Computer programming"
        suspicious_mismatches = [
            (['taxi'], ['62', '86', '85']),  # Taxi company with IT/Health/Education SIC
            (['pest_control'], ['62', '41', '85']),  # Pest control with IT/Construction/Education SIC
            (['it', 'software'], ['49', '56', '81']),  # IT company with Transport/Catering/Cleaning SIC
        ]
        
        for industries, bad_sics in suspicious_mismatches:
            if any(ind in detected_industries for ind in industries):
                if any(sic[:2] in bad_sics for sic in sic_codes):
                    sic_desc = ', '.join(sic_descriptions) if sic_descriptions else ', '.join(sic_codes)
                    self.report.add_industry_sic_mismatch(sup_id, name, cnum, 
                                                         ', '.join(detected_industries), sic_desc)
                    return False
        
        return True
    
    def find_duplicate_records(self) -> List[Tuple[int, List[int]]]:
        """CHECK 5: Find duplicate supplier records"""
        # Fetch all suppliers
        self._execute("SELECT id, name, company_number FROM suppliers ORDER BY id;")
        all_suppliers = [self._row_to_dict(row) for row in self.cursor.fetchall()]
        
        # Group by normalized company number
        cnum_groups = {}
        for sup in all_suppliers:
            cnum = normalize_ch_company_number(sup['company_number'])
            if cnum:
                if cnum not in cnum_groups:
                    cnum_groups[cnum] = []
                cnum_groups[cnum].append(sup)
        
        # Find duplicates by company number
        duplicates = []
        for cnum, group in cnum_groups.items():
            if len(group) > 1:
                master = group[0]
                dup_ids = [s['id'] for s in group[1:]]
                dup_names = [s['name'] for s in group[1:]]
                self.report.add_duplicate_record(master['id'], master['name'], dup_ids, dup_names)
                duplicates.append((master['id'], dup_ids))
        
        # Group by similar names (fuzzy matching)
        name_groups = {}
        no_cnum_suppliers = [s for s in all_suppliers if not normalize_ch_company_number(s['company_number'])]
        
        for sup in no_cnum_suppliers:
            clean_name = clean_name_for_matching(sup['name'])
            
            # Check for similar existing names
            found_group = False
            for existing_clean_name in list(name_groups.keys()):
                if calculate_name_similarity(clean_name, existing_clean_name) >= 0.90:
                    name_groups[existing_clean_name].append(sup)
                    found_group = True
                    break
            
            if not found_group:
                name_groups[clean_name] = [sup]
        
        # Find duplicates by name
        for clean_name, group in name_groups.items():
            if len(group) > 1:
                master = group[0]
                dup_ids = [s['id'] for s in group[1:]]
                dup_names = [s['name'] for s in group[1:]]
                self.report.add_duplicate_record(master['id'], master['name'], dup_ids, dup_names)
                duplicates.append((master['id'], dup_ids))
        
        return duplicates
    
    def run_full_audit(self) -> SupplierAuditReport:
        """Run all audit checks"""
        print("\n" + "=" * 80)
        print("STARTING COMPREHENSIVE SUPPLIER DATABASE AUDIT")
        print("=" * 80)
        
        # Fetch all suppliers
        self._execute("SELECT * FROM suppliers;")
        all_suppliers = [self._row_to_dict(row) for row in self.cursor.fetchall()]
        self.report.total_suppliers = len(all_suppliers)
        
        print(f"\n📊 Total suppliers to audit: {self.report.total_suppliers}")
        
        if self.ch_client.api_key:
            print("✅ Companies House API key found - full validation enabled")
        else:
            print("⚠️  No Companies House API key - skipping external validation checks")
        
        print("\n🔍 Running audit checks...")
        
        # Run all checks
        for i, supplier in enumerate(all_suppliers, 1):
            if i % 50 == 0:
                print(f"  Progress: {i}/{self.report.total_suppliers} suppliers audited...")
            
            # CHECK 1: Company number validity
            self.check_company_number_validity(supplier)
            
            # CHECK 2: Name-to-number consistency (requires API)
            if self.ch_client.api_key:
                self.check_name_to_number_consistency(supplier)
            
            # CHECK 3: Contact info confidence
            self.check_contact_info_confidence(supplier)
            
            # CHECK 4: Industry/SIC consistency (requires API)
            if self.ch_client.api_key:
                self.check_industry_sic_consistency(supplier)
        
        # CHECK 5: Find duplicates
        print(f"\n🔄 Checking for duplicate records...")
        self.find_duplicate_records()
        
        print(f"\n✅ Audit complete!")
        
        return self.report
    
    def apply_corrections(self, dry_run: bool = False):
        """Apply corrections to flagged records"""
        print("\n" + "=" * 80)
        print("APPLYING CORRECTIONS" + (" (DRY RUN)" if dry_run else ""))
        print("=" * 80)
        
        # Correction 1: Blank out low-confidence contact info
        if self.report.low_confidence_contacts:
            print(f"\n🧹 Blanking {len(self.report.low_confidence_contacts)} low-confidence contact fields...")
            for item in self.report.low_confidence_contacts:
                if not dry_run:
                    self._execute("""
                        UPDATE suppliers 
                        SET email = NULL, phone = NULL, website = NULL, address = NULL
                        WHERE id = ?;
                    """, (item['id'],))
                    self.report.records_blanked += 1
        
        # Correction 2: Merge duplicate records
        if self.report.duplicate_records:
            print(f"\n🔀 Merging {len(self.report.duplicate_records)} duplicate supplier groups...")
            for item in self.report.duplicate_records:
                master_id = item['master_id']
                dup_ids = item['duplicate_ids']
                
                for dup_id in dup_ids:
                    if not dry_run:
                        # Move all awards to master record
                        self._execute("""
                            UPDATE contract_awards 
                            SET supplier_id = ? 
                            WHERE supplier_id = ?;
                        """, (master_id, dup_id))
                        
                        # Delete duplicate supplier
                        self._execute("DELETE FROM suppliers WHERE id = ?;", (dup_id,))
                        self.report.records_merged += 1
        
        # Correction 3: Fix invalid company numbers to NO_REG_ format
        placeholders_to_fix = [item for item in self.report.invalid_company_numbers 
                               if item['reason'] == "Placeholder value"]
        if placeholders_to_fix:
            print(f"\n🔧 Converting {len(placeholders_to_fix)} placeholder company numbers to NO_REG_ format...")
            for item in placeholders_to_fix:
                if not dry_run:
                    new_cnum = f"NO_REG_{item['id']}"
                    self._execute("""
                        UPDATE suppliers 
                        SET company_number = ? 
                        WHERE id = ?;
                    """, (new_cnum, item['id']))
                    self.report.records_corrected += 1
        
        # Commit changes
        if not dry_run:
            self.conn.commit()
            print(f"\n✅ All corrections committed to database")
        else:
            print(f"\n💡 Dry run complete - no changes made to database")


def run_full_supplier_audit(conn, apply_fixes: bool = True, dry_run: bool = False):
    """Main entry point for running full supplier audit"""
    
    # Initialize Companies House client
    ch_api_key = os.environ.get("COMPANIES_HOUSE_API_KEY")
    ch_client = CompaniesHouseClient(ch_api_key)
    
    # Create auditor
    auditor = SupplierDatabaseAuditor(conn, ch_client)
    
    # Run audit
    report = auditor.run_full_audit()
    
    # Print summary
    report.print_summary()
    
    # Print detailed findings
    report.print_detailed_findings()
    
    # Apply corrections if requested
    if apply_fixes:
        auditor.apply_corrections(dry_run=dry_run)
        
        # Print final summary
        print("\n" + "=" * 80)
        print("FINAL AUDIT SUMMARY")
        print("=" * 80)
        print(f"✅ Records Corrected: {report.records_corrected}")
        print(f"🧹 Records Blanked: {report.records_blanked}")
        print(f"🔀 Records Merged: {report.records_merged}")
        print(f"\nTotal issues resolved: {report.records_corrected + report.records_blanked + report.records_merged}")
    
    return report


if __name__ == "__main__":
    # Can be run standalone
    import argparse
    
    parser = argparse.ArgumentParser(description="Run comprehensive supplier database audit")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fixed without making changes")
    parser.add_argument("--no-fix", action="store_true", help="Only audit, don't apply any fixes")
    
    args = parser.parse_args()
    
    # Connect to database
    from tender_app.db import get_db_connection
    conn = get_db_connection()
    
    try:
        run_full_supplier_audit(conn, apply_fixes=not args.no_fix, dry_run=args.dry_run)
    finally:
        conn.close()
