"""Who the applicant of a planning application is (Re-test 4 P2).

PlanIt carries an applicant for only a small share of applications (316 of 38,000 held, 90 of the 6,370 large schemes), because most
councils show the agent in its place ("C/O Agent", "See source") or nothing. Three honest sources are used, in this order:

  1. what the register published, with its placeholders removed         clean_party()
  2. a company the description itself names ("... on behalf of X Ltd")   applicant_from_description()
  3. on request, the council's own "Further Information" page            fetch_council_applicant()

Nothing is guessed: a result says where it came from, and when none names a company the lead shows the agent and a way to look.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# What a register shows when it does not publish the applicant.
_PLACEHOLDER = re.compile(
    r"^\s*(?:c/?o\.?\s*(?:the\s+)?agent|care of agent|see (?:source|agent|application|documents?|form)|not (?:available|published|stated|provided|given)|"
    r"confidential|withheld|redacted|n/?a|none|unknown|tbc|-+|\.+|as agent|same as agent|agent)\s*$", re.I)
_COMPANY_FORM = re.compile(r"\b(?:ltd|limited|llp|plc|llc|lp|inc|cic|group|holdings|homes|developments?|estates|properties|housing|partnership|trust|council|"
                           r"association|university|college|nhs|foundation)\b", re.I)
_PERSONAL = re.compile(r"^\s*(?:mr|mrs|ms|miss|mx|dr|sir|lord|lady|prof)\b\.?", re.I)

# "on behalf of Taylor Wimpey UK Ltd", "submitted by Riverdale Developments". A bare "for X Homes" is not used: it also matches
# "Code for Sustainable Homes", a planning condition.
_NAMED = re.compile(
    r"\b(?:on behalf of|submitted by|applicant(?: is)?:?)\s+((?:[A-Z][A-Za-z0-9&'’.\-]*\s+){0,4}[A-Z][A-Za-z0-9&'’.\-]*\s+"
    r"(?:Ltd|Limited|LLP|PLC|Plc|Homes|Developments|Holdings|Group|Estates|Properties|Housing|Partnership|Trust|Council)\b)")


def clean_party(value: Any) -> str | None:
    """The applicant exactly as published, or None when the register left a placeholder there."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or _PLACEHOLDER.match(text):
        return None
    return text


def is_company(value: str | None) -> bool:
    """A company or organisation, not a private individual (whose name is not used for outreach)."""
    text = (value or "").strip()
    return bool(text) and bool(_COMPANY_FORM.search(text)) and not (_PERSONAL.match(text) and not re.search(r"\b(?:ltd|limited|llp|plc)\b", text, re.I))


def applicant_from_description(description: str | None) -> str | None:
    """A company the application's own description names as the one it is made for or by."""
    m = _NAMED.search(str(description or ""))
    if not m:
        return None
    name = m.group(1).strip().rstrip(".,")
    return name if is_company(name) else None


def resolve(row: dict[str, Any]) -> tuple[str | None, str | None]:
    """(applicant company, where it came from: "register" or "description"); (None, None) when no company is named."""
    published = clean_party(row.get("applicant_company"))
    if published:
        return published, "register"
    found = applicant_from_description(row.get("description"))
    return (found, "description") if found else (None, None)


def details_url(detail_url: str | None) -> str | None:
    """The Idox Public Access "Further Information" tab for an application whose record is on an Idox portal; None for any other."""
    url = str(detail_url or "")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or "/online-applications/applicationdetails.do" not in url.lower():
        return None
    return re.sub(r"activeTab=\w+", "activeTab=details", url, flags=re.I) if "activeTab=" in url else url + "&activeTab=details"


def parse_details_page(html: str) -> dict[str, str]:
    """Applicant and agent as an Idox "Further Information" page lists them, placeholders removed."""
    from bs4 import BeautifulSoup

    out: dict[str, str] = {}
    soup = BeautifulSoup(html or "", "lxml")
    for tr in soup.select("table#applicationDetails tr"):
        th, td = tr.find("th"), tr.find("td")
        if not th or not td:
            continue
        label = th.get_text(" ", strip=True).lower()
        value = clean_party(td.get_text(" ", strip=True))
        if not value:
            continue
        if label in ("applicant name", "applicant", "applicant company name", "applicant company"):
            out.setdefault("applicant", value)
        elif label in ("agent company name", "agent company"):
            out.setdefault("agent_company", value)
    return out


def fetch_council_applicant(detail_url: str | None, timeout: int = 15) -> dict[str, Any]:
    """Ask the council's own page who the applicant is. {"applicant": name|None, "reason": why not}. One request; a council that refuses,
    is slow or hides the applicant gives a reason and no name."""
    import requests

    url = details_url(detail_url)
    if not url:
        return {"applicant": None, "reason": "The council's record is not on a portal this can read. Open it from the link."}
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (TenderFlow planning lookup)"})
    except requests.RequestException:
        return {"applicant": None, "reason": "The council's site did not answer. Open the record from the link."}
    if resp.status_code != 200:
        return {"applicant": None, "reason": f"The council's site answered {resp.status_code}. Open the record from the link."}
    found = parse_details_page(resp.text)
    if found.get("applicant"):
        return {"applicant": found["applicant"], "reason": None, "company": is_company(found["applicant"])}
    return {"applicant": None, "reason": "The council does not publish the applicant for this application (it shows the agent)."}
