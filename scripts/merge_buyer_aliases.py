#!/usr/bin/env python3
"""Put one public body's awards under one buyer name (Buyer intelligence QA R12).

The same body is published under several names: NHS England as "The NHS Commissioning Board (operating under the name of NHS
England)", "NHS COMMISSIONING BOARD" and "NHS England (NHS Commissioning Board)"; a council's procurement unit as
"Leeds City Council - PACS"; a London borough as "Camden Council" and "London Borough of Camden". etenders_scraper.awards.canonical_authority_name lists the rules; new awards are renamed on ingestion.
Regional teams such as "NHS England - North West" are separate publishers and are left alone.

This rewrites contract_awards.authority_name. The old spelling is listed first, so it can be restored by hand if a rule was wrong.
Dry run by default; --apply commits.

    python scripts/merge_buyer_aliases.py
    python scripts/merge_buyer_aliases.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etenders_scraper.awards import canonical_authority_name  # noqa: E402
from tender_app.db import get_db_connection  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(r"""SELECT authority_name, COUNT(*) FROM contract_awards
                   WHERE authority_name ~* 'nhs\s+commissioning|^\s*nhs\s+england\s*$|council|borough'
                   GROUP BY 1""")
    changes = [(old, canonical_authority_name(old), n) for old, n in cur.fetchall()]
    changes = [(old, new, n) for old, new, n in changes if new and new != old]
    for old, new, n in sorted(changes, key=lambda c: -c[2]):
        print(f"{n:6}  {old!r}  ->  {new!r}")
    print(f"{len(changes)} spellings, {sum(c[2] for c in changes)} awards")
    if not args.apply:
        print("dry run: nothing changed (use --apply)")
        return
    for old, new, _n in changes:
        cur.execute("UPDATE contract_awards SET authority_name = %s WHERE authority_name = %s", (new, old))
    conn.commit()
    print("applied. The buyer statistics refresh on their own within the hour, or on the next supplier request.")


if __name__ == "__main__":
    main()
