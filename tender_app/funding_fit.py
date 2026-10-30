"""Plain-rule eligibility guide for a grant card: Likely eligible / Check eligibility / Unlikely to qualify.

Rules only, no AI and no credits. The applicant comes from the company profile (type, size, sector, turnover, postcode)
overridden by what the user set in the page filters. Every verdict carries one line saying why, so it is a guide the
user can check, not a prediction: the card tells them to confirm with the funder."""
from __future__ import annotations

import re
from typing import Any

LIKELY_WORDS = 2
UK_WIDE = "National"
NATIONS = ("England", "Scotland", "Wales", "Northern Ireland")
ENGLISH_REGIONS = ("North East England", "North West England", "South East England", "South West England", "Midlands")
# Display name -> the grant location it matches in addition to "England" (London, East and Yorkshire have no regional grants).
_REGION_GRANT = {
    "North East England": "North East England", "North West England": "North West England",
    "South East England": "South East England", "South West England": "South West England",
    "Midlands": "Midlands", "East Midlands": "Midlands", "West Midlands": "Midlands",
}
_AREAS = {
    "North East England": "DH DL NE SR TS",
    "North West England": "BB BL CA CH CW FY L LA M OL PR SK WA WN",
    "Yorkshire and the Humber": "BD DN HD HG HU HX LS S WF YO",
    "Midlands": "B CV DE DY HR LE LN NG NN ST SY TF WR WS WV",
    "East of England": "AL CB CM CO IP LU NR PE SG SS WD",
    "London": "E EC N NW SE SW W WC BR CR DA EN HA IG KT RM SM TW UB",
    "South East England": "BN CT GU HP ME MK OX PO RG RH SL SO TN",
    "South West England": "BA BH BS DT EX GL PL SN SP TA TQ TR",
    "Scotland": "AB DD DG EH FK G HS IV KA KW KY ML PA PH TD ZE",
    "Wales": "CF LD LL NP SA",
    "Northern Ireland": "BT",
}
AREA_REGION = {area: region for region, areas in _AREAS.items() for area in areas.split()}
_APPLICANTS = {"private": "Private Sector", "public": "Private Sector", "partnership": "Private Sector", "sole_trader": "Private Sector"}
APPLICANT_LABEL = {"Private Sector": "a private company", "Non-profit": "a non-profit", "Public Sector": "a public body",
                   "Personal / Individual": "an individual"}
_SIZE_NAME = {"micro": "micro", "small": "small", "medium": "medium", "large": "large"}
_FX = {"£": 1.0, "$": 0.8, "€": 0.85}
_SCALE = {"": 1.0, "k": 1e3, "m": 1e6, "million": 1e6, "bn": 1e9}

# Topics a company must plausibly work in: (grant text, words that make the profile a fit, label, what a miss means).
_TOPICS = (
    (re.compile(r"engineering biology|synthetic biology|biotech", re.I), re.compile(r"bio|life science|pharma|laborator|genom", re.I), "engineering biology", "blocker"),
    (re.compile(r"life sciences?|medicines?|clinical|nhs", re.I), re.compile(r"life science|health|medical|clinical|pharma|bio|nhs", re.I), "life sciences", "blocker"),
    (re.compile(r"\bfarm(?:s|er|ers|ing)?\b|agricultur|forestry|woodland|\bgrowers?\b|landowners?|land managers?", re.I), re.compile(r"farm|agricultur|forest|woodland|grower|horticult|land", re.I), "farming, growing or forestry", "blocker"),
    (re.compile(r"fisher(?:y|ies)|seafood|aquaculture", re.I), re.compile(r"fish|seafood|aquacult|marine", re.I), "fisheries and seafood", "blocker"),
    (re.compile(r"satellite|earth observation|space (?:data|sector|industry)|\bspace\b", re.I), re.compile(r"space|satellite|geospatial|earth observation|remote sensing", re.I), "space", "check"),
)


# Subjects a company in each business-type letter (NACE) plausibly works on, matched against a grant's name and opening summary.
_SECTOR_TOPICS = {
    "J": r"\bai\b|artificial intelligence|machine learning|software|digital|\bdata\b|cyber|computing|quantum|telecom|semiconductor",
    "C": r"manufactur|engineering|advanced materials|automotive|aerospace|\bproduction\b|industrial|supply chain",
    "M": r"consult|professional services|management services",
    "F": r"construction|retrofit|housing|heat pump|boiler|cladding|insulation|infrastructure|civil engineering|heat network|roofing|energy efficiency|building safety|building regulations",
    "A": r"farm|agricultur|forestry|woodland|horticult|food production",
    "D": r"energy|net zero|hydrogen|renewable|power|nuclear|decarbon",
    "E": r"waste|recycl|water|circular economy|environment",
    "H": r"transport|freight|logistics|vehicle|charging|rail|aviation|maritime",
    "I": r"hospitality|tourism|food|culture|visit",
    "Q": r"health|care|clinical|\bnhs\b|medic|social care",
    "G": r"retail|export|trade|supply chain|consumer",
    "K": r"finance|fintech|insurance|investment|banking",
    "L": r"housing|property|real estate|regeneration|landlord",
    "N": r"employment|skills|recruit|apprentic|jobs",
    "P": r"education|school|training|skills|learning",
    "R": r"culture|arts|music|creative|heritage|sport",
}
_GENERIC = frozenset("""based business deliver ensure costs active including enabling enterprise allowed complete drive clear driven
    enable support project projects grant funding fund scheme programme apply applicants applicant eligible organisation organisations
    sector services service work working new help helps provide provides improve improving range
    companies company registered private address office offices operating operates marketing incorporated number director directors
    england wales scotland ireland united kingdom limited trading established includes include including various general
    organisation organisations national regional local community people customers customer market markets industry industries""".split())


def affinity_reason(grant: dict[str, Any], applicant: dict[str, Any]) -> str:
    """Why the grant is about what the company does, or "" when there is no real evidence. Only the grant's name and
    opening summary count: a subject typical of the company's business type, or two or more distinctive profile words.
    Words found deep in the eligibility text are everyday wording and prove nothing."""
    head = " ".join(filter(None, (grant.get("name"), (grant.get("summary") or "")[:300])))
    topic = _SECTOR_TOPICS.get(applicant.get("sector") or "")
    if topic and re.search(topic, head, re.I):
        title = sector_title(applicant.get("sector") or "")
        return "Fits your sector" + (f" ({title})" if title else "")
    return ""


_SECTOR_LABEL = {
    "D": "energy and net zero", "E": "waste, water and the environment", "H": "transport and logistics", "I": "hospitality and tourism",
    "Q": "health and care", "G": "retail and trade", "K": "finance", "L": "housing and property", "N": "employment and skills",
    "P": "education and training", "R": "culture, arts and sport", "F": "construction", "A": "agriculture", "C": "manufacturing",
}
# Programmes for people and communities, not for a company (matched on the grant's name only, so the call is deliberate).
_NOT_FOR_COMPANIES = re.compile(r"elected office|\belection|candidates?\b|domestic abuse|refugee|asylum|veterans?\b|bereave|foster|\bcarers?\b|food bank|homeless", re.I)


def off_sector_reason(grant: dict[str, Any], applicant: dict[str, Any]) -> str:
    """Why a grant is about something other than this company's business, or "" when nothing clear says so. Only the grant's
    name is read, and only a subject typical of another business type counts (Re-test 3 FN4: a "Check" with nothing to check)."""
    name = grant.get("name") or ""
    if _NOT_FOR_COMPANIES.search(name):
        return "Aimed at people and communities, not companies"
    sector = (applicant.get("sector") or "").upper()
    if not sector:
        return ""
    for letter, rx in _SECTOR_TOPICS.items():
        if letter != sector and letter in _SECTOR_LABEL and re.search(rx, name, re.I):
            return "Aimed at " + _SECTOR_LABEL[letter] + ", which is not your sector" + (f" ({sector_title(sector)})" if sector_title(sector) else "")
    return ""


def has_affinity(grant: dict[str, Any], applicant: dict[str, Any]) -> bool:
    return bool(affinity_reason(grant, applicant))


def sector_title(letter: str) -> str:
    return {"A": "agriculture", "B": "mining", "C": "manufacturing", "F": "construction", "J": "information and communication",
            "M": "professional, scientific and technical", "Q": "health"}.get((letter or "").upper(), "")


def parse_money(raw: Any) -> float | None:
    """£ turnover from the free text a profile holds: 1.8m, £1,800,000, €1.5M."""
    m = re.search(r"([£$€])?\s*([\d][\d,]*(?:\.\d+)?)\s*(k|m|million|bn)?", str(raw or "").strip(), re.I)
    if not m:
        return None
    try:
        value = float(m.group(2).replace(",", ""))
    except ValueError:
        return None
    return value * _SCALE.get((m.group(3) or "").lower(), 1.0) * _FX.get(m.group(1) or "£", 1.0)


def money(n: float) -> str:
    if n >= 1e6:
        return "£" + f"{n / 1e6:.1f}".rstrip("0").rstrip(".") + "m"
    if n >= 1e3:
        return "£" + f"{n / 1e3:.1f}".rstrip("0").rstrip(".") + "k"
    return "£" + str(int(n))


def region_of(meta: dict[str, Any]) -> str:
    """The nation or English region a profile sits in, from its postcode (else its county); '' when unknown or not UK."""
    country = str(meta.get("country") or "").strip().upper()
    if country and country not in ("GB", "UK", "UNITED KINGDOM", "ENGLAND", "SCOTLAND", "WALES", "NORTHERN IRELAND"):
        return ""
    m = re.match(r"\s*([A-Za-z]{1,2})\d", str(meta.get("postcode") or ""))
    return AREA_REGION.get(m.group(1).upper(), "") if m else ""


def nation_of(region: str) -> str:
    return region if region in ("Scotland", "Wales", "Northern Ireland") else ("England" if region else "")


def applicant_from_profile(meta: dict[str, Any] | None, text: str = "", name: str = "") -> dict[str, Any]:
    meta = meta or {}
    country = str(meta.get("country") or "").strip().upper()
    sector = str(meta.get("business_type") or "").strip().upper()
    return {
        "kind": _APPLICANTS.get(str(meta.get("company_type") or ""), ""),
        "size": _SIZE_NAME.get(str(meta.get("enterprise_type") or ""), ""),
        "region": region_of(meta),
        "abroad": bool(country) and country not in ("GB", "UK", "UNITED KINGDOM", "ENGLAND", "SCOTLAND", "WALES", "NORTHERN IRELAND"),
        "turnover": parse_money(meta.get("turnover")),
        "sector": sector,
        "about": " ".join(filter(None, (text, name, sector_title(sector)))),
    }


def covers(locations: list[str], where: str, include_uk_wide: bool = True) -> bool:
    """Whether a grant's locations take in the place the work is done (where is a nation or an English region; '' is anywhere)."""
    if not where:
        return True
    locs = set(locations or [])
    if UK_WIDE in locs:
        return include_uk_wide
    if where in locs:
        return True
    if where in ("Scotland", "Wales", "Northern Ireland"):
        return False
    if where == "England":
        return "England" in locs or bool(locs & set(ENGLISH_REGIONS))
    return "England" in locs or _REGION_GRANT.get(where, "") in locs


def where_label(locations: list[str]) -> str:
    locs = list(locations or [])
    if UK_WIDE in locs:
        return "UK-wide"
    return ", ".join(locs)


def _project_cost_floor(text: str) -> float | None:
    m = re.search(r"project[s]?\s+(?:costing|costs?|worth|of|with a (?:total )?cost of)\s+(?:at least |over |more than |a minimum of )?([£$€])\s?([\d,.]+)\s?(k|m|million|bn)?\s+or more", text, re.I)
    if not m:
        # "eligible projects must exceed £100 million in total costs" / "a total cost (including capital) of £100 million or more"
        m = (re.search(r"(?:exceed|over|above|at least|minimum of)\s+([£$€])\s?([\d,.]+)\s?(k|m|million|bn)?\s+in\s+total\s+costs?", text, re.I)
             or re.search(r"total\s+costs?\b[^.£$€]{0,120}?(?:of|at least)\s+([£$€])\s?([\d,.]+)\s?(k|m|million|bn)?\s+or\s+(?:more|above|over)", text, re.I))
    if not m:
        return None
    try:
        return float(m.group(2).replace(",", "")) * _SCALE.get((m.group(3) or "").lower(), 1.0) * _FX.get(m.group(1), 1.0)
    except ValueError:
        return None


def _facts_to_check(text: str) -> list[str]:
    """Hard numbers a grant states that the applicant has to meet, as plain checks (project cost range, project length)."""
    out: list[str] = []
    m = re.search(r"total costs? between ([£$€])\s?([\d,.]+)\s?(k|m|million)?\s+and\s+([£$€])?\s?([\d,.]+)\s?(k|m|million)?", text, re.I)
    if m:
        try:
            lo = float(m.group(2).replace(",", "")) * _SCALE.get((m.group(3) or "").lower(), 1.0) * _FX.get(m.group(1), 1.0)
            hi = float(m.group(5).replace(",", "")) * _SCALE.get((m.group(6) or m.group(3) or "").lower(), 1.0) * _FX.get(m.group(4) or m.group(1), 1.0)
            out.append("Project costs must be between " + money(lo) + " and " + money(hi))
        except ValueError:
            pass
    m = re.search(r"last(?:s)? between (\d+) and (\d+) (months?|years?)", text, re.I)
    if m:
        out.append(f"Project must last {m.group(1)} to {m.group(2)} {m.group(3).lower()}")
    return out


def assess(grant: dict[str, Any], applicant: dict[str, Any], *, kind: str = "", where: str = "", has_profile: bool = False) -> dict[str, str]:
    """{'verdict': likely|check|unlikely, 'why'|'check'|'blocker': one line}. `kind` and `where` are the page filters."""
    kind = kind or applicant.get("kind") or ""
    region = where or applicant.get("region") or ""
    locs = list(grant.get("locations") or [])
    text = " ".join(filter(None, (grant.get("name"), grant.get("summary"), grant.get("eligibility"))))
    head = " ".join(filter(None, (grant.get("name"), (grant.get("summary") or "")[:400], (grant.get("eligibility") or "")[:500])))
    blockers: list[str] = []
    checks: list[str] = []
    whys: list[str] = []

    types = list(grant.get("applicants") or [])
    if kind and types and kind not in types:
        blockers.append("Open to " + ", ".join(types).lower() + " applicants; you are applying as " + APPLICANT_LABEL.get(kind, kind.lower()))

    if locs and set(locs) <= {"International"}:
        # GOV.UK tags a call "International" when the partner is abroad; the applicant is still a UK business, so this is never a
        # location blocker, only something to confirm.
        checks.append("International collaboration: the project needs a partner outside the UK. Read the call for which countries")
    elif UK_WIDE in locs:
        if applicant.get("abroad"):
            checks.append("UK-wide, but your profile address is outside the UK: confirm you can apply")
        else:
            whys.append("UK wide")
    elif locs:
        if applicant.get("abroad") and not where:
            blockers.append("Only open in " + where_label(locs) + "; your profile address is outside the UK")
        elif region and not covers(locs, region):
            blockers.append("Only open in " + where_label(locs) + "; you're based in " + region)
        elif not region:
            checks.append("Only open in " + where_label(locs) + ": add a postcode to your profile to check")
        else:
            whys.append(where_label(locs))

    size = applicant.get("size")
    if re.search(r"\bsmes?\b|small and medium", head, re.I):
        if size == "large":
            blockers.append("For SMEs (under 250 staff); your profile says large")
        elif size:
            whys.append("SME (under 250 staff)")
        else:
            checks.append("For SMEs (under 250 staff): add your company size to your profile")

    about = applicant.get("about") or ""
    for rx, affinity, label, mode in _TOPICS:
        if rx.search(grant.get("name") or "") or rx.search((grant.get("summary") or "")[:300]):
            if about and not affinity.search(about):
                if mode == "blocker":
                    blockers.append(("Must be a " + label + " business" if label.startswith("farming") else "Sector mismatch: " + label))
                else:
                    checks.append("Project must use " + ("satellite data" if label == "space" else label) + ": confirm you have a use case")
            break

    floor = _project_cost_floor(text)
    turnover = applicant.get("turnover")
    if floor and turnover and floor > 10 * turnover:
        blockers.append("Needs a project costing " + money(floor) + " or more: your turnover is " + money(turnover))
    elif floor and not turnover:
        checks.append("Your project must cost " + money(floor) + " or more in total. Confirm before applying.")

    if re.search(r"18 to 24|aged 18", text, re.I) and re.search(r"new (?:role|job)|create", text, re.I):
        checks.append("You must create a new role for an 18 to 24 year old")
    if re.search(r"\b20%.{0,80}(growth|grow)|(growth|grow).{0,80}\b20%", text, re.I):
        checks.append("Needs 20%+ annual growth in staff or turnover for 3 years: not in your profile")
    funder = str(grant.get("funder") or "")
    research_council = bool(re.search(r"UKRI individual eligibility|research and innovation communities|research organisations? eligible for UKRI|may (?:only )?join as (?:a )?partner", text, re.I))         and not re.match(r"\s*Innovate UK\b", funder)
    # A grant that says the applicant must BE a research organisation: a company can only be a project partner.
    must_be_ro = re.search(r"must be based at (?:a |an )?(?:uk )?(?:research|higher education)|eligible for (?:EPSRC|UKRI|BBSRC|MRC|NERC|STFC|AHRC|ESRC)\s+funding|\bresearch organisation \(RO\)|must be (?:a |an )?(?:uk )?research organisation", text, re.I)
    if must_be_ro:
        checks.insert(0, "A research organisation must lead: you can be a project partner. Confirm with the funder")
    elif research_council or re.search(r"led by (?:a |an )?(?:uk )?(?:research|university|academic)|research organisations? (?:to )?lead|research-led", text, re.I):
        checks.append("Usually led by a research organisation or university: a business may be able to join as a partner. Confirm with the funder")

    for line in _facts_to_check(text):
        checks.append(line)

    if blockers:
        return {"verdict": "unlikely", "blocker": blockers[0], "blockers": blockers, "checks": checks, "reasons": whys}
    if checks:
        return {"verdict": "check", "check": checks[0], "checks": checks, "blockers": [], "reasons": whys}
    # "Likely" needs real evidence the work fits, not just no obstacle.
    reason = affinity_reason(grant, applicant) if has_profile else ""
    if reason and whys:
        return {"verdict": "likely", "why": " · ".join([reason] + whys), "reasons": [reason] + whys, "checks": [], "blockers": []}
    if has_profile:
        off = off_sector_reason(grant, applicant)
        if off:
            return {"verdict": "unlikely", "blocker": off, "blockers": [off], "checks": [], "reasons": whys}
        seen = ("Matched so far: " + " · ".join(whys) + ". ") if whys else ""
        line = seen + "Nothing in your profile rules it out, but nothing in the grant points to your sector either: read the full criteria"
        # a "Check" always carries the thing to check, so the card never shows a verdict with nothing under it
        return {"verdict": "check", "check": line, "checks": [line], "blockers": [], "reasons": whys}
    line = "Add a company profile to check this against your business"
    return {"verdict": "check", "check": line, "checks": [line], "blockers": [], "reasons": whys}
