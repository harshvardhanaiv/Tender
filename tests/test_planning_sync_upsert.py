"""The planning sync must never roll a live row back to an older local copy.

Before the fix both --dump and --apply used an unconditional ON CONFLICT DO UPDATE, so in the real
live backup 2,101 of 26,245 overlapping rows (decisions, states, scores that live's own harvester had
moved on) would have been overwritten with the older local values.
Uses a TEMP table in a rolled-back transaction: nothing real is touched.
Run: python tests/test_planning_sync_upsert.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

import planning_sync_live_local as sync
from etenders_scraper.planning.harvester import UPSERT_COLUMNS
from tender_app.db import get_db_connection


def _row(pid, decision, changed):
    vals = {c: None for c in UPSERT_COLUMNS}
    vals.update({"id": pid, "authority": "ZZ Test Authority", "decision": decision, "last_changed": changed})
    if "raw_json" in vals:
        vals["raw_json"] = "{}"
    return [vals[c] for c in UPSERT_COLUMNS]


def test_only_newer_rows_overwrite_and_new_rows_insert():
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("CREATE TEMP TABLE planning_applications (LIKE public.planning_applications INCLUDING ALL)")
        cols = ", ".join(f'"{c}"' for c in UPSERT_COLUMNS)
        marks = ", ".join(["%s"] * len(UPSERT_COLUMNS))
        sql = f"INSERT INTO planning_applications ({cols}) VALUES ({marks}) {sync.upsert_conflict_clause()}"
        # existing "live" rows
        for pid, dec, ts in (("zz/live-newer", "Permitted", "2026-10-05 08:00:00"),
                             ("zz/local-newer", "Referred", "2026-09-01 08:00:00"),
                             ("zz/same", "Refused", "2026-09-10 08:00:00"),
                             ("zz/live-null", "Old", None)):
            cur.execute(f"INSERT INTO planning_applications ({cols}) VALUES ({marks})", _row(pid, dec, ts))
        # incoming "local" copies
        for pid, dec, ts in (("zz/live-newer", "Referred", "2026-09-17 08:00:00"),
                             ("zz/local-newer", "Permitted", "2026-10-01 08:00:00"),
                             ("zz/same", "ShouldNotChange", "2026-09-10 08:00:00"),
                             ("zz/live-null", "New", "2026-09-10 08:00:00"),
                             ("zz/brand-new", "Undecided", "2026-09-10 08:00:00")):
            cur.execute(sql, _row(pid, dec, ts))
        cur.execute("SELECT id, decision FROM planning_applications ORDER BY id")
        got = dict(cur.fetchall())
        assert got["zz/live-newer"] == "Permitted", f"live's newer row was rolled back: {got['zz/live-newer']}"
        assert got["zz/local-newer"] == "Permitted", "a genuinely newer local row must still update live"
        assert got["zz/same"] == "Refused", "an unchanged timestamp must not overwrite"
        assert got["zz/live-null"] == "New", "a live row with no timestamp is filled in"
        assert got["zz/brand-new"] == "Undecided", "rows live does not have are inserted"
    finally:
        conn.rollback()
        conn.close()
    print("ok    test_only_newer_rows_overwrite_and_new_rows_insert")


def test_dump_and_apply_use_the_same_guarded_clause():
    src = (ROOT / "scripts" / "planning_sync_live_local.py").read_text(encoding="utf-8")
    assert src.count("upsert_conflict_clause()") >= 3, "helper must be defined once and used by --dump and --apply"
    assert 'ON CONFLICT ("id") DO UPDATE SET {update_set_str}' not in src, "an unguarded upsert is back"
    print("ok    test_dump_and_apply_use_the_same_guarded_clause")


def test_the_dump_batches_rows_instead_of_one_statement_per_row():
    """One INSERT per row meant 32,063 network round trips over the remote connection (hours)."""
    assert sync.DUMP_BATCH >= 100, f"DUMP_BATCH is {sync.DUMP_BATCH}; a per-row dump is hours over the network"
    src = (ROOT / "scripts" / "planning_sync_live_local.py").read_text(encoding="utf-8")
    assert "range(0, len(rows), DUMP_BATCH)" in src, "the dump loop must write DUMP_BATCH rows per statement"
    print("ok    test_the_dump_batches_rows_instead_of_one_statement_per_row")


if __name__ == "__main__":
    test_only_newer_rows_overwrite_and_new_rows_insert()
    test_dump_and_apply_use_the_same_guarded_clause()
    test_the_dump_batches_rows_instead_of_one_statement_per_row()
    print("3 passed, 0 skipped, 0 failed")
