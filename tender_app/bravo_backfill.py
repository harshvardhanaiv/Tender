"""Fill value / procedure / publication date on tenders already stored (recent searches, saved searches, pipeline).

Some portals' list rows carry none of these; they only exist on the notice page. Each portal's own detail
fetcher is used (PCS, Sell2Wales, eTenders IE/NI, Find a Tender, Contracts Finder, ProContract, GCA). Portals
without a real detail fetcher (meta/API-only ones) are left alone, and placeholders are never written.
New Bravo searches fill them while scraping (bravo_search._fill_missing_from_details).

Run once for everything:  python -m tender_app.bravo_backfill
"""
from __future__ import annotations

import json
from typing import Any, Callable

from etenders_scraper.sources.bravo_search import fetch_bravo_details
from etenders_scraper.sources.registry import SOURCES, parse_tender_key

BRAVO_SOURCES = ("pcs", "sell2wales")
DETAIL_SOURCES = BRAVO_SOURCES + (
    "etenders_ie", "etenders_ni", "find_tender", "contracts_finder", "procontract", "gca_agreements",
)
_FIELDS = ("estimated_value_eur", "procedure", "date_published")
_PLACEHOLDERS = {"", "n/a", "na", "-", "none", "see official notice", "open opportunity"}


def _real(value: Any) -> str:
    v = str(value or "").strip()
    return "" if v.lower() in _PLACEHOLDERS else v


def fetch_detail(row: dict[str, Any]) -> dict[str, Any]:
    """Real notice details for one stored row, normalised to estimated_value_eur / procedure / date_published.
    Returns {} when the portal can't be reached or has no detail fetcher; never invents values."""
    source, rid = row["source"], str(row["resource_id"])
    try:
        if source in BRAVO_SOURCES:
            det = fetch_bravo_details(source, rid, site_root=_site_root(source), timeout=40)
        elif source == "find_tender":
            from etenders_scraper.sources.find_tender import fetch_find_tender_details
            det = fetch_find_tender_details(rid)
        elif source == "contracts_finder":
            from etenders_scraper.sources.contracts_finder import fetch_contracts_finder_details
            det = fetch_contracts_finder_details(rid)
        elif source == "procontract":
            from etenders_scraper.sources.procontract import fetch_procontract_details
            det = fetch_procontract_details(rid, detail_url=row.get("detail_url", ""))
        elif source == "gca_agreements":
            from etenders_scraper.sources.gca_agreements import fetch_gca_agreement_details
            det = fetch_gca_agreement_details(rid)
        elif source in ("etenders_ie", "etenders_ni"):
            from etenders_scraper.parser import DETAIL_URL_TEMPLATE, parse_detail_fields
            from etenders_scraper.scraper import EtendersScraper
            base = SOURCES[source]["base_url"]
            scraper = EtendersScraper(delay_seconds=0.5, base_url=base)
            html = scraper.client.get(f"{base.rstrip('/')}/{DETAIL_URL_TEMPLATE.format(resource_id=rid)}")
            det = parse_detail_fields(html)
        else:
            return {}
    except Exception:
        return {}
    det = det or {}
    return {
        "estimated_value_eur": _real(det.get("estimated_value_eur") or det.get("estimated_value")),
        "procedure": _real(det.get("procedure")),
        "date_published": _real(det.get("date_published") or det.get("date_of_publication_invitation")),
    }


def _site_root(source: str) -> str:
    return SOURCES[source]["site_root"]


def needs_fill(row: dict[str, Any]) -> bool:
    return (
        isinstance(row, dict)
        and row.get("source") in DETAIL_SOURCES
        and bool(row.get("resource_id"))
        and any(not row.get(f) for f in _FIELDS)
        and not row.get("_detail_filled")
    )


def enrich_rows(rows: list[dict[str, Any]], budget: float = 20.0, max_rows: int = 15) -> list[dict[str, Any]]:
    """Fill the blanks of the given rows in place (time-boxed). Returns the rows that changed."""
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    todo = [r for r in rows if needs_fill(r)][:max_rows]
    if not todo:
        return []

    def one(row):
        return row, fetch_detail(row)

    changed: list[dict[str, Any]] = []
    pool = ThreadPoolExecutor(max_workers=8)
    futures = [pool.submit(one, r) for r in todo]
    try:
        for fut in as_completed(futures, timeout=budget):
            try:
                row, det = fut.result()
            except Exception:
                continue
            if not det:
                continue
            for f in _FIELDS:
                if not row.get(f) and det.get(f):
                    row[f] = det[f]
                    if row not in changed:
                        changed.append(row)
            row["_detail_filled"] = True  # don't refetch notices that genuinely have no value
            if row not in changed:
                changed.append(row)
    except Exception:
        pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return changed


def persist_cache_rows(get_db_conn_fn: Callable, search_type: str, search_id: int, rows: list[dict[str, Any]]) -> None:
    from tender_app.saved_search_cache import _generate_tender_id, _get_table_meta

    if not rows:
        return
    _, cache_tbl, fk_col, _ = _get_table_meta(search_type)
    conn = get_db_conn_fn()
    cur = conn.cursor()
    for r in rows:
        clean = {k: v for k, v in r.items() if k not in ("_cached", "_first_seen_at", "_last_confirmed_at", "_is_new")}
        cur.execute(
            f"UPDATE {cache_tbl} SET tender_data = %s WHERE {fk_col} = %s AND tender_id = %s",
            (json.dumps(clean, default=str), search_id, _generate_tender_id(r)),
        )
    conn.commit()
    cur.close()
    conn.close()


def backfill_all(get_db_conn_fn: Callable, per_search_budget: float = 120.0, max_rows: int = 200) -> dict[str, int]:
    """Fill every stored recent/saved-search tender and every pipeline entry that is missing fields."""
    from tender_app.saved_search_cache import _get_table_meta

    stats = {"cache_rows_filled": 0, "pipeline_rows_filled": 0}
    for search_type in ("recent", "saved"):
        _, cache_tbl, fk_col, _ = _get_table_meta(search_type)
        conn = get_db_conn_fn()
        cur = conn.cursor()
        cur.execute(
            f"SELECT {fk_col}, tender_data FROM {cache_tbl} "
            "WHERE " + " OR ".join(f"tender_data::text LIKE '%\"source\": \"{s}\"%'" for s in DETAIL_SOURCES)
        )
        by_search: dict[int, list[dict[str, Any]]] = {}
        for sid, data in cur.fetchall():
            try:
                row = json.loads(data) if isinstance(data, str) else dict(data)
            except Exception:
                continue
            if needs_fill(row):
                by_search.setdefault(sid, []).append(row)
        cur.close()
        conn.close()
        print(f"[backfill] {search_type}: {sum(len(v) for v in by_search.values())} tenders to fill "
              f"across {len(by_search)} searches", flush=True)
        for sid, rows in by_search.items():
            changed = enrich_rows(rows, budget=per_search_budget, max_rows=max_rows)
            persist_cache_rows(get_db_conn_fn, search_type, sid, changed)
            stats["cache_rows_filled"] += len(changed)
            print(f"[backfill] {search_type} search {sid}: filled {len(changed)}/{len(rows)}", flush=True)

    # Pipeline ("saved" tenders): only the value column exists there.
    conn = get_db_conn_fn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, tender_key FROM pipeline WHERE (estimated_value IS NULL OR estimated_value = '') "
        "AND source IN (" + ",".join(f"'{s}'" for s in DETAIL_SOURCES) + ")"
    )
    pending = cur.fetchall()
    print(f"[backfill] pipeline: {len(pending)} entries to check", flush=True)
    for pid, key in pending:
        try:
            src, rid = parse_tender_key(key)
        except Exception:
            continue
        if src not in DETAIL_SOURCES:
            continue
        det = fetch_detail({"source": src, "resource_id": rid})
        if det.get("estimated_value_eur"):
            cur.execute("UPDATE pipeline SET estimated_value = %s WHERE id = %s", (det["estimated_value_eur"], pid))
            stats["pipeline_rows_filled"] += 1
    conn.commit()
    cur.close()
    conn.close()
    return stats


if __name__ == "__main__":
    from tender_app.db import get_db_connection

    print(backfill_all(get_db_connection))
