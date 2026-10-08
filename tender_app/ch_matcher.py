"""
Companies House Registration Number Validator & High-Confidence Name Matcher
Enforces strict UK Companies House registration number formats and ensures supplier
contact details are matched with high confidence (>=85% similarity), preventing
contact detail leaks between unrelated companies.
"""

import re
import difflib
from typing import Any, Tuple

# UK Companies House registration number patterns:
# - 8 digits (e.g. 00488342, 09823411)
# - 2 letters + 6 digits (e.g. SC129481, NI048123, OC454533, SO123456, CE123456, CS123456, FC123456, R0123456)
CH_REG_REGEX = re.compile(r"^(?:[A-Z]{2}\d{6}|\d{8})$", re.IGNORECASE)

def normalize_ch_company_number(cnum: Any) -> str | None:
    """
    Validates and normalizes a Companies House registration number.
    Returns normalized 8-character string (e.g. '00488342' or 'SC129481') if valid,
    or None if invalid (e.g. 'Procurem', 'Public', 'CF94469', '1 added:').
    """
    if not cnum:
        return None
        
    s = str(cnum).strip().upper()
    s = re.sub(r"^#", "", s).strip()
    
    if not s or s.lower() in ("n/a", "none", "null", "not available", "unknown", "public", "procurem"):
        return None

    # "CF"/"UK"/"BR" are this app's own synthetic placeholder prefixes (Contracts Finder
    # supplier IDs and the fallback-hash generator in etenders_scraper/awards.py, "BR" being
    # the Bravo/Scotland-Wales portal fallback) — never a real Companies House prefix.
    # Without this check, a placeholder whose digit part happens to be 6 digits long
    # (e.g. "CF132995", "BR482913") slips past the generic 2-letter+6-digit regex below
    # and gets treated as a verified CH number, which then blocks it from ever converging
    # with the same supplier's real CH row (resolve_supplier_company_number stops at step 1
    # instead of falling through to the by-name lookup).
    if s[:2] in ("CF", "UK", "BR"):
        return None

    # Auto-pad pure digits if 6 or 7 digits long
    if s.isdigit():
        if len(s) in (6, 7):
            s = s.zfill(8)
            
    # Check against official Companies House registration regex
    if CH_REG_REGEX.match(s):
        return s
        
    return None

def is_valid_ch_company_number(cnum: Any) -> bool:
    """Returns True if cnum is a valid 8-character UK Companies House registration number."""
    return normalize_ch_company_number(cnum) is not None

def clean_name_for_matching(name: str) -> str:
    """Strips legal entity suffixes, punctuation, noise conjunctions, and extra spaces for high-confidence matching."""
    if not name:
        return ""
    s = name.lower().strip()
    # Remove common legal suffixes and noise words (including conjunctions like and, &)
    s = re.sub(r"\b(ltd|limited|plc|llp|dac|inc|corp|co|company|the|t/a|and|of|for|services|group)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def calculate_name_similarity(target_name: str | None, candidate_name: str | None) -> float:
    """
    Calculates similarity ratio between target supplier name and candidate registry name.
    Returns float ratio between 0.0 and 1.0.
    """
    if not target_name or not candidate_name:
        return 0.0
        
    raw1 = target_name.lower().strip()
    raw2 = candidate_name.lower().strip()
    
    if raw1 == raw2:
        return 1.0
        
    c1 = clean_name_for_matching(target_name)
    c2 = clean_name_for_matching(candidate_name)
    
    if not c1 or not c2:
        return 0.0
        
    if c1 == c2:
        return 1.0
        
    words1 = c1.split()
    words2 = c2.split()
    set1 = set(words1)
    set2 = set(words2)
    
    # Calculate word jaccard / overlap score
    common_words = set1 & set2
    max_words = max(len(set1), len(set2))
    min_words = min(len(set1), len(set2))
    
    # If all words match exactly in both sets
    if set1 == set2:
        return 1.0
        
    # SequenceMatcher ratio on cleaned names
    sm_ratio = difflib.SequenceMatcher(None, c1, c2).ratio()
    
    # Word overlap ratio
    overlap_ratio = len(common_words) / max_words if max_words > 0 else 0.0
    
    # If one is a subset, score is high only if overlap covers >= 85% of total words
    if (set1.issubset(set2) or set2.issubset(set1)) and overlap_ratio >= 0.85:
        return round(max(sm_ratio, 0.90), 3)
        
    return round(sm_ratio, 3)

def is_high_confidence_match(target_name: str | None, candidate_name: str | None, threshold: float = 0.85) -> bool:
    """Returns True if candidate name matches target supplier name with similarity >= threshold (default 85%)."""
    score = calculate_name_similarity(target_name, candidate_name)
    return score >= threshold

def clean_contact_field(value: Any) -> str:
    """Normalizes contact fields (email, phone, website, address). Returns empty string if unlisted, not available, or invalid."""
    if not value:
        return ""
    s = str(value).strip()
    if not s or s.lower() in ("n/a", "none", "null", "not available", "unknown", "-", ""):
        return ""
    return s

