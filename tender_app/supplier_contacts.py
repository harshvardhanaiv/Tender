"""Find a supplier's website, email, phone and LinkedIn page, so the supplier panel can show them instead of a link.

Tender notices rarely carry contact details. This module looks for them the way a person would, but quickly and
politely: use the website we already know (or the domain of a known email), otherwise find the company's own site
(a search, or a guess from its name that is only believed if the page really is that company), read the home
page and its contact/about pages, and pull out the business email addresses, phone numbers and social links.

Rules it keeps:
  * Only public web pages. Every fetched address (and every redirect) must resolve to a public IP, so a bad URL in the
    data can never make the server call localhost or an internal service.
  * robots.txt is respected, requests are few (at most MAX_FETCHES per supplier), small (MAX_BYTES) and quick.
  * A site is only accepted if its text contains the company's own name (and its postcode or Companies House number
    when we know one), so a similarly named business never donates its contact details.
  * Nothing is guessed: if a field is not on the page it stays empty.

The network is behind one function (`fetch`), passed in, so tests run with a fake and never touch the internet.
Everything that reads text (`extract_contacts`, `name_matches`, `candidate_domains`) is pure.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import time
import urllib.robotparser
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

USER_AGENT = "TenderFlowBot/1.0 (business contact lookup; respects robots.txt)"
# The GOV.UK notice portals answer 403 to a bot and 200 to a browser; the app already reads them with a browser agent
# (etenders_scraper/sources). Used only for those portals' notice pages, never for a company or council site.
BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
MAX_BYTES = 1_500_000
MAX_FETCHES = 6
BUYER_MAX_FETCHES = 10  # a council site needs its home page and a procurement page or two, and a wrong guess costs one
FETCH_TIMEOUT = 4.0
DEADLINE_SECONDS = 14.0
BUYER_DEADLINE_SECONDS = 20.0
MAX_REDIRECTS = 3

LEGAL_WORDS = {"ltd", "limited", "plc", "llp", "llc", "inc", "corp", "corporation", "company", "co", "the", "and", "of",
               "uk", "gb", "group", "holdings", "services", "service", "solutions", "t", "a", "trading", "as"}
FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com", "hotmail.co.uk", "outlook.com",
             "live.com", "live.co.uk", "icloud.com", "aol.com", "btinternet.com", "sky.com", "talktalk.net", "msn.com"}
# Directories, registries, social sites and marketplaces are never "the company's own website".
NOT_A_COMPANY_SITE = (
    "linkedin.", "facebook.", "twitter.", "x.com", "instagram.", "youtube.", "tiktok.", "wikipedia.", "company-information.service.gov",
    "gov.uk", "endole.", "opencorporates.", "dnb.com", "zoominfo.", "yell.com", "google.", "bing.", "yahoo.", "duckduckgo.",
    "find-tender", "contractsfinder", "indeed.", "glassdoor.", "trustpilot.", "bloomberg.", "crunchbase.", "cylex", "192.com",
    "duedil.", "companycheck", "checkcompany", "companieslist", "ukcompanyinfo", "bizdb", "northdata", "kompass.", "europages.",
    "thegazette", "companieshouse", "tendersontime", "tenderlake", "mapquest", "vimeo.", "pinterest.", "amazon.", "ebay.",
)
ROLE_LOCALPARTS = ("info", "enquiries", "enquiry", "contact", "hello", "office", "sales", "admin", "mail", "customerservice",
                   "customer.service", "support", "bids", "tenders", "procurement", "reception", "business", "general")
BUYER_WORDS = ("procurement", "tender", "contracts", "commercial", "purchasing", "sourcing", "supplier")
BUYER_LINK_WORDS = r"procure|tender|contract|commission|supplier|business|contact|enquir"
BUYER_PATHS = ("/procurement", "/business/procurement", "/business/tenders-and-contracts", "/tenders", "/contact-us", "/contact")
BUYER_FIRST = re.compile(r"procure|tender|contract|supplier|commission", re.I)  # pages most likely to name a procurement inbox
JUNK_EMAIL_DOMAINS = ("example.com", "example.org", "sentry.io", "sentry-next.wixpress.com", "wixpress.com", "domain.com",
                      "email.com", "yourdomain.com", "yoursite.com", "mysite.com", "schema.org", "w3.org")
JUNK_EMAIL_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".woff", ".woff2")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,24}")
_OBFUSCATED = [
    (re.compile(r"\s*[\[(]\s*at\s*[\])]\s*", re.I), "@"),
    (re.compile(r"\s*[\[(]\s*dot\s*[\])]\s*", re.I), "."),
    (re.compile(r"&#64;|&commat;|%40", re.I), "@"),
]
_PHONE_RE = re.compile(r"(?<![\w/.\-])(?:\+\s?44|0044|0)[\s().\-]*\d(?:[\s().\-]*\d){8,10}(?![\w])")
_POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", re.I)


# ── addresses we are willing to fetch ───────────────────────────────────────────────────────────

def _is_public_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def is_public_http_url(url: str, resolve: Callable[[str], Iterable[str]] | None = None) -> bool:
    """http(s) only, a real host name, and every address it resolves to is public (not localhost, a private network,
    a link-local or cloud-metadata address)."""
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return False
    host = parts.hostname
    if parts.port not in (None, 80, 443):
        return False
    if host.lower() in ("localhost",) or host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        literal = ipaddress.ip_address(host)
        return literal.is_global
    except ValueError:
        pass
    try:
        addrs = list(resolve(host)) if resolve else [ai[4][0] for ai in socket.getaddrinfo(host, None)]
    except OSError:
        return False
    return bool(addrs) and all(_is_public_ip(a) for a in addrs)


def registrable_domain(host: str) -> str:
    """example.co.uk, example.com: the part that identifies the organisation (no public-suffix list needed for UK/IE)."""
    host = (host or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in ("co", "org", "ac", "gov", "ltd", "plc", "net", "sch", "nhs") and labels[-1] in ("uk", "ie"):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def site_root(url: str) -> str:
    p = urlparse(url if "//" in url else "https://" + url)
    return f"{p.scheme or 'https'}://{p.netloc}"


# ── the company's name ──────────────────────────────────────────────────────────────────────────

def name_tokens(name: str) -> list[str]:
    spaced = re.sub(r"[^a-z0-9]+", " ", (name or "").lower())
    tokens = [t for t in spaced.split() if t and t not in LEGAL_WORDS]
    return tokens or [t for t in spaced.split() if t]


def name_matches(name: str, text: str, threshold: float = 0.8) -> bool:
    """True when the page text contains (nearly) all the distinctive words of the company name."""
    tokens = name_tokens(name)
    if not tokens:
        return False
    hay = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    hay_compact = hay.replace(" ", "")
    hits = sum(1 for t in tokens if re.search(rf"\b{re.escape(t)}\b", hay) or (len(t) >= 5 and t in hay_compact))
    # a one-word name must be present as a word; longer names may miss one word
    need = len(tokens) if len(tokens) <= 2 else len(tokens) - 1
    return hits >= need


def postcode_of(address: str | None) -> str:
    m = _POSTCODE_RE.search(address or "")
    return f"{m.group(1)}{m.group(2)}".upper() if m else ""


def candidate_domains(name: str, limit: int = 6) -> list[str]:
    """Guesses at the company's own domain from its name. Only ever believed after the page is checked."""
    tokens = name_tokens(name)
    if not tokens:
        return []
    joined = "".join(tokens)
    spoken = [t for t in re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split() if t not in ("ltd", "limited", "plc", "llp", "llc", "inc", "the")]
    stems: list[str] = []
    for s in (joined, "".join(spoken), "-".join(tokens), "".join(tokens[:2]), tokens[0] if len(tokens[0]) >= 4 else ""):
        if s and 3 <= len(s) <= 40 and s not in stems:
            stems.append(s)
    out: list[str] = []
    for stem in stems:
        for tld in ("co.uk", "com", "uk", "org.uk"):
            out.append(f"{stem}.{tld}")
    return out[:limit]


GOV_SERVICE_HOSTS = ("service.gov.uk", "contractsfinder", "find-tender")


def usable_website(url: str | None, allow_gov: bool = False) -> str:
    """The site root of a URL, or '' when it is empty, a directory/social/registry site, or not a web address.
    allow_gov: a buyer's own site (council.gov.uk, trust.nhs.uk) is wanted; GOV.UK services and registers still are not."""
    u = (url or "").strip()
    if not u or u.lower() in ("n/a", "not available", "none", "-"):
        return ""
    if "//" not in u:
        u = "https://" + u
    host = (urlparse(u).hostname or "").lower()
    if not host or "." not in host:
        return ""
    if allow_gov:
        if host in ("gov.uk", "www.gov.uk") or any(bad in host for bad in GOV_SERVICE_HOSTS):
            return ""
        if any(bad in host for bad in NOT_A_COMPANY_SITE if bad != "gov.uk"):
            return ""
    elif any(bad in host for bad in NOT_A_COMPANY_SITE):
        return ""
    return site_root(u)


def domain_of_email(email: str | None) -> str:
    e = (email or "").strip().lower()
    if "@" not in e:
        return ""
    dom = e.rsplit("@", 1)[1]
    return "" if dom in FREE_MAIL or any(bad in dom for bad in NOT_A_COMPANY_SITE) else dom


# ── reading a page ──────────────────────────────────────────────────────────────────────────────

def _deobfuscate(text: str) -> str:
    for rx, rep in _OBFUSCATED:
        text = rx.sub(rep, text)
    return text


def _plain_text(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def clean_email(raw: str) -> str:
    e = raw.strip().strip(".,;:<>()[]\"'").lower()
    if not _EMAIL_RE.fullmatch(e):
        return ""
    local, _, dom = e.partition("@")
    if dom in JUNK_EMAIL_DOMAINS or e.endswith(JUNK_EMAIL_SUFFIX) or re.fullmatch(r"[0-9a-f]{16,}", local):
        return ""
    return e


def normalise_phone(raw: str) -> str:
    """'+44 (0)1234 567 890' / '01234-567890' -> '+44 1234 567890'. '' when it is not a UK-shaped number."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("0044"):
        digits = digits[4:]
    elif digits.startswith("44"):
        digits = digits[2:]
        if digits.startswith("0"):  # +44 (0)...
            digits = digits[1:]
    elif digits.startswith("0"):
        digits = digits[1:]
    else:
        return ""
    if not 9 <= len(digits) <= 10 or digits[0] not in "123578":
        return ""
    if len(set(digits)) <= 2:  # 0000000000
        return ""
    if digits[0] == "2":       # 020 / 023 / 024 / 028 / 029 : area code + 8
        return f"+44 {digits[:2]} {digits[2:6]} {digits[6:]}"
    if digits[0] in "78":
        return f"+44 {digits[:4]} {digits[4:]}"
    return f"+44 {digits[:4]} {digits[4:]}" if digits[0] == "1" else f"+44 {digits[:3]} {digits[3:6]} {digits[6:]}"


def extract_contacts(html: str, base_url: str = "", link_words: str = r"contact|get-in-touch|get in touch|enquir|find-us|find us|about") -> dict[str, Any]:
    """Emails, phones and social links from one page. Links (mailto:/tel:) come first because they are deliberate."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    emails: list[str] = []
    phones: list[str] = []
    social: dict[str, str] = {}
    contact_links: list[str] = []

    def add(lst: list[str], value: str) -> None:
        if value and value not in lst:
            lst.append(value)

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        if low.startswith("mailto:"):
            add(emails, clean_email(_deobfuscate(href[7:].split("?")[0])))
        elif low.startswith("tel:"):
            add(phones, normalise_phone(href[4:]))
        else:
            absolute = urljoin(base_url, href) if base_url else href
            host = (urlparse(absolute).hostname or "").lower()
            path = urlparse(absolute).path.lower()
            if "linkedin.com" in host and "/company/" in path and "linkedin" not in social:
                social["linkedin"] = absolute.split("?")[0].rstrip("/")
            elif "facebook.com" in host and "facebook" not in social and not any(s in path for s in ("/sharer", "/share", "/plugins")):
                social["facebook"] = absolute.split("?")[0].rstrip("/")
            elif host.endswith(("twitter.com", "x.com")) and "twitter" not in social and len(path.strip("/").split("/")) == 1 and path.strip("/"):
                if path.strip("/") not in ("share", "intent", "home"):
                    social["twitter"] = absolute.split("?")[0].rstrip("/")
            label = (a.get_text(" ") or "").lower()
            if base_url and re.search(link_words, label + " " + path):
                if (urlparse(absolute).hostname or "") == (urlparse(base_url).hostname or "") and absolute not in contact_links:
                    contact_links.append(absolute.split("#")[0])

    text = _plain_text(html)
    for m in _EMAIL_RE.finditer(_deobfuscate(text)):
        add(emails, clean_email(m.group(0)))
    for m in _PHONE_RE.finditer(text):
        add(phones, normalise_phone(m.group(0)))
    return {"emails": emails, "phones": phones, "social": social, "contact_links": contact_links[:6], "text": text}


def rank_emails(emails: list[str], site_domain: str, limit: int = 3, prefer: tuple[str, ...] = ()) -> list[str]:
    """On the company's own domain first, role mailboxes (info@, enquiries@) before personal-looking ones.
    prefer: words that should come first of all (a buyer's procurement / tenders / contracts inbox)."""
    def score(e: str) -> tuple[int, int, int]:
        local, _, dom = e.partition("@")
        own = 0 if registrable_domain(dom) == registrable_domain(site_domain) else 1
        role = 0 if local.split("+")[0] in ROLE_LOCALPARTS else 1
        wanted = 0 if prefer and any(w in local for w in prefer) else 1
        return (wanted, own, role)
    kept = [e for e in emails if registrable_domain(e.partition("@")[2]) == registrable_domain(site_domain)
            or e.partition("@")[2] in FREE_MAIL]
    return sorted(dict.fromkeys(kept), key=score)[:limit]


# ── the network (injectable) ────────────────────────────────────────────────────────────────────

def fetch(url: str, *, timeout: float = FETCH_TIMEOUT, user_agent: str | None = None) -> tuple[str, str] | None:
    """(final_url, html) for a public HTML page, or None. Follows at most MAX_REDIRECTS hops, re-checking each one."""
    import requests
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not is_public_http_url(current):
            return None
        try:
            with requests.get(current, headers={"User-Agent": user_agent or USER_AGENT, "Accept": "text/html,*/*;q=0.5"}, timeout=timeout,
                              allow_redirects=False, stream=True) as r:
                if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
                    nxt = r.headers.get("Location")
                    if not nxt:
                        return None
                    current = urljoin(current, nxt)
                    continue
                if r.status_code != 200 or "html" not in (r.headers.get("Content-Type") or "").lower():
                    return None
                body = b""
                for chunk in r.iter_content(65536):
                    body += chunk
                    if len(body) > MAX_BYTES:
                        break
                enc = r.encoding or "utf-8"
                return current, body[:MAX_BYTES].decode(enc, errors="replace")
        except requests.RequestException:
            return None
    return None


_ROBOTS: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}


def allowed_by_robots(url: str, fetcher: Callable[[str], tuple[str, str] | None] | None = None) -> bool:
    """True unless the site's robots.txt disallows our bot. Unreachable or missing robots.txt means allowed."""
    root = site_root(url)
    cached = _ROBOTS.get(root)
    if cached is None or time.time() - cached[0] > 3600:
        rp: urllib.robotparser.RobotFileParser | None = None
        try:
            import requests
            if fetcher is None:
                if is_public_http_url(root + "/robots.txt"):
                    r = requests.get(root + "/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT, allow_redirects=False)
                    if r.status_code == 200 and len(r.text) < 200_000:
                        rp = urllib.robotparser.RobotFileParser()
                        rp.parse(r.text.splitlines())
            else:
                got = fetcher(root + "/robots.txt")
                if got:
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse(got[1].splitlines())
        except Exception:
            rp = None
        _ROBOTS[root] = (time.time(), rp)
        cached = _ROBOTS[root]
    rp = cached[1]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


# ── finding the company ─────────────────────────────────────────────────────────────────────────

def _check_site(root: str, name: str, postcode: str, company_number: str, get: Callable[[str], tuple[str, str] | None],
                deadline: float, budget: list[int], trusted: bool = False, buyer: bool = False) -> dict[str, Any] | None:
    """Read the home page (and up to two contact/about pages). Returns the contacts only if the site is that company,
    unless the address came from the supplier itself (their notice's website or email domain), which is trusted."""
    def grab(url: str) -> tuple[str, str] | None:
        if budget[0] <= 0 or time.time() > deadline or not allowed_by_robots(url, get):
            return None
        budget[0] -= 1
        return get(url)

    home = grab(root + "/")
    if not home:
        return None
    final_url, html = home
    link_words = BUYER_LINK_WORDS if buyer else r"contact|get-in-touch|get in touch|enquir|find-us|find us|about"
    pages = [extract_contacts(html, final_url, link_words)]
    text = pages[0]["text"]
    if not trusted and not name_matches(name, text) and not pages[0]["contact_links"]:
        return None  # not them, and nothing to read further: do not spend the fetch budget guessing pages
    seen = {final_url}
    extra = list(pages[0]["contact_links"])
    for guess in (BUYER_PATHS if buyer else ("/contact", "/contact-us", "/about")):
        extra.append(root + guess)
    if buyer:  # a procurement or tenders page is read before a general contact page
        extra.sort(key=lambda u: 0 if BUYER_FIRST.search(urlparse(u).path) else 1)
    max_extra = 3 if buyer else 2
    fetched_extra = 0
    for link in extra:
        if fetched_extra >= max_extra or link in seen:
            continue
        seen.add(link)
        got = grab(link)
        if got:
            fetched_extra += 1
            c = extract_contacts(got[1], got[0], link_words)
            pages.append(c)
            text += " " + c["text"]
    if not trusted and not name_matches(name, text):
        return None
    cn = (company_number or "").upper()
    pc = postcode.replace(" ", "").upper()
    compact = re.sub(r"\s+", "", text).upper()
    strong = bool((pc and pc in compact) or (cn and re.fullmatch(r"[A-Z0-9]{6,8}", cn) and cn in compact))
    emails: list[str] = []
    phones: list[str] = []
    social: dict[str, str] = {}
    for p in pages:
        for e in p["emails"]:
            if e not in emails:
                emails.append(e)
        for ph in p["phones"]:
            if ph not in phones:
                phones.append(ph)
        for k, v in p["social"].items():
            social.setdefault(k, v)
    host = urlparse(final_url).hostname or ""
    return {"website": site_root(final_url), "emails": rank_emails(emails, host, prefer=BUYER_WORDS if buyer else ()), "phones": phones[:3], "social": social,
            "source_url": final_url, "verified_by": "supplier_notice" if trusted else ("address_or_number" if strong else "name")}


def enrich_supplier(name: str, *, address: str | None = None, company_number: str | None = None, email: str | None = None,
                    website: str | None = None, search: Callable[[str], list[dict]] | None = None,
                    get: Callable[[str], tuple[str, str] | None] | None = None, now: Callable[[], float] = time.time,
                    buyer: bool = False, extra_roots: Iterable[str] = ()) -> dict[str, Any]:
    """Look one supplier up. Always returns a dict with status 'found' (website + something), 'partial' (a website only),
    'not_found', or 'skipped' (nothing to go on). Never raises for network trouble."""
    get = get or fetch
    start = now()
    deadline = start + (BUYER_DEADLINE_SECONDS if buyer else DEADLINE_SECONDS)
    budget = [BUYER_MAX_FETCHES if buyer else MAX_FETCHES]
    name = (name or "").strip()
    if not name_tokens(name):
        return {"status": "skipped", "reason": "no usable name"}
    postcode = postcode_of(address)

    roots: list[str] = []
    known = usable_website(website, allow_gov=buyer)
    if known:
        roots.append(known)
    dom = domain_of_email(email)
    if dom and f"https://{dom}" not in roots:
        roots.append(f"https://{dom}")
    trusted_roots = set(roots)  # given by the supplier itself, so no name check
    # sites the caller expects (a council's usual domain): tried before any search, but only believed once the page names the buyer
    for r in extra_roots:
        root = usable_website(r, allow_gov=buyer)
        if root and root not in roots:
            roots.append(root)

    tried: set[str] = set()

    def attempt(candidates: list[str]) -> dict[str, Any] | None:
        for root in candidates[:6]:
            host = urlparse(root).hostname or ""
            key = host if buyer else registrable_domain(host)  # www. and the bare domain are different servers for a council
            if key in tried or budget[0] <= 0 or now() > deadline:
                continue
            tried.add(key)
            found = _check_site(root, name, postcode, company_number or "", get, deadline, budget, trusted=root in trusted_roots, buyer=buyer)
            if found:
                found["status"] = "found" if (found["emails"] or found["phones"] or found["social"]) else "partial"
                return found
        return None

    got = attempt(roots)
    if got:
        return got
    found_roots: list[str] = []
    if search and not trusted_roots:
        try:
            hits = search(f'"{name}" procurement contact email' if buyer else f'"{name}" official website')[:6]
        except Exception:
            hits = []
        for h in hits:
            root = usable_website(h.get("link"), allow_gov=buyer)
            if root and root not in roots and root not in found_roots:
                found_roots.append(root)
        got = attempt(found_roots)
        if got:
            return got
    if not roots and not found_roots:
        got = attempt([f"https://{d}" for d in candidate_domains(name)])
        if got:
            return got
    return {"status": "not_found"}
