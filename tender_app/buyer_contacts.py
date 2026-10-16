"""Find a buyer's published procurement inbox, so Growth Studio can fill the contact box instead of sending the user
to the notice to hunt for it.

Two places are read, in this order (both public pages, fetched the safe way tender_app/supplier_contacts.py does):

  1. the notice itself (Find a Tender, Contracts Finder, Sell2Wales, Public Contracts Scotland): the buyer's contact
     email is part of the notice. Only addresses that look like the public body's own are kept, because an award
     notice also names the winning supplier.
  2. the buyer's own website (found by search): its procurement / contracts / contact pages.

Role-based inboxes (procurement@, tenders@, contracts@, info@) are preferred. A named person's address is only
returned when nothing else exists, and the caller marks it so the user is warned (Growth Studio already does).
Nothing is guessed: if no address is published, the answer is "not found".

Pure parts (`public_body_email`, `pick_best`) are tested without a network; the network is the injected `get`.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

from tender_app import supplier_contacts as sc
from tender_app.growth_studio import ROLE_WORDS, looks_personal_email, valid_email

# Hosts a notice link may point at. The link is stored data, so only these exact hosts are ever fetched.
NOTICE_HOSTS = frozenset({
    "www.find-tender.service.gov.uk", "find-tender.service.gov.uk",
    "www.contractsfinder.service.gov.uk", "contractsfinder.service.gov.uk",
    "www.sell2wales.gov.wales", "sell2wales.gov.wales",
    "www.publiccontractsscotland.gov.uk", "publiccontractsscotland.gov.uk",
    "www.etendersni.gov.uk", "etendersni.gov.uk",
})
# Helpdesks of the notice portals themselves appear on every notice: never the buyer.
PORTAL_HELPDESK_DOMAINS = ("cabinetoffice.gov.uk", "digital.cabinet-office.gov.uk", "crowncommercial.gov.uk", "notifications.service.gov.uk",
                           "publiccontractsscotland.gov.uk", "gov.wales", "sell2wales.gov.wales", "ogcio.gov.uk", "finance-ni.gov.uk")
PUBLIC_BODY_SUFFIXES = (".gov.uk", ".nhs.uk", ".nhs.net", ".police.uk", ".mod.uk", ".ac.uk", ".sch.uk", ".gov.wales", ".gov.scot", ".gov.ie",
                        ".llyw.cymru", ".wales", ".scot", ".org.uk")
NO_REPLY = re.compile(r"no[-_.]?reply|donotreply|do-not-reply|mailer-daemon|postmaster|webmaster|privacy|dpo|dataprotection|foi|complaints|press", re.I)
PROCUREMENT_WORDS = ("procurement", "tender", "contracts", "contract", "commercial", "purchasing", "sourcing", "supplier", "buying")
BUYER_FRESH_DAYS = {"found": 30, "not_found": 7}


def _domain(email: str) -> str:
    return email.rpartition("@")[2].lower()


def public_body_email(email: str, buyer_name: str, website_domain: str = "") -> bool:
    """Whether an address found on a notice plausibly belongs to the buyer (not the winning supplier, not the portal)."""
    e = (email or "").strip().lower()
    if not valid_email(e) or NO_REPLY.search(e.partition("@")[0]):
        return False
    dom = _domain(e)
    if any(dom == h or dom.endswith("." + h) for h in PORTAL_HELPDESK_DOMAINS):
        return False
    if website_domain and sc.registrable_domain(dom) == sc.registrable_domain(website_domain):
        return True
    if dom.endswith(PUBLIC_BODY_SUFFIXES):
        return True
    tokens = [t for t in sc.name_tokens(buyer_name) if len(t) >= 4]
    return bool(tokens) and any(t in dom.replace("-", "") for t in tokens)


def _role_score(email: str) -> int:
    """0 procurement-type inbox, 1 other role inbox (info@, enquiries@), 2 looks like a named person."""
    local = email.partition("@")[0].lower()
    if any(w in local for w in PROCUREMENT_WORDS):
        return 0
    if looks_personal_email(email):
        return 2
    parts = re.split(r"[._\-]", local)
    return 1 if any(p in ROLE_WORDS for p in parts) or local in sc.ROLE_LOCALPARTS else 2


def pick_best(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order candidates: procurement inboxes first, then other role inboxes, then named people; notice before website
    inside a tier. Duplicates removed. Each item: {email, source, source_url, role}."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for c in candidates:
        e = c["email"].lower()
        if e in seen:
            continue
        seen.add(e)
        out.append({**c, "email": e, "role": _role_score(e) < 2})
    return sorted(out, key=lambda c: (_role_score(c["email"]), 0 if c["source"] == "notice" else 1))


def safe_notice_url(url: str | None) -> str:
    u = (url or "").strip()
    try:
        p = urlparse(u)
    except ValueError:
        return ""
    return u if p.scheme in ("http", "https") and (p.hostname or "").lower() in NOTICE_HOSTS else ""


def find_buyer_contact(buyer_name: str, notice_urls: list[str] | None = None, *,
                       search: Callable[[str], list[dict]] | None = None,
                       get: Callable[[str], tuple[str, str] | None] | None = None,
                       now: Callable[[], float] = time.time) -> dict[str, Any]:
    """{'status': 'found'|'not_found', 'email': best or None, 'candidates': [...], 'source': 'notice'|'website', 'source_url'}."""
    get = get or sc.fetch
    deadline = now() + sc.DEADLINE_SECONDS
    found: list[dict[str, Any]] = []

    for url in [safe_notice_url(u) for u in (notice_urls or [])][:2]:
        if not url or now() > deadline or not sc.allowed_by_robots(url, get):
            continue
        page = get(url)
        if not page:
            continue
        for email in sc.extract_contacts(page[1], page[0])["emails"]:
            if public_body_email(email, buyer_name):
                found.append({"email": email, "source": "notice", "source_url": page[0]})

    site = {"status": "not_found"}
    if not any(c["role"] if "role" in c else _role_score(c["email"]) < 2 for c in found) and now() < deadline:
        site = sc.enrich_supplier(buyer_name, search=search, get=get, now=now, buyer=True)
        for email in site.get("emails") or []:
            if public_body_email(email, buyer_name, urlparse(site.get("website") or "").hostname or ""):
                found.append({"email": email, "source": "website", "source_url": site.get("source_url") or site.get("website")})

    ranked = pick_best(found)[:4]
    if not ranked:
        return {"status": "not_found", "email": None, "candidates": [], "website": site.get("website")}
    best = ranked[0]
    return {"status": "found", "email": best["email"], "candidates": ranked, "source": best["source"],
            "source_url": best["source_url"], "website": site.get("website")}
