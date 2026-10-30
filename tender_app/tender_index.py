"""The scheduled index of open tender notices (design: docs/PORTAL_INDEX_DESIGN.md, steps 1 and 2).

Nothing here is called by Tender Search yet: it fills `tender_notices` from the two OCDS feeds (Contracts Finder, Find a Tender) so
a later step can answer a search from the table instead of calling the portals. Every notice is normalised once, here, so "open" no
longer depends on the client guessing from a portal's own wording (re-test T1) and dates and regions are real values (T7).

  normalise_release(release, source, label)  one OCDS release -> one notice dict, or None
  upsert_notices(cursor, notices)            insert or refresh by (source, resource_id)
  close_expired(cursor)                      open notices whose deadline has passed become closed
  ingest_contracts_finder(get_conn, ...)     scan the Contracts Finder feed into the table
  ingest_find_tender(get_conn, ...)          read the Find a Tender OCDS feed into the table
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from .regions import itl1_region

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tender_notices (
    source                 TEXT NOT NULL,
    resource_id            TEXT NOT NULL,
    title                  TEXT NOT NULL DEFAULT '',
    description            TEXT NOT NULL DEFAULT '',
    contracting_authority  TEXT NOT NULL DEFAULT '',
    notice_type            TEXT NOT NULL DEFAULT '',
    status                 TEXT NOT NULL DEFAULT 'unknown',
    published_at           TIMESTAMPTZ,
    deadline_at            TIMESTAMPTZ,
    value                  DOUBLE PRECISION,
    currency               TEXT NOT NULL DEFAULT 'GBP',
    cpv_codes              TEXT NOT NULL DEFAULT '',
    location_text          TEXT NOT NULL DEFAULT '',
    region                 TEXT NOT NULL DEFAULT '',
    postcode               TEXT NOT NULL DEFAULT '',
    detail_url             TEXT NOT NULL DEFAULT '',
    raw                    JSONB,
    search_tsv             TSVECTOR,
    first_seen_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at              TIMESTAMPTZ,
    PRIMARY KEY (source, resource_id)
)
"""
INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS tender_notices_tsv ON tender_notices USING GIN (search_tsv)",
    "CREATE INDEX IF NOT EXISTS tender_notices_open ON tender_notices (status, deadline_at)",
    "CREATE INDEX IF NOT EXISTS tender_notices_seen ON tender_notices (source, last_seen_at)",
)
RUNS_SQL = """
CREATE TABLE IF NOT EXISTS index_runs (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    ok          BOOLEAN,
    complete    BOOLEAN,
    rows_seen   INTEGER NOT NULL DEFAULT 0,
    rows_new    INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT ''
)
"""

# A notice that says it reports a result, not an invitation to bid.
_AWARD_TAGS = {"award", "awardupdate", "awardcancellation", "contract", "contractupdate", "contractamendment", "contracttermination", "implementation", "implementationupdate"}
_AWARD_TITLE = re.compile(r"^\s*(contract\s+)?award(ed)?\s+(of|notice)\b", re.I)
_POSTCODE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", re.I)


def ensure_tables(cursor) -> None:
    cursor.execute(TABLE_SQL)
    for sql in INDEX_SQL:
        cursor.execute(sql)
    cursor.execute(RUNS_SQL)


def _parse_ts(value: Any) -> datetime | None:
    """An ISO 8601 string from a feed as an aware datetime (UTC when it carries no zone); None when it is not one."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def classify(tags: list[str], tender_status: str, title: str, deadline_at: datetime | None, now: datetime | None = None) -> tuple[str, str]:
    """(notice_type, status): status is one of open, closed, awarded, cancelled.

    Open needs an invitation to bid (a tender notice, not an award) that is still active and has a deadline of today or later:
    a notice with no deadline is never counted as open (T1)."""
    now = now or datetime.now(timezone.utc)
    low_tags = {t.lower() for t in (tags or [])}
    status = (tender_status or "").lower()
    if low_tags & {"planning", "planningupdate"} and not (low_tags & {"tender", "tenderupdate"}):
        return "planning", "planned"   # a prior information notice: early signal, not an invitation to bid
    if low_tags & _AWARD_TAGS and not (low_tags & {"tender", "tenderupdate", "planning"}):
        return "award", "awarded"
    if _AWARD_TITLE.match(title or ""):
        return "award", "awarded"
    if status in ("cancelled", "withdrawn", "unsuccessful"):
        return "tender", "cancelled"
    if status == "complete":
        return "tender", "closed"
    if status in ("active", "planned", "") and deadline_at is not None and deadline_at.date() >= now.date():
        return "tender", "open"
    return "tender", "closed"


def _first_address(release: dict[str, Any]) -> dict[str, Any]:
    tender = release.get("tender") or {}
    for item in tender.get("items") or []:
        for addr in item.get("deliveryAddresses") or []:
            if addr:
                return addr
        if item.get("deliveryAddress"):
            return item["deliveryAddress"]
    for lot in tender.get("lots") or []:
        for addr in lot.get("deliveryAddresses") or []:
            if addr:
                return addr
    for party in release.get("parties") or []:
        if "buyer" in (party.get("roles") or []) and party.get("address"):
            return party["address"]
    return (release.get("buyer") or {}).get("address") or {}


def normalise_release(release: dict[str, Any], source: str, label: str, detail_url: Callable[[dict[str, Any]], str] | None = None,
                      now: datetime | None = None) -> dict[str, Any] | None:
    """One OCDS release as an index row, or None when it has no id to store it under."""
    ocid = release.get("ocid") or ""
    if not ocid:
        return None
    tender = release.get("tender") or {}
    title = (tender.get("title") or "").strip()
    deadline = _parse_ts((tender.get("tenderPeriod") or {}).get("endDate"))
    published = _parse_ts(release.get("date"))
    notice_type, status = classify(release.get("tag") or [], tender.get("status") or "", title, deadline, now)

    cpvs: list[str] = []
    for item in tender.get("items") or []:
        cls = item.get("classification") or {}
        if cls.get("id"):
            cpvs.append(f"{cls['id']} - {cls['description']}" if cls.get("description") else str(cls["id"]))
    addr = _first_address(release)
    parts = [addr.get("streetAddress"), addr.get("locality"), addr.get("region"), addr.get("postalCode")]
    location_text = ", ".join(p for p in parts if p)
    postcode = (addr.get("postalCode") or "").strip().upper()
    if not postcode:
        m = _POSTCODE.search(location_text)
        postcode = f"{m.group(1)} {m.group(2)}".upper() if m else ""
    region = itl1_region(addr.get("region"), location_text) if location_text else ""
    if region == "UK":
        region = ""   # "UK" is what the helper says when it cannot tell: the index keeps that as unknown, not as a place

    value = (tender.get("value") or {}).get("amount")
    rel_id = release.get("id") or ""
    notice_uuid = rel_id.rsplit("-", 1)[0] if "-" in rel_id else rel_id
    url = detail_url({"ocid": ocid, "id": rel_id, "uuid": notice_uuid}) if detail_url else ""
    return {
        "source": source,
        "resource_id": ocid,
        "title": title,
        "description": (tender.get("description") or "").strip(),
        "contracting_authority": ((release.get("buyer") or {}).get("name") or "").strip(),
        "notice_type": notice_type,
        "status": status,
        "published_at": published,
        "deadline_at": deadline,
        "value": float(value) if isinstance(value, (int, float)) else None,
        "currency": (tender.get("value") or {}).get("currency") or "GBP",
        "cpv_codes": ", ".join(cpvs),
        "location_text": location_text,
        "region": region,
        "postcode": postcode,
        "detail_url": url,
        "raw": release,
        "source_label": label,
    }


_UPSERT_SQL = """
INSERT INTO tender_notices
    (source, resource_id, title, description, contracting_authority, notice_type, status, published_at, deadline_at, value, currency,
     cpv_codes, location_text, region, postcode, detail_url, raw, search_tsv, last_seen_at)
VALUES (%(source)s, %(resource_id)s, %(title)s, %(description)s, %(contracting_authority)s, %(notice_type)s, %(status)s, %(published_at)s,
        %(deadline_at)s, %(value)s, %(currency)s, %(cpv_codes)s, %(location_text)s, %(region)s, %(postcode)s, %(detail_url)s, %(raw)s,
        to_tsvector('english', %(title)s || ' ' || %(description)s || ' ' || %(contracting_authority)s || ' ' || %(cpv_codes)s), now())
ON CONFLICT (source, resource_id) DO UPDATE SET
    title = EXCLUDED.title, description = EXCLUDED.description, contracting_authority = EXCLUDED.contracting_authority,
    notice_type = EXCLUDED.notice_type, status = EXCLUDED.status, published_at = EXCLUDED.published_at,
    deadline_at = EXCLUDED.deadline_at, value = EXCLUDED.value, currency = EXCLUDED.currency, cpv_codes = EXCLUDED.cpv_codes,
    location_text = EXCLUDED.location_text, region = EXCLUDED.region, postcode = EXCLUDED.postcode, detail_url = EXCLUDED.detail_url,
    raw = EXCLUDED.raw, search_tsv = EXCLUDED.search_tsv, last_seen_at = now(),
    closed_at = CASE WHEN EXCLUDED.status = 'open' THEN NULL ELSE COALESCE(tender_notices.closed_at, now()) END
WHERE tender_notices.published_at IS NULL OR EXCLUDED.published_at IS NULL OR EXCLUDED.published_at >= tender_notices.published_at
RETURNING (xmax = 0) AS inserted
"""


def _finite(obj: Any) -> Any:
    """The release with every Infinity and NaN replaced by null: a feed can carry them (Find a Tender sends an unbounded
    "maximumParticipants" as Infinity) and Postgres refuses them in JSON."""
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_finite(v) for v in obj]
    return obj


def upsert_notices(cursor, notices: list[dict[str, Any]]) -> tuple[int, int]:
    """(rows seen, rows new). A notice already stored is refreshed, never duplicated."""
    new = 0
    for n in notices:
        row = {**n, "raw": json.dumps(_finite(n["raw"]), ensure_ascii=False, default=str, allow_nan=False)}
        cursor.execute(_UPSERT_SQL, row)
        got = cursor.fetchone()   # no row: an older release of a notice already stored with a newer one, so it changed nothing
        if got and got[0]:
            new += 1
    return len(notices), new


def close_expired(cursor, now: datetime | None = None) -> int:
    """Open notices whose deadline has passed are closed (the portal's own status is not updated when the deadline passes)."""
    cursor.execute("UPDATE tender_notices SET status = 'closed', closed_at = COALESCE(closed_at, now()) "
                   "WHERE status = 'open' AND deadline_at < %s", (now or datetime.now(timezone.utc),))
    return cursor.rowcount


def close_unseen(cursor, source: str, before: datetime) -> int:
    """After a COMPLETE scan of a source: its open notices that the scan no longer listed have been withdrawn or amended away."""
    cursor.execute("UPDATE tender_notices SET status = 'closed', closed_at = COALESCE(closed_at, now()) "
                   "WHERE source = %s AND status = 'open' AND last_seen_at < %s", (source, before))
    return cursor.rowcount


def _start_run(conn, source: str) -> int:
    cur = conn.cursor()
    cur.execute("INSERT INTO index_runs (source) VALUES (%s) RETURNING id", (source,))
    run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def _finish_run(conn, run_id: int, ok: bool, complete: bool, seen: int, new: int, error: str = "") -> None:
    cur = conn.cursor()
    cur.execute("UPDATE index_runs SET finished_at = now(), ok = %s, complete = %s, rows_seen = %s, rows_new = %s, error = %s WHERE id = %s",
                (ok, complete, seen, new, error[:500], run_id))
    conn.commit()


def _ingest(get_conn: Callable[[], Any], source: str, batches, to_notice, *, scan_is_full: bool = False) -> dict[str, Any]:
    """Write every batch of releases the feed yields; one run row says what happened, even when the feed fails part way."""
    conn = get_conn()
    started = datetime.now(timezone.utc)
    seen = new = 0
    state: dict[str, Any] = {}
    try:
        ensure_tables(conn.cursor())
        conn.commit()
        run_id = _start_run(conn, source)
        error = ""
        try:
            for releases in batches(state):
                notices = [n for n in (to_notice(r) for r in releases) if n]
                s, n = upsert_notices(conn.cursor(), notices)
                conn.commit()
                seen, new = seen + s, new + n
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            error = f"{type(exc).__name__}: {exc}"
        complete = bool(state.get("complete")) and not error
        cur = conn.cursor()
        close_expired(cur)
        if scan_is_full and complete:
            close_unseen(cur, source, started)
        conn.commit()
        _finish_run(conn, run_id, ok=not error, complete=complete, seen=seen, new=new, error=error or state.get("reason", ""))
        return {"source": source, "ok": not error, "complete": complete, "seen": seen, "new": new, "error": error or state.get("reason", "")}
    finally:
        conn.close()


def ingest_contracts_finder(get_conn: Callable[[], Any], days: int = 2, max_pages: int | None = None, until_days: int = 0,
                            stages: str | None = None) -> dict[str, Any]:
    """Every notice (tenders, updates, awards, planning) published in the last `days` days, written to tender_notices. A short window keeps the 30-minute
    job light. A backfill walks back in windows: `days` ago up to `until_days` ago, with stages="tender" to keep the awards (which
    contract_awards already holds) out of it."""
    from etenders_scraper.sources import contracts_finder as cf

    def batches(state):
        session = cf._new_session()
        window = (datetime.utcnow() - timedelta(days=days), datetime.utcnow() - timedelta(days=until_days))
        for i, releases in enumerate(cf._iter_release_pages(session, state, window, stages=stages)):
            yield releases
            if max_pages and i + 1 >= max_pages:
                state["reason"] = f"stopped after {max_pages} pages as asked"
                return

    def to_notice(release):
        return normalise_release(release, "contracts_finder", "Contracts Finder",
                                 lambda r: f"https://www.contractsfinder.service.gov.uk/Notice/{r['uuid']}")

    return _ingest(get_conn, "contracts_finder", batches, to_notice, scan_is_full=False)


FAT_API = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"


def ingest_find_tender(get_conn: Callable[[], Any], days: int = 2, max_pages: int | None = None) -> dict[str, Any]:
    """Find a Tender notices updated in the last `days` days, from its OCDS release-package API (no page scraping)."""
    from urllib.parse import parse_qs, urlparse
    from etenders_scraper.sources import find_tender as fat

    def batches(state):
        session = fat._make_session()
        # No "stages" filter: with stages=tender the feed returned 5 notices for a day in which it listed 66 tender and 23 tender-update
        # releases (and 342 awards), so the filter hid most of what the portal publishes.
        params: dict[str, Any] = {"limit": 100,
                                  "updatedFrom": (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")}
        for page in range(500):
            resp = fat._request(session, "GET", FAT_API, params=params)
            if resp.status_code == 429:
                state["reason"] = f"rate-limited by Find a Tender after {page} pages"
                return
            fat._check_status(resp, "the OCDS feed")
            data = resp.json()
            releases = data.get("releases") or []
            if releases:
                yield releases
            elif page == 0:
                raise fat.FindTenderError("Find a Tender returned no notices: its API is not serving data right now")
            nxt = (data.get("links") or {}).get("next") or ""
            q = parse_qs(urlparse(nxt).query) if nxt else {}
            if not q.get("cursor"):
                state["complete"] = True
                return
            params = {k: v[0] for k, v in q.items()}
            if max_pages and page + 1 >= max_pages:
                state["reason"] = f"stopped after {max_pages} pages as asked"
                return
        state["reason"] = "hit the 500-page safety limit"

    def to_notice(release):
        return normalise_release(release, "find_tender", "Find a Tender",
                                 lambda r: f"https://www.find-tender.service.gov.uk/Notice/{r['uuid']}")

    return _ingest(get_conn, "find_tender", batches, to_notice, scan_is_full=False)


def index_status(cursor) -> list[dict[str, Any]]:
    """The latest run of each source, for an admin to see a portal that has stopped updating."""
    cursor.execute("""SELECT DISTINCT ON (source) source, started_at, finished_at, ok, complete, rows_seen, rows_new, error
                      FROM index_runs ORDER BY source, started_at DESC""")
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) for r in cursor.fetchall()]
