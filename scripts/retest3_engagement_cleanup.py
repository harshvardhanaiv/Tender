"""One-off clean-up of two market engagements left by QA (re-test 3 W6). DRY RUN unless --apply is given.

  * "QA test CRM replacement (delete me)": deleted.
  * the "Tree Planting" draft: with --tree-planting fix its category is set to the "Grounds & tree works" preset (CPV 77211600 belongs
    to it); with --tree-planting delete it is deleted; without the option it is only listed.

Every row about to be changed is written, whole, to the revert file first (default engagement_cleanup_revert.json); --revert FILE puts
those rows back exactly as they were.

    python scripts/retest3_engagement_cleanup.py                                   # dry run: lists what it would do
    python scripts/retest3_engagement_cleanup.py --tree-planting fix --apply
    python scripts/retest3_engagement_cleanup.py --revert engagement_cleanup_revert.json

Uses the database in DB_HOST / DB_NAME / DB_USER / DB_PASSWORD. Read the dry run against production before adding --apply.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

QA_TITLE = "QA test CRM replacement%"
TREE_TITLE = "Tree Planting%"


def plan(rows, tree_action):
    """[(action, row)] from market_engagements rows (dicts): the QA test is deleted; the Tree Planting draft as `tree_action` says."""
    out = []
    for r in rows:
        title = str(r.get("title") or "")
        if title.lower().startswith("qa test crm replacement"):
            out.append(("delete", r))
        elif title.lower().startswith("tree planting") and r.get("status") == "draft" and tree_action in ("fix", "delete"):
            out.append((tree_action, r))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--tree-planting", choices=("fix", "delete"))
    ap.add_argument("--revert", metavar="FILE")
    ap.add_argument("--revert-file", default="engagement_cleanup_revert.json")
    args = ap.parse_args(argv)

    from tender_app.db import get_db_connection
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        if args.revert:
            saved = json.load(open(args.revert, encoding="utf-8"))
            for row in saved:
                cols = list(row)
                cur.execute(f"INSERT INTO market_engagements ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                            f"ON CONFLICT (id) DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "id"), [row[c] for c in cols])
            conn.commit()
            print(f"restored {len(saved)} rows from {args.revert}")
            return 0
        cur.execute("SELECT * FROM market_engagements WHERE title ILIKE %s OR title ILIKE %s ORDER BY id", (QA_TITLE, TREE_TITLE))
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
        for r in rows:
            print(f"found {r['id']}: {r['title']!r} status={r['status']} user={r['username']} category={r['category_label']!r}")
        todo = plan(rows, args.tree_planting)
        for action, r in todo:
            print(f"  would {action}: {r['id']} {r['title']!r}")
        if not todo:
            print("nothing to do")
        if not args.apply:
            print("DRY RUN: nothing was changed. Add --apply to do the above.")
            return 0
        json.dump([r for _, r in todo], open(args.revert_file, "w", encoding="utf-8"), default=str, indent=1)
        print(f"revert list written to {args.revert_file}")
        for action, r in todo:
            if action == "delete":
                cur.execute("DELETE FROM market_engagements WHERE id = %s", (r["id"],))
            else:
                cur.execute("UPDATE market_engagements SET category_json = %s, category_label = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                            (json.dumps({"preset": "grounds-tree-works"}), "Grounds & tree works", r["id"]))
        conn.commit()
        print(f"{len(todo)} rows changed")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
