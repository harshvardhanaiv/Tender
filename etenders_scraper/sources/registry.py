from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..deadline import enrich_deadline_fields
from ..filters import SearchFilters
from ..scraper import EtendersScraper
from .bravo_search import search_bravo_portal
from .etenders_portal import ni_manual_search_message, search_etenders_portal, _NI_SEARCH_TIMEOUT_SEC

# Page size for eTenders-style portals (Ireland / NI).
_ETENDERS_PAGE_SIZE = 100
# NI captcha: give 3 OCR attempts enough wall-clock time
_NI_TIMEOUT = _NI_SEARCH_TIMEOUT_SEC

SOURCES: dict[str, dict[str, str]] = {
    "etenders_ie": {
        "label": "Ireland eTenders",
        "type": "etenders",
        "base_url": "https://www.etenders.gov.ie/epps/",
        "confidence": "high",
        "source_type_label": "Direct Portal API",
    },
    "etenders_ni": {
        "label": "eTenders NI",
        "type": "etenders",
        "base_url": "https://etendersni.gov.uk/epps/",
        "requires_captcha": "1",
        "confidence": "high",
        "source_type_label": "Direct Portal API",
    },
    "sell2wales": {
        "label": "Sell2Wales",
        "type": "bravo",
        "site_root": "https://www.sell2wales.gov.wales",
        "confidence": "high",
        "source_type_label": "Direct Portal API",
    },
    "pcs": {
        "label": "Public Contracts Scotland",
        "type": "bravo",
        "site_root": "https://www.publiccontractsscotland.gov.uk",
        "confidence": "high",
        "source_type_label": "Direct Portal API",
    },
    "find_tender": {
        "label": "Find a Tender (UK)",
        "type": "find_tender",
        "site_root": "https://www.find-tender.service.gov.uk",
        "confidence": "high",
        "source_type_label": "Direct Portal API",
    },
    "contracts_finder": {
        "label": "Contracts Finder (UK)",
        "type": "contracts_finder",
        "site_root": "https://www.contractsfinder.service.gov.uk",
        "confidence": "high",
        "source_type_label": "Direct Portal API",
    },
    "procontract": {
        "label": "ProContract (UK Councils)",
        "type": "procontract",
        "site_root": "https://procontract.due-north.com",
        "confidence": "high",
        "source_type_label": "Direct Portal API",
    },
    "gca_agreements": {
        "label": "GCA Frameworks (UK)",
        "type": "gca_agreements",
        "site_root": "https://www.gca.gov.uk",
        "confidence": "high",
        "source_type_label": "Direct Portal API",
    },
    "sam_gov": {
        "label": "USA (SAM.gov)",
        "type": "meta",
        "domain": "sam.gov",
        "confidence": "lower",
        "source_type_label": "Web Indexed",
    },
    "canadabuys": {
        "label": "CanadaBuys",
        "type": "meta",
        "domain": "canadabuys.canada.ca",
        "confidence": "lower",
        "source_type_label": "Web Indexed",
    },
    "eu_ted": {
        "label": "EU Tenders (TED)",
        "type": "meta",
        "domain": "ted.europa.eu",
        "confidence": "lower",
        "source_type_label": "Web Indexed",
    },
    "austender": {
        "label": "Australia AusTender",
        "type": "meta",
        "domain": "tenders.gov.au",
        "confidence": "lower",
        "source_type_label": "Web Indexed",
    },
    "gebiz": {
        "label": "Singapore (GeBIZ)",
        "type": "gebiz",
        "site_root": "https://www.gebiz.gov.sg",
        "confidence": "high",
        "source_type_label": "Direct Portal API",
    },
    "gets_nz": {
        "label": "New Zealand (GETS)",
        "type": "gets",
        "site_root": "https://www.gets.govt.nz",
        "confidence": "high",
        "source_type_label": "Direct Portal API",
    },
    "boamp": {
        "label": "France (BOAMP)",
        "type": "boamp",
        "site_root": "https://www.boamp.fr",
        "confidence": "high",
        "source_type_label": "Direct Portal API",
    },
    "bund": {
        "label": "Germany (Bund.de)",
        "type": "bund",
        "site_root": "https://www.evergabe-online.de",
        "confidence": "high",
        "source_type_label": "Direct Portal API",
    },
}

# Portals whose notices are UK / Ireland tenders -- the only ones the county filter can place.
# Any portal missing from here has ALL of its rows removed whenever a county is selected, so a
# new UK portal must be added here (this is how ProContract and GCA vanished from England-only
# searches). Keep in step with REGION_PORTAL_IDS in web/app.js and UK_IE_SOURCE_IDS in
# web/uk_counties.js; tests/test_county_filter_portals.py checks all three.
UK_IE_SOURCE_IDS = frozenset(
    {
        "etenders_ie",
        "etenders_ni",
        "sell2wales",
        "pcs",
        "find_tender",
        "contracts_finder",
        "procontract",
        "gca_agreements",
    }
)


def source_public_url(cfg: dict[str, str]) -> str:
    if cfg["type"] == "etenders":
        return cfg["base_url"].rstrip("/") + "/home.do"
    return cfg.get("site_root", "")


def _fetch_ni_portal(
    source_id: str, cfg: dict[str, str], keyword: str
) -> tuple[list[dict[str, Any]], str | None]:
    """NI search — fetch all matching pages."""
    return search_etenders_portal(
        base_url=cfg["base_url"],
        keyword=keyword,
        source_id=source_id,
        source_label=cfg["label"],
        max_results=2000,
        max_pages=20,
        requires_captcha=True,
        time_budget_sec=_NI_TIMEOUT,
    )


def _fetch_ie_batch(
    source_id: str,
    cfg: dict[str, str],
    keyword: str,
    *,
    on_progress: Callable[[list[dict[str, Any]]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    progress_every: int = 1000,
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch every Ireland eTenders result for `keyword`. Returns (rows, completed).

    No page/row cap: Ireland is fast, so fetch every matching page. iter_advanced stops when the
    portal reports the last page or returns no rows. The repeat guard only protects against a
    portal re-serving its last page when the summary can't be parsed.

    on_progress(rows_so_far) is called each time another `progress_every` rows have arrived, so
    a caller can show results while a large search is still running. If should_stop() turns true
    the fetch is abandoned and completed is False."""
    scraper = EtendersScraper(
        delay_seconds=0.15,
        page_size=_ETENDERS_PAGE_SIZE,
        base_url=cfg["base_url"],
    )
    batch: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    repeats = 0
    next_report = progress_every
    for row in scraper.iter_advanced(SearchFilters(description=keyword)):
        if should_stop and should_stop():
            return batch, False
        rid = row.get("resource_id")
        if rid:
            if rid in seen_ids:
                repeats += 1
                if repeats >= _ETENDERS_PAGE_SIZE:
                    break
                continue
            seen_ids.add(rid)
        repeats = 0
        row["source"] = source_id
        row["source_label"] = cfg["label"]
        batch.append(row)
        if on_progress and len(batch) >= next_report:
            on_progress(list(batch))
            next_report += progress_every
    return batch, True


def _fetch_source_batch(
    source_id: str, cfg: dict[str, str], keyword: str, notes: dict[str, Any] | None = None
) -> tuple[str, list[dict[str, Any]], str | None]:
    """Everything one portal returns for `keyword`: (source id, rows, warning). `notes`, when given,
    receives extra facts about the answer (`available`: how many notices the portal says match)."""
    if cfg["type"] == "etenders":
        if source_id == "etenders_ie":
            batch, _ = _fetch_ie_batch(source_id, cfg, keyword)
            return source_id, batch, None
        batch, err = _fetch_ni_portal(source_id, cfg, keyword)
        return source_id, batch, err
    elif cfg["type"] == "find_tender":
        from .find_tender import search_find_tender
        info: dict[str, Any] = {}
        batch = search_find_tender(
            keyword=keyword,
            source_id=source_id,
            source_label=cfg["label"],
            max_results=None,
            info=info,
        )
        if notes is not None and info.get("total") is not None:
            notes["available"] = info["total"]
        # A page that failed part-way still leaves the earlier ones: say so rather than present the
        # rows as the whole answer (same idea as the Contracts Finder partial-scan warning below).
        err = (
            f"Only part of the Find a Tender results could be read ({info['note']}), so these results are incomplete."
            if info.get("note")
            else None
        )
        return source_id, batch, err
    elif cfg["type"] == "contracts_finder":
        from .contracts_finder import search_contracts_finder
        notes: list[str] = []
        batch = search_contracts_finder(
            keyword=keyword,
            source_id=source_id,
            source_label=cfg["label"],
            max_results=None,
            notes=notes,
        )
        # A scan cut short by a rate limit still returns the newest notices it got; say so, or the
        # search shows them as the complete result (Rounds 24-26: "completed" with a handful of rows).
        err = (
            f"Only part of the Contracts Finder feed could be searched ({notes[0]}), so these results are incomplete."
            if notes
            else None
        )
        return source_id, batch, err
    elif cfg["type"] == "procontract":
        from .procontract import search_procontract
        batch = search_procontract(
            keyword=keyword,
            source_id=source_id,
            source_label=cfg["label"],
            max_results=None,
        )
        return source_id, batch, None
    elif cfg["type"] == "gca_agreements" or source_id == "gca_agreements":
        from .gca_agreements import search_gca_agreements
        batch = search_gca_agreements(
            keyword=keyword,
            source_id=source_id,
            source_label=cfg["label"],
            max_results=None,
        )
        return source_id, batch, None
    elif source_id == "sam_gov":
        from .sam_gov_api import search_sam_gov_api
        batch, err = search_sam_gov_api(keyword=keyword)
        return source_id, batch, err
    elif source_id == "eu_ted":
        from .ted_api import search_eu_ted_api
        batch, err = search_eu_ted_api(keyword=keyword)
        return source_id, batch, err
    elif source_id == "canadabuys":
        from .canadabuys_api import search_canadabuys_api
        batch, err = search_canadabuys_api(keyword=keyword)
        return source_id, batch, err
    elif source_id == "austender":
        from .austender_api import search_austender_api
        batch, err = search_austender_api(keyword=keyword)
        return source_id, batch, err
    elif source_id == "gets_nz" or cfg.get("type") == "gets":
        from .gets_api import search_gets_api
        batch, err = search_gets_api(keyword=keyword)
        return source_id, batch, err
    elif source_id == "gebiz" or cfg.get("type") == "gebiz":
        from .gebiz_api import search_gebiz_api
        batch, err = search_gebiz_api(keyword=keyword)
        return source_id, batch, err
    elif source_id == "boamp" or cfg.get("type") == "boamp":
        from .boamp_api import search_boamp_api
        batch, err = search_boamp_api(keyword=keyword)
        return source_id, batch, err
    elif source_id == "bund" or cfg.get("type") == "bund":
        from .bund_api import search_bund_api
        batch, err = search_bund_api(keyword=keyword)
        return source_id, batch, err
    elif cfg["type"] == "meta":
        from .meta_search import search_meta_portal
        batch = search_meta_portal(
            keyword=keyword,
            source_id=source_id,
            source_label=cfg["label"],
            domain=cfg["domain"],
        )
        return source_id, batch, None
    batch = search_bravo_portal(
        site_root=cfg["site_root"],
        keyword=keyword,
        source_id=source_id,
        source_label=cfg["label"],
        max_results=None,
    )
    return source_id, batch, None


def search_single_source(keyword: str, source_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Search one portal only."""
    keyword = keyword.strip()
    cfg = SOURCES.get(source_id)
    if not cfg:
        raise ValueError(f"Unknown source: {source_id}")

    sid, batch, err = _fetch_source_batch(source_id, cfg, keyword)
    errors = {sid: err} if err else {}
    rows: list[dict[str, Any]] = []
    for row in batch:
        enrich_deadline_fields(row)
        rows.append(row)
    rows.sort(key=_sort_key)

    critical = sum(1 for r in rows if r.get("deadline_urgency") == "critical")
    meta = {
        "errors": errors,
        "source_counts": {source_id: len(rows)},
        "total": len(rows),
        "urgent_count": critical,
        "soon_count": sum(1 for r in rows if r.get("deadline_urgency") == "soon"),
        "deadline_alert_days": 10,
    }
    return rows, meta


def search_all_sources(keyword: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Search every configured portal and return merged rows + meta."""
    keyword = keyword.strip()
    if not keyword:
        return [], {"errors": {}, "source_counts": {}}

    rows_by_source: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}

    timings: dict[str, float] = {}

    def _run(source_id: str, cfg: dict[str, str]) -> tuple[str, list[dict[str, Any]], str | None]:
        t0 = time.monotonic()
        result = _fetch_source_batch(source_id, cfg, keyword)
        timings[source_id] = round(time.monotonic() - t0, 1)
        return result

    # Portals run in parallel; total wait ≈ slowest portal (usually Scotland), not sum.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_run, sid, cfg): sid for sid, cfg in SOURCES.items()
        }
        for fut in as_completed(futures, timeout=900):
            sid = futures[fut]
            try:
                source_id, batch, err = fut.result()
                rows_by_source[source_id] = batch
                if err:
                    errors[source_id] = err
            except Exception as exc:  # noqa: BLE001
                errors[sid] = str(exc)
                rows_by_source[sid] = []

    for sid in SOURCES:
        batch = rows_by_source.get(sid, [])
        for row in batch:
            enrich_deadline_fields(row)
        batch.sort(key=_sort_key)
        rows_by_source[sid] = batch

    merged = _interleave_sources(rows_by_source)

    critical = sum(1 for r in merged if r.get("deadline_urgency") == "critical")
    soon = sum(1 for r in merged if r.get("deadline_urgency") == "soon")

    meta = {
        "errors": errors,
        "source_counts": {sid: len(rows_by_source.get(sid, [])) for sid in SOURCES},
        "source_timings_sec": timings,
        "total": len(merged),
        "urgent_count": critical,
        "soon_count": soon,
        "deadline_alert_days": 10,
    }
    return merged, meta


def _interleave_sources(rows_by_source: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Round-robin merge so each portal appears in the combined list."""
    lists = [rows_by_source[sid] for sid in SOURCES if rows_by_source.get(sid)]
    if not lists:
        return []
    merged: list[dict[str, Any]] = []
    max_len = max(len(lst) for lst in lists)
    for i in range(max_len):
        for lst in lists:
            if i < len(lst):
                merged.append(lst[i])
    return merged


def _sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    """Critical deadlines first, then soon, then by days ascending."""
    urgency = row.get("deadline_urgency") or "unknown"
    rank = {"critical": 0, "soon": 1, "normal": 2, "past": 3, "unknown": 4}.get(urgency, 4)
    days = row.get("days_until_deadline")
    days_sort = days if isinstance(days, int) else 9999
    return (rank, days_sort, (row.get("title") or "").lower())


def tender_key(source: str, resource_id: str) -> str:
    return f"{source}:{resource_id}"


def parse_tender_key(key: str) -> tuple[str, str]:
    if ":" in key:
        source, rid = key.split(":", 1)
        return source, rid
    return "etenders_ie", key


def search_ie_only_page(keyword: str, *, page: int, page_size: int) -> tuple[list[dict[str, Any]], int | None]:
    """Original Ireland eTenders paginated search (server-side pages)."""
    cfg = SOURCES["etenders_ie"]
    scraper = EtendersScraper(
        delay_seconds=0.15,
        page_size=page_size,
        base_url=cfg["base_url"],
    )
    rows, summary = scraper.search_description_page(keyword, page=page)
    for row in rows:
        row["source"] = "etenders_ie"
        row["source_label"] = cfg["label"]
        enrich_deadline_fields(row)
    total = summary["total"] if summary else None
    return rows, total
