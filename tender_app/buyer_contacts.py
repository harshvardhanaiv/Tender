"""Find a buyer's published procurement inbox, so Growth Studio can fill the contact box instead of sending the user
to the notice to hunt for it.

Two places are read, in this order (both public pages, fetched the safe way tender_app/supplier_contacts.py does):

  1. the notice itself (Find a Tender, Contracts Finder, Sell2Wales, Public Contracts Scotland): the buyer's contact
     email and website are part of the notice. Only addresses that look like the public body's own are kept, because
     an award notice also names the winning supplier. A named person's address on the notice is not used as the inbox,
     but its domain (and the notice's website) says where the buyer's own site is.
  2. the buyer's own website: the one the notice gave, the domain of the notice's address, the usual domain of a public
     body of that name (swale.gov.uk for Swale Borough Council: only believed once the page names the buyer), and only
     then a web search. Its procurement / tenders / contracts / contact pages are read.

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


# Words that name the kind of body, not the body, when working out its website.
_BODY_WORDS = {"borough", "district", "county", "city", "council", "metropolitan", "unitary", "authority", "london", "of", "the",
               "and", "royal", "town", "parish", "combined", "mbc", "bc", "cc", "dc"}
_NHS_WORDS = {"nhs", "foundation", "trust", "the", "of", "and", "integrated", "care", "board", "icb", "hospitals", "hospital", "university"}
# Hosts that are the buyer's own kind of site (a notice's "website" must be one of these or carry the buyer's name).
BODY_SITE_SUFFIXES = (".gov.uk", ".nhs.uk", ".police.uk", ".ac.uk", ".sch.uk", ".gov.wales", ".gov.scot", ".mod.uk", ".llyw.cymru")
NOT_THE_BUYER_HOSTS = PORTAL_HELPDESK_DOMAINS + ("nationalarchives.gov.uk",)


def public_body_domains(name: str, limit: int = 4) -> list[str]:
    """Where a public body of this name usually keeps its website: swale.gov.uk for Swale Borough Council, camden.gov.uk
    for the London Borough of Camden, leeds.ac.uk for the University of Leeds, kent.police.uk for Kent Police. Guesses only:
    the caller reads the page and believes it only if it names the buyer. [] when the name is not obviously a public body."""
    low = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9& ]+", " ", (name or "").lower().replace("&", " and "))).strip()
    words = low.split()
    if not words:
        return []
    out: list[str] = []

    def add(stem: str, suffix: str) -> None:
        stem = re.sub(r"[^a-z0-9]", "", stem)
        if 3 <= len(stem) <= 40:
            d = f"www.{stem}.{suffix}"
            if d not in out:
                out.append(d)

    if "nhs" in words:
        core = [w for w in words if w not in _NHS_WORDS]
        if core:
            add("".join(core), "nhs.uk")
    elif re.search(r"\b(university|college)\b", low):
        core = [w for w in words if w not in ("university", "college", "of", "the", "and")]
        if core:
            add("".join(core), "ac.uk")
    elif re.search(r"\b(police|constabulary)\b", low):
        core = [w for w in words if w not in ("police", "constabulary", "service", "force", "of", "the", "and", "crime", "commissioner")]
        if core:
            add("".join(core), "police.uk")
    elif re.search(r"\b(council|borough|district|county|city|metropolitan)\b", low):
        core = [w for w in words if w not in _BODY_WORDS]
        if core:
            add("".join(core), "gov.uk")
            if "and" in words:  # Cheshire West and Chester
                add("".join(w for w in words if w not in (_BODY_WORDS - {"and"})), "gov.uk")
            if "royal" in words:  # Royal Borough of Greenwich -> royalgreenwich.gov.uk
                add("royal" + "".join(core), "gov.uk")
            if "city" in words:  # Aberdeen City Council -> aberdeencity.gov.uk
                add("".join(core) + "city", "gov.uk")
            add("".join(core) + "council", "gov.uk")
            add("".join(core), "gov.wales")
    return out[:limit]


def notice_websites(html: str, base_url: str, buyer_name: str, limit: int = 2) -> list[str]:
    """The buyer's own website as the notice gives it ("Website http://www.swale.gov.uk/"): a link to a public-body kind of
    site (gov.uk, nhs.uk, ac.uk ...) or to one whose name carries the buyer's name. Portal, helpdesk and archive links are
    left out, and so is a tendering portal or a supplier's site."""
    from urllib.parse import urljoin
    from bs4 import BeautifulSoup

    tokens = [t for t in sc.name_tokens(buyer_name) if len(t) >= 4 and t not in _BODY_WORDS]
    out: list[str] = []
    for a in BeautifulSoup(html or "", "html.parser").find_all("a", href=True):
        href = urljoin(base_url, a["href"].strip())
        if not href.lower().startswith(("http://", "https://")):
            continue
        root = sc.usable_website(href, allow_gov=True)
        host = (urlparse(root).hostname or "") if root else ""
        if not host or any(host == h or host.endswith("." + h) for h in NOT_THE_BUYER_HOSTS):
            continue
        reg = sc.registrable_domain(host)
        if (reg.endswith(BODY_SITE_SUFFIXES) or any(t in reg.replace("-", "") for t in tokens)) and root not in out:
            out.append(root)
    return out[:limit]


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


def _portal_fetch(url: str):
    return sc.fetch(url, user_agent=sc.BROWSER_USER_AGENT)


def find_buyer_contact(buyer_name: str, notice_urls: list[str] | None = None, *,
                       search: Callable[[str], list[dict]] | None = None,
                       get: Callable[[str], tuple[str, str] | None] | None = None,
                       now: Callable[[], float] = time.time) -> dict[str, Any]:
    """{'status': 'found'|'not_found', 'email': best or None, 'candidates': [...], 'source': 'notice'|'website', 'source_url'}.
    The notice portals refuse a bot, so their pages are read with a browser agent (the app's scrapers do the same); an
    injected `get` (tests) is used for everything."""
    notice_get = get or _portal_fetch
    get = get or sc.fetch
    deadline = now() + sc.BUYER_DEADLINE_SECONDS
    found: list[dict[str, Any]] = []
    notice_sites: list[str] = []

    for url in [safe_notice_url(u) for u in (notice_urls or [])][:2]:
        if not url or now() > deadline or not sc.allowed_by_robots(url, notice_get):
            continue
        page = notice_get(url)
        if not page:
            continue
        for email in sc.extract_contacts(page[1], page[0])["emails"]:
            if public_body_email(email, buyer_name):
                found.append({"email": email, "source": "notice", "source_url": page[0]})
        for root in notice_websites(page[1], page[0], buyer_name):
            if root not in notice_sites:
                notice_sites.append(root)

    site = {"status": "not_found"}
    if not any(_role_score(c["email"]) < 2 for c in found) and now() < deadline:
        # a named person's address on the notice is never used as the inbox, but its domain says where the buyer's site is
        site = sc.enrich_supplier(buyer_name, email=found[0]["email"] if found else None, website=notice_sites[0] if notice_sites else None,
                                  search=search, get=get, now=now, buyer=True,
                                  extra_roots=[f"https://{d}" for d in public_body_domains(buyer_name)] + notice_sites[1:])
        for email in site.get("emails") or []:
            if public_body_email(email, buyer_name, urlparse(site.get("website") or "").hostname or ""):
                found.append({"email": email, "source": "website", "source_url": site.get("source_url") or site.get("website")})

    ranked = pick_best(found)[:4]
    if not ranked:
        return {"status": "not_found", "email": None, "candidates": [], "website": site.get("website")}
    best = ranked[0]
    return {"status": "found", "email": best["email"], "candidates": ranked, "source": best["source"],
            "source_url": best["source_url"], "website": site.get("website")}
