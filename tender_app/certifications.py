"""Step-by-step guides to the certificates and evidence tenders ask for (ISO 9001, Cyber Essentials, SSIP ...).

Static, free help: what the certificate is, why buyers ask, the steps to get it, what to have ready, how long and roughly what
it costs, and which policies TenderFlow can write for the preparation. Costs and times are indicative only and the guide says
so; the user should get quotes. Each guide lists words that, found in a file name in the user's Documents, show they already hold it."""
from __future__ import annotations

from typing import Any

NOTE = "Times and costs are indicative for a small company. They vary by certification body and company size, so get at least three quotes."


def _g(key: str, title: str, short: str, level: str, what: str, why: str, issuer: str, steps: list[tuple[str, str]], needs: list[str],
       time: str, cost: str, renewal: str, pitfalls: list[str], policies: list[str], links: list[tuple[str, str]], doc_words: list[str]) -> dict[str, Any]:
    return {
        "key": key, "title": title, "short": short, "level": level, "what": what, "why": why, "issuer": issuer,
        "steps": [{"title": t, "detail": d} for t, d in steps], "needs": needs, "time": time, "cost": cost, "renewal": renewal,
        "pitfalls": pitfalls, "policies": policies, "links": [{"label": a, "url": b} for a, b in links], "doc_words": doc_words,
    }


_ISO_STEPS = [
    ("Decide the scope", "Which sites, services and staff the certificate will cover. Keep it to what you really do and what the tenders you want ask about."),
    ("Get the standard and do a gap check", "Buy a copy of the standard and compare what you already do with its requirements. List what is missing."),
    ("Write the system", "Write the policy, objectives, procedures and records the standard asks for. Keep it as small as the standard allows and make it describe how you actually work."),
    ("Run it for a few months", "Use the system, keep the records, and train staff. The auditor will want evidence that it is in use, not just written."),
    ("Do an internal audit and a management review", "Check yourself against the standard and have the owners review the results. Both are mandatory and are asked for at the audit."),
    ("Choose a UKAS-accredited certification body", "Ask three for quotes. The certificate only carries weight with buyers if the body is accredited by UKAS (the UK accreditation service)."),
    ("Stage 1 audit, then Stage 2 audit", "Stage 1 checks your documents and readiness. Stage 2 checks the system works in practice. Fix any non-conformities they raise."),
    ("Receive the certificate", "Keep a copy of the certificate and its scope statement. Upload it to Documents here so tenders can use it."),
    ("Keep it alive", "Expect surveillance audits each year and a full recertification audit every three years."),
]
_ISO_NEEDS = ["A named person to own the system", "Your key processes written down", "A few months of records to show", "Budget for a certification body and some time from the team"]

GUIDES: dict[str, dict[str, Any]] = {g["key"]: g for g in (
    _g("iso9001", "ISO 9001 (quality management)", "The most-asked quality certificate in UK tenders.", "asked in most tenders",
       "An international standard for a quality management system: how you plan, deliver, check and improve your work for customers.",
       "Buyers use it as proof you run consistent, controlled processes. Many tenders score it or ask it as a pass/fail question.",
       "A certification body accredited by UKAS. The standard itself is published by ISO.",
       _ISO_STEPS, _ISO_NEEDS, "Typically 3 to 9 months from start to certificate for a small company.",
       "Certification body fees are usually in the low thousands of pounds over three years, plus your time. A consultant, if you use one, is extra.",
       "Certificate lasts 3 years with annual surveillance audits.",
       ["Buying a template system that does not describe how you really work: auditors test it against your actual work.",
        "Choosing a certification body that is not UKAS-accredited: some buyers will not accept it.",
        "Leaving no time to collect records before the audit."],
       ["complaints", "business_continuity"],
       [("ISO 9001 overview", "https://www.iso.org/iso-9001-quality-management.html"), ("Find a UKAS-accredited body", "https://www.ukas.com/find-an-organisation/")],
       ["9001", "iso9001", "iso 9001"]),
    _g("iso14001", "ISO 14001 (environmental management)", "Shows you manage your environmental impact.", "asked in many tenders",
       "An international standard for an environmental management system: how you identify, control and reduce your environmental impacts.",
       "Asked in construction, facilities, waste, transport and many public-sector tenders, and often scored as part of social value and sustainability.",
       "A certification body accredited by UKAS.",
       _ISO_STEPS, _ISO_NEEDS + ["A list of the environmental laws that apply to your work"], "Typically 3 to 9 months for a small company.",
       "Similar to ISO 9001: certification body fees usually in the low thousands of pounds over three years, plus your time.",
       "Certificate lasts 3 years with annual surveillance audits.",
       ["Not knowing which environmental laws apply to you: the auditor will ask.", "Setting targets you do not measure."],
       ["environmental", "carbon_reduction_plan"],
       [("ISO 14001 overview", "https://www.iso.org/iso-14001-environmental-management.html"), ("Find a UKAS-accredited body", "https://www.ukas.com/find-an-organisation/")],
       ["14001", "iso14001", "iso 14001"]),
    _g("iso45001", "ISO 45001 (health and safety management)", "Shows a managed approach to health and safety.", "asked in many tenders",
       "An international standard for an occupational health and safety management system.",
       "Common in construction, facilities, transport and manufacturing tenders. Some buyers accept an SSIP scheme instead, so check the question.",
       "A certification body accredited by UKAS.",
       _ISO_STEPS, _ISO_NEEDS + ["Your risk assessments and accident records"], "Typically 3 to 9 months for a small company.",
       "Certification body fees usually in the low thousands of pounds over three years, plus your time.",
       "Certificate lasts 3 years with annual surveillance audits.",
       ["Going for ISO 45001 when the tender only asks for an SSIP scheme: that is quicker and cheaper.", "Weak accident and near-miss records."],
       ["health_safety", "safeguarding"],
       [("ISO 45001 overview", "https://www.iso.org/iso-45001-occupational-health-and-safety.html"), ("Find a UKAS-accredited body", "https://www.ukas.com/find-an-organisation/")],
       ["45001", "iso45001", "iso 45001"]),
    _g("iso27001", "ISO 27001 (information security)", "The gold standard for information security.", "asked when handling data or IT",
       "An international standard for an information security management system: how you assess and control risks to information.",
       "Asked in software, IT, data and some professional services tenders. It is heavier than Cyber Essentials, so start with that unless the tender names ISO 27001.",
       "A certification body accredited by UKAS.",
       _ISO_STEPS + [("Plan for the Statement of Applicability", "ISO 27001 needs a risk assessment and a Statement of Applicability listing which controls you use and why.")],
       _ISO_NEEDS + ["An inventory of your information assets and systems"], "Typically 6 to 12 months for a small company.",
       "More than ISO 9001: certification body fees usually in the thousands of pounds over three years, plus your time and any tooling.",
       "Certificate lasts 3 years with annual surveillance audits.",
       ["Choosing ISO 27001 when the tender only needs Cyber Essentials.", "An unclear scope: say exactly which systems and services are covered."],
       ["information_security", "data_protection", "business_continuity"],
       [("ISO 27001 overview", "https://www.iso.org/iso-iec-27001-information-security.html"), ("Find a UKAS-accredited body", "https://www.ukas.com/find-an-organisation/")],
       ["27001", "iso27001", "iso 27001"]),
    _g("cyber_essentials", "Cyber Essentials and Cyber Essentials Plus", "The UK government's basic cyber-security certificate.", "asked in most tenders",
       "A government-backed scheme from the National Cyber Security Centre (NCSC), run by IASME. Cyber Essentials is a self-assessment checked by a certification body. Cyber Essentials Plus adds a hands-on technical audit.",
       "Required for many UK central-government contracts that handle personal data or provide IT, and increasingly asked by councils and the NHS.",
       "A certification body licensed by IASME.",
       [("Check what your tender asks for", "Cyber Essentials or Cyber Essentials Plus. Plus costs more and takes longer."),
        ("Know your five controls", "Firewalls, secure configuration, user access control, malware protection and security update management."),
        ("List your devices and networks", "Laptops, phones, servers, cloud services and routers that are in scope."),
        ("Fix the gaps", "Turn on multi-factor authentication, remove unused accounts, update software, set up a firewall, install malware protection."),
        ("Choose an IASME-licensed certification body", "Find one on the IASME website. Many offer a package with help."),
        ("Complete the online self-assessment", "A director or senior person signs it. It is marked by the certification body."),
        ("For Plus: book the technical audit", "An assessor tests a sample of your devices and network from the inside and outside."),
        ("Receive the certificate", "Upload it to Documents here and note the expiry date.")],
       ["A list of your devices, software and cloud services", "Admin access to your devices and router", "A senior person to sign the self-assessment"],
       "Cyber Essentials: often a few days to a few weeks. Plus: add weeks for the audit.",
       "Cyber Essentials usually costs a few hundred pounds for a very small company and rises with size. Plus costs more, often a few thousand pounds.",
       "The certificate lasts 12 months, so renew every year.",
       ["Leaving devices or cloud services out of scope: that can fail the assessment.", "Missed security updates: apply them before you submit.", "Letting it lapse: tenders often need a current certificate."],
       ["information_security", "data_protection"],
       [("Cyber Essentials overview (NCSC)", "https://www.ncsc.gov.uk/cyberessentials/overview"), ("Find a certification body (IASME)", "https://iasme.co.uk/cyber-essentials/")],
       ["cyber essentials", "cyberessentials", "cyber_essentials"]),
    _g("ssip", "Health and safety accreditation (SSIP: CHAS, SafeContractor, Constructionline ...)", "The quick route for health and safety pre-qualification.", "asked in most construction and FM tenders",
       "SSIP (Safety Schemes in Procurement) members such as CHAS, SafeContractor and Constructionline assess your health and safety and give an accreditation that many buyers accept instead of their own questionnaire.",
       "Construction, repairs, facilities and many service tenders ask for one. It is much quicker and cheaper than ISO 45001.",
       "An SSIP member scheme. Check the buyer accepts the scheme you choose.",
       [("Check which schemes your buyers accept", "Look at your tenders, or ask the buyer. Pick one on the SSIP member list."),
        ("Gather your health and safety documents", "Policy, risk assessments, method statements, training records, accident records, insurance certificates."),
        ("Register and submit your application", "Online. You answer questions and upload the documents."),
        ("Respond to the assessor", "They may ask for changes or more evidence."),
        ("Receive the accreditation", "Upload the certificate here and note the expiry date."),
        ("Renew every year", "Most schemes renew annually.")],
       ["A written health and safety policy", "Risk assessments and method statements for your main work", "Training and accident records", "Insurance certificates"],
       "Often 1 to 4 weeks once your documents are ready.",
       "An annual fee that depends on the scheme and your size, often from a few hundred pounds.",
       "Usually renewed every year.",
       ["Choosing a scheme the buyer does not accept.", "Out-of-date insurance or training records."],
       ["health_safety"],
       [("SSIP list of recognised schemes", "https://www.ssip.org.uk/")],
       ["chas", "safecontractor", "safe contractor", "constructionline", "ssip", "exor", "alcumus"]),
    _g("insurance", "Insurance certificates (employer's, public liability, professional indemnity)", "Nearly every tender asks for these.", "asked in most tenders",
       "Evidence of your insurance cover: employer's liability, public liability, and where relevant professional indemnity and product liability.",
       "Buyers set minimum levels in the tender. Employer's liability is also a legal requirement if you employ people (at least £5 million of cover).",
       "An insurer or broker.",
       [("Read the cover levels the tender asks for", "Usually stated per claim. Note which types it needs."),
        ("Ask your broker or insurer", "Say which tenders you are bidding for. They can raise limits and often issue a certificate straight away."),
        ("Get the certificates", "Keep the current ones as PDFs."),
        ("Upload them to Documents here", "Add the expiry date so you get a reminder before they lapse."),
        ("Renew on time", "A lapsed certificate can disqualify a bid.")],
       ["Your current policy documents", "The cover levels each tender asks for"],
       "Usually a few days.",
       "Depends on your trade, turnover and the cover limits. A broker can give a quote quickly.",
       "Usually renewed every year.",
       ["Cover limits below the tender's minimum: raise them before you bid.", "Submitting a certificate that has expired."],
       [],
       [("Employers' liability insurance guide (HSE)", "https://www.hse.gov.uk/pubns/hse40.htm")],
       ["insurance", "liability", "indemnity"]),
)}


def listing(have: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """Every guide, each with the files in the user's Documents that look like it."""
    have = have or {}
    return [{**g, "have": have.get(g["key"], [])} for g in GUIDES.values()]


def match_documents(filenames: list[str]) -> dict[str, list[str]]:
    """Which guide each file name looks like (a hint from the words in the name, not proof)."""
    out: dict[str, list[str]] = {}
    for name in filenames:
        low = (name or "").lower()
        for key, g in GUIDES.items():
            if any(w in low for w in g["doc_words"]):
                out.setdefault(key, []).append(name)
    return {k: v[:3] for k, v in out.items()}
