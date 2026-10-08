#!/usr/bin/env python3
"""Planning Leads backfill: load historical UK planning applications from PlanIt.

The nightly harvester (etenders_scraper/planning/harvester.py) only looks back a couple
of weeks. Run this once to populate history, and again after any outage longer than
the harvester's 30-day catch-up limit.

PlanIt allows roughly 300 requests a day at one per minute, so a large backfill takes
several nights. Progress is saved after every completed (size, month) window in
planning_harvest_state.cursor_state; just re-run the same command each night and it
resumes where it stopped. The daily cap is shared with the nightly harvester — if you
run a backfill during the day, that night's incremental may find the budget spent.

Usage (inside the app container):
    docker compose exec app python scripts/planning_backfill.py --months 24
    docker compose exec app python scripts/planning_backfill.py --months 1 --sizes Large --max-requests 5
    docker compose exec app python scripts/planning_backfill.py --status
    docker compose exec app python scripts/planning_backfill.py --months 24 --reset
    docker compose exec app python scripts/planning_backfill.py --rescore-all   # no PlanIt requests

Please respect PlanIt's request to run bulk jobs overnight (18:00-06:00).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add repository root to python path for imports
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tender_app.db import get_db_connection
from tender_app.db_ext import run_saas_migrations
from etenders_scraper.planning import harvester
from etenders_scraper.planning.planit_api import PlanItRateLimited


def month_windows(months: int, today: date | None = None) -> list[tuple[date, date]]:
    """Calendar-month windows, newest first, overlapping by one day so the result is
    correct whether PlanIt's end_date is inclusive or exclusive."""
    today = today or date.today()
    windows = []
    first = today.replace(day=1)
    for _ in range(months):
        next_first = (first + timedelta(days=32)).replace(day=1)
        windows.append((first, min(next_first, today)))
        first = (first - timedelta(days=1)).replace(day=1)
    return windows


def window_key(size: str, start: date) -> str:
    return f"{size}:{start.isoformat()}"


def in_overnight_window(now: datetime | None = None) -> bool:
    hour = (now or datetime.now()).hour
    return hour >= 18 or hour < 6


def print_status(conn) -> None:
    state = harvester.get_harvest_state(conn)
    done = state["cursor_state"].get("backfill_done", [])
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT authority), MIN(start_date), MAX(start_date) FROM planning_applications")
        total, authorities, min_d, max_d = cur.fetchone()
        cur.execute("SELECT country, app_size, COUNT(*) FROM planning_applications GROUP BY 1, 2 ORDER BY 3 DESC")
        breakdown = cur.fetchall()
    print(f"Applications: {total} across {authorities} authorities, submitted {min_d} .. {max_d}")
    for country, size, n in breakdown:
        print(f"  {country or 'Unknown country':18s} {size or '-':7s} {n}")
    print(f"PlanIt requests today: {state['requests_today']} / {state['daily_request_cap']}")
    print(f"Last nightly run: {state['last_run_at']} ({state['last_status']}); last success {state['last_success_at']}")
    if state["last_error"]:
        print(f"Last error: {state['last_error']}")
    print(f"Backfill windows completed: {len(done)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill UK planning applications from PlanIt")
    parser.add_argument("--months", type=int, default=24,
                        help="How many calendar months back to cover, including the current one (default 24)")
    parser.add_argument("--sizes", nargs="+", default=list(harvester.HARVEST_SIZES),
                        choices=["Large", "Medium", "Small"],
                        help="PlanIt app_size values to fetch (default: Large Medium)")
    parser.add_argument("--max-requests", type=int, default=250,
                        help="Stop after this many PlanIt requests in this run (default 250, "
                             "leaving headroom under the 300/day cap for the nightly harvest)")
    parser.add_argument("--reset", action="store_true",
                        help="Forget completed windows and refetch everything in range")
    parser.add_argument("--daytime-ok", action="store_true",
                        help="Allow running outside PlanIt's requested 18:00-06:00 bulk window")
    parser.add_argument("--status", action="store_true", help="Print progress and exit")
    parser.add_argument("--rescore-all", action="store_true",
                        help="Reclassify minor works and recompute every lead score, then exit. "
                             "Makes no PlanIt requests. Run after changing etenders_scraper/planning/scoring.py")
    args = parser.parse_args()

    conn = get_db_connection()
    try:
        run_saas_migrations(conn)

        if args.status:
            print_status(conn)
            return 0

        if args.rescore_all:
            print(f"Rescored {harvester.rescore(conn, all_rows=True)} applications.")
            print(f"Marked {harvester.mark_duplicate_schemes(conn)} resubmissions as duplicate.")
            return 0

        if not args.daytime_ok and not in_overnight_window():
            print("PlanIt asks for bulk jobs to run between 18:00 and 06:00. "
                  "Re-run then, or pass --daytime-ok for a small test.")
            return 2

        state = harvester.get_harvest_state(conn)
        cursor_state = state["cursor_state"]
        done = set() if args.reset else set(cursor_state.get("backfill_done", []))

        harvester.refresh_areas(conn)
        areas = harvester.load_areas(conn)

        windows = [(size, start, end) for start, end in month_windows(args.months) for size in args.sizes]
        # The current month is still filling up, so never mark it complete.
        current_month = date.today().replace(day=1)
        pending = [w for w in windows if window_key(w[0], w[1]) not in done or w[1] == current_month]
        print(f"{len(windows)} windows in range, {len(pending)} to fetch "
              f"(sizes {', '.join(args.sizes)}, {args.months} months).")

        used = 0
        totals = {"fetched": 0, "inserted": 0, "updated": 0}
        for size, start, end in pending:
            remaining = args.max_requests - used
            if remaining <= 0:
                print(f"Reached --max-requests {args.max_requests}. Re-run to continue.")
                break
            print(f"  {size:6s} {start} .. {end} ", end="", flush=True)
            try:
                stats = harvester.harvest_backfill_window(
                    conn, start=start, end=end, app_size=size, max_requests=remaining, areas=areas)
            except (harvester.BudgetExhausted, PlanItRateLimited) as exc:
                print(f"stopped: {exc}. Progress saved; re-run tomorrow to continue.")
                break
            used += stats["requests"]
            for k in totals:
                totals[k] += stats[k]
            print(f"{stats['fetched']} fetched, {stats['inserted']} new, {stats['updated']} updated, "
                  f"{stats['requests']} requests" + (f", {stats['splits']} splits" if stats["splits"] else ""))
            for err in stats["errors"]:
                print(f"    ! {err}")

            if not stats["errors"] and start != current_month:
                done.add(window_key(size, start))
                cursor_state["backfill_done"] = sorted(done)
                harvester.save_cursor_state(conn, cursor_state)

        rescored = harvester.rescore(conn)
        marked_dup = harvester.mark_duplicate_schemes(conn)
        print(f"Done this run: {used} requests, {totals['fetched']} fetched, "
              f"{totals['inserted']} new, {totals['updated']} updated, {rescored} rescored, "
              f"{marked_dup} marked duplicate.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
