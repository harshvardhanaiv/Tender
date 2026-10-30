"""One-off repair of History titles saved before the clean-up (re-test 3 HS2): "Analyzing Pasted Details...", "View Notice", markdown
**bold**, a trailing "- AWARD".

A title that is only a placeholder is taken from the saved result when that holds one (its "title" field), else becomes "Untitled tender".
Nothing else about a row is touched.

Usage:
    python scripts/history_title_cleanup.py                       # DRY RUN (default): lists the changes, writes the revert list, changes nothing
    python scripts/history_title_cleanup.py --apply               # makes the changes (after the dry run has been read)
    python scripts/history_title_cleanup.py --revert FILE         # puts the old titles back from a revert list written by a run

The database is the one in DB_HOST / DB_NAME / DB_USER / DB_PASSWORD. Run it against production only after reading the dry run.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tender_app.text_fix import clean_history_title  # noqa: E402

FALLBACK = "Untitled tender"


def saved_title(analysis_json) -> str:
    """A title inside a saved result, if it has one."""
    try:
        data = json.loads(analysis_json) if isinstance(analysis_json, str) else (analysis_json or {})
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    for holder in (data, data.get("tender"), data.get("row"), data.get("summary")):
        if isinstance(holder, dict):
            for key in ("title", "tender_title"):
                found = clean_history_title(holder.get(key))
                if found:
                    return found
    return ""


def proposed_title(title, analysis_json=None) -> str:
    """The title a stored row should have: the cleaned one, else the saved result's own, else "Untitled tender"."""
    return clean_history_title(title) or saved_title(analysis_json) or FALLBACK


def plan(rows):
    """[(id, old, new)] for the rows whose title changes."""
    return [(rid, old, proposed_title(old, js)) for rid, old, js in rows if proposed_title(old, js) != (old or "")]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="make the changes (default is a dry run)")
    ap.add_argument("--revert", metavar="FILE", help="restore the old titles from a revert list")
    ap.add_argument("--revert-file", default="history_title_revert.json", help="where a run writes the old titles (default: %(default)s)")
    args = ap.parse_args(argv)

    from tender_app.db import get_db_connection
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        if args.revert:
            with open(args.revert, encoding="utf-8") as fh:
                old = json.load(fh)
            for rid, title in old.items():
                cur.execute("UPDATE analyses SET tender_title = %s WHERE id = %s", (title, int(rid)))
            conn.commit()
            print(f"restored {len(old)} titles from {args.revert}")
            return 0
        cur.execute("SELECT id, tender_title, analysis_json FROM analyses ORDER BY id")
        changes = plan(cur.fetchall())
        for rid, old, new in changes:
            print(f"{rid}: {old!r} -> {new!r}")
        print(f"{len(changes)} titles would change" if not args.apply else f"{len(changes)} titles changed")
        with open(args.revert_file, "w", encoding="utf-8") as fh:
            json.dump({str(rid): old for rid, old, _ in changes}, fh, indent=1)
        print(f"revert list written to {args.revert_file}")
        if args.apply:
            for rid, _, new in changes:
                cur.execute("UPDATE analyses SET tender_title = %s WHERE id = %s", (new, rid))
            conn.commit()
        else:
            print("DRY RUN: nothing was changed. Add --apply to make these changes.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
