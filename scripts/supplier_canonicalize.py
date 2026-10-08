#!/usr/bin/env python3
"""Repair suppliers.company_number and merge the fragments it caused.

Background: ingest used to store any non-empty source identifier verbatim as company_number, so a
single company became one supplier row per notice (GB-FTS-n, CF-n ids differ per notice), and
free text ("n/a", company names, "'04203130", address fragments) ended up in the column. The
ingest is fixed (etenders_scraper/awards.py: resolve_supplier_company_number); this script
cleans what is already stored, using the same identity rules.

Phases:
  A. Repair mangled numbers. Strip quotes / zero-width characters / whitespace; if the result is a
     valid Companies House number, adopt it (merging into the row that already holds it).
  B. Merge same-name fragments. Suppliers are grouped by normalized name (case, punctuation and
     spacing ignored). A group is merged only when the target is unambiguous:
       - exactly one row has a real CH number  -> everything else in the group merges into it
       - no row has a real CH number           -> merge into the best row (PPON first, then most
                                                   contact detail, then oldest)
     Groups with two or more different real CH numbers (e.g. councils sharing an acronym) are
     left alone and reported.
  C. Rewrite any remaining unusable company_number to a deterministic, collision-safe name hash.

Merging re-points contract_awards to the surviving row, fills its blank contact fields from the
merged rows, then deletes them. Real CH rows are never deleted or renamed.

Dry-run by default; --apply commits. Safe to re-run (idempotent), and safe while a backfill is
writing: every group is its own short transaction and row locks are taken in id order.

    python scripts/supplier_canonicalize.py            # dry run
    python scripts/supplier_canonicalize.py --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etenders_scraper.awards import _CLEAN_ID_RE, _PPON_RE, _fallback_company_number, supplier_name_key  # noqa: E402
from tender_app.ch_matcher import normalize_ch_company_number  # noqa: E402

COLS = "id, company_number, name, address, email, phone, website, region, sme_status, vcse_status"
CONTACT_FIELDS = ("address", "email", "phone", "website")
ZERO_WIDTH = re.compile("[​-‏⁠﻿]")
BLANK_STATUS = {"", "unknown"}

# supplier_name_key() (etenders_scraper/awards.py) only ignores case/punctuation/spacing -- it
# deliberately stops there because it also backs the live ingestion-time supplier match (and a
# matching DB index, SUPPLIER_NAME_KEY_SQL), where being wrong merges two different real
# companies going forward with no easy way to notice. A 2026-09-29 audit found ~23,000 supplier
# rows that are the same real company under a different legal-form suffix ("Foo Ltd" / "Foo
# Limited" / "FOO PLC"), which that narrower key doesn't fold. --wide uses this key instead, for
# this one-time cleanup only: same merge/ambiguity safety logic below, wider grouping.
_LEGAL_SUFFIX_RE = re.compile(r"\b(LTD|LIMITED|PLC|LLP|LLC|INC|CORP)\b")


def wide_supplier_name_key(name: str) -> str:
    spaced = re.sub(r"[^A-Za-z0-9]+", " ", name or "").upper().strip()
    return re.sub(r"\s+", "", _LEGAL_SUFFIX_RE.sub("", spaced))


def clean_number(raw: str) -> str:
    text = ZERO_WIDTH.sub("", str(raw or ""))
    return re.sub(r"^[\s'\"`#]+|[\s'\"`]+$", "", text).upper()


def is_real(cn: str) -> bool:
    return normalize_ch_company_number(cn) is not None


def is_ppon(cn: str) -> bool:
    return bool(_PPON_RE.match(str(cn or "").upper()))


def contact_score(row: dict) -> int:
    return sum(1 for f in CONTACT_FIELDS if str(row.get(f) or "").strip())


def pick_survivor(rows: list[dict]) -> dict | None:
    real = [r for r in rows if is_real(r["company_number"])]
    if len(real) == 1:
        return real[0]
    if len(real) > 1:
        return None  # ambiguous
    return sorted(rows, key=lambda r: (not is_ppon(r["company_number"]), -contact_score(r), r["id"]))[0]


def merge(cur, survivor: dict, dups: list[dict]) -> int:
    """Fold dups into survivor. Returns the number of contract_awards rows re-pointed."""
    ids = sorted([survivor["id"]] + [d["id"] for d in dups])
    cur.execute("SELECT id FROM suppliers WHERE id = ANY(%s) ORDER BY id FOR UPDATE;", (ids,))
    if len(cur.fetchall()) != len(ids):
        return -1  # a row vanished (concurrent ingest/cleanup); skip, the next run picks it up

    fills = {}
    for field in CONTACT_FIELDS:
        if not str(survivor.get(field) or "").strip():
            value = next((str(d[field]).strip() for d in dups if str(d.get(field) or "").strip()), None)
            if value:
                fills[field] = value
    if str(survivor.get("sme_status") or "").strip().lower() in BLANK_STATUS:
        value = next((d["sme_status"] for d in dups if str(d.get("sme_status") or "").strip().lower() not in BLANK_STATUS), None)
        if value:
            fills["sme_status"] = value
    if str(survivor.get("vcse_status") or "").strip().lower() in BLANK_STATUS:
        value = next((d["vcse_status"] for d in dups if str(d.get("vcse_status") or "").strip().lower() not in BLANK_STATUS), None)
        if value:
            fills["vcse_status"] = value
    if str(survivor.get("region") or "").strip().upper() in ("", "UK"):
        value = next((d["region"] for d in dups if str(d.get("region") or "").strip().upper() not in ("", "UK")), None)
        if value:
            fills["region"] = value

    dup_ids = [d["id"] for d in dups]
    dup_cns = [d["company_number"] for d in dups]
    cur.execute(
        "UPDATE contract_awards SET supplier_id = %s, company_number = %s, supplier_name = %s "
        "WHERE supplier_id = ANY(%s) OR company_number = ANY(%s);",
        (survivor["id"], survivor["company_number"], survivor["name"], dup_ids, dup_cns),
    )
    moved = cur.rowcount
    cur.execute("DELETE FROM suppliers WHERE id = ANY(%s);", (dup_ids,))  # frees the numbers first
    if fills:
        sets = ", ".join(f"{col} = %s" for col in fills)
        cur.execute(f"UPDATE suppliers SET {sets} WHERE id = %s;", (*fills.values(), survivor["id"]))
    return moved


def load(cur) -> list[dict]:
    cur.execute(f"SELECT {COLS} FROM suppliers;")
    names = [c.strip() for c in COLS.split(",")]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="commit changes (default: dry run)")
    ap.add_argument("--wide", action="store_true", help="Phase B also folds legal-form suffixes (Ltd/Limited/PLC/...); see wide_supplier_name_key")
    ap.add_argument("--lock-timeout", default="8s", help="give up on a group instead of waiting on a busy row")
    args = ap.parse_args()
    name_key = wide_supplier_name_key if args.wide else supplier_name_key

    from tender_app.db import get_db_connection

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SET lock_timeout = %s;", (args.lock_timeout,))
    rows = load(cur)
    print(f"Loaded {len(rows):,} suppliers. Mode: {'APPLY' if args.apply else 'DRY RUN'}\n")
    stats = defaultdict(int)

    # ---- Phase A: repair mangled numbers --------------------------------------------------
    by_cn = {r["company_number"]: r for r in rows}
    for r in list(rows):
        cn = r["company_number"] or ""
        if _CLEAN_ID_RE.match(cn.upper()):
            continue
        fixed = normalize_ch_company_number(clean_number(cn))
        if not fixed or fixed == cn:
            continue
        holder = by_cn.get(fixed)
        if holder and holder["id"] != r["id"]:
            stats["A: merged into existing holder of the cleaned number"] += 1
            if args.apply:
                try:
                    merge(cur, holder, [r]); conn.commit()
                except Exception as exc:  # lock timeout etc.
                    conn.rollback(); cur.execute("SET lock_timeout = %s;", (args.lock_timeout,)); stats["errors (retry later)"] += 1; print("  skip", r["id"], exc)
            rows = [x for x in rows if x["id"] != r["id"]]
        else:
            stats["A: number cleaned in place"] += 1
            if args.apply:
                try:
                    cur.execute("UPDATE suppliers SET company_number = %s WHERE id = %s;", (fixed, r["id"]))
                    cur.execute("UPDATE contract_awards SET company_number = %s WHERE supplier_id = %s;", (fixed, r["id"]))
                    conn.commit()
                except Exception as exc:
                    conn.rollback(); cur.execute("SET lock_timeout = %s;", (args.lock_timeout,)); stats["errors (retry later)"] += 1; print("  skip", r["id"], exc)
            by_cn.pop(cn, None); r["company_number"] = fixed; by_cn[fixed] = r

    # ---- Phase B: merge same-name fragments -------------------------------------------------
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        key = name_key(r["name"])
        if len(key) >= 5:
            groups[key].append(r)
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"Name groups with >1 row: {len(multi):,}")
    samples: list[str] = []
    removed_ids: set[int] = set()
    for i, (key, members) in enumerate(sorted(multi.items(), key=lambda kv: -len(kv[1])), 1):
        survivor = pick_survivor(members)
        if survivor is None:
            stats["B: ambiguous (2+ real CH numbers) - left alone"] += 1
            continue
        dups = [m for m in members if m["id"] != survivor["id"]]
        stats["B: groups merged"] += 1
        stats["B: supplier rows removed"] += len(dups)
        removed_ids.update(d["id"] for d in dups)
        if len(samples) < 8:
            samples.append(f"  {len(members)}x {survivor['name']!r} -> keep {survivor['company_number']}")
        if args.apply:
            try:
                merge(cur, survivor, dups); conn.commit()
            except Exception as exc:
                conn.rollback(); cur.execute("SET lock_timeout = %s;", (args.lock_timeout,)); stats["errors (retry later)"] += 1
                print("  skip group", key[:40], str(exc).splitlines()[0])
    print("\n".join(samples))

    # ---- Phase C: remaining unusable numbers -> deterministic hash --------------------------
    if args.apply:
        remaining = load(cur)
    else:  # dry run: what would be left after phase B
        remaining = [r for r in rows if r["id"] not in removed_ids]
    taken = {r["company_number"] for r in remaining}
    for r in remaining:
        cn = r["company_number"] or ""
        if _CLEAN_ID_RE.match(cn.upper()) or is_real(cn):
            continue
        clean = supplier_name_key(r["name"]) or "UNKNOWN"
        new = None
        for salt in range(30):
            cand = _fallback_company_number("CF", clean + (str(salt) if salt else ""))
            if cand not in taken:
                new = cand
                break
        if not new:
            stats["C: could not allocate a number"] += 1
            continue
        stats["C: junk number replaced"] += 1
        taken.add(new)
        if args.apply:
            try:
                cur.execute("UPDATE suppliers SET company_number = %s WHERE id = %s;", (new, r["id"]))
                cur.execute("UPDATE contract_awards SET company_number = %s WHERE supplier_id = %s;", (new, r["id"]))
                conn.commit()
            except Exception as exc:
                conn.rollback(); cur.execute("SET lock_timeout = %s;", (args.lock_timeout,)); stats["errors (retry later)"] += 1; print("  skip", r["id"], exc)

    print("\n=== Summary ===")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v:,}")
    if args.apply:
        cur.execute("SELECT count(*) FROM suppliers;")
        print(f"\nSuppliers now: {cur.fetchone()[0]:,}")
    else:
        conn.rollback()
        print("\nDRY RUN - nothing changed. Re-run with --apply.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
