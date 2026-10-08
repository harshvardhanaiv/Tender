import os
import re
import io
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

def extract_text_from_file(file_path: str, max_chars: int = 50000) -> str:
    """Extracts plain text from PDF, DOCX, TXT, or generic document files."""
    if not os.path.exists(file_path):
        return ""
    
    ext = os.path.splitext(file_path)[1].lower()
    
    try:
        if ext == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(file_path)
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
                if text.strip():
                    return text[:max_chars]
            except Exception:
                pass
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(file_path)
                text = "\n".join(page.get_text() for page in doc)
                if text.strip():
                    return text[:max_chars]
            except Exception:
                pass

        elif ext in (".docx", ".doc"):
            try:
                with zipfile.ZipFile(file_path) as z:
                    if "word/document.xml" in z.namelist():
                        with z.open("word/document.xml") as doc:
                            tree = ET.parse(doc)
                        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                        text = " ".join(el.text for el in tree.iter(f"{{{ns}}}t") if el.text)
                        if text.strip():
                            return text[:max_chars]
            except Exception:
                pass

        # Text / generic file fallback
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(max_chars)
    except Exception:
        return ""

MONTH_MAP = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12
}

def parse_date_str(date_raw: str) -> str:
    """Standardizes various date string formats into YYYY-MM-DD format."""
    if not date_raw:
        return ""
    date_raw = date_raw.strip().rstrip(".,;:")
    # Remove ordinal suffixes (1st, 2nd, 3rd, 4th)
    date_raw = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_raw, flags=re.IGNORECASE)
    
    # Try ISO YYYY-MM-DD
    m = re.search(r'\b(20\d\d|19\d\d)[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])\b', date_raw)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Try DD-MM-YYYY or DD/MM/YYYY
    m = re.search(r'\b(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])[-/.](20\d\d|19\d\d)\b', date_raw)
    if m:
        try:
            dt = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Try DD Month YYYY or Month DD, YYYY
    m1 = re.search(r'\b(0?[1-9]|[12]\d|3[01])\s+([A-Za-z]{3,9})\s+(20\d\d|19\d\d)\b', date_raw)
    if m1:
        day, mon_str, year = int(m1.group(1)), m1.group(2).lower(), int(m1.group(3))
        if mon_str in MONTH_MAP:
            try:
                dt = datetime(year, MONTH_MAP[mon_str], day)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

    m2 = re.search(r'\b([A-Za-z]{3,9})\s+(0?[1-9]|[12]\d|3[01])\s*,\s*(20\d\d|19\d\d)\b', date_raw)
    if m2:
        mon_str, day, year = m2.group(1).lower(), int(m2.group(2)), int(m2.group(3))
        if mon_str in MONTH_MAP:
            try:
                dt = datetime(year, MONTH_MAP[mon_str], day)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

    return ""

def auto_extract_document_dates(file_path: str) -> tuple[str, str]:
    """
    Extracts (issue_date, expiry_date) from document text in YYYY-MM-DD format.
    Returns (issue_date, expiry_date) strings (empty if not found).
    """
    text = extract_text_from_file(file_path)
    if not text:
        return "", ""

    issue_date = ""
    expiry_date = ""

    lines = text.splitlines()
    
    # Regex keywords
    issue_keywords = [
        r'issue\s*date', r'issued\s*date', r'date\s*of\s*issue', r'issued\s*on',
        r'award\s*date', r'date\s*of\s*award', r'registration\s*date', r'certified\s*on',
        r'effective\s*date', r'commencement\s*date', r'created\s*date', r'signed\s*date', r'dated'
    ]
    
    expiry_keywords = [
        r'expir(?:y|ation)\s*date', r'expires', r'valid\s*until', r'valid\s*up\s*to',
        r'validity', r'expires\s*on', r'due\s*date', r'valid\s*through', r'completion\s*date',
        r'end\s*date', r'valid\s*to'
    ]

    issue_pattern = re.compile(r'(?:' + '|'.join(issue_keywords) + r')[\s:\-\=]+([^\n\r,;]{5,30})', re.IGNORECASE)
    expiry_pattern = re.compile(r'(?:' + '|'.join(expiry_keywords) + r')[\s:\-\=]+([^\n\r,;]{5,30})', re.IGNORECASE)

    # Search in text lines
    for line in lines:
        if not issue_date:
            m = issue_pattern.search(line)
            if m:
                parsed = parse_date_str(m.group(1))
                if parsed:
                    issue_date = parsed

        if not expiry_date:
            m = expiry_pattern.search(line)
            if m:
                parsed = parse_date_str(m.group(1))
                if parsed:
                    expiry_date = parsed

    # Full text scan if line-by-line misses
    if not issue_date:
        m = issue_pattern.search(text)
        if m:
            issue_date = parse_date_str(m.group(1))

    if not expiry_date:
        m = expiry_pattern.search(text)
        if m:
            expiry_date = parse_date_str(m.group(1))

    # Fallback: if dates are present in document text but keyword was ambiguous
    if not issue_date or not expiry_date:
        all_dates = []
        date_regex = re.compile(r'\b(?:(?:20\d\d|19\d\d)[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])|(?:0?[1-9]|[12]\d|3[01])[-/.](?:0?[1-9]|1[0-2])[-/.](?:20\d\d|19\d\d)|(?:0?[1-9]|[12]\d|3[01])\s+[A-Za-z]{3,9}\s+(?:20\d\d|19\d\d))\b')
        matches = date_regex.findall(text)
        for d_str in matches:
            p = parse_date_str(d_str)
            if p and p not in all_dates:
                all_dates.append(p)
        
        all_dates.sort()
        if all_dates:
            if not issue_date and len(all_dates) >= 1:
                issue_date = all_dates[0]
            if not expiry_date and len(all_dates) >= 2:
                expiry_date = all_dates[-1]

    return issue_date, expiry_date
