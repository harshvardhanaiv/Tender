"""Deep tender scrape: crawl a notice page, its linked pages and documents, and
surface the things a normal detail fetch misses (response forms, portals,
contact emails, deadlines, attached documents).

Deterministic - no AI calls. Everything found is reported with the place it was
found so the user can verify it.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

DOC_EXTS = (".pdf", ".docx", ".doc", ".txt", ".xlsx", ".xls", ".csv", ".zip", ".pptx", ".odt", ".rtf")
EXTRACTABLE_EXTS = (".pdf", ".docx", ".doc", ".txt", ".xlsx", ".csv", ".rtf")

FORM_HOSTS = (
    "docs.google.com/forms", "forms.gle", "forms.office.com", "forms.cloud.microsoft",
    "forms.microsoft.com", "jotform.com", "typeform.com", "smartsurvey.co.uk", "surveymonkey.",
    "wufoo.com", "formstack.com", "cognitoforms.com", "airtable.com/shr", "tally.so",
    "survey.", "qualtrics.com", "onlinesurveys.ac.uk", "checkbox.", "formsite.com",
)
PORTAL_HOSTS = (
    "proactis", "delta-esourcing", "in-tendhost", "intend.", "atamis", "bravosolution", "jaggaer",
    "eu-supply", "supplierlive", "procontract", "ariba.com", "tendersdirect", "basware",
    "contractsfinder.service.gov.uk", "find-tender.service.gov.uk", "etenders.gov.ie",
    "etendersni.gov.uk", "publiccontractsscotland.gov.uk", "sell2wales.gov.wales",
    "bipsolutions", "pro-sourcing", "oneadvanced", "mytenders", "sourcingplatform", "ted.europa.eu",
)
SKIP_HOSTS = (
    "twitter.com", "facebook.com", "linkedin.com", "youtube.com", "instagram.com", "t.co",
    "youtu.be", "pinterest.com", "w3.org", "x.com", "schema.org",
)
JUNK_PATH = (
    "/login", "/register", "/signin", "/help", "/cookie", "/privacy", "/terms", "/aboutus",
    "/sitehelp", "/membership", "/redirect", "/contact", "/accessibility", "/websitepolicies",
    "/helpandresources", "/download.aspx",
)
ACTION_WORDS = re.compile(
    r"\b(submit|submission|return|respond|response|complete|apply|application|register|registration|"
    r"express(?:ion)? of interest|questionnaire|form|quote|quotation|tender portal|upload|deadline|"
    r"closing date|by \d{1,2}|mandatory|must)\b",
    re.I,
)
URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
DATE_RE = re.compile(
    r"\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}"
    r"(?:\s*(?:at|,)?\s*\d{1,2}[:.]\d{2}\s*(?:am|pm)?)?|\d{1,2}/\d{1,2}/\d{4})\b",
    re.I,
)
DEADLINE_CTX = re.compile(r"(deadline|closing|close[sd]?|by|no later than|before|due|return)", re.I)

MAX_DOCS = 12
MAX_PAGES = 6
TIME_BUDGET_S = 50
_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_S = 30 * 60


def _is_public_url(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            # NAT64 (64:ff9b::/96) addresses embed a public IPv4; judge that instead.
            if ip.version == 6 and ip.packed[:12] == bytes.fromhex("0064ff9b0000000000000000"):
                ip = ipaddress.ip_address(ip.packed[12:])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                return False
        return True
    except Exception:
        return False


def _clean_url(url: str) -> str:
    return url.split("#")[0].rstrip(".,;:'\"")


def _classify(url: str, label: str = "") -> str:
    """Return form | document | portal | email | other | skip."""
    if url.lower().startswith("mailto:"):
        return "email"
    p = urllib.parse.urlparse(url)
    host = (p.netloc or "").lower()
    low = url.lower()
    if any(h in host for h in SKIP_HOSTS):
        # docs.google.com is a form host, everything else social is skipped
        if not any(f in low for f in FORM_HOSTS):
            return "skip"
    if any(f in low for f in FORM_HOSTS):
        return "form"
    if p.path.lower().endswith(DOC_EXTS):
        return "document"
    if "google.com" in host and "/forms" not in p.path.lower():
        return "skip"
    if any(h in host for h in PORTAL_HOSTS):
        return "portal"
    return "other"


def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "head", "svg"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)


def _context(text: str, url: str, width: int = 260) -> str:
    i = text.find(url)
    if i < 0:
        base = url.split("?")[0]
        i = text.find(base)
    if i < 0:
        return ""
    seg = text[max(0, i - width): i + len(url) + width]
    return re.sub(r"\s+", " ", seg).strip()


def _fetch(url: str, timeout: int = 15) -> requests.Response | None:
    if not _is_public_url(url):
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        return r
    except Exception:
        return None


def _page_links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "tel:")):
            continue
        if href.lower().startswith("mailto:"):
            out.append((href, a.get_text(" ", strip=True)))
            continue
        full = _clean_url(urllib.parse.urljoin(base_url, href))
        if full.startswith(("http://", "https://")):
            out.append((full, a.get_text(" ", strip=True)))
    # Forms embedded straight into the page (iframe src / form action)
    for tag, attr in (("iframe", "src"), ("form", "action"), ("embed", "src")):
        for el in soup.find_all(tag, **{attr: True}):
            full = _clean_url(urllib.parse.urljoin(base_url, el[attr].strip()))
            if full.startswith(("http://", "https://")) and _classify(full) in ("form", "portal"):
                out.append((full, f"Embedded {tag}"))
    return out


def _doc_text(content: bytes, filename: str) -> str:
    try:
        from etenders_scraper.cft_documents import _extract_bytes
        return _extract_bytes(content, filename, max_chars=60000) or ""
    except Exception:
        return ""


def _fetch_document(url: str, label: str) -> dict[str, Any]:
    filename = urllib.parse.unquote(urllib.parse.urlparse(url).path.split("/")[-1]) or label or "document"
    item: dict[str, Any] = {"title": label or filename, "filename": filename, "url": url, "status": "not_read", "text": ""}
    if not filename.lower().endswith(EXTRACTABLE_EXTS):
        item["status"] = "listed_only"
        return item
    r = _fetch(url, timeout=25)
    if r is None or r.status_code != 200:
        item["status"] = f"http_{r.status_code}" if r is not None else "unreachable"
        return item
    text = _doc_text(r.content, filename)
    item["status"] = "read" if text.strip() else "no_text"
    item["text"] = text
    return item


def _fetch_page(url: str, label: str) -> dict[str, Any]:
    item: dict[str, Any] = {"title": label or url, "url": url, "status": "unreachable", "text": "", "links": []}
    r = _fetch(url, timeout=15)
    if r is None:
        return item
    if r.status_code != 200:
        item["status"] = f"http_{r.status_code}"
        return item
    ct = r.headers.get("Content-Type", "").lower()
    if "html" not in ct:
        item["status"] = "read"
        item["text"] = _doc_text(r.content, url.split("/")[-1] or "document.pdf")
        return item
    soup = BeautifulSoup(r.text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    item["links"] = _page_links(soup, r.url)
    item["page_title"] = title
    item["text"] = _visible_text(soup)
    item["status"] = "read"
    return item


def _relevant_subpage(url: str, label: str, primary_host: str) -> bool:
    p = urllib.parse.urlparse(url)
    host = p.netloc.lower()
    path = p.path.lower()
    if host != primary_host:
        return False
    if any(j in path for j in JUNK_PATH):
        return False
    if path.endswith(DOC_EXTS):
        return False
    low = (label or "").lower()
    keys = ("document", "attachment", "notice", "tender", "lot", "question", "clarification", "detail",
            "requirement", "specification", "itt", "appendix", "schedule", "instruction", "more info", "view")
    return any(k in low or k in path for k in keys)


def _collect_urls(text: str) -> list[str]:
    return [_clean_url(u) for u in URL_RE.findall(text or "")]


def _deadlines(text: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in DATE_RE.finditer(text or ""):
        ctx = re.sub(r"\s+", " ", text[max(0, m.start() - 110): m.end() + 40]).strip()
        if not DEADLINE_CTX.search(ctx):
            continue
        key = m.group(0).lower()
        if key in seen:
            continue
        seen.add(key)
        found.append({"date": m.group(0), "context": ctx})
        if len(found) >= 12:
            break
    return found


def deep_scrape_tender(source: str, resource_id: str, detail_url: str, structured: dict | None = None,
                       cft_docs: list[dict] | None = None, refresh: bool = False,
                       extra_documents: list[dict] | None = None) -> dict[str, Any]:
    key = (source, resource_id)
    now = time.time()
    if not refresh:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit and now - hit[0] < _CACHE_TTL_S:
                return {**hit[1], "cached": True}

    started = time.time()
    structured = structured or {}
    texts: dict[str, str] = {}          # where -> text
    found: dict[str, dict[str, Any]] = {}   # url -> link record
    pages: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    notes: list[str] = []

    def register(url: str, label: str, found_in: str, text_for_ctx: str = "") -> None:
        url = _clean_url(url)
        if url.lower().startswith("mailto:"):
            addr = url[7:].split("?")[0].strip()
            # Portal's own help desk / share links are page chrome, not tender contacts.
            if not addr or "@" not in addr or (primary_host and addr.split("@")[-1].lower() in primary_host):
                return
            url = "mailto:" + addr
        kind = _classify(url, label)
        if kind == "skip":
            return
        rec = found.get(url)
        if rec is None:
            rec = {"url": url, "kind": kind, "label": (label or "").strip(), "found_in": [], "context": ""}
            found[url] = rec
        if found_in not in rec["found_in"]:
            rec["found_in"].append(found_in)
        if label and not rec["label"]:
            rec["label"] = label.strip()
        if not rec["context"] and text_for_ctx and not url.startswith("mailto:"):
            rec["context"] = _context(text_for_ctx, url)

    # 1. Primary notice page
    primary_host = urllib.parse.urlparse(detail_url).netloc.lower() if detail_url else ""
    primary_text = ""
    primary_soup_links: list[tuple[str, str]] = []
    if detail_url:
        page = _fetch_page(detail_url, "Notice page")
        pages.append({k: v for k, v in page.items() if k not in ("text", "links")} | {"chars": len(page["text"])})
        primary_text = page["text"]
        primary_soup_links = page["links"]
        if page["status"] != "read":
            notes.append(f"The notice page could not be read ({page['status']}).")
        texts["Notice page"] = primary_text
    else:
        notes.append("This tender has no notice page URL, so only the stored details were checked.")

    # 2. Stored structured details (descriptions often hold URLs the page hides)
    structured_text = "\n".join(
        f"{k}: {v}" for k, v in structured.items() if isinstance(v, (str, int, float)) and str(v).strip()
    )
    if structured_text:
        texts["Stored tender details"] = structured_text

    for where, txt in list(texts.items()):
        for u in _collect_urls(txt):
            register(u, "", where, txt)
        for e in EMAIL_RE.findall(txt):
            register("mailto:" + e, e, where)
    for u, label in primary_soup_links:
        register(u, label, "Notice page links", primary_text)

    # 3. Attached documents (portal-specific list first, then linked files)
    doc_candidates: list[tuple[str, str]] = []
    for d in (cft_docs or []):
        if d.get("is_direct_url") and d.get("doc_id"):
            doc_candidates.append((d["doc_id"], d.get("title") or d.get("filename") or ""))
    for rec in list(found.values()):
        if rec["kind"] == "document":
            doc_candidates.append((rec["url"], rec["label"]))
    seen_doc: set[str] = set()
    uniq_docs = []
    for u, lbl in doc_candidates:
        if u not in seen_doc:
            seen_doc.add(u)
            uniq_docs.append((u, lbl))
    if len(uniq_docs) > MAX_DOCS:
        notes.append(f"{len(uniq_docs)} documents found; the first {MAX_DOCS} were read.")
    uniq_docs = uniq_docs[:MAX_DOCS]

    # 4. Same-site sub-pages (one level deep)
    sub_candidates: list[tuple[str, str]] = []
    seen_sub: set[str] = {detail_url.split("#")[0]} if detail_url else set()
    for u, label in primary_soup_links:
        if u.startswith("mailto:") or u in seen_sub:
            continue
        if _relevant_subpage(u, label, primary_host):
            seen_sub.add(u)
            sub_candidates.append((u, label))
    sub_candidates = sub_candidates[:MAX_PAGES]

    with ThreadPoolExecutor(max_workers=6) as pool:
        doc_futs = [pool.submit(_fetch_document, u, l) for u, l in uniq_docs]
        page_futs = [pool.submit(_fetch_page, u, l) for u, l in sub_candidates]
        for fut in doc_futs:
            remaining = max(1.0, TIME_BUDGET_S - (time.time() - started))
            try:
                d = fut.result(timeout=remaining)
            except Exception:
                continue
            documents.append(d)
        for fut in page_futs:
            remaining = max(1.0, TIME_BUDGET_S - (time.time() - started))
            try:
                pg = fut.result(timeout=remaining)
            except Exception:
                continue
            pages.append({k: v for k, v in pg.items() if k not in ("text", "links")} | {"chars": len(pg["text"])})
            if pg["text"]:
                texts[f"Page: {pg['title']}"] = pg["text"]
                for u, label in pg["links"]:
                    register(u, label, f"Page: {pg['title']}", pg["text"])

    documents.extend(extra_documents or [])
    for d in documents:
        if d["text"]:
            where = f"Document: {d['title']}"
            texts[where] = d["text"]
            for u in _collect_urls(d["text"]):
                register(u, "", where, d["text"])
            for e in EMAIL_RE.findall(d["text"]):
                register("mailto:" + e, e, where)

    # 5. Check each discovered form / portal link (title only, best effort)
    forms = [r for r in found.values() if r["kind"] == "form"]
    portals = [r for r in found.values() if r["kind"] == "portal" and
               urllib.parse.urlparse(r["url"]).netloc.lower() != primary_host]

    def probe(rec: dict[str, Any]) -> None:
        r = _fetch(rec["url"], timeout=10)
        if r is None:
            rec["reachable"] = None
            return
        rec["reachable"] = r.status_code < 400
        if "html" in r.headers.get("Content-Type", "").lower():
            m = re.search(r"<title[^>]*>(.*?)</title>", r.text[:20000], re.S | re.I)
            if m:
                rec["page_title"] = re.sub(r"\s+", " ", m.group(1)).strip()[:160]

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(probe, forms + portals[:5]))

    # 6. Action items: forms first, then portals, then sentences that ask the bidder to do something
    action_items: list[dict[str, Any]] = []
    for rec in forms:
        action_items.append({
            "type": "form", "url": rec["url"], "label": rec.get("page_title") or rec["label"] or "Online form",
            "context": rec["context"], "found_in": rec["found_in"], "reachable": rec.get("reachable"),
        })
    for rec in portals:
        action_items.append({
            "type": "portal", "url": rec["url"], "label": rec.get("page_title") or rec["label"] or "External portal",
            "context": rec["context"], "found_in": rec["found_in"], "reachable": rec.get("reachable"),
        })

    instructions: list[dict[str, str]] = []
    seen_sent: set[str] = set()
    for where, txt in texts.items():
        for sent in re.split(r"(?<=[.!?])\s+|\n+", txt):
            s = sent.strip()
            if 25 <= len(s) <= 400 and ACTION_WORDS.search(s) and (DATE_RE.search(s) or URL_RE.search(s) or
                                                                    re.search(r"\b(must|mandatory|required)\b", s, re.I)):
                k = s.lower()[:80]
                if k in seen_sent:
                    continue
                seen_sent.add(k)
                instructions.append({"text": s, "found_in": where})
                if len(instructions) >= 25:
                    break
        if len(instructions) >= 25:
            break

    all_text = "\n\n".join(texts.values())
    emails = sorted({r["url"][7:] for r in found.values() if r["kind"] == "email"})
    other_links = [
        {"url": r["url"], "label": r["label"], "found_in": r["found_in"]}
        for r in found.values()
        if r["kind"] == "other" and not any(j in r["url"].lower() for j in JUNK_PATH)
    ][:40]

    result = {
        "ok": True,
        "source": source,
        "resource_id": resource_id,
        "detail_url": detail_url,
        "action_items": action_items,
        "instructions": instructions,
        "deadlines": _deadlines(all_text),
        "emails": emails,
        "documents": [
            {"title": d["title"], "filename": d["filename"], "url": d["url"], "status": d["status"],
             "chars": len(d["text"]), "excerpt": d["text"][:600]}
            for d in documents
        ],
        "documents_unread": [
            {"title": lbl, "url": u} for u, lbl in doc_candidates if u not in {d["url"] for d in documents}
        ][:20],
        "pages": pages,
        "other_links": other_links,
        "text_chars": len(all_text),
        "full_text": all_text[:80000],
        "notes": notes,
        "elapsed_s": round(time.time() - started, 1),
        "cached": False,
    }
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), result)
        if len(_CACHE) > 200:
            for k in sorted(_CACHE, key=lambda x: _CACHE[x][0])[:50]:
                _CACHE.pop(k, None)
    return result
