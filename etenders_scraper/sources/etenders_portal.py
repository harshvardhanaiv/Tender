from __future__ import annotations

import time
from typing import Any

from ..filters import SearchFilters, apply_filters
from ..parser import parse_form_fields, parse_search_rows
from ..scraper import ADVANCED_FORM_PATH, PAGE_SIZE_PARAM, EtendersScraper, _SearchContext

_MAX_CAPTCHA_ATTEMPTS = 3
_NI_PAGE_SIZE = 10
_NI_SEARCH_TIMEOUT_SEC = 15  # give OCR more time — 3 attempts × ~4s each
_OCR_INSTANCE: Any = None


def ni_manual_search_message(source_label: str) -> str:
    return (
        f"{source_label} requires a captcha for automated searches. "
        "Open the portal manually to search this source."
    )


def search_etenders_portal(
    *,
    base_url: str,
    keyword: str,
    source_id: str,
    source_label: str,
    max_results: int | None = None,
    max_pages: int | None = None,
    requires_captcha: bool = False,
    time_budget_sec: float | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Search an eTenders-style epps portal. Returns (rows, error_message)."""
    page_size = _NI_PAGE_SIZE if requires_captcha else 50
    attempts = _MAX_CAPTCHA_ATTEMPTS if requires_captcha else 1
    if requires_captcha and max_pages is None:
        max_pages = 1
    deadline = (
        time.monotonic() + time_budget_sec if time_budget_sec else None
    )

    for _ in range(attempts):
        if deadline is not None and time.monotonic() >= deadline:
            return [], ni_manual_search_message(source_label)
        scraper = EtendersScraper(
            delay_seconds=0,
            page_size=page_size,
            base_url=base_url,
            timeout=8,
        )
        extra: dict[str, str] = {}
        if requires_captcha:
            captcha_value, captcha_err = _solve_captcha(scraper)
            if captcha_err:
                return [], captcha_err
            extra["captcha"] = captcha_value

        rows, blocked = _search_with_first_page_check(
            scraper,
            keyword=keyword,
            extra=extra,
            source_id=source_id,
            source_label=source_label,
            max_results=max_results,
            max_pages=max_pages,
            check_captcha=requires_captcha,
        )
        if blocked:
            if deadline is not None and time.monotonic() >= deadline:
                return [], ni_manual_search_message(source_label)
            continue
        return rows, None

    return [], ni_manual_search_message(source_label)


def _search_with_first_page_check(
    scraper: EtendersScraper,
    *,
    keyword: str,
    extra: dict[str, str],
    source_id: str,
    source_label: str,
    max_results: int | None,
    max_pages: int | None,
    check_captcha: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """Run advanced search. Returns (rows, blocked) where blocked means retry captcha."""
    scraper._pagination_token = None
    html = scraper.client.get(ADVANCED_FORM_PATH)
    method, action, form_fields = parse_form_fields(html)
    payload = apply_filters(form_fields, SearchFilters(description=keyword, extra=extra))
    payload[PAGE_SIZE_PARAM] = str(scraper.page_size)

    target_action = action or "advancedSearchAction.do"
    scraper._search_context = _SearchContext(
        method=method,
        action=target_action,
        payload=dict(payload),
    )

    first_html = scraper._fetch_advanced_search_page(1)
    if check_captcha and _captcha_failed(first_html):
        return [], True

    first_rows = parse_search_rows(first_html, scraper.base_url)
    # Only treat as blocked if captcha mismatch is explicit — empty results are valid
    if check_captcha and not first_rows and _captcha_failed(first_html):
        return [], True

    rows: list[dict[str, Any]] = []
    for row in scraper._iter_from_first_results_html(
        first_html,
        start_page=1,
        max_pages=max_pages,
        max_rows=max_results,
        use_advanced_pagination=True,
    ):
        row["source"] = source_id
        row["source_label"] = source_label
        rows.append(row)
    return rows, False


def _captcha_failed(html: str) -> bool:
    return "captcha mismatch" in html.lower()


def _get_ocr() -> Any:
    """Reuse one OCR instance (ddddocr load is slow on first call)."""
    global _OCR_INSTANCE
    if _OCR_INSTANCE is None:
        import ddddocr

        _OCR_INSTANCE = ddddocr.DdddOcr(show_ad=False)
    return _OCR_INSTANCE


def _solve_captcha(scraper: EtendersScraper) -> tuple[str | None, str | None]:
    try:
        _get_ocr()
    except ImportError:
        return None, (
            "eTenders NI requires captcha solving; install the ddddocr package."
        )

    captcha_url = scraper.base_url.rstrip("/") + "/genCaptcha/captcha.jpg"
    try:
        content = scraper.client.get_bytes(captcha_url)
    except Exception as e:
        return None, f"Failed to download captcha image: {e}"
    code = _get_ocr().classification(content)
    if not code:
        return None, "Could not read eTenders NI captcha image."
    return code, None
