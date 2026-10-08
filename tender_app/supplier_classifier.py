"""
Supplier Classification Module for TenderFlow
Provides verified SME and VCSE entity size classification using Companies House data,
accounts categories, employee thresholds, and safety net major contractor registries.
"""

import re
from typing import Any, Tuple

# Safety net registry of known major contractors, PLCs, and large enterprise groups.
# Even if raw scraped data briefly mislabels them, these entities CANNOT pass as SMEs.
KNOWN_LARGE_ENTERPRISES = {
    # IT, Defense, Engineering & Business Services Majors
    "accenture", "capita", "balfour beatty", "serco", "amey", "kier", "mitie",
    "atos", "cgi", "ibm", "deloitte", "pwc", "pricewaterhousecoopers", "ey", "ernst & young",
    "kpmg", "mckinsey", "boston consulting group", "bae systems", "lockheed martin",
    "rolls-royce", "babcock", "qinetiq", "sopra steria", "computacenter", "softcat",
    "fujitsu", "oracle", "microsoft", "sap", "amazon", "aws", "google", "salesforce",
    "cisco", "vodafone", "bt", "british telecom", "virgin media", "dxc", "mott macdonald",
    "arup", "wsp", "aecom", "jacobs", "atkins", "atkinsrealis", "thameslink", "network rail",
    "royal mail", "boeing", "northrop grumman", "general dynamics", "raytheon", "thales",
    "leidos", "capgemini", "cognizant", "infosys", "wipro", "tata consultancy", "tcs",
    "hcl", "tech mahindra", "ntt data", "slb", "halliburton", "baker hughes", "shell",
    "bp", "exxonmobil", "chevron", "totalenergies", "national grid", "centrica", "sse",
    "scottish power", "eon", "edf", "united utilities", "severn trent", "anglian water",
    "thames water", "dwr cymru", "welsh water", "yorkshire water", "southern water",
    "barclays", "hsbc", "lloyds", "natwest", "rbs", "santander", "standard chartered",
    "aviva", "legal & general", "prudential", "schroders", "bupa", "g4s", "veolia",
    "biffa", "suez", "galliford try", "morgan sindall", "skanska", "bam nuttall",
    "bam construct", "costain", "mace", "laing o'rourke", "sir robert mcalpine",
    "volkerwessels", "isg", "wates", "bouygues", "vinci", "skanska", "dragados",
    "ferrovial", "acciona", "strabag", "multiplex", "wincanton", "dhl", "fedex", "ups",
    "royal mail", "civica", "eircom", "virgin media O2", "openreach", "ee limited"
}

# VCSE registry indicators (Charities, CICs, Social Enterprises)
KNOWN_VCSE_KEYWORDS = [
    "charity", "charitable", "foundation", "trust", "community interest company",
    "cic", "social enterprise", "voluntary", "voluntary sector", "association",
    "society", "scio", "cio", "housing association", "shelter", "age uk", "barnardo",
    "oxfam", "red cross", "mind", "mencap", "nspcc", "macmillan", "rspca",
    "princes trust", "salvation army", "st john ambulance", "ymca", "ywca",
    "community trust", "development trust", "action group", "samaritans"
]

def clean_company_name(name: str) -> str:
    if not name:
        return ""
    # Normalize spaces, lowercase, strip common entity suffixes for matching
    clean = name.lower().strip()
    return clean

def is_known_large_enterprise(name: str | None, company_number: str | None = None) -> bool:
    """Returns True if the company name or number matches a known major contractor / large enterprise."""
    if not name:
        return False
    norm_name = clean_company_name(name)
    
    # Check exact/substring matches against known major enterprise names
    for le in KNOWN_LARGE_ENTERPRISES:
        if le in norm_name:
            return True
            
    # Check if company number or name indicates a Public Limited Company (PLC)
    if " plc" in norm_name or norm_name.endswith(" plc") or " public limited company" in norm_name:
        return True

    return False

def is_known_vcse(name: str | None, company_number: str | None = None, company_type: str | None = None) -> bool:
    """Returns True if company details indicate a VCSE (Charity, CIC, Social Enterprise)."""
    if company_type:
        ctype = company_type.lower()
        if any(term in ctype for term in ["charity", "cic", "community interest", "cio", "scio", "registered-society"]):
            return True
            
    if not name:
        return False

    norm_name = clean_company_name(name)
    # NHS bodies (and, more broadly, statutory public bodies) are never VCSE, even though "NHS
    # Foundation Trust" / "NHS Trust" contain the generic "trust"/"foundation" keywords below --
    # those are meant to catch genuine charitable trusts and foundations, not the ~200 statutory
    # NHS trusts whose name happens to include the same words.
    if "nhs" in norm_name:
        return False
    return any(kw in norm_name for kw in KNOWN_VCSE_KEYWORDS)

def classify_supplier_real_data(
    name: str | None,
    company_number: str | None = None,
    ch_data: dict[str, Any] | None = None,
    scraped_sme: str | None = None,
    scraped_vcse: str | None = None
) -> Tuple[str, str, str]:
    """
    Classifies a supplier's SME and VCSE status based on verified criteria.
    
    Returns tuple: (sme_status, vcse_status, verification_reason)
      sme_status options: "SME", "Non-SME Enterprise", "Unclassified"
      vcse_status options: "VCSE", "Non-VCSE", "Unclassified"
    """
    ch_data = ch_data or {}
    comp_name = name or ch_data.get("company_name") or ""
    
    # 1. Hard-coded Major Enterprise Safety Net
    if is_known_large_enterprise(comp_name, company_number):
        return ("Non-SME Enterprise", "Non-VCSE", "Major Contractor / Large Enterprise Registry")
        
    # 2. Inspect VCSE status
    company_type = str(ch_data.get("company_type") or "").lower()
    is_vcse = is_known_vcse(comp_name, company_number, company_type)
    vcse_status = "VCSE" if is_vcse else ("Non-VCSE" if comp_name else "Unclassified")

    # 3. Companies House / Real Accounts Data Inspection
    accounts_info = ch_data.get("accounts") or {}
    accounts_cat = str(accounts_info.get("last_accounts", {}).get("type") or accounts_info.get("category") or "").lower()
    emp_count = ch_data.get("employee_count") or accounts_info.get("employee_count")
    
    # Check if PLC
    if "plc" in company_type or "public limited company" in company_type:
        return ("Non-SME Enterprise", vcse_status, "Verified PLC (Public Limited Company)")
        
    # Check explicit accounts category from Companies House filings
    if accounts_cat in ["full", "group", "large", "parent"]:
        return ("Non-SME Enterprise", vcse_status, f"Verified Large Accounts Category ({accounts_cat})")
        
    if accounts_cat in ["micro-entity", "small", "small-abridged", "total-exemption-small", "unaudited-abridged", "medium"]:
        return ("SME", vcse_status, f"Verified Companies House Accounts Category ({accounts_cat})")
        
    # Check explicit employee count if available
    if emp_count is not None:
        try:
            emp = int(emp_count)
            if emp >= 250:
                return ("Non-SME Enterprise", vcse_status, f"Verified Employee Count ({emp} >= 250)")
            elif emp > 0:
                return ("SME", vcse_status, f"Verified Employee Count ({emp} < 250)")
        except (ValueError, TypeError):
            pass

    # Check scraped/notice metadata ONLY if it explicitly indicates SME/VCSE AND is not unclassified
    # Note: We require scraped SME values to be verified against non-default logic.
    if scraped_sme and str(scraped_sme).strip().upper() in ["SME", "YES", "TRUE"]:
        # Safety double-check: if name sounds like a large corp, don't trust scraped SME
        if not is_known_large_enterprise(comp_name, company_number):
            return ("SME", vcse_status, "Notice Field Scraped SME Flag")
            
    if scraped_sme and str(scraped_sme).strip().upper() in ["NON-SME", "NON-SME ENTERPRISE", "NO", "FALSE"]:
        return ("Non-SME Enterprise", vcse_status, "Notice Field Scraped Non-SME Flag")

    # 4. Default to "Unclassified" if no real criteria or Companies House filings available.
    # NEVER default unverified companies to "SME".
    return ("Unclassified", vcse_status, "Unclassified / No Accounts Category Data")
