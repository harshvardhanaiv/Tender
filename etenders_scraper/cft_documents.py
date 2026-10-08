"""Fetch and extract text from eTenders CfT tender documents (no login required)."""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

import requests
from bs4 import BeautifulSoup

from .client import DEFAULT_HEADERS

_DOCS_URL     = "{base_url}cft/listContractDocuments.do?resourceId={resource_id}"
_DOWNLOAD_URL = "{base_url}cft/downloadContractDocument.do?documentId={doc_id}&resourceId={resource_id}"

# Filetypes we attempt to extract text from
_TEXT_EXTS  = {".txt", ".xml", ".html", ".htm", ".csv"}
_PDF_EXTS   = {".pdf"}
_DOCX_EXTS  = {".docx"}
_SKIP_EXTS  = {".zip"}   # zip contents are extracted recursively


def list_cft_documents(
    base_url: str,
    resource_id: str,
    *,
    timeout: int = 15,
) -> list[dict[str, str]]:
    """Return list of {doc_id, title, filename} from the CfT documents tab."""
    url = _DOCS_URL.format(base_url=base_url.rstrip("/") + "/", resource_id=resource_id)
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    docs: list[dict[str, str]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", onclick=True):
        m = re.search(r"downloadDocForAnonymous\(['\"]?(\d+)['\"]?\)", a.get("onclick", ""))
        if not m:
            continue
        doc_id = m.group(1)
        if doc_id in seen:
            continue
        seen.add(doc_id)
        filename = a.get_text(" ", strip=True)
        # title comes from the sibling TD in the same row
        tr = a.find_parent("tr")
        title = ""
        if tr:
            tds = tr.find_all("td")
            if len(tds) >= 2:
                title = tds[1].get_text(" ", strip=True)
        docs.append({"doc_id": doc_id, "title": title or filename, "filename": filename})

    return docs


def fetch_and_extract_text(
    base_url: str,
    resource_id: str,
    doc_id: str,
    filename: str,
    *,
    timeout: int = 30,
    max_chars: int = 15_000,
) -> str:
    """Download a single document and return extracted plain text."""
    url = _DOWNLOAD_URL.format(
        base_url=base_url.rstrip("/") + "/",
        doc_id=doc_id,
        resource_id=resource_id,
    )
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
    except Exception:
        return ""

    return _extract_bytes(resp.content, filename, max_chars=max_chars)


def _extract_bytes(data: bytes, filename: str, *, max_chars: int = 15_000) -> str:
    name = filename.lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""

    if ext == ".zip":
        return _extract_zip(data, max_chars=max_chars)
    if ext in _PDF_EXTS:
        return _pdf_text(data)[:max_chars]
    if ext in _DOCX_EXTS:
        return _docx_text(data)[:max_chars]
    if ext in _TEXT_EXTS:
        return data.decode("utf-8", errors="ignore")[:max_chars]
    # Unknown — try UTF-8 decode as last resort
    try:
        return data.decode("utf-8", errors="ignore")[:max_chars]
    except Exception:
        return ""


def _extract_zip(data: bytes, *, max_chars: int) -> str:
    parts: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                fname = info.filename.lower()
                ext = "." + fname.rsplit(".", 1)[-1] if "." in fname else ""
                # Skip nested zips and tiny / binary files
                if ext in (".zip", ".png", ".jpg", ".gif", ".ico"):
                    continue
                try:
                    inner = zf.read(info.filename)
                    text = _extract_bytes(inner, info.filename, max_chars=max_chars // 3)
                    if text.strip():
                        parts.append(f"[{info.filename}]\n{text}")
                except Exception:
                    continue
    except Exception:
        return ""
    return "\n\n".join(parts)[:max_chars]


def _pdf_text(data: bytes) -> str:
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return "[PDF — install pypdf for text extraction]"


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            with z.open("word/document.xml") as doc:
                tree = ET.parse(doc)
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        return " ".join(el.text for el in tree.iter(f"{{{ns}}}t") if el.text)
    except Exception:
        return ""
