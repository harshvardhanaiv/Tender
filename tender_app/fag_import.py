"""Import the open government grants listed on Find a Grant (GOV.UK) into funding_opportunities.

Find a Grant has no API. Its pages are a Next.js site that carries each page's data as JSON in the page
itself (__NEXT_DATA__): the list at /grants?page=N has about ten grants a page with their name, funder, award
range, places, who may apply and dates, and /grants/<label> adds the apply link and the eligibility text. The
whole service is about 130 grants, so a daily refresh is a few dozen requests. The content is published under
the Open Government Licence v3.0 (the page says where the data comes from).

A grant is a chance to apply for money, not a tender, so it is stored apart from contracts and awards.

    python -m tender_app.fag_import
    python -m tender_app.fag_import --no-detail     # list pages only
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import time
from datetime import date
from typing import Any, Iterator

import requests

BASE = "https://find-government-grants.service.gov.uk"
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_GAP = 0.5            # seconds between requests: this is a small public service, be gentle with it
_TIMEOUT = 40
_ATTEMPTS = 4
_MAX_PAGES = 60       # a guard, the real list is about thirteen pages
_ELIGIBILITY_MAX = 1500
_SUMMARY_MAX = 1200
# The service insists on a closing date, so a scheme that is open all year gets a far-off one; its text says so.
_ONGOING_TEXT = re.compile(r"year[- ]round|all year|open (?:all|throughout)|rolling|no closing date|until funding (?:is )?(?:ex|all)", re.I)
_ONGOING_YEARS = 3


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "TenderFlow funding import", "Accept": "text/html"})
    return s


def page_data(html: str) -> dict[str, Any]:
    """The pageProps of a Find a Grant page, or {} when the page carries none."""
    m = _NEXT_DATA.search(html or "")
    if not m:
        return {}
    try:
        return (json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}
    except ValueError:
        return {}


def _get(session: requests.Session, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    for attempt in range(_ATTEMPTS):
        time.sleep(_GAP)
        try:
            resp = session.get(BASE + path, params=params, timeout=_TIMEOUT)
            if resp.status_code == 404:
                return {}
            if resp.status_code < 400:
                data = page_data(resp.text)
                if data:
                    return data
        except requests.RequestException:
            pass
        time.sleep(min(30, 2 ** (attempt + 1)))
    raise RuntimeError(f"Find a Grant did not answer: {path}")


def iter_list(session: requests.Session) -> Iterator[dict[str, Any]]:
    seen: set[str] = set()
    for page in range(1, _MAX_PAGES + 1):
        data = _get(session, "/grants", {"page": page})
        items = data.get("searchResult") or []
        fresh = [i for i in items if i.get("label") and i["label"] not in seen]
        if not fresh:
            return
        for item in fresh:
            seen.add(item["label"])
            yield item
        if len(seen) >= int(data.get("totalGrants") or 0):
            return


def rich_text(node: Any) -> str:
    """Plain text of a Contentful rich-text document (what the grant pages use for their tabs)."""
    parts: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, dict):
            if isinstance(n.get("value"), str):
                parts.append(n["value"])
            for child in n.get("content") or []:
                walk(child)
            if n.get("nodeType") in ("paragraph", "heading-1", "heading-2", "heading-3", "list-item"):
                parts.append("\n")
        elif isinstance(n, list):
            for child in n:
                walk(child)

    walk(node)
    return re.sub(r"\n\s*\n+", "\n", "".join(parts)).strip()


def _day(raw: Any) -> str | None:
    text = str(raw or "")[:10]
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def _num(raw: Any) -> float | None:
    return float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None


def _strings(raw: Any) -> list[str]:
    return [str(x).strip() for x in raw or [] if str(x).strip()] if isinstance(raw, list) else []


def is_ongoing(summary: str, close: str | None, today: date | None = None) -> bool:
    today = today or date.today()
    if _ONGOING_TEXT.search(summary or ""):
        return True
    return bool(close) and int(close[:4]) >= today.year + _ONGOING_YEARS


def build_record(item: dict[str, Any], detail: dict[str, Any] | None = None, today: date | None = None) -> dict[str, Any] | None:
    """One grant from its list entry (and, when fetched, its detail fields)."""
    label = (item.get("label") or "").strip()
    name = (item.get("grantName") or "").strip()
    if not label or not name:
        return None
    fields = (detail or {}).get("fields") or {}
    summary = (item.get("grantShortDescription") or fields.get("grantShortDescription") or "").strip()
    close = _day(item.get("grantApplicationCloseDate") or fields.get("grantApplicationCloseDate"))
    apply_url = fields.get("grantWebpageUrl") or None
    return {
        "label": label,
        "name": name,
        "funder": (item.get("grantFunder") or fields.get("grantFunder") or "").strip() or None,
        "summary": summary[:_SUMMARY_MAX] or None,
        "min_award": _num(item.get("grantMinimumAward")),
        "max_award": _num(item.get("grantMaximumAward")),
        "total_award": _num(item.get("grantTotalAwardAmount")),
        "locations": _strings(item.get("grantLocation")),
        "applicants": _strings(item.get("grantApplicantType")),
        "open_date": _day(item.get("grantApplicationOpenDate")),
        "close_date": close,
        "ongoing": is_ongoing(summary, close, today),
        "apply_url": apply_url if isinstance(apply_url, str) and apply_url.startswith("http") else None,
        "eligibility": rich_text(fields.get("grantEligibilityTab"))[:_ELIGIBILITY_MAX] or None,
        "url": f"{BASE}/grants/{label}",
    }


COLUMNS = (
    "label", "name", "funder", "summary", "min_award", "max_award", "total_award", "locations", "applicants",
    "open_date", "close_date", "ongoing", "apply_url", "eligibility", "url",
)


def save_record(cursor: Any, rec: dict[str, Any]) -> bool:
    """Insert or refresh one grant; True when it was new."""
    cols = ", ".join(COLUMNS)
    marks = ", ".join(["%s"] * len(COLUMNS))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c != "label")
    cursor.execute(
        f"INSERT INTO funding_opportunities ({cols}, seen_at) VALUES ({marks}, NOW()) ON CONFLICT (label) DO UPDATE "
        f"SET {updates}, seen_at = NOW() RETURNING (xmax = 0)",
        tuple(rec[c] for c in COLUMNS),
    )
    return bool(cursor.fetchone()[0])


def import_grants(conn: Any, detail: bool = True, log=print) -> dict[str, int]:
    """Read every listed grant and store it. Grants that are no longer listed (closed and withdrawn ones) are
    removed, so the table is always what the service shows today."""
    session = _session()
    cursor = conn.cursor()
    cursor.execute("SELECT NOW()")
    started = cursor.fetchone()[0]
    stats = {"seen": 0, "new": 0, "updated": 0, "skipped": 0, "removed": 0}
    for item in iter_list(session):
        stats["seen"] += 1
        fetched = None
        if detail:
            try:
                fetched = _get(session, f"/grants/{item['label']}").get("grantDetail")
            except RuntimeError as exc:
                log(f"[funding] no detail for {item['label']}: {exc}")
        rec = build_record(item, fetched)
        if not rec:
            stats["skipped"] += 1
            continue
        stats["new" if save_record(cursor, rec) else "updated"] += 1
    if stats["seen"]:  # an empty answer must never wipe the table
        cursor.execute("DELETE FROM funding_opportunities WHERE seen_at < %s", (started,))
        stats["removed"] = cursor.rowcount
    conn.commit()
    cursor.close()
    log(f"[funding] {stats}")
    return stats


REFRESH_AFTER_HOURS = 22   # a restart or a code reload must not re-read the service; only a stale table is refreshed
CHECK_EVERY_SECONDS = 6 * 3600


def is_stale(conn: Any, hours: float = REFRESH_AFTER_HOURS) -> bool:
    """True when the table is empty or its newest row was last seen more than `hours` ago."""
    from tender_app.db_ext import create_funding_opportunities

    cur = conn.cursor()
    try:
        create_funding_opportunities(cur)
        conn.commit()
        cur.execute("SELECT MAX(seen_at) < (NOW() AT TIME ZONE 'utc') - make_interval(hours => %s) FROM funding_opportunities", (hours,))
        stale = cur.fetchone()[0]
        return stale is None or bool(stale)
    finally:
        cur.close()


def refresh_if_stale(get_db_connection) -> dict[str, int] | None:
    conn = get_db_connection()
    try:
        return import_grants(conn) if is_stale(conn) else None
    finally:
        conn.close()


def start_funding_scheduler(get_db_connection) -> None:
    """Keep funding_opportunities fresh: a background thread that refreshes the table about once a day."""
    def _loop() -> None:
        while True:
            try:
                refresh_if_stale(get_db_connection)
            except Exception as exc:  # the service being down must not stop the thread
                print(f"[funding] refresh failed: {exc}")
            threading.Event().wait(CHECK_EVERY_SECONDS)

    threading.Thread(target=_loop, daemon=True, name="FundingRefresh").start()
    print("[funding] Daily refresh started.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-detail", action="store_true", help="skip the per-grant page (no apply link or eligibility)")
    args = parser.parse_args()
    from tender_app.db import get_db_connection
    from tender_app.db_ext import create_funding_opportunities

    conn = get_db_connection()
    cur = conn.cursor()
    create_funding_opportunities(cur)
    conn.commit()
    cur.close()
    try:
        print(import_grants(conn, detail=not args.no_detail))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
