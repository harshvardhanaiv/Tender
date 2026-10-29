"""One region name per supplier: the ITL1 regions of the UK and Ireland, worked out from the stored region and the address.

The stored region mixes "UKI - London", "London", "South East England" and plain "UK" (for suppliers that have a full postcode),
so a region filter and the region on a card disagreed. This gives each supplier one of REGIONS, or "UK" when nothing says
where it is."""
from __future__ import annotations

import re

REGIONS = (
    "North East", "North West", "Yorkshire and The Humber", "East Midlands", "West Midlands", "East of England", "London",
    "South East", "South West", "Scotland", "Wales", "Northern Ireland", "Ireland",
)

_AREAS = {
    "North East": "DH DL NE SR TS",
    "North West": "BB BL CA CH CW FY L LA M OL PR SK WA WN",
    "Yorkshire and The Humber": "BD DN HD HG HU HX LS S WF YO",
    "East Midlands": "DE LE LN NG NN",
    "West Midlands": "B CV DY HR ST SY TF WR WS WV",
    "East of England": "AL CB CM CO IP LU NR PE SG SS WD",
    "London": "E EC N NW SE SW W WC BR CR DA EN HA IG KT RM SM TW UB",
    "South East": "BN CT GU HP ME MK OX PO RG RH SL SO TN",
    "South West": "BA BH BS DT EX GL PL SN SP TA TQ TR",
    "Scotland": "AB DD DG EH FK G HS IV KA KW KY ML PA PH TD ZE",
    "Wales": "CF LD LL NP SA",
    "Northern Ireland": "BT",
}
AREA_REGION = {area: region for region, areas in _AREAS.items() for area in areas.split()}

_ALIASES = {
    "north east england": "North East", "north west england": "North West", "yorkshire and the humber": "Yorkshire and The Humber",
    "yorkshire and humber": "Yorkshire and The Humber", "east of england": "East of England", "east anglia": "East of England",
    "south east england": "South East", "south west england": "South West", "london": "London", "greater london": "London",
    "scotland": "Scotland", "wales": "Wales", "northern ireland": "Northern Ireland", "ireland": "Ireland",
    "republic of ireland": "Ireland", "east midlands": "East Midlands", "west midlands": "West Midlands",
    "north east": "North East", "north west": "North West", "south east": "South East", "south west": "South West",
}
_ITL_PREFIX = re.compile(r"^UK[A-Z0-9]{1,2}\s*-\s*", re.I)
_POSTCODE = re.compile(r"\b([A-Z]{1,2})\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.I)


def itl1_region(region: str | None, address: str | None = None) -> str:
    """The supplier's region from REGIONS (or "UK" when neither the region nor the address says)."""
    # The registered address's postcode decides when it names a region: a "region" stored on the record is where contracts were
    # won, so Galliford Try (Uxbridge, UB8) and Mears (Gloucester, GL3) were labelled Scotland and London.
    m = _POSTCODE.search(str(address or ""))
    if m:
        found = AREA_REGION.get(m.group(1).upper())
        if found:
            return found
    cleaned = _ITL_PREFIX.sub("", str(region or "").strip())
    named = _ALIASES.get(cleaned.lower())
    if named:
        return named
    if re.search(r"\b(dublin|cork|galway|limerick|ireland|eire)\b", str(address or ""), re.I):
        return "Ireland"
    return "UK"


# The first word of a company's name, when it is distinctive: "Serco Limited", "Serco Leisure Operating Limited" and "Serco Group
# plc" share it. Used only to list records that look related, labelled as sharing a name, never as proof of ownership.
_GENERIC_FIRST = frozenset("""THE UK NHS BRITISH NATIONAL ROYAL FIRST NEW GLOBAL INTERNATIONAL GROUP CITY COUNTY LONDON UNIVERSITY COLLEGE HOUSING
    CONSTRUCTION SERVICES SERVICE SOLUTIONS CARE HEALTH HEALTHCARE TRUST COMMUNITY ENERGY ENGINEERING TECHNOLOGY TECHNOLOGIES SECURITY
    CLEANING PROPERTY PROJECT PROJECTS MANAGEMENT CONSULTING CONSULTANCY SUPPLY SUPPLIES EDUCATION TRAINING DIGITAL DATA MEDICAL
    SPORTS SPORT LEISURE PROFESSIONAL ASSOCIATES PARTNERS PARTNERSHIP COMPANY WORKS ASSOCIATION FOUNDATION""".split())


def brand_key(name: str | None) -> str:
    text = re.sub(r"[^A-Z0-9 ]+", " ", str(name or "").upper().replace("&", " AND "))
    words = [w for w in text.split() if w]
    if words and words[0] == "THE":
        words = words[1:]
    if not words:
        return ""
    first = words[0]
    return first if len(first) >= 3 and first not in _GENERIC_FIRST and not first.isdigit() else ""
