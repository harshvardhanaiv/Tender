from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import urljoin

from .client import DEFAULT_BASE_URL, EtendersClient
from .filters import SearchFilters, apply_filters, discover_field_map
from .parser import (
    DETAIL_URL_TEMPLATE,
    extract_pagination_token,
    parse_detail_fields,
    parse_form_fields,
    parse_results_summary,
    parse_search_rows,
)

logger = logging.getLogger(__name__)

ADVANCED_FORM_PATH = "prepareAdvancedSearch.do?type=cftFTS"
LIST_PATH = "quickSearchAction.do"
SEARCH_TYPE = "cftFTS"
PAGE_SIZE_PARAM = "T01_ps"


@dataclass
class _SearchContext:
    """Keeps advanced-search session state for pagination."""

    method: str
    action: str
    payload: dict[str, Any]


class EtendersScraper:
    def __init__(
        self,
        *,
        delay_seconds: float = 1.0,
        page_size: int = 50,
        base_url: str | None = None,
        timeout: int = 60,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/") + "/"
        self.client = EtendersClient(
            delay_seconds=delay_seconds,
            base_url=self.base_url,
            timeout=timeout,
        )
        self.page_size = page_size
        self._pagination_token: str | None = None
        self._search_context: _SearchContext | None = None

    def discover_fields(self) -> dict[str, str | None]:
        html = self.client.get(ADVANCED_FORM_PATH)
        _, _, fields = parse_form_fields(html)
        return discover_field_map(fields)

    def search_by_description(
        self,
        query: str,
        *,
        max_results: int = 100,
        max_pages: int | None = None,
    ) -> list[dict[str, Any]]:
        """Advanced-search by Description field and return list rows (no detail fetch)."""
        if max_pages is None:
            max_pages = max(1, (max_results + self.page_size - 1) // self.page_size)
        filters = SearchFilters(description=query)
        rows = self.scrape_advanced(
            filters,
            max_pages=max_pages,
            max_rows=max_results,
            include_details=False,
        )
        return self._ensure_detail_urls(rows)

    def search_description_page(
        self,
        query: str,
        *,
        page: int,
    ) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
        """Search by Description and return exactly one page of results.

        Returns (rows, summary) where summary has keys start/end/total when available.
        """
        if page < 1:
            page = 1

        # Initialize advanced search context.
        self._pagination_token = None
        html = self.client.get(ADVANCED_FORM_PATH)
        method, action, form_fields = parse_form_fields(html)
        payload = apply_filters(form_fields, SearchFilters(description=query))
        payload[PAGE_SIZE_PARAM] = str(self.page_size)

        target_action = action or "advancedSearchAction.do"
        self._search_context = _SearchContext(
            method=method,
            action=target_action,
            payload=dict(payload),
        )

        # Fetch page 1 to discover the displaytag pagination token.
        first_html = self._fetch_advanced_search_page(1)
        if page == 1:
            rows = self._ensure_detail_urls(parse_search_rows(first_html, self.base_url))
            return rows, parse_results_summary(first_html)

        # Token is now known; fetch the requested page directly without re-fetching page 1.
        page_html = self._fetch_advanced_search_page(page)
        rows = self._ensure_detail_urls(parse_search_rows(page_html, self.base_url))
        return rows, parse_results_summary(page_html)

    def fetch_tender_details(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fetch full CfT workspace data for each row (used on export)."""
        return self._enrich_rows_with_details(rows)

    def scrape_list(
        self,
        *,
        max_pages: int | None = None,
        max_rows: int | None = None,
        include_details: bool = False,
    ) -> list[dict[str, Any]]:
        self._search_context = None
        rows = list(
            self.iter_list(max_pages=max_pages, max_rows=max_rows),
        )
        return self._enrich_rows_with_details(rows) if include_details else rows

    def iter_list(
        self,
        *,
        max_pages: int | None = None,
        max_rows: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        page = 1
        fetched = 0
        while True:
            if max_pages is not None and page > max_pages:
                break
            html = self._fetch_quick_search_page(page)
            rows = parse_search_rows(html, self.base_url)
            if not rows:
                logger.info("No rows on page %s; stopping.", page)
                break

            for row in rows:
                yield row
                fetched += 1
                if max_rows is not None and fetched >= max_rows:
                    return

            summary = parse_results_summary(html)
            if summary and summary["end"] >= summary["total"]:
                break
            page += 1

    def scrape_advanced(
        self,
        filters: SearchFilters,
        *,
        max_pages: int | None = None,
        max_rows: int | None = None,
        include_details: bool = False,
    ) -> list[dict[str, Any]]:
        rows = list(
            self.iter_advanced(filters, max_pages=max_pages, max_rows=max_rows),
        )
        return self._enrich_rows_with_details(rows) if include_details else rows

    def iter_advanced(
        self,
        filters: SearchFilters,
        *,
        max_pages: int | None = None,
        max_rows: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        self._pagination_token = None
        html = self.client.get(ADVANCED_FORM_PATH)
        method, action, form_fields = parse_form_fields(html)
        payload = apply_filters(form_fields, filters)
        payload[PAGE_SIZE_PARAM] = str(self.page_size)

        target_action = action or "advancedSearchAction.do"
        self._search_context = _SearchContext(
            method=method,
            action=target_action,
            payload=dict(payload),
        )

        html = self._fetch_advanced_search_page(1)
        yield from self._iter_from_first_results_html(
            html,
            start_page=1,
            max_pages=max_pages,
            max_rows=max_rows,
            use_advanced_pagination=True,
        )

    def _pagination_params(self, page: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            PAGE_SIZE_PARAM: self.page_size,
            "type": SEARCH_TYPE,
        }
        if self._pagination_token:
            params[f"d-{self._pagination_token}-p"] = page
        return params

    def _fetch_quick_search_page(self, page: int) -> str:
        params = self._pagination_params(page)
        params["searchType"] = SEARCH_TYPE

        html = self.client.get(LIST_PATH, params=params)
        if not self._pagination_token:
            self._pagination_token = extract_pagination_token(html)
            if self._pagination_token and page > 1:
                params[f"d-{self._pagination_token}-p"] = page
                html = self.client.get(LIST_PATH, params=params)
        return html

    def _fetch_advanced_search_page(self, page: int) -> str:
        if self._search_context is None:
            raise RuntimeError("Advanced search was not initialised")

        ctx = self._search_context
        request_data: dict[str, Any] = {
            **ctx.payload,
            **self._pagination_params(page),
        }

        target = urljoin(self.base_url, ctx.action)
        if ctx.method == "get":
            html = self.client.get(target, params=request_data)
        else:
            html = self.client.post(target, data=request_data)

        if not self._pagination_token:
            self._pagination_token = extract_pagination_token(html)
            if self._pagination_token and page > 1:
                request_data[f"d-{self._pagination_token}-p"] = page
                if ctx.method == "get":
                    html = self.client.get(target, params=request_data)
                else:
                    html = self.client.post(target, data=request_data)
        return html

    def _iter_from_first_results_html(
        self,
        html: str,
        *,
        start_page: int,
        max_pages: int | None,
        max_rows: int | None,
        use_advanced_pagination: bool = False,
    ) -> Iterator[dict[str, Any]]:
        self._pagination_token = extract_pagination_token(html) or self._pagination_token
        page = start_page
        fetched = 0

        while True:
            if max_pages is not None and (page - start_page) >= max_pages:
                break

            if page == start_page:
                current_html = html
            elif use_advanced_pagination:
                current_html = self._fetch_advanced_search_page(page)
            else:
                current_html = self._fetch_quick_search_page(page)

            rows = parse_search_rows(current_html, self.base_url)
            if not rows:
                break

            for row in rows:
                yield row
                fetched += 1
                if max_rows is not None and fetched >= max_rows:
                    return

            summary = parse_results_summary(current_html)
            if summary and summary["end"] >= summary["total"]:
                break
            page += 1

    def _ensure_detail_urls(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for row in rows:
            resource_id = row.get("resource_id")
            if resource_id and not row.get("detail_url"):
                row["detail_url"] = urljoin(
                    self.base_url,
                    DETAIL_URL_TEMPLATE.format(resource_id=resource_id),
                )
        return rows

    def _enrich_rows_with_details(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def _fetch_one(index_row: tuple[int, dict[str, Any]]) -> None:
            index, row = index_row
            resource_id = row.get("resource_id")
            detail_url = row.get("detail_url")
            if not detail_url and resource_id:
                detail_url = urljoin(
                    self.base_url,
                    DETAIL_URL_TEMPLATE.format(resource_id=resource_id),
                )
                row["detail_url"] = detail_url
            if not detail_url:
                return
            try:
                html = self.client.get(detail_url)
                details = parse_detail_fields(html)
                merged = dict(details)
                merged.update({k: v for k, v in row.items() if v})
                row.clear()
                row.update(merged)
                row["resource_id"] = resource_id or row.get("resource_id", "")
                row["detail_url"] = detail_url
            except Exception as exc:  # noqa: BLE001 - keep scraping on per-row failure
                logger.warning("Failed to fetch details for row %s (%s): %s", index, detail_url, exc)
                row["detail_fetch_error"] = str(exc)

        # Parallel detail fetching — keeps relative order, 5 concurrent requests.
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(_fetch_one, item): item for item in enumerate(rows, start=1)}
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Unexpected error in detail worker: %s", exc)
        return rows
