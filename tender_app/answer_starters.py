"""Answer Bank starter packs: the standard questions UK tenders keep asking, with an editable first answer.

Every answer is a starting point written so it states nothing the company has not said: facts only the company knows are
bracketed gaps such as [add: employer's liability cover, £]. The user edits them in the Answer Bank. A pack is added once;
a question the user already has is left alone. Free."""
from __future__ import annotations

from typing import Any


def _q(category: str, question: str, answer: str) -> dict[str, str]:
    return {"category": category, "question": question, "answer": answer}


PACKS: dict[str, dict[str, Any]] = {
    "general": {
        "title": "General starter pack",
        "summary": "The questions almost every tender asks: who you are, insurance, quality, people, risk, social value.",
        "items": [
            _q("Company overview", "Please provide a brief overview of your organisation, its size and its main activities.",
               "[add: company name] is a [add: legal form, e.g. limited company] established in [add: year] and based in [add: location]. We employ [add: number] people and provide [add: main services]. Our customers include [add: customer types, e.g. local authorities, housing providers]."),
            _q("Company overview", "Please state your company registration number and registered office address.",
               "Company registration number: [add: number]. Registered office: [add: address]. VAT registration number: [add: number]."),
            _q("Insurance", "Please confirm the levels of insurance you hold.",
               "We hold the following cover, with certificates available on request: employer's liability £[add: amount]; public liability £[add: amount]; professional indemnity £[add: amount, if applicable]; product liability £[add: amount, if applicable]. Insurer: [add: insurer name]. Renewal date: [add: date]."),
            _q("Quality", "Describe your quality management arrangements and any accreditations you hold.",
               "[add: describe how work is checked, e.g. documented procedures, supervision, audits and customer feedback]. Accreditations we hold: [add: e.g. ISO 9001, or state none]."),
            _q("Health & safety", "Describe your health and safety arrangements. Provide your accident and incident record for the last three years.",
               "We maintain a written health and safety policy, carry out risk assessments for our activities, and train staff for their roles. Our reportable accident record for the last three years is: [add: figures by year, or none]. Health and safety accreditations: [add: e.g. SSIP / CHAS / SafeContractor, or state none]."),
            _q("Equality & diversity", "How do you ensure compliance with the Equality Act 2010?",
               "We have an equality, diversity and inclusion policy that applies to recruitment, pay, development and how we treat customers and suppliers. [add: any training, monitoring or reasonable adjustment practice you can evidence]."),
            _q("Environment", "Describe your environmental policy and how you reduce your environmental impact.",
               "We have an environmental policy and we manage the impacts of our work through [add: measures you actually take, e.g. waste segregation, route planning, energy use]. Environmental accreditations: [add: e.g. ISO 14001, or state none]."),
            _q("Social value", "What social value will you deliver through this contract?",
               "We will deliver social value through [add: commitments you can evidence, e.g. local employment or apprenticeships, local suppliers, volunteering]. We will measure and report on these as [add: how you will measure]."),
            _q("Data protection & security", "How do you comply with UK GDPR and protect information?",
               "We handle personal data under a data protection policy and only for the purposes it was collected. We protect information through [add: measures, e.g. access controls, encryption, backups, staff training]. Security certifications: [add: e.g. Cyber Essentials, ISO 27001, or state none]. ICO registration number: [add: number]."),
            _q("Anti-bribery & ethics", "Do you have anti-bribery, anti-corruption and modern slavery policies?",
               "We have an anti-bribery and corruption policy and a modern slavery statement, available on request, and we act in line with the Bribery Act 2010 and the Modern Slavery Act 2015. [add: how staff are trained and how concerns are raised]."),
            _q("Delivery", "Describe your approach to mobilisation and the first 100 days.",
               "[add: your mobilisation steps in order, e.g. kick-off meeting, named contract manager, resourcing, systems set-up, reporting start date, first review with the customer]."),
            _q("Delivery", "How do you identify, manage and report risks and issues during a contract?",
               "We keep a risk and issues register for each contract, review it with the customer at [add: frequency] meetings, and escalate to [add: role] when a risk exceeds our agreed tolerance. [add: an example of a risk you managed]."),
            _q("People", "How do you recruit, train and retain staff, and do any staff transfer under TUPE?",
               "[add: how you recruit and check staff, training and qualifications, retention]. Regarding TUPE: [add: confirm whether you have experience of TUPE transfers, or state it is not applicable]."),
            _q("Subcontracting", "Do you intend to use subcontractors? How do you select and manage them?",
               "[add: Yes, for [work], or No]. We select subcontractors on [add: criteria, e.g. capability, insurance, references] and manage them through [add: contracts, monitoring, payment terms]."),
            _q("References", "Provide examples of similar contracts you have delivered.",
               "Contract 1: [add: customer, value, dates, what you did, outcome, referee contact]. Contract 2: [add]. Contract 3: [add]."),
        ],
    },
    "construction_fm": {
        "title": "Construction, repairs and facilities management",
        "summary": "Extra answers for building, maintenance, repairs and FM tenders.",
        "items": [
            _q("Health & safety", "Describe your approach to CDM 2015 and managing contractors on site.",
               "[add: your role under CDM 2015 (e.g. contractor or principal contractor), how you prepare method statements and risk assessments, how contractors are inducted and monitored]."),
            _q("Delivery", "How do you handle emergency and out-of-hours repairs?",
               "[add: your response times, call-handling arrangements, out-of-hours cover and how the customer is updated]."),
            _q("Quality", "How do you manage customer satisfaction and the quality of completed work?",
               "[add: post-completion inspections, customer feedback methods, how defects are dealt with and within what time]."),
            _q("Compliance", "What trade and competency registrations do your operatives hold?",
               "[add: e.g. Gas Safe, NICEIC, CSCS, with registration numbers, or state which apply]."),
        ],
    },
    "it_digital": {
        "title": "IT, software and digital services",
        "summary": "Extra answers for software, hosting and digital delivery tenders.",
        "items": [
            _q("Data protection & security", "Where is data hosted and how is it protected?",
               "[add: hosting provider and location (UK/EU), encryption at rest and in transit, access control, backups, penetration testing]."),
            _q("Delivery", "Describe your delivery methodology and how you manage change.",
               "[add: e.g. agile with sprint reviews, release process, how change requests are assessed and approved]."),
            _q("Service", "Describe your support model and service levels.",
               "[add: support hours, channels, response and resolution targets, escalation route, uptime commitment]."),
            _q("Exit", "How will you support exit and transition at the end of the contract?",
               "[add: data return format and timescales, knowledge transfer, support for the incoming supplier]."),
        ],
    },
    "professional_services": {
        "title": "Professional and consultancy services",
        "summary": "Extra answers for consultancy, design and advisory tenders.",
        "items": [
            _q("People", "Describe the team you will use: roles, qualifications and experience.",
               "[add: named lead and team roles, relevant qualifications and memberships, years of experience, proposed time commitment]."),
            _q("Quality", "How do you review and quality-assure your work before it goes to the customer?",
               "[add: peer review steps, sign-off by a senior person, templates and checklists]."),
            _q("Conflicts", "Do you have any conflicts of interest and how would you manage them?",
               "[add: state none, or describe the conflict and how you would manage it]."),
            _q("Insurance", "Confirm your professional indemnity insurance level and period of cover.",
               "[add: amount per claim, insurer, expiry, and the period it will be maintained after the contract]."),
        ],
    },
    "care_support": {
        "title": "Health, care and support services",
        "summary": "Extra answers for care, support and community services.",
        "items": [
            _q("Safeguarding", "Describe your safeguarding arrangements for children and adults at risk.",
               "We have a safeguarding policy and a designated safeguarding lead ([add: name and role]). Staff are trained on [add: what and how often] and know how to report a concern. [add: how you carry out DBS and other pre-employment checks]."),
            _q("Quality", "How are you regulated and how do you monitor the quality of care?",
               "[add: regulator and latest rating, e.g. CQC rating and date, or state not applicable]. We monitor quality through [add: how you audit and gather feedback from people who use the service]."),
            _q("People", "How do you recruit, train and support care and support staff?",
               "[add: recruitment checks, induction (e.g. Care Certificate), mandatory training, supervision and retention approach]."),
            _q("Delivery", "How do you involve people who use your service in planning and reviewing their support?",
               "[add: person-centred planning, review frequency, how feedback and complaints are used]."),
        ],
    },
}


def listing() -> list[dict[str, Any]]:
    return [{"key": k, "title": p["title"], "summary": p["summary"], "count": len(p["items"])} for k, p in PACKS.items()]
