"""Harvest PlanIt planning applications into Postgres.

Two entry points share one windowing routine (`_harvest_window`):

* `harvest_incremental` — nightly. Two fixed look-back windows per size: submissions
  in the last NEW_LOOKBACK_DAYS (new schemes) and decisions in the last
  DECIDED_LOOKBACK_DAYS (the Undecided -> Permitted change that matters most).
* `harvest_backfill_window` — used by scripts/planning_backfill.py. Windows on
  submission date.

Why not window on PlanIt's last_changed? It is bumped on every re-scrape. Measured
2026-09-17 for Large schemes over 3 days: 13 new submissions, but 6,557 "changed"
(and 3,651 "different"). The chosen windows measured, over 7 days: 38 Large + 117
Medium submissions, 116 Large + 806 Medium decisions — about 9 requests a night with a
14-day decision look-back. Because the look-backs overlap night to night, a missed run
self-heals the next night; no watermark is needed for correctness.

Known gap: field changes that are neither a new submission nor a decision (e.g. an
agent_company filled in later, or a withdrawal with no decided_date) are only picked
up by re-running the backfill over that period.

PlanIt returns at most MAX_RESULTS_PER_QUERY results for any one query no matter how
far you page, so a window whose `total` exceeds that is split in half by date and each
half fetched separately. Halves overlap by one day so the split is correct whether
PlanIt treats end dates as inclusive or exclusive; duplicates are harmless because the
upsert is keyed on the PlanIt name.

Request budget: every PlanIt call goes through `_consume_request_budget`, an atomic
counter in planning_harvest_state, so the ~300/day ceiling holds across the scheduler,
the admin trigger, the backfill CLI, and process restarts.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from psycopg2.extras import execute_values

from etenders_scraper.planning import planit_api
from etenders_scraper.planning.normalize import (
    normalize_planit_area,
    normalize_planit_record,
    resolve_area_countries,
)
from etenders_scraper.planning.scoring import RECENCY_WINDOW_DAYS, lead_score, low_value_reason

SOURCE = "planit"
HARVEST_SIZES = ("Large", "Medium")
AREAS_REFRESH_DAYS = 7
NEW_LOOKBACK_DAYS = 7
# Longer than NEW_LOOKBACK_DAYS because PlanIt often learns of a decision some days
# after its decided_date, as it re-scrapes each authority on its own cycle.
DECIDED_LOOKBACK_DAYS = 14
# After an outage, the look-back stretches back to the last successful run, up to this
# many days. Beyond that it is a backfill job: run scripts/planning_backfill.py.
INCREMENTAL_MAX_LOOKBACK_DAYS = 30
# 02:00 UTC is 02:00-03:00 UK time: inside PlanIt's requested 18:00-06:00 bulk window.
NIGHTLY_RUN_HOUR_UTC = 2

UPSERT_COLUMNS = (
    "id", "uid", "planning_portal_id", "authority", "authority_id", "country", "region",
    "description", "address", "postcode", "latitude", "longitude",
    "app_size", "app_state", "app_type", "n_dwellings", "low_value_reason",
    "applicant_name", "applicant_company", "agent_name", "agent_company", "agent_address",
    "case_officer", "ward_name",
    "start_date", "decided_date", "consultation_end_date", "target_decision_date",
    "decision", "docs_url", "detail_url", "planit_url",
    "lead_score", "raw_json", "last_changed",
)


class BudgetExhausted(Exception):
    """PlanIt daily request budget used up; stop cleanly and resume another night."""


def _adapter():
    """Return the configured data-source module. Only PlanIt exists today; a paid feed
    (PlanWire, PlanAPI, Searchland) is added by writing a sibling module with the same
    fetch_applications/fetch_areas_page signatures and registering it here."""
    source = (os.environ.get("PLANNING_SOURCE") or SOURCE).strip().lower()
    if source == "planit":
        return planit_api
    raise RuntimeError(f"Unknown PLANNING_SOURCE {source!r}; only 'planit' is implemented")


# ---------------------------------------------------------------------------
# Harvest state / request budget
# ---------------------------------------------------------------------------

def _ensure_state_row(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO planning_harvest_state (source, requests_today, requests_date)
            VALUES (%s, 0, CURRENT_DATE)
            ON CONFLICT (source) DO NOTHING
            """,
            (SOURCE,),
        )
    conn.commit()


def _consume_request_budget(conn, n: int = 1) -> None:
    """Atomically reserve `n` PlanIt requests from today's budget or raise.

    A single conditional UPDATE, so two concurrent harvesters cannot both pass the check
    and together overshoot the cap. The counter resets when the date rolls over.
    """
    cap = planit_api.DAILY_REQUEST_CAP
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE planning_harvest_state
            SET requests_today = CASE WHEN requests_date = CURRENT_DATE
                                      THEN requests_today + %s ELSE %s END,
                requests_date  = CURRENT_DATE
            WHERE source = %s
              AND (requests_date IS DISTINCT FROM CURRENT_DATE OR requests_today + %s <= %s)
            RETURNING requests_today
            """,
            (n, n, SOURCE, n, cap),
        )
        granted = cur.fetchone() is not None
    conn.commit()
    if not granted:
        raise BudgetExhausted(f"PlanIt daily budget of {cap} requests reached")


def get_harvest_state(conn) -> dict[str, Any]:
    _ensure_state_row(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT last_success_at, last_run_at, last_status, last_error,
                   CASE WHEN requests_date = CURRENT_DATE THEN requests_today ELSE 0 END,
                   cursor_state
            FROM planning_harvest_state WHERE source = %s
            """,
            (SOURCE,),
        )
        row = cur.fetchone()
    cursor_state: dict[str, Any] = {}
    if row and row[5]:
        try:
            cursor_state = json.loads(row[5])
        except ValueError:
            cursor_state = {}
    return {
        "last_success_at": row[0] if row else None,
        "last_run_at": row[1] if row else None,
        "last_status": row[2] if row else None,
        "last_error": row[3] if row else None,
        "requests_today": int(row[4] or 0) if row else 0,
        "daily_request_cap": planit_api.DAILY_REQUEST_CAP,
        "cursor_state": cursor_state,
    }


def _record_run(conn, *, status: str, error: str | None, success_at: datetime | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE planning_harvest_state
            SET last_run_at = CURRENT_TIMESTAMP,
                last_status = %s,
                last_error  = %s,
                last_success_at = COALESCE(%s, last_success_at)
            WHERE source = %s
            """,
            (status, error, success_at, SOURCE),
        )
    conn.commit()


def save_cursor_state(conn, cursor_state: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE planning_harvest_state SET cursor_state = %s WHERE source = %s",
            (json.dumps(cursor_state, default=str), SOURCE),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Areas (authority -> country/region)
# ---------------------------------------------------------------------------

def load_areas(conn) -> dict[int, dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT area_id, area_name, country, region FROM planning_areas")
        return {
            r[0]: {"area_id": r[0], "area_name": r[1], "country": r[2], "region": r[3]}
            for r in cur.fetchall()
        }


def _areas_stale(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(updated_at) FROM planning_areas")
        latest = cur.fetchone()[0]
    return latest is None or latest < datetime.now() - timedelta(days=AREAS_REFRESH_DAYS)


def refresh_areas(conn, *, force: bool = False) -> int:
    """Reload planning_areas from PlanIt if older than a week. Costs ~2 requests."""
    if not force and not _areas_stale(conn):
        return 0

    adapter = _adapter()
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        _consume_request_budget(conn)
        records, err, total = adapter.fetch_areas_page(page=page)
        if err:
            raise RuntimeError(f"Area refresh failed: {err}")
        rows.extend(a for a in (normalize_planit_area(r) for r in records) if a)
        if not records or total is None or page * adapter.MAX_PAGE_SIZE >= total:
            break
        page += 1

    if not rows:
        return 0
    resolve_area_countries(rows)
    cols = ("area_id", "area_name", "long_name", "area_type", "gss_code", "country", "region", "is_planning")
    with conn.cursor() as cur:
        execute_values(
            cur,
            f"""
            INSERT INTO planning_areas ({", ".join(cols)}, updated_at) VALUES %s
            ON CONFLICT (area_id) DO UPDATE SET
                {", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "area_id")},
                updated_at = CURRENT_TIMESTAMP
            """,
            [tuple(r[c] for c in cols) for r in rows],
            template="(" + ", ".join(["%s"] * len(cols)) + ", CURRENT_TIMESTAMP)",
        )
        # Backfill country/region onto any applications harvested before their
        # authority was known.
        cur.execute(
            """
            UPDATE planning_applications pa
            SET country = ar.country, region = ar.region
            FROM planning_areas ar
            WHERE pa.authority_id = ar.area_id
              AND (pa.country IS DISTINCT FROM ar.country OR pa.region IS DISTINCT FROM ar.region)
            """
        )
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_planning_applications(conn, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """Insert or update rows. Returns (inserted, updated)."""
    if not rows:
        return 0, 0

    # PlanIt can return the same application twice within a page when windows overlap;
    # ON CONFLICT cannot touch one row twice in a single statement, so dedupe first.
    by_id: dict[str, dict[str, Any]] = {}
    for r in rows:
        by_id[r["id"]] = r

    # Never re-insert a record an admin removed on request.
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM planning_suppressions WHERE id = ANY(%s)", (list(by_id),))
        for (suppressed_id,) in cur.fetchall():
            by_id.pop(suppressed_id, None)
    if not by_id:
        return 0, 0

    values = []
    for r in by_id.values():
        r = {**r, "lead_score": lead_score(r)}
        values.append(tuple(r.get(c) for c in UPSERT_COLUMNS))

    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in UPSERT_COLUMNS if c != "id")
    with conn.cursor() as cur:
        results = execute_values(
            cur,
            f"""
            INSERT INTO planning_applications ({", ".join(UPSERT_COLUMNS)}) VALUES %s
            ON CONFLICT (id) DO UPDATE SET {updates}, updated_at = CURRENT_TIMESTAMP
            RETURNING (xmax = 0)
            """,
            values,
            fetch=True,
        )
    conn.commit()
    inserted = sum(1 for (is_insert,) in results if is_insert)
    return inserted, len(results) - inserted


# ---------------------------------------------------------------------------
# Windowed fetch
# ---------------------------------------------------------------------------

def _split(start: date, end: date) -> list[tuple[date, date]] | None:
    """Split an inclusive date window in two, overlapping by one day. None if the
    window is a single day and cannot be split further."""
    span = (end - start).days
    if span <= 0:
        return None
    if span == 1:
        return [(start, start), (end, end)]
    mid = start + timedelta(days=span // 2)
    return [(start, mid), (mid, end)]


def _harvest_window(
    conn,
    areas: dict[int, dict[str, Any]],
    stats: dict[str, Any],
    *,
    field: str,
    start: date,
    end: date,
    app_size: str,
    max_requests: int,
) -> None:
    """Fetch every page of one (field, window, size) query, splitting if PlanIt's
    per-query result cap would truncate it. `field` is "start" or "decided"."""
    adapter = _adapter()
    date_kwargs = (
        {"decided_start": start, "decided_end": end}
        if field == "decided"
        else {"start_date": start, "end_date": end}
    )

    page = 1
    while True:
        if stats["requests"] >= max_requests:
            raise BudgetExhausted(f"Run request limit of {max_requests} reached")
        _consume_request_budget(conn)
        stats["requests"] += 1

        records, err, total = adapter.fetch_applications(app_size=app_size, page=page, **date_kwargs)
        if err:
            stats["errors"].append(f"{app_size} {field} {start}..{end} p{page}: {err}")
            return

        rows = [r for r in (normalize_planit_record(rec, areas) for rec in records) if r]
        ins, upd = upsert_planning_applications(conn, rows)
        stats["inserted"] += ins
        stats["updated"] += upd
        stats["fetched"] += len(records)
        stats["unknown_area_ids"].update(
            r["authority_id"] for r in rows if r["authority_id"] is not None and r["authority_id"] not in areas
        )

        if page == 1 and total is not None and total > adapter.MAX_RESULTS_PER_QUERY:
            halves = _split(start, end)
            if halves:
                stats["splits"] += 1
                for h_start, h_end in halves:
                    _harvest_window(conn, areas, stats, field=field, start=h_start, end=h_end,
                                    app_size=app_size, max_requests=max_requests)
                return
            stats["errors"].append(
                f"{app_size} {field} {start}: {total} results in a single day exceeds "
                f"PlanIt's {adapter.MAX_RESULTS_PER_QUERY} cap; some records skipped"
            )

        reachable = min(total or 0, adapter.MAX_RESULTS_PER_QUERY)
        if not records or page >= math.ceil(reachable / adapter.MAX_PAGE_SIZE):
            return
        page += 1


def _new_stats() -> dict[str, Any]:
    return {"requests": 0, "fetched": 0, "inserted": 0, "updated": 0, "splits": 0,
            "errors": [], "unknown_area_ids": set()}


def _finish_stats(stats: dict[str, Any]) -> dict[str, Any]:
    stats["unknown_area_ids"] = sorted(stats["unknown_area_ids"])
    return stats


def harvest_incremental(
    conn,
    *,
    sizes: tuple[str, ...] = HARVEST_SIZES,
    max_requests: int = 40,
) -> dict[str, Any]:
    """Nightly: fetch recent submissions and recent decisions for each size.

    Look-backs are fixed, and stretch back to the last successful run after an outage
    (capped at INCREMENTAL_MAX_LOOKBACK_DAYS). last_success_at only advances when the
    whole run completed cleanly, so a failed night widens the next night's window.
    """
    _ensure_state_row(conn)
    run_started = datetime.now()
    state = get_harvest_state(conn)
    stats = _new_stats()

    today = date.today()
    earliest = today - timedelta(days=INCREMENTAL_MAX_LOOKBACK_DAYS)
    last_ok = state["last_success_at"].date() if state["last_success_at"] else None

    def _window_start(lookback_days: int) -> date:
        start = today - timedelta(days=lookback_days)
        if last_ok and last_ok - timedelta(days=1) < start:
            start = last_ok - timedelta(days=1)
        return max(start, earliest)

    new_start = _window_start(NEW_LOOKBACK_DAYS)
    decided_start = _window_start(DECIDED_LOOKBACK_DAYS)
    if last_ok and last_ok < earliest:
        stats["errors"].append(
            f"Last successful run was {last_ok}, more than {INCREMENTAL_MAX_LOOKBACK_DAYS} days ago; "
            f"only the last {INCREMENTAL_MAX_LOOKBACK_DAYS} days were fetched. "
            "Run scripts/planning_backfill.py to cover the gap."
        )
    stats["window"] = f"submitted {new_start}..{today}, decided {decided_start}..{today}"

    status, error = "success", None
    try:
        refresh_areas(conn)
        areas = load_areas(conn)
        for size in sizes:
            _harvest_window(conn, areas, stats, field="start", start=new_start, end=today,
                            app_size=size, max_requests=max_requests)
            _harvest_window(conn, areas, stats, field="decided", start=decided_start, end=today,
                            app_size=size, max_requests=max_requests)
        if stats["unknown_area_ids"]:
            refresh_areas(conn, force=True)
        stats["rescored"] = rescore(conn)
        stats["marked_duplicate"] = mark_duplicate_schemes(conn)
        if stats["errors"]:
            status, error = "partial", "; ".join(stats["errors"])[:2000]
    except (BudgetExhausted, planit_api.PlanItRateLimited) as exc:
        status, error = "budget_exhausted", str(exc)
    except Exception as exc:
        conn.rollback()
        status, error = "error", f"{type(exc).__name__}: {exc}"

    _record_run(conn, status=status, error=error,
                success_at=run_started if status == "success" else None)
    stats["status"] = status
    return _finish_stats(stats)


def harvest_backfill_window(
    conn,
    *,
    start: date,
    end: date,
    app_size: str,
    max_requests: int,
    areas: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Backfill one submission-date window for one size. Raises BudgetExhausted if the
    budget runs out partway; the caller records progress and resumes later."""
    _ensure_state_row(conn)
    if areas is None:
        refresh_areas(conn)
        areas = load_areas(conn)
    stats = _new_stats()
    _harvest_window(conn, areas, stats, field="start", start=start, end=end,
                    app_size=app_size, max_requests=max_requests)
    return _finish_stats(stats)


def rescore(conn, *, all_rows: bool = False) -> int:
    """Recompute low_value_reason and lead_score. Returns the number of rows changed.

    Nightly (all_rows=False): only rows inside the recency window. Scores are computed at
    upsert, but the recency component decays daily while an unchanged record is never
    re-upserted; older rows have zero recency points, so their score is stable.

    all_rows=True: every row. Run after changing scoring weights or the low-value
    patterns in scoring.py (scripts/planning_backfill.py --rescore-all).
    """
    where, params = "TRUE", ()
    if not all_rows:
        where = "COALESCE(decided_date, start_date) >= %s"
        params = (date.today() - timedelta(days=RECENCY_WINDOW_DAYS + 2),)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, description, app_size, app_state, app_type, n_dwellings,
                   start_date, decided_date, low_value_reason, lead_score
            FROM planning_applications
            WHERE {where}
            """,
            params,
        )
        changes = []
        for row_id, desc, size, state, typ, dwell, start_d, decided_d, old_reason, old_score in cur.fetchall():
            row = {"description": desc, "app_size": size, "app_state": state, "app_type": typ,
                   "n_dwellings": dwell, "start_date": start_d, "decided_date": decided_d}
            # Reclassify from the description, ignoring the stored value, so pattern
            # changes take effect.
            new_reason = low_value_reason(row)
            row["low_value_reason"] = new_reason
            new_score = lead_score(row)
            if new_score != old_score or new_reason != old_reason:
                changes.append((new_score, new_reason, row_id))
        if changes:
            execute_values(
                cur,
                "UPDATE planning_applications AS pa SET lead_score = v.score, low_value_reason = v.reason "
                "FROM (VALUES %s) AS v(score, reason, id) WHERE pa.id = v.id",
                changes,
                template="(%s::smallint, %s::varchar, %s)",
            )
    conn.commit()
    return len(changes)


def mark_duplicate_schemes(conn) -> int:
    """Flags resubmissions of the same physical scheme as low_value_reason='duplicate'.

    Large sites often collect several outline/full applications over months as a scheme is
    revised (e.g. an amended resubmission after the original was withdrawn, or genuinely
    separate applications the applicant filed close together) -- same address, same authority,
    same app_type, same dwelling count, different official reference. Each one independently
    scores as a full, distinct "lead", inflating lead counts for what is really one physical
    development (see scoring.py's module docstring). Exact match on (address, authority,
    app_type, n_dwellings) is deliberately strict -- genuinely different phases of a site
    almost never share an identical dwelling count too -- so this only catches true
    resubmissions, never distinct opportunities. The most recent (by start_date) in each group
    is kept as the live lead; the rest are flagged, never deleted. Idempotent: only rows not
    already excluded for another reason are touched, and it converges to a stable state on
    re-runs (nothing to do once every group's earlier rows are already flagged).
    """
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE planning_applications AS pa SET low_value_reason = 'duplicate'
            FROM (
                SELECT id
                FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY address, authority, app_type, n_dwellings
                        ORDER BY start_date DESC NULLS LAST, id DESC
                    ) AS rn
                    FROM planning_applications
                    WHERE low_value_reason IS NULL
                      AND address IS NOT NULL AND address != ''
                      AND n_dwellings IS NOT NULL AND n_dwellings > 0
                ) ranked
                WHERE rn > 1
            ) dupes
            WHERE pa.id = dupes.id
        """)
        changed = cur.rowcount
    conn.commit()
    return changed


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

PLANNING_SCHEDULER_STATUS: dict[str, Any] = {
    "active": False,
    "running": False,
    "last_run_at": None,
    "last_run_status": "initialized",
    "last_run_stats": None,
    "last_error": None,
    "next_run_at": None,
    "schedule": f"Nightly at {NIGHTLY_RUN_HOUR_UTC:02d}:00 UTC",
    "source": "UK PlanIt (planit.org.uk)",
}

_run_lock = threading.Lock()


def get_planning_scheduler_status() -> dict[str, Any]:
    return PLANNING_SCHEDULER_STATUS


def run_planning_harvest(get_db_conn_func: Callable[[], Any], **kwargs: Any) -> dict[str, Any]:
    """Run one incremental harvest. Shared by the scheduler and the admin trigger; the
    lock stops them overlapping and double-spending the request budget."""
    if not _run_lock.acquire(blocking=False):
        return {"status": "already_running"}
    PLANNING_SCHEDULER_STATUS["running"] = True
    conn = None
    try:
        conn = get_db_conn_func()
        print("[Planning Harvester] Starting incremental PlanIt harvest...")
        stats = harvest_incremental(conn, **kwargs)
        PLANNING_SCHEDULER_STATUS["last_run_status"] = stats["status"]
        PLANNING_SCHEDULER_STATUS["last_run_stats"] = stats
        PLANNING_SCHEDULER_STATUS["last_error"] = "; ".join(stats["errors"]) or None
        print(
            f"[Planning Harvester] {stats['status']}: {stats['requests']} requests, "
            f"{stats['inserted']} new, {stats['updated']} updated, window {stats.get('window')}"
        )
        return stats
    except Exception as exc:
        PLANNING_SCHEDULER_STATUS["last_run_status"] = "error"
        PLANNING_SCHEDULER_STATUS["last_error"] = str(exc)
        print(f"[Planning Harvester] error: {exc}")
        return {"status": "error", "error": str(exc)}
    finally:
        PLANNING_SCHEDULER_STATUS["running"] = False
        PLANNING_SCHEDULER_STATUS["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if conn is not None:
            conn.close()
        _run_lock.release()


def _seconds_until_next_run(now: datetime | None = None) -> tuple[float, datetime]:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    target = now.replace(hour=NIGHTLY_RUN_HOUR_UTC, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds(), target


def start_planning_scheduler(get_db_conn_func: Callable[[], Any]) -> None:
    """Start the nightly harvest thread. Mirrors awards.start_award_scheduler, but
    fires at a fixed hour inside PlanIt's requested overnight window instead of on a
    rolling interval."""

    def _worker():
        time.sleep(180)  # Same post-boot delay as the award scheduler.
        PLANNING_SCHEDULER_STATUS["active"] = True
        while True:
            wait, target = _seconds_until_next_run()
            PLANNING_SCHEDULER_STATUS["next_run_at"] = target.strftime("%Y-%m-%d %H:%M:%S UTC")
            time.sleep(wait)
            run_planning_harvest(get_db_conn_func)

    threading.Thread(target=_worker, daemon=True, name="planning-harvester").start()
    print(f"[Planning Harvester] Initialized nightly PlanIt harvest ({NIGHTLY_RUN_HOUR_UTC:02d}:00 UTC).")
