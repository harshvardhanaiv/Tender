#!/usr/bin/env python3
"""Turn HTML entities left in award text into the characters they stand for (Buyer workspace QA W5: "Children&apos;s").

Notices arrive with &apos; &amp; &quot; and numeric entities in titles and names; new awards are decoded on ingestion
(etenders_scraper.awards.ingest_award_record). This repairs the rows already stored. Dry run by default; --apply commits.

    python scripts/decode_html_entities.py
    python scripts/decode_html_entities.py --apply
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_app.db import get_db_connection  # noqa: E402

COLUMNS = ("tender_title", "authority_name", "supplier_name")
PATTERN = "&(amp|apos|quot|lt|gt|#[0-9]+|#x[0-9a-fA-F]+);"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = get_db_connection()
    cur = conn.cursor()
    total = 0
    for col in COLUMNS:
        cur.execute(f"SELECT id, {col} FROM contract_awards WHERE {col} ~ %s", (PATTERN,))
        rows = [(i, v, html.unescape(v)) for i, v in cur.fetchall() if v and html.unescape(v) != v]
        total += len(rows)
        print(f"{col}: {len(rows)} rows", ("e.g. " + repr(rows[0][1][:60]) + " -> " + repr(rows[0][2][:60])) if rows else "")
        if args.apply:
            for i, _old, new in rows:
                cur.execute(f"UPDATE contract_awards SET {col} = %s WHERE id = %s", (new, i))
    if not args.apply:
        print(f"{total} values to change. Dry run: nothing changed (use --apply)")
        return
    conn.commit()
    print(f"applied: {total} values. Rebuild happens on its own within the hour.")


if __name__ == "__main__":
    main()
