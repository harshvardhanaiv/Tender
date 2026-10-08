"""
Search caching and background polling synchronization module.
Provides fast-path caching for Saved Searches and Recent Searches, diff tracking
for 'New' tenders, freshness timestamps, email alert opt-in checks, and background polling.
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

RECENT_SEARCHES_CAP = 50


def _is_invalid_row(r: dict) -> bool:
    if not isinstance(r, dict):
        return True
    if r.get("is_error_page") is True or r.get("is_bad_page") is True:
        return True
    title = str(r.get("title") or "").strip().lower()
    if not title or title in ("error", "404", "not found", "access denied"):
        return True
    return False


def _generate_tender_id(row: dict) -> str:
    import hashlib
    if not isinstance(row, dict):
        return ""
    rid = str(row.get("resource_id") or row.get("id") or "").strip()
    if rid:
        return rid
    url = str(row.get("detail_url") or row.get("url") or row.get("link") or "").strip()
    title = str(row.get("title") or "").strip()
    portal = str(row.get("source") or row.get("portal") or "").strip()
    if url:
        return hashlib.md5(url.encode("utf-8")).hexdigest()
    raw = f"{portal}:{title}".lower()
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _get_table_meta(search_type: str = "saved") -> Tuple[str, str, str, str]:
    """Returns (parent_table, cache_table, fk_column, select_cols)."""
    if search_type == "recent":
        return ("recent_searches", "recent_search_cache", "recent_search_id", "id, username, query, scope, last_refreshed_at, email_alerts_enabled")
    return ("saved_searches", "saved_search_cache", "saved_search_id", "id, username, name, query, scope, last_refreshed_at")


def refresh_search_cache(
    get_db_conn_fn,
    search_id: int,
    search_type: str = "saved",
    username: Optional[str] = None,
    force_live: bool = False
) -> Dict[str, Any]:
    """
    Executes a saved or recent search query through the search pipeline, diffs against
    its cache table, updates timestamps/status, and marks new vs removed items.
    """
    parent_tbl, cache_tbl, fk_col, select_cols = _get_table_meta(search_type)
    conn = get_db_conn_fn()
    cur = conn.cursor()

    # 1. Fetch search metadata
    query_sql = f"SELECT {select_cols} FROM {parent_tbl} WHERE id = %s"
    params = [search_id]
    if username:
        query_sql += " AND username = %s"
        params.append(username)
    cur.execute(query_sql, tuple(params))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"ok": False, "error": f"{search_type.capitalize()} search {search_id} not found"}

    if search_type == "recent":
        s_id, s_user, s_query, s_scope, s_last_refreshed, s_email_alerts = row
        s_name = s_query
    else:
        s_id, s_user, s_name, s_query, s_scope, s_last_refreshed = row
        s_email_alerts = True

    s_scope = s_scope or "all"

    # 2. Run query through the scraper / search pipeline
    try:
        from server import search_single_source, start_progressive_search, job_snapshot
        fresh_rows = []
        if s_scope == "all" or "," in s_scope:
            job = start_progressive_search(s_query, s_scope)
            # A broad "all portals" search can take well over the old 7.5s cap to finish —
            # wait up to ~60s for phase=="complete" so the cache reflects the full result
            # set, not just whichever few portals happened to answer first.
            for _ in range(120):
                time.sleep(0.5)
                rows, meta, phase = job_snapshot(job)
                if phase == "complete" or len(rows) > 0:
                    fresh_rows = rows
                    if phase == "complete":
                        break
        else:
            rows, meta = search_single_source(s_query, s_scope)
            fresh_rows = rows or []
    except Exception as e:
        print(f"[{cache_tbl}] Error running search for '{s_query}' ({s_scope}): {e}")
        fresh_rows = []

    # Filter invalid / error / fake tenders
    fresh_rows = [r for r in fresh_rows if not _is_invalid_row(r)]

    new_count, updated_count, total_active, now_ts = _diff_and_upsert_rows(
        cur, cache_tbl, fk_col, parent_tbl, s_id, fresh_rows
    )

    conn.commit()
    cur.close()
    conn.close()

    return {
        "ok": True,
        "success": True,
        "search_id": s_id,
        "search_type": search_type,
        "query": s_query,
        "scope": s_scope,
        "total_active": total_active,
        "new_count": new_count,
        "updated_count": updated_count,
        "last_refreshed_at": now_ts.isoformat() + "Z"
    }


def _diff_and_upsert_rows(cur, cache_tbl: str, fk_col: str, parent_tbl: str, s_id: int, fresh_rows: list) -> Tuple[int, int, int, datetime]:
    """Shared diff/upsert core used by both a live re-scrape and a direct sync from
    rows the caller already fetched (e.g. a search the browser just completed).
    Existing cached tenders are never dropped — results only accumulate."""
    cur.execute(
        f"SELECT tender_id, first_seen_at, last_confirmed_at, status FROM {cache_tbl} WHERE {fk_col} = %s",
        (s_id,)
    )
    existing_cache = {r[0]: {"first_seen": r[1], "last_confirmed": r[2], "status": r[3]} for r in cur.fetchall()}

    now_ts = datetime.utcnow()
    seen_ids = set()
    new_count = 0
    updated_count = 0

    for r in fresh_rows:
        tid = _generate_tender_id(r)
        if not tid or tid in seen_ids:
            continue
        seen_ids.add(tid)
        r_json = json.dumps(r, default=str)

        if tid in existing_cache:
            cur.execute(
                f"UPDATE {cache_tbl} SET last_confirmed_at = %s, status = 'active', tender_data = %s WHERE {fk_col} = %s AND tender_id = %s",
                (now_ts, r_json, s_id, tid),
            )
            updated_count += 1
        else:
            cur.execute(
                f"""INSERT INTO {cache_tbl} ({fk_col}, tender_id, first_seen_at, last_confirmed_at, status, tender_data)
                    VALUES (%s, %s, %s, %s, 'active', %s)""",
                (s_id, tid, now_ts, now_ts, r_json),
            )
            new_count += 1

    # Do NOT mark vanished tenders as 'removed' (tenders accumulate additively on refresh/sync).

    cur.execute(
        f"UPDATE {parent_tbl} SET last_refreshed_at = %s WHERE id = %s",
        (now_ts, s_id),
    )

    cur.execute(
        f"SELECT COUNT(*) FROM {cache_tbl} WHERE {fk_col} = %s AND (status = 'active' OR status IS NULL)",
        (s_id,)
    )
    tot_row = cur.fetchone()
    total_active = tot_row[0] if tot_row else len(seen_ids)

    return new_count, updated_count, total_active, now_ts


def sync_search_cache_from_rows(
    get_db_conn_fn,
    search_id: int,
    rows: list,
    search_type: str = "recent",
    username: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Writes rows the caller already fetched (a search the browser just ran and fully
    displayed) straight into the cache — no re-scrape, so the cache always reflects
    the complete result set the user actually saw, not whatever a time-boxed
    re-scrape managed to gather.
    """
    parent_tbl, cache_tbl, fk_col, select_cols = _get_table_meta(search_type)
    conn = get_db_conn_fn()
    cur = conn.cursor()

    query_sql = f"SELECT id FROM {parent_tbl} WHERE id = %s"
    params = [search_id]
    if username:
        query_sql += " AND username = %s"
        params.append(username)
    cur.execute(query_sql, tuple(params))
    if not cur.fetchone():
        cur.close()
        conn.close()
        return {"ok": False, "error": f"{search_type.capitalize()} search {search_id} not found"}

    fresh_rows = [r for r in (rows or []) if not _is_invalid_row(r)]
    new_count, updated_count, total_active, now_ts = _diff_and_upsert_rows(
        cur, cache_tbl, fk_col, parent_tbl, search_id, fresh_rows
    )

    conn.commit()
    cur.close()
    conn.close()

    return {
        "ok": True,
        "success": True,
        "search_id": search_id,
        "search_type": search_type,
        "total_active": total_active,
        "new_count": new_count,
        "updated_count": updated_count,
        "last_refreshed_at": now_ts.isoformat() + "Z",
    }


def get_cached_search_results(
    get_db_conn_fn,
    search_id: int,
    search_type: str = "saved",
    username: Optional[str] = None,
    window_hours: int = 48
) -> Dict[str, Any]:
    """
    Fetches active cached tenders, computes '_is_new' flag within window_hours,
    and returns metadata (last_refreshed_at, total, new_count, rows).
    Auto-populates on first access if never refreshed.
    """
    parent_tbl, cache_tbl, fk_col, select_cols = _get_table_meta(search_type)
    conn = get_db_conn_fn()
    cur = conn.cursor()

    query_sql = f"SELECT {select_cols} FROM {parent_tbl} WHERE id = %s"
    params = [search_id]
    if username:
        query_sql += " AND username = %s"
        params.append(username)
    cur.execute(query_sql, tuple(params))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"ok": False, "error": f"{search_type.capitalize()} search {search_id} not found"}

    if search_type == "recent":
        s_id, s_user, s_query, s_scope, s_last_refreshed, s_email_alerts = row
        s_name = s_query
    else:
        s_id, s_user, s_name, s_query, s_scope, s_last_refreshed = row
        s_email_alerts = True

    cur.close()
    conn.close()

    # Auto-populate on first access if never refreshed
    if not s_last_refreshed:
        ref_res = refresh_search_cache(get_db_conn_fn, search_id, search_type=search_type, username=username)
        if not ref_res.get("ok"):
            return ref_res

    conn = get_db_conn_fn()
    cur = conn.cursor()
    cur.execute(
        f"SELECT tender_id, first_seen_at, last_confirmed_at, tender_data FROM {cache_tbl} WHERE {fk_col} = %s AND (status = 'active' OR status = 'removed' OR status IS NULL) ORDER BY first_seen_at DESC",
        (s_id,)
    )
    rows_data = cur.fetchall()

    cur.execute(f"SELECT last_refreshed_at FROM {parent_tbl} WHERE id = %s", (s_id,))
    lr_row = cur.fetchone()
    latest_refreshed = lr_row[0] if lr_row else s_last_refreshed
    cur.close()
    conn.close()

    threshold_dt = datetime.utcnow() - timedelta(hours=window_hours)
    result_rows = []
    new_tenders_count = 0

    for tid, f_seen, l_conf, t_data in rows_data:
        try:
            item = json.loads(t_data)
        except Exception:
            continue

        is_new = False
        if f_seen:
            f_dt = f_seen if isinstance(f_seen, datetime) else None
            if not f_dt:
                try:
                    f_dt = datetime.fromisoformat(str(f_seen).replace("Z", ""))
                except Exception:
                    pass
            if f_dt and f_dt >= threshold_dt:
                is_new = True
                new_tenders_count += 1

        item["_cached"] = True
        item["_first_seen_at"] = str(f_seen)
        item["_last_confirmed_at"] = str(l_conf)
        item["_is_new"] = is_new
        result_rows.append(item)

    iso_refreshed = ""
    if latest_refreshed:
        if isinstance(latest_refreshed, datetime):
            iso_refreshed = latest_refreshed.isoformat() + "Z"
        else:
            iso_refreshed = str(latest_refreshed)
            if not iso_refreshed.endswith("Z"):
                iso_refreshed += "Z"

    return {
        "ok": True,
        "success": True,
        "search_id": s_id,
        "search_type": search_type,
        "saved_search_id": s_id if search_type == "saved" else None,
        "recent_search_id": s_id if search_type == "recent" else None,
        "name": s_name,
        "query": s_query,
        "scope": s_scope or "all",
        "last_refreshed_at": iso_refreshed,
        "email_alerts_enabled": bool(s_email_alerts),
        "total": len(result_rows),
        "new_count": new_tenders_count,
        "rows": result_rows
    }


def invalidate_search_cache(get_db_conn_fn, search_id: int, search_type: str = "saved") -> bool:
    """Purges cached results for a given search."""
    parent_tbl, cache_tbl, fk_col, _ = _get_table_meta(search_type)
    try:
        conn = get_db_conn_fn()
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {cache_tbl} WHERE {fk_col} = %s", (search_id,))
        cur.execute(f"UPDATE {parent_tbl} SET last_refreshed_at = NULL WHERE id = %s", (search_id,))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[{cache_tbl}] Error invalidating cache for {search_id}: {e}")
        return False


def copy_recent_cache_to_saved(get_db_conn_fn, recent_id_or_query, saved_id_or_scope, saved_search_id: Optional[int] = None, username: Optional[str] = None):
    """
    When saving a search that already has cached entries in recent_search_cache,
    copy them into saved_search_cache so discovery history and [NEW] badges carry over.
    Supports calling as:
      copy_recent_cache_to_saved(get_db, recent_id, saved_search_id)
    OR
      copy_recent_cache_to_saved(get_db, query, scope, saved_search_id, username)
    """
    try:
        conn = get_db_conn_fn()
        cur = conn.cursor()

        if isinstance(recent_id_or_query, int):
            recent_id = recent_id_or_query
            target_saved_id = saved_id_or_scope
            cur.execute(
                "SELECT last_refreshed_at FROM recent_searches WHERE id = %s",
                (recent_id,)
            )
            r_row = cur.fetchone()
            last_refreshed = r_row[0] if r_row else None
        else:
            query = recent_id_or_query
            scope = saved_id_or_scope
            target_saved_id = saved_search_id
            # Find matching recent search
            cur.execute(
                "SELECT id, last_refreshed_at FROM recent_searches WHERE username = %s AND LOWER(query) = LOWER(%s) AND LOWER(scope) = LOWER(%s) ORDER BY searched_at DESC, id DESC LIMIT 1",
                (username, query.strip(), (scope or "all").strip()),
            )
            r_row = cur.fetchone()
            if not r_row:
                cur.close()
                conn.close()
                return {"ok": True, "copied": 0}
            recent_id, last_refreshed = r_row

        cur.execute(
            "SELECT tender_id, first_seen_at, last_confirmed_at, status, tender_data FROM recent_search_cache WHERE recent_search_id = %s",
            (recent_id,)
        )
        cached_items = cur.fetchall()
        if not cached_items:
            cur.close()
            conn.close()
            return {"ok": True, "copied": 0}

        copied = 0
        for tid, f_seen, l_conf, status, t_data in cached_items:
            cur.execute(
                """INSERT INTO saved_search_cache (saved_search_id, tender_id, first_seen_at, last_confirmed_at, status, tender_data)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (saved_search_id, tender_id) DO UPDATE
                    SET last_confirmed_at = EXCLUDED.last_confirmed_at, status = EXCLUDED.status, tender_data = EXCLUDED.tender_data""",
                (target_saved_id, tid, f_seen, l_conf, status, t_data),
            )
            copied += 1

        if last_refreshed:
            cur.execute(
                "UPDATE saved_searches SET last_refreshed_at = %s WHERE id = %s",
                (last_refreshed, target_saved_id),
            )

        conn.commit()
        cur.close()
        conn.close()
        return {"ok": True, "copied": copied}
    except Exception as e:
        print(f"[SavedSearchCache] Error copying recent cache to saved: {e}")
        return {"ok": False, "error": str(e), "copied": 0}


def cleanup_expired_cache_entries(get_db_conn_fn, retention_days: int = 30) -> int:
    """
    Purges removed tenders older than retention window from saved_search_cache,
    and prunes recent_search_cache for entries outside the top-N recent-searches window
    (RECENT_SEARCHES_CAP) and older than retention.
    """
    total_deleted = 0
    try:
        conn = get_db_conn_fn()
        cur = conn.cursor()
        cutoff = datetime.utcnow() - timedelta(days=retention_days)

        # 1. Clean saved_search_cache
        cur.execute(
            "DELETE FROM saved_search_cache WHERE status = 'removed' AND last_confirmed_at < %s",
            (cutoff,)
        )
        total_deleted += cur.rowcount

        cur.execute("DELETE FROM saved_search_cache WHERE saved_search_id NOT IN (SELECT id FROM saved_searches)")
        total_deleted += cur.rowcount

        # 2. Clean recent_search_cache:
        # A) Orphaned entries whose recent_search was deleted
        cur.execute("DELETE FROM recent_search_cache WHERE recent_search_id NOT IN (SELECT id FROM recent_searches)")
        total_deleted += cur.rowcount

        # B) Entries aged out of the top-N recent searches AND past retention window
        cur.execute(f"""
            DELETE FROM recent_search_cache
            WHERE recent_search_id NOT IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (PARTITION BY username ORDER BY searched_at DESC) as rn
                    FROM recent_searches
                ) sub WHERE rn <= {RECENT_SEARCHES_CAP}
            ) AND last_confirmed_at < %s
        """, (cutoff,))
        total_deleted += cur.rowcount

        conn.commit()
        cur.close()
        conn.close()
        return total_deleted
    except Exception as e:
        print(f"[SearchCache] Error during cleanup: {e}")
        return total_deleted


# Backward-compatible convenience aliases
def refresh_saved_search_cache(get_db_conn_fn, saved_search_id: int, username: Optional[str] = None, force_live: bool = False):
    return refresh_search_cache(get_db_conn_fn, saved_search_id, search_type="saved", username=username, force_live=force_live)


def get_cached_saved_search_results(get_db_conn_fn, saved_search_id: int, username: Optional[str] = None, window_hours: int = 48):
    return get_cached_search_results(get_db_conn_fn, saved_search_id, search_type="saved", username=username, window_hours=window_hours)


def invalidate_saved_search_cache(get_db_conn_fn, saved_search_id: int):
    return invalidate_search_cache(get_db_conn_fn, saved_search_id, search_type="saved")


def refresh_recent_search_cache(get_db_conn_fn, recent_search_id: int, username: Optional[str] = None, force_live: bool = False):
    return refresh_search_cache(get_db_conn_fn, recent_search_id, search_type="recent", username=username, force_live=force_live)


def get_cached_recent_search_results(get_db_conn_fn, recent_search_id: int, username: Optional[str] = None, window_hours: int = 48):
    return get_cached_search_results(get_db_conn_fn, recent_search_id, search_type="recent", username=username, window_hours=window_hours)


def invalidate_recent_search_cache(get_db_conn_fn, recent_search_id: int):
    return invalidate_search_cache(get_db_conn_fn, recent_search_id, search_type="recent")
