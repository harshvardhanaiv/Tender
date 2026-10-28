"""Policy templates a bidder is routinely asked for (anti-bribery, modern slavery, equality ...).

The AI writes the policy from the outline below, the company profile and the facts the user gives. It must never invent
what the company does or holds: anything it does not know stays as a bracketed gap such as [add: name of the person
responsible]. The result is a Word file with a document-control block (version, issue date, review date, approved by) that is
saved into the company's Documents with a review date, so the existing expiry alerts remind the user to renew it.

Nothing here is legal advice; every file says so."""
from __future__ import annotations

import io
import re
from datetime import date
from typing import Any

REVIEW_MONTHS = (6, 12, 24)
DEFAULT_REVIEW_MONTHS = 12
_FACT_MAX = 400
_COMMON_FACTS = (
    {"key": "responsible", "label": "Who is responsible for this policy", "hint": "Name and job title, e.g. Jane Smith, Managing Director", "required": False},
)


def _t(key: str, title: str, summary: str, outline: list[tuple[str, str]], facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"key": key, "title": title, "summary": summary, "outline": outline, "facts": list(_COMMON_FACTS) + (facts or [])}


TEMPLATES: dict[str, dict[str, Any]] = {t["key"]: t for t in (
    _t("anti_bribery", "Anti-bribery and corruption policy",
       "Asked in most UK public tenders. Covers the Bribery Act 2010, gifts and hospitality, facilitation payments and reporting.",
       [("Purpose and scope", "Who the policy applies to: employees, directors, agents, subcontractors."),
        ("Our commitment", "Zero tolerance of bribery and corruption; compliance with the Bribery Act 2010."),
        ("What is not acceptable", "Giving, offering, promising, requesting or accepting a bribe; facilitation payments; bribing a foreign public official."),
        ("Gifts and hospitality", "Rules on gifts, hospitality and entertainment: proportionate, transparent, recorded, never to influence a decision."),
        ("Political and charitable donations", "How donations are approved."),
        ("Third parties", "Due diligence on agents, suppliers and subcontractors; contract terms."),
        ("Record keeping and financial controls", "Accurate books, a gifts and hospitality register."),
        ("Raising concerns", "How to report a concern confidentially; no retaliation."),
        ("Training and awareness", "Induction and refresher training."),
        ("Breaches and consequences", "Disciplinary action; reporting to the authorities where required."),
        ("Responsibility and review", "Who owns the policy and when it is reviewed.")]),
    _t("modern_slavery", "Modern slavery statement",
       "Needed for selection questionnaires and, above the turnover threshold, a legal duty under the Modern Slavery Act 2015.",
       [("Our commitment", "Opposition to slavery, servitude, forced labour and human trafficking."),
        ("Our business and supply chains", "What the company does and where it buys from, only as stated in the profile."),
        ("Risk assessment", "Where modern slavery risk may arise in the business and supply chain."),
        ("Due diligence", "Checks on suppliers and labour providers; contract terms; right-to-work checks."),
        ("Policies in place", "Related policies (recruitment, whistleblowing, procurement)."),
        ("Training", "Awareness for staff who buy or recruit."),
        ("Reporting concerns", "How a concern is raised; no retaliation."),
        ("Measuring effectiveness", "How the company will know the steps work."),
        ("Approval", "Approved by the board or senior owner; date.")]),
    _t("equality_diversity", "Equality, diversity and inclusion policy",
       "Almost always required. Covers the Equality Act 2010 protected characteristics, recruitment, pay and complaints.",
       [("Purpose and commitment", "Equal opportunity and a workplace free from discrimination."),
        ("Scope", "Staff, applicants, workers, contractors, customers."),
        ("Protected characteristics", "List the nine protected characteristics under the Equality Act 2010."),
        ("Recruitment and selection", "Fair, merit-based processes and reasonable adjustments."),
        ("Training, progression and pay", "Equal access to development; fair pay."),
        ("Harassment and victimisation", "What is unacceptable and how it is dealt with."),
        ("Reasonable adjustments", "Support for disabled staff and applicants."),
        ("Raising a concern", "Informal and formal routes."),
        ("Monitoring and review", "How the policy is monitored and when it is reviewed.")]),
    _t("health_safety", "Health and safety policy",
       "Required in writing for any employer of five or more people (Health and Safety at Work etc. Act 1974).",
       [("Statement of intent", "Commitment to the health, safety and welfare of staff and others affected."),
        ("Responsibilities", "Directors, managers, employees, and the competent person."),
        ("Risk assessment", "How hazards are identified, assessed and controlled."),
        ("Safe systems of work and method statements", "Only as relevant to the work in the profile."),
        ("Training and competence", "Induction, job-specific training, records."),
        ("Accidents, incidents and near misses", "Reporting, recording, RIDDOR, investigation."),
        ("First aid, fire and emergencies", "Arrangements, evacuation."),
        ("Contractors and visitors", "Control of contractors."),
        ("Consultation and monitoring", "Staff consultation, inspections and audits."),
        ("Review", "When the policy is reviewed and signed.")],
       [{"key": "employees", "label": "Number of employees", "hint": "e.g. 24", "required": False}]),
    _t("data_protection", "Data protection and GDPR policy",
       "Frequently asked. Covers UK GDPR principles, lawful basis, rights, breaches and retention.",
       [("Purpose and scope", "Personal data of staff, customers and others."),
        ("Data protection principles", "The UK GDPR principles."),
        ("Lawful basis and transparency", "Lawful bases, privacy notices."),
        ("Individual rights", "Access, rectification, erasure, restriction, portability, objection; one-month response."),
        ("Security of personal data", "Technical and organisational measures."),
        ("Sharing data and processors", "Contracts with processors; international transfers."),
        ("Personal data breaches", "Internal reporting; ICO notification within 72 hours where required."),
        ("Retention and disposal", "Keeping data only as long as needed."),
        ("Roles", "Data protection lead or officer; ICO registration."),
        ("Review", "When the policy is reviewed.")],
       [{"key": "ico", "label": "ICO registration number", "hint": "Leave blank if not registered", "required": False}]),
    _t("information_security", "Information security policy",
       "Underpins Cyber Essentials and ISO 27001 answers. Covers access, devices, passwords, backups and incidents.",
       [("Purpose and scope", "Information and systems the policy protects."),
        ("Roles and responsibilities", "Owner and user duties."),
        ("Access control", "Least privilege, joiners, movers, leavers, multi-factor authentication."),
        ("Devices and remote working", "Encryption, patching, antivirus, lost devices."),
        ("Passwords and authentication", "Password rules, password manager."),
        ("Backups and recovery", "Frequency and testing."),
        ("Email, web and removable media", "Phishing awareness and acceptable use."),
        ("Suppliers and cloud services", "Due diligence."),
        ("Incident reporting", "How a suspected incident is reported and handled."),
        ("Training and review", "Awareness training and review date.")],
       [{"key": "certs", "label": "Security certifications you hold", "hint": "e.g. Cyber Essentials Plus, ISO 27001. Leave blank if none.", "required": False}]),
    _t("environmental", "Environmental and sustainability policy",
       "Asked in most tenders and scored in many. Covers legal compliance, waste, energy, travel and supply chain.",
       [("Our commitment", "Reducing environmental impact; legal compliance."),
        ("Significant environmental impacts", "Only those relevant to the work in the profile."),
        ("Energy, carbon and travel", "Reducing energy use, emissions and travel."),
        ("Waste and resources", "Reduce, reuse, recycle; hazardous waste."),
        ("Procurement and supply chain", "Preferring sustainable suppliers and products."),
        ("Objectives and targets", "Measurable objectives, only as the user states them; otherwise bracketed gaps."),
        ("Training and engagement", "Staff awareness."),
        ("Monitoring and review", "How progress is measured; review date.")],
       [{"key": "net_zero", "label": "Net zero or carbon target, if any", "hint": "e.g. Net zero by 2035. Leave blank if none.", "required": False}]),
    _t("carbon_reduction_plan", "Carbon reduction plan",
       "Required from bidders for UK central-government contracts above £5m a year (PPN 06/21). Emissions figures are yours to supply.",
       [("Commitment to achieving net zero", "Net zero by 2050 at the latest; the company's own target if stated."),
        ("Baseline emissions", "Table of Scope 1, 2 and the included Scope 3 emissions for the baseline year. Use only figures the user gives; otherwise [add: tCO2e]."),
        ("Current emissions reporting", "Same table for the current year. Only figures the user gives."),
        ("Emissions reduction targets", "Targets as stated by the user; otherwise bracketed gaps."),
        ("Carbon reduction projects", "Measures completed and planned, only as stated by the user."),
        ("Declaration and sign-off", "Confirmation the plan follows the UK Net Zero Standard and is approved by a director.")],
       [{"key": "baseline_year", "label": "Baseline year", "hint": "e.g. 2023", "required": False},
        {"key": "emissions", "label": "Your emissions figures (tCO2e), if you have them", "hint": "Scope 1, 2 and 3 for baseline and current year", "required": False}]),
    _t("safeguarding", "Safeguarding policy",
       "Needed for anything touching children or adults at risk. Covers recognising and reporting concerns and safer recruitment.",
       [("Purpose and scope", "Who is protected and who the policy applies to."),
        ("Our commitment", "Everyone has the right to be safe."),
        ("Types of abuse and harm", "Recognising abuse and neglect."),
        ("Safer recruitment", "References, DBS checks where the role requires them, induction."),
        ("Responding to a concern", "Immediate action, informing the safeguarding lead, recording, referral to statutory bodies."),
        ("Designated safeguarding lead", "Role and contact."),
        ("Confidentiality and information sharing", "Sharing on a need-to-know basis."),
        ("Training", "Awareness and refresher training."),
        ("Review", "When the policy is reviewed.")],
       [{"key": "lead", "label": "Designated safeguarding lead", "hint": "Name and role", "required": False}]),
    _t("business_continuity", "Business continuity policy",
       "Comes up in quality and resilience questions. Covers risks, roles, recovery priorities and testing.",
       [("Purpose and scope", "Keeping critical services running through disruption."),
        ("Risks considered", "Loss of premises, staff, IT, suppliers, utilities, cyber incident."),
        ("Critical activities and recovery priorities", "Only as can be drawn from the profile; otherwise bracketed gaps."),
        ("Roles and communication", "Who decides, who tells customers and staff."),
        ("Recovery arrangements", "Remote working, backups, alternative suppliers."),
        ("Testing and review", "Exercises and review date.")]),
    _t("complaints", "Complaints policy and procedure",
       "Shows how customer complaints are received, investigated and learned from.",
       [("Our commitment", "Welcoming feedback and acting on it."),
        ("What counts as a complaint", "Expressions of dissatisfaction."),
        ("How to complain", "Contact route."),
        ("How we respond", "Acknowledgement and response times, stated only if the user gives them; otherwise bracketed gaps."),
        ("Escalation", "Review by a senior person; external routes where relevant."),
        ("Learning from complaints", "Recording, trends, improvements."),
        ("Review", "When the policy is reviewed.")]),
    _t("social_value", "Social value statement",
       "Scored in many tenders (Social Value Act 2012, PPN 06/20). Describes the local and community benefit you deliver.",
       [("Our approach", "What social value means for this company."),
        ("Local jobs, apprenticeships and skills", "Only commitments or past actions the user states."),
        ("Local supply chain and SMEs", "Buying locally, supporting small firms."),
        ("Environment and carbon", "Link to the environmental policy."),
        ("Community, wellbeing and inclusion", "Volunteering, partnerships, accessibility."),
        ("Measuring and reporting", "How the company measures outcomes.")],
       [{"key": "commitments", "label": "Social value actions you can evidence", "hint": "e.g. 3 apprentices hired in 2025, 8 volunteer days a year", "required": False}]),
)}


def listing() -> list[dict[str, Any]]:
    """What the page needs to show the library: no outlines."""
    return [{"key": t["key"], "title": t["title"], "summary": t["summary"], "facts": t["facts"]} for t in TEMPLATES.values()]


def clean_facts(template: dict[str, Any], raw: Any) -> dict[str, str]:
    """Only the facts this template asks for, as short plain text."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for f in template["facts"]:
        v = raw.get(f["key"])
        if isinstance(v, str) and v.strip():
            out[f["key"]] = re.sub(r"\s+", " ", v).strip()[:_FACT_MAX]
    return out


def review_months(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_REVIEW_MONTHS
    return n if n in REVIEW_MONTHS else DEFAULT_REVIEW_MONTHS


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    last = (date(year + (month == 12), month % 12 + 1, 1) - date.resolution).day
    return date(year, month, min(d.day, last))


_SYSTEM = (
    "You write UK business policies for small and medium companies. Use plain, professional English. Use ONLY the company "
    "information and facts you are given. Never invent facts about the company: its size, services, locations, certifications, "
    "insurance, targets, figures, names or dates. Where a statement needs a fact you do not have, write a bracketed gap such as "
    "[add: name of the person responsible] and list it in gaps. Do not claim the company holds any accreditation unless the facts "
    "say so. Reply with JSON only."
)


def build_prompt(template: dict[str, Any], company: str, profile_text: str, facts: dict[str, str]) -> tuple[str, str]:
    outline = "\n".join(f"{i}. {h}: {g}" for i, (h, g) in enumerate(template["outline"], 1))
    given = "\n".join(f"- {f['label']}: {facts[f['key']]}" for f in template["facts"] if f["key"] in facts) or "None given."
    user = (
        f"Write the policy: {template['title']}.\n\nCOMPANY: {company or 'not named'}\n\nCOMPANY PROFILE:\n{profile_text or 'None given.'}\n\n"
        f"FACTS GIVEN BY THE USER:\n{given}\n\nSECTIONS, in this order, following the guidance:\n{outline}\n\n"
        'Return JSON: {"sections": [{"heading": "the section heading", "paragraphs": ["short paragraph"], "bullets": ["optional list items"]}], '
        '"gaps": ["each bracketed gap the user must fill, in plain words"]}\n'
        "Each section 1 to 3 short paragraphs, plus bullets only where a list reads better. Write in the first person plural (we, our)."
    )
    return _SYSTEM, user


def _text(v: Any, limit: int) -> str:
    return v.strip()[:limit] if isinstance(v, str) else ""


def clean(raw: Any, template: dict[str, Any]) -> dict[str, Any]:
    """The AI's answer cut down to headings, paragraphs and bullets; empty when it gave no usable sections."""
    d = raw if isinstance(raw, dict) else {}
    sections = []
    for s in (d.get("sections") if isinstance(d.get("sections"), list) else [])[:20]:
        if not isinstance(s, dict) or not _text(s.get("heading"), 150):
            continue
        paras = [p for p in (_text(x, 1500) for x in (s.get("paragraphs") if isinstance(s.get("paragraphs"), list) else [])[:6]) if p]
        bullets = [b for b in (_text(x, 400) for x in (s.get("bullets") if isinstance(s.get("bullets"), list) else [])[:10]) if b]
        if paras or bullets:
            sections.append({"heading": _text(s["heading"], 150), "paragraphs": paras, "bullets": bullets})
    gaps = [g for g in (_text(x, 200) for x in (d.get("gaps") if isinstance(d.get("gaps"), list) else [])[:30]) if g]
    return {"sections": sections, "gaps": gaps}


def file_name(company: str, title: str, version: str = "v1") -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "_", f"{company}_{title}").strip("_")[:100] or "Policy"
    return f"{base}_{version}.docx"


def build_docx(template: dict[str, Any], company: str, content: dict[str, Any], issued: date, review: date, approved_by: str = "") -> bytes:
    """The policy as a Word file: title, document control table, the sections, then the review note."""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)
    if company:
        doc.add_paragraph(company).runs[0].bold = True
    doc.add_heading(template["title"], level=0)
    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for k, v in (("Version", "1.0"), ("Issued", issued.strftime("%d %B %Y")), ("Next review", review.strftime("%d %B %Y")),
                 ("Approved by", approved_by or "[add: name and job title]")):
        cells = table.add_row().cells
        cells[0].text, cells[1].text = k, v
    doc.add_paragraph()
    for s in content["sections"]:
        doc.add_heading(s["heading"], level=1)
        for p in s["paragraphs"]:
            doc.add_paragraph(p)
        for b in s["bullets"]:
            doc.add_paragraph(b, style="List Bullet")
    doc.add_paragraph()
    note = doc.add_paragraph("Draft prepared with TenderFlow from your company profile. It is a starting point, not legal advice: check every statement is true for "
                             "your organisation, fill in the [bracketed] gaps and have it approved before you use it.")
    note.runs[0].italic = True
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
