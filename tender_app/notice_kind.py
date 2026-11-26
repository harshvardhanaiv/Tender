"""Is this notice a call for competition, or only a pre-market notice (speculative / prior information / market
engagement)?

A pre-market notice has no questionnaire, price schedule or evaluation criteria and nothing to submit yet; the buyer
usually asks suppliers to register for a briefing. The clarification and bid writers must not treat it as a live tender
(13 "critical" questions and a 9-page priced response for a registration email, Carmarthenshire NOV680441).
"""
from __future__ import annotations

import re

# Notices that announce themselves as pre-market. CORE phrases are enough on their own; the others need more care
# because a live tender can mention them in passing ("a market engagement event was held in March", "following our PIN").
_CORE = re.compile(r"speculative notice|not a call for competition|pre-?market (?:engagement|consultation)|"
                   r"preliminary market (?:engagement|consultation)", re.I)
_EVENT = re.compile(r"market engagement (?:event|exercise)|market (?:briefing|engagement) (?:session|event)|"
                    r"invitation to attend a (?:briefing|market)", re.I)
# "PIN" alone is not used: "PIN pads", "PIN number". The full name or the bracketed abbreviation is.
_PIN = re.compile(r"prior information notice|planned procurement notice|\(PIN\)|notice type:?\s*PIN\b", re.I)
# A notice TYPE that is itself a call for competition ("PIN with call for competition", "Tender notice") is live.
_LIVE_TYPE = re.compile(r"(?<!without )call for competition|reducing time limits|tender notice|contract notice|"
                        r"award notice|contract award|contract details|\bUK[4-7]\b|\bF0[2367]\b", re.I)
_PAST = re.compile(r"(?:was|were|has been|have been|had been)\s+(?:held|carried out|undertaken|completed)|"
                   r"previous(?:ly)?|earlier|last year", re.I)
# Present-tense signs that tenders are being taken now: these always win.
_HARD_LIVE = re.compile(r"submission deadline:\s*\d|tenders? (?:must|should) be (?:submitted|returned)|"
                        r"closing date:\s*\d|deadline for (?:the )?(?:receipt|submission) of tenders?", re.I)
_SOFT_LIVE = re.compile(r"invitation to tender|\bITT\b|call for competition(?! is not)", re.I)


def is_pre_market(*texts: str | None, fields: tuple = ()) -> bool:
    """`fields` are the notice's own labels (title, procedure, notice type); `texts` are free text (description)."""
    head = " ".join(str(f) for f in fields if f)[:2000]
    body = " ".join(str(t) for t in texts if t)[:12000]
    blob = f"{head} {body}"
    if not blob.strip() or _HARD_LIVE.search(blob) or _LIVE_TYPE.search(head):
        return False
    soft = _SOFT_LIVE.search(blob)
    if _CORE.search(head) or _PIN.search(head):
        return True
    # In the body the same phrases count only when the notice opens with them, or no tender wording and no past
    # tense sits next to them ("a market engagement event was held in March" is a live tender recalling one).
    for pat in (_CORE, _EVENT):
        m = pat.search(body)
        if m:
            near = body[max(0, m.start() - 120): m.end() + 120]
            if m.start() < 600 and not _PAST.search(near):
                return True
            return not soft and not _PAST.search(near)
    if _PIN.search(body[:400]):          # a PIN named at the very start of the description
        return not soft
    return False


def clarification_rules() -> str:
    return (
        "\nNOTICE TYPE: this is a PRE-MARKET notice (speculative / prior information / market engagement), not a live "
        "tender. There are no tender documents yet. Write the questions the bidder should put to the buyer at the "
        "briefing, not a clarification letter about tender documents. Rules: 4 to 6 questions in total, one topic each; use "
        "Critical only for a question that decides whether to bid at all (most are High); do NOT ask about insurance limits, "
        "liability caps, contract terms, evaluation weightings, response templates or the portal (they come with the "
        "tender). Do NOT ask about anything the notice already states (dates, contacts, the lots it lists) and do not name "
        "specific certifications (ISO, Cyber Essentials) the notice does not mention. Do ask about the lot structure and whether lots may be bid separately, the framework term, how "
        "providers are appointed, current volumes or spend, regulatory registrations needed, and the timetable only where the notice leaves it open. "
        "Do not ask the same thing twice. Never say the bidder has reviewed tender documents.\n"
    )


def writer_rules() -> str:
    return (
        "THIS IS A PRE-MARKET NOTICE, NOT A CALL FOR COMPETITION. Nothing can be submitted yet. Write only what the "
        "notice asks suppliers to do (for example register for a briefing by email): a short, practical response, not "
        "a tender response. Do NOT invent question numbers or submission documents, a price schedule or pricing "
        "units (write 'No pricing is requested at this stage' if pricing comes up), or declarations the notice does "
        "not ask for. Do not volunteer capability gaps to the buyer in a registration; those belong in the internal "
        "bid writer's note. Address the named contact. The registration email needs only: organisation, company "
        "number, attendee name and role, the lots of interest, and one sentence of interest.\n"
    )


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def notice_steps(text: str, dates: list | None = None) -> list[str]:
    """The plan for a notice: register, use the briefing, watch for the tender. Facts only from the notice text."""
    emails = []
    for e in _EMAIL.findall(text or ""):
        if e.lower() not in [x.lower() for x in emails]:
            emails.append(e)
    action = next((d for d in (dates or []) if d.get("action") and not d.get("passed")), None)
    reg = "Decide whether to attend the briefing."
    if emails:
        reg += " To register, email " + " and ".join(emails[:3]) + "."
    if action:
        reg += f" Dated action: {action.get('event')} on {action.get('date')}."
    reg += " The notice states no registration deadline, so register early."
    return [
        "This is a notice, not a tender: there is nothing to submit yet.",
        reg,
        "Use the briefing to ask about the lot structure, the term, how providers are appointed, current volumes and the "
        "registrations required. The Clarifications tab drafts up to six questions for it.",
        "Watch for the tender itself (the notice says when it is expected) and check the fit first: the Bid response "
        "tab shows a fit check before any credits are spent.",
    ]
