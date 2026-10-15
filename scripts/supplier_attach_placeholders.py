#!/usr/bin/env python3
"""Attach placeholder-number supplier rows to their unique real-company anchor.

Conservative rules:
- A "placeholder row" is a supplier whose company_number is NOT a real Companies House number
  (CF..., GB-FTS-..., hashes, empty). Real-number rows are never deleted, renamed, or merged away.
- A repaired key is computed by stripping:
    a) leading numbering tokens like "(9) (18) (23)"
    b) trailing account notes like " - USE OTHER ACCOUNT"
    c) trailing parentheticals about lots: "(Lot 2)", "(Lots 1, 3, 4 & 5)"
    d) trailing "United Kingdom"
    e) glued trailing UI words after legal suffix: "...LTDClick"
    f) trailing "UK", "(UK)", or "Head Office" ONLY IF removing it makes the key match a unique anchor
- Attach ONLY when exactly ONE real-number anchor matches that key.
- "T/A" / "trading as" rows are out of scope and left alone.

Usage:
    python scripts/supplier_attach_placeholders.py          # dry run
    python scripts/supplier_attach_placeholders.py --apply  # apply changes
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_app.ch_matcher import normalize_ch_company_number
from tender_app.db import get_db_connection
from scripts.supplier_canonicalize import (
    COLS,
    CONTACT_FIELDS,
    BLANK_STATUS,
    load,
    merge,
    is_real,
    wide_supplier_name_key,
)

_LEGAL_SUFFIX_RE = re.compile(r"\b(LTD|LIMITED|PLC|LLP|LLC|INC|CORP)\b", re.IGNORECASE)

# a) leading numbering tokens: (9) (18) (23) or [1] [2]
LEADING_TOKENS_RE = re.compile(r"^(?:\s*[\(\[]\s*\d+\s*[\)\]])+\s*")

# b) trailing account note: - USE OTHER ACCOUNT, - DO NOT USE, etc.
ACCOUNT_NOTE_RE = re.compile(
    r"\s*-\s*(?:USE OTHER ACCOUNT|DO NOT USE|USE NEW ACCOUNT|DUPLICATE|USE OTHER ACC).*$",
    re.IGNORECASE,
)

# c) trailing parenthetical about lots: (Lot 2), (Lots 1, 3, 4 & 5)
LOTS_RE = re.compile(r"\s*\(\s*Lots?\s+[^)]+\)\s*$", re.IGNORECASE)

# d) trailing United Kingdom
TRAILING_UK_COUNTRY_RE = re.compile(r"\s+United\s+Kingdom\s*$", re.IGNORECASE)

# e) glued trailing UI word after legal suffix: ...LTDClick, ...LimitedClick, ...PLCView
GLUED_UI_RE = re.compile(
    r"\b(LTD|LIMITED|PLC|LLP|LLC|INC|CORP)(Click|View|More|Show|Details|Profile)\b",
    re.IGNORECASE,
)

# Out of scope: T/A or Trading As
TA_RE = re.compile(r"\b(?:T/A|T\\A|TRADING\s+AS)\b", re.IGNORECASE)


def clean_repaired_name(name: str) -> Tuple[str, List[str]]:
    """Applies rules a-e to the raw name and returns (cleaned_name, rules_applied)."""
    rules_applied = []
    text = name or ""

    # a) leading numbering tokens
    new_text = LEADING_TOKENS_RE.sub("", text)
    if new_text != text:
        rules_applied.append("a_leading_numbering")
        text = new_text

    # b) account note
    new_text = ACCOUNT_NOTE_RE.sub("", text)
    if new_text != text:
        rules_applied.append("b_account_note")
        text = new_text

    # c) lots parenthetical
    new_text = LOTS_RE.sub("", text)
    if new_text != text:
        rules_applied.append("c_lots")
        text = new_text

    # d) trailing United Kingdom
    new_text = TRAILING_UK_COUNTRY_RE.sub("", text)
    if new_text != text:
        rules_applied.append("d_united_kingdom")
        text = new_text

    # e) glued UI word after legal suffix
    new_text = GLUED_UI_RE.sub(r"\1", text)
    if new_text != text:
        rules_applied.append("e_glued_ui")
        text = new_text

    return text.strip(), rules_applied


def extract_keys_for_anchor(name: str) -> set[str]:
    """Returns the set of all keys an anchor could match under:
    1. wide_supplier_name_key
    2. repaired base key without legal suffixes
    3. trailing UK / (UK) / U.K. / Head Office stripped key
    """
    keys = set()
    wk = wide_supplier_name_key(name)
    if wk:
        keys.add(wk)

    cleaned, _ = clean_repaired_name(name)
    spaced = re.sub(r"[^A-Za-z0-9]+", " ", cleaned).upper().strip()
    no_legal = _LEGAL_SUFFIX_RE.sub(" ", spaced).strip()
    bk1 = re.sub(r"\s+", "", no_legal)
    if bk1:
        keys.add(bk1)

    m = re.search(r"\b(UK|U\s+K|HEAD\s+OFFICE)\s*$", no_legal)
    if m:
        no_uk = no_legal[:m.start()].strip()
        bk2 = re.sub(r"\s+", "", no_uk)
        if bk2:
            keys.add(bk2)

    return keys


def extract_base_key(name: str) -> Tuple[str, List[str]]:
    """Strips noise rules (a-e), legal suffixes, and trailing UK/Head Office (rule f).

    Returns (base_key, rules_applied).
    """
    cleaned, rules = clean_repaired_name(name)
    spaced = re.sub(r"[^A-Za-z0-9]+", " ", cleaned).upper().strip()
    no_legal = _LEGAL_SUFFIX_RE.sub(" ", spaced).strip()

    # Rule f: trailing UK, (UK), U.K., or Head Office
    m = re.search(r"\b(UK|U\s+K|HEAD\s+OFFICE)\s*$", no_legal)
    if m:
      no_uk = no_legal[: m.start()].strip()
      if no_uk:
        rules.append("f_uk_headoffice_stripped")
        no_legal = no_uk

    base_key = re.sub(r"\s+", "", no_legal)
    return base_key, rules


def find_attachments(
    rows: List[Dict[str, Any]],
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    Dict[str, int],
]:
    """Finds all valid placeholder -> anchor attachments using the UNION of every key test.

    A placeholder attaches ONLY if the set of REAL-number suppliers whose
    wide_supplier_name_key OR extract_base_key OR UK/(UK)-stripped key equals
    the placeholder's repaired key has exactly 1 member.

    Returns:
        (planned_attachments, ambiguous_rows, out_of_scope_rows, rule_counts)
    """
    anchors_by_key: Dict[str, Dict[int, Dict[str, Any]]] = defaultdict(dict)
    placeholders: List[Dict[str, Any]] = []

    for r in rows:
        if is_real(r["company_number"]):
            a_keys = extract_keys_for_anchor(r.get("name") or "")
            for k in a_keys:
                anchors_by_key[k][r["id"]] = r
        else:
            placeholders.append(r)

    planned: List[Dict[str, Any]] = []
    ambiguous: List[Dict[str, Any]] = []
    out_of_scope: List[Dict[str, Any]] = []
    rule_counts: Dict[str, int] = defaultdict(int)

    for p in placeholders:
        p_name = p.get("name") or ""

        # Check out of scope (T/A)
        if TA_RE.search(p_name):
            out_of_scope.append({"row": p, "reason": "T/A trading as"})
            continue

        bk, applied_rules = extract_base_key(p_name)
        if not bk:
            continue

        matching_anchors = list(anchors_by_key.get(bk, {}).values())
        if len(matching_anchors) == 1:
            target_anchor = matching_anchors[0]
            rules_to_record = applied_rules if applied_rules else ["direct_match"]
            for r_name in rules_to_record:
                rule_counts[r_name] += 1

            tier, reasons = compute_tier(p, target_anchor, bk, rules_to_record)
            planned.append({
                "placeholder": p,
                "anchor": target_anchor,
                "key": bk,
                "rules": rules_to_record,
                "tier": tier,
                "tier_reasons": reasons,
            })
        elif len(matching_anchors) > 1:
            ambiguous.append({
                "row": p,
                "key": bk,
                "reason": f"Multiple real anchors ({len(matching_anchors)}) match union of keys",
                "matching_anchors": matching_anchors,
            })

    return planned, ambiguous, out_of_scope, rule_counts


def get_legal_form(name: str) -> Optional[str]:
    m = re.search(r"\b(PLC|PUBLIC\s+LIMITED\s+COMPANY)\b", name, re.IGNORECASE)
    if m:
        return "PLC"
    m = re.search(r"\b(LLP|LIMITED\s+LIABILITY\s+PARTNERSHIP)\b", name, re.IGNORECASE)
    if m:
        return "LLP"
    m = re.search(r"\b(INC|INCORPORATED|CORP|CORPORATION)\b", name, re.IGNORECASE)
    if m:
        return "INC"
    m = re.search(r"\b(LTD|LIMITED)\b", name, re.IGNORECASE)
    if m:
        return "LTD"
    m = re.search(r"\b(CIC|COMMUNITY\s+INTEREST\s+COMPANY)\b", name, re.IGNORECASE)
    if m:
        return "CIC"
    return None


def has_uk_or_headoffice(name: str) -> bool:
    return bool(re.search(r"\b(UK|U\.K\.|U\s+K|UNITED\s+KINGDOM|HEAD\s+OFFICE)\b", name or "", re.IGNORECASE))


def compute_tier(
    placeholder: Dict[str, Any], anchor: Dict[str, Any], key: str, rules: List[str]
) -> Tuple[str, List[str]]:
    reasons = []
    # 1. rule-f
    if "f_uk_headoffice_stripped" in rules:
        reasons.append("rule_f_uk_headoffice")

    # 2. single bare word with awards >= 10
    awards = placeholder.get("award_count", 0)
    p_name = (placeholder.get("name") or "").strip()
    a_name = (anchor.get("name") or "").strip()
    is_bare_word = len(p_name.split()) == 1 and awards >= 10
    if is_bare_word:
        reasons.append(f"bare_single_word_awards_{awards}")

    # 3. legal form mismatch (PLC vs Ltd vs LLP vs Inc)
    p_legal = get_legal_form(p_name)
    a_legal = get_legal_form(a_name)
    has_legal_mismatch = bool(p_legal and a_legal and p_legal != a_legal)
    if has_legal_mismatch:
        reasons.append(f"legal_form_mismatch_{p_legal}_vs_{a_legal}")

    if not reasons:
        return "1", []

    # Tier 1b: bare names matching anchor apart from legal suffix and case, no UK/Head Office, no legal form mismatch
    if not has_uk_or_headoffice(p_name) and not has_uk_or_headoffice(a_name) and not has_legal_mismatch:
        wk_p = wide_supplier_name_key(p_name)
        wk_a = wide_supplier_name_key(a_name)
        if wk_p and wk_p == wk_a:
            return "1b", ["tier1b_clean_bare_name_match"]

    return "2", reasons


def ensure_alias_table(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS supplier_aliases (
            alias_company_number TEXT PRIMARY KEY,
            canonical_supplier_id INT REFERENCES suppliers(id) ON DELETE CASCADE,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
        );
        CREATE INDEX IF NOT EXISTS idx_supplier_aliases_canonical ON supplier_aliases(canonical_supplier_id);
    """)


def attach_placeholder(cur, anchor: Dict[str, Any], placeholder: Dict[str, Any]) -> int:
    """Re-points placeholder's awards to anchor WITHOUT overwriting supplier_name.

    - Preserves contract_awards.supplier_name as the original raw name from the tender notice.
    - Re-points contract_awards.supplier_id and company_number to anchor.
    - Records the alias mapping in supplier_aliases in the same transaction.
    - Enriches anchor with missing website, address, SME/VCSE status, and region.
    - Deletes the placeholder row from suppliers.
    """
    p_id = placeholder["id"]
    cur.execute("SELECT id FROM suppliers WHERE id = %s;", (p_id,))
    if not cur.fetchall():
        return -1

    fills = {}
    if not str(anchor.get("website") or "").strip():
        val = str(placeholder.get("website") or "").strip()
        if val:
            fills["website"] = val
    if not str(anchor.get("address") or "").strip():
        val = str(placeholder.get("address") or "").strip()
        if val:
            fills["address"] = val
    if str(anchor.get("sme_status") or "").strip().lower() in BLANK_STATUS:
        val = str(placeholder.get("sme_status") or "").strip().lower()
        if val not in BLANK_STATUS:
            fills["sme_status"] = placeholder.get("sme_status")
    if str(anchor.get("vcse_status") or "").strip().lower() in BLANK_STATUS:
        val = str(placeholder.get("vcse_status") or "").strip().lower()
        if val not in BLANK_STATUS:
            fills["vcse_status"] = placeholder.get("vcse_status")
    if str(anchor.get("region") or "").strip().upper() in ("", "UK"):
        val = str(placeholder.get("region") or "").strip().upper()
        if val not in ("", "UK"):
            fills["region"] = placeholder.get("region")

    p_cn = placeholder.get("company_number")
    cur.execute(
        "UPDATE contract_awards SET supplier_id = %s, company_number = %s "
        "WHERE supplier_id = %s OR (company_number = %s AND %s IS NOT NULL);",
        (anchor["id"], anchor["company_number"], p_id, p_cn, p_cn),
    )
    moved = cur.rowcount

    # Insert alias mapping into supplier_aliases in the same transaction
    if p_cn:
        clean_cn = str(p_cn).strip().upper()
        cur.execute(
            """
            INSERT INTO supplier_aliases (alias_company_number, canonical_supplier_id)
            VALUES (%s, %s)
            ON CONFLICT (alias_company_number) DO UPDATE SET canonical_supplier_id = EXCLUDED.canonical_supplier_id;
            """,
            (clean_cn, anchor["id"]),
        )

    cur.execute("DELETE FROM suppliers WHERE id = %s;", (p_id,))
    if fills:
        sets = ", ".join(f"{col} = %s" for col in fills)
        cur.execute(f"UPDATE suppliers SET {sets} WHERE id = %s;", (*fills.values(), anchor["id"]))
    return moved


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="commit changes (default: dry run)")
    ap.add_argument(
        "--tier",
        default="1",
        choices=["1", "1b", "1+1b", "2"],
        help="Tier to run: 1 (safe), 1b (bare name), 1+1b (all safe), or 2 (review)",
    )
    ap.add_argument("--lock-timeout", default="8s", help="lock timeout per transaction")
    args = ap.parse_args()

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SET lock_timeout = %s;", (args.lock_timeout,))

    # Preload award counts
    cur.execute("SELECT supplier_id, count(*) FROM contract_awards GROUP BY supplier_id;")
    awards_by_id = dict(cur.fetchall())

    rows = load(cur)
    for r in rows:
        r["award_count"] = awards_by_id.get(r["id"], 0)

    mode_str = f"APPLY (Tier {args.tier})" if args.apply else f"DRY RUN (Tier {args.tier} only)"
    print(f"Loaded {len(rows):,} suppliers. Mode: {mode_str}\n")

    planned, ambiguous, out_of_scope, rule_counts = find_attachments(rows)

    tier1_items = [item for item in planned if item["tier"] == "1"]
    tier1b_items = [item for item in planned if item["tier"] == "1b"]
    tier2_items = [item for item in planned if item["tier"] == "2"]

    print(f"Total placeholder rows that would attach across all tiers: {len(planned):,}")
    print(f"  - TIER 1 (safe): {len(tier1_items):,}")
    print(f"  - TIER 1b (bare name): {len(tier1b_items):,}")
    print(f"  - TIER 2 (review): {len(tier2_items):,}")
    print(f"Ambiguous placeholder rows (2+ real anchors): {len(ambiguous):,}")
    print(f"Out of scope rows (T/A): {len(out_of_scope):,}")

    if args.tier == "1":
        active_items = tier1_items
    elif args.tier == "1b":
        active_items = tier1b_items
    elif args.tier == "1+1b":
        active_items = tier1_items + tier1b_items
    else:
        active_items = tier2_items

    print(f"\nActive Tier: TIER {args.tier} ({len(active_items):,} attachments)")

    print("\n--- Rule counts (all planned) ---")
    for r_name, cnt in sorted(rule_counts.items()):
        print(f"  {r_name}: {cnt:,}")

    print(f"\n--- Sample Tier {args.tier} attachments (first 40) ---")
    for item in active_items[:40]:
        p = item["placeholder"]
        a = item["anchor"]
        reasons_str = f" [reasons: {', '.join(item['tier_reasons'])}]" if item.get("tier_reasons") else ""
        print(f"  {p['name']} ({p['id']}, {p['company_number']}) -> {a['name']} ({a['id']}, {a['company_number']}) [rules: {', '.join(item['rules'])}]{reasons_str}")

    if args.apply:
        ensure_alias_table(cur)
        conn.commit()
        print(f"\nApplying {len(active_items):,} Tier {args.tier} attachments...")
        stats = defaultdict(int)
        for i, item in enumerate(active_items, 1):
            p = item["placeholder"]
            a = item["anchor"]
            try:
                res = attach_placeholder(cur, a, p)
                conn.commit()
                if res >= 0:
                    stats["attached"] += 1
                else:
                    stats["already_merged"] += 1
            except Exception as exc:
                conn.rollback()
                cur.execute("SET lock_timeout = %s;", (args.lock_timeout,))
                stats["errors"] += 1
                print(f"  skip attachment {p['id']} -> {a['id']}: {str(exc).splitlines()[0]}")

        print("\n=== Apply Summary ===")
        for k, v in sorted(stats.items()):
            print(f"  {k}: {v:,}")

        cur.execute("SELECT count(*) FROM suppliers;")
        print(f"\nSuppliers now: {cur.fetchone()[0]:,}")
    else:
        conn.rollback()
        print(f"\nDRY RUN complete for Tier {args.tier} - nothing changed. Run with --apply --tier {args.tier} to commit.")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
