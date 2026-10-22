"""Import Innovate UK grants from UKRI Gateway to Research (GtR) into grant_awards.

GtR is a database of funded research and innovation projects, not of tenders: each row here is a grant,
so it is stored apart from contract_awards and never counted in contract totals. The public API needs no key.
A project lists its funding (value and dates) and its lead organisation behind separate links, so each
project costs one funding call; organisations are fetched once and cached.

    python -m tender_app.gtr_import --years 3            # the live import
    python -m tender_app.gtr_import --years 1 --limit 50 # a small trial
"""
from __future__ import annotations

import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator

import requests

from etenders_scraper.awards import supplier_name_key

API = "https://gtr.ukri.org/gtr/api"
PROJECT_PAGE = "https://gtr.ukri.org/projects?ref="
FUNDER = "Innovate UK"
_PAGE_SIZE = 100
_WORKERS = 3
_TIMEOUT = 40
_MIN_GAP = 0.25      # seconds between any two requests, shared by every worker: GtR answers 429 above a few a second
_ATTEMPTS = 8
_pace_lock = threading.Lock()
_last_request = 0.0


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/json", "User-Agent": "TenderFlow grant import"})
    return s


def _get_json(session: requests.Session, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET one JSON document, paced, retrying a rate limit (429), a server error or a non-JSON answer with a
    growing wait (or the server's Retry-After). A rate-limited import used to lose whole batches."""
    global _last_request
    for attempt in range(_ATTEMPTS):
        with _pace_lock:
            wait = _MIN_GAP - (time.monotonic() - _last_request)
            if wait > 0:
                time.sleep(wait)
            _last_request = time.monotonic()
        retry_after = None
        try:
            resp = session.get(url, params=params, timeout=_TIMEOUT)
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
            else:
                resp.raise_for_status()
                return resp.json()
        except (requests.ConnectionError, requests.Timeout, ValueError):
            pass  # ValueError: an HTML or empty answer where JSON was expected, which is what a rate limit looks like
        delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(60, 2 ** (attempt + 1))
        time.sleep(delay)
    raise RuntimeError(f"GtR did not answer after {_ATTEMPTS} tries: {url}")


def _https(url: str) -> str:
    return url.replace("http://", "https://", 1)


def _day(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _link(project: dict[str, Any], rel: str) -> str | None:
    for link in (project.get("links") or {}).get("link") or []:
        if link.get("rel") == rel and link.get("href"):
            return _https(link["href"])
    return None


def iter_projects(session: requests.Session, funder: str = FUNDER) -> Iterator[dict[str, Any]]:
    """Every project led by `funder`, newest start date first."""
    page = 1
    while True:
        data = _get_json(
            session,
            f"{API}/projects",
            {"q": funder, "f": "pro.lf", "s": _PAGE_SIZE, "p": page, "so": "D", "sf": "pro.sd"},
        )
        items = data.get("project") or []
        yield from items
        if page >= int(data.get("totalPages") or 0) or not items:
            return
        page += 1


def fetch_funding(session: requests.Session, project: dict[str, Any]) -> dict[str, Any]:
    url = _link(project, "FUND")
    if not url:
        return {}
    fund = _get_json(session, url)
    amount = (fund.get("valuePounds") or {}).get("amount")
    return {"value": float(amount) if amount is not None else None, "start": _day(fund.get("start")), "end": _day(fund.get("end"))}


def fetch_org(session: requests.Session, url: str) -> dict[str, Any]:
    # s=1 keeps the reply small: an organisation links to every project it has ever had.
    org = _get_json(session, url, {"s": 1})
    address = next(iter((org.get("addresses") or {}).get("address") or []), {}) or {}
    return {
        "name": (org.get("name") or "").strip(),
        "reg": (org.get("regNumber") or "").strip() or None,
        "website": org.get("website") or None,
        "postcode": address.get("postCode"),
        "region": address.get("region"),
    }


def build_record(project: dict[str, Any], funding: dict[str, Any], org: dict[str, Any]) -> dict[str, Any] | None:
    name = org.get("name") or ""
    pid = project.get("id")
    if not pid or not name or not project.get("title"):
        return None
    ref = next((i.get("value") for i in (project.get("identifiers") or {}).get("identifier") or [] if i.get("value")), None)
    return {
        "gtr_project_id": pid,
        "supplier_name": name,
        "name_key": supplier_name_key(name),
        "company_number": org.get("reg"),
        "postcode": org.get("postcode"),
        "region": org.get("region"),
        "website": org.get("website"),
        "funder": project.get("leadFunder") or FUNDER,
        "programme": project.get("grantCategory"),
        "title": project["title"].strip(),
        "abstract": (project.get("abstractText") or project.get("techAbstractText") or "").strip() or None,
        "status": project.get("status"),
        "value_gbp": funding.get("value"),
        "start_date": funding.get("start"),
        "end_date": funding.get("end"),
        "project_ref": ref,
        "url": f"{PROJECT_PAGE}{ref}" if ref else f"https://gtr.ukri.org/project/{pid}",
    }


_COLUMNS = (
    "gtr_project_id", "supplier_id", "supplier_name", "name_key", "company_number", "postcode", "region", "website",
    "funder", "programme", "title", "abstract", "status", "value_gbp", "start_date", "end_date", "project_ref", "url",
)


def match_supplier(cursor: Any, name_key: str) -> int | None:
    """The existing supplier a grant recipient is, if exactly one has this name. Never creates a supplier:
    thousands of grant-only companies would bury the supplier search in firms with no contracts."""
    if not name_key:
        return None
    cursor.execute(
        "SELECT id FROM suppliers WHERE UPPER(REGEXP_REPLACE(name, '[^A-Za-z0-9]', '', 'g')) = %s LIMIT 2",
        (name_key,),
    )
    rows = cursor.fetchall()
    return rows[0][0] if len(rows) == 1 else None


def save_record(cursor: Any, rec: dict[str, Any]) -> bool:
    """Insert or refresh one grant; True when it was new."""
    row = dict(rec, supplier_id=match_supplier(cursor, rec["name_key"]))
    cols = ", ".join(_COLUMNS)
    marks = ", ".join(["%s"] * len(_COLUMNS))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _COLUMNS if c != "gtr_project_id")
    cursor.execute(
        f"INSERT INTO grant_awards ({cols}) VALUES ({marks}) ON CONFLICT (gtr_project_id) DO UPDATE SET {updates} "
        "RETURNING (xmax = 0)",
        tuple(row[c] for c in _COLUMNS),
    )
    return bool(cursor.fetchone()[0])


def import_grants(conn: Any, years: float = 3, limit: int | None = None, log=print) -> dict[str, int]:
    """Walk GtR newest first and stop at the first project that started before the cut-off."""
    cutoff = (date.today() - timedelta(days=int(365 * years))).isoformat()
    session = _session()
    org_cache: dict[str, dict[str, Any]] = {}
    stats = {"seen": 0, "new": 0, "updated": 0, "skipped": 0}
    cursor = conn.cursor()

    def prepare(project: dict[str, Any]) -> dict[str, Any] | None:
        funding = fetch_funding(session, project)
        if (funding.get("start") or "9999") < cutoff:
            return {"stop": True}
        org_url = _link(project, "LEAD_ORG")
        if not org_url:
            return None
        if org_url not in org_cache:
            org_cache[org_url] = fetch_org(session, org_url)
        return build_record(project, funding, org_cache[org_url])

    batch: list[dict[str, Any]] = []
    done = False

    def flush() -> None:
        nonlocal done
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            results = list(pool.map(_safe(prepare), batch))
        for rec in results:
            if rec and rec.get("stop"):
                done = True
                continue
            if done:
                continue
            if not rec:
                stats["skipped"] += 1
                continue
            stats["new" if save_record(cursor, rec) else "updated"] += 1
        conn.commit()
        batch.clear()
        log(f"[gtr] {stats}")

    for project in iter_projects(session):
        stats["seen"] += 1
        batch.append(project)
        if len(batch) >= _PAGE_SIZE:
            flush()
            if done or (limit and stats["seen"] >= limit):
                break
    if batch and not done:
        flush()
    cursor.close()
    return stats


def _safe(fn):
    def run(arg):
        try:
            return fn(arg)
        except Exception as exc:  # one bad project must not stop the import
            print(f"[gtr] skipped a project: {exc}")
            return None

    return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--years", type=float, default=3)
    parser.add_argument("--limit", type=int, default=None, help="stop after about this many projects")
    args = parser.parse_args()
    from tender_app.db import get_db_connection
    from tender_app.db_ext import create_grant_awards

    conn = get_db_connection()
    cur = conn.cursor()
    create_grant_awards(cur)
    conn.commit()
    cur.close()
    try:
        print(import_grants(conn, years=args.years, limit=args.limit))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
