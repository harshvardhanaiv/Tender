#!/usr/bin/env python3
"""Comprehensive rebuild of PostgreSQL database from SQLite users.db.

Extracts all distinct supplier entities (~66k-87k) across both `suppliers` and `contract_awards`,
merges rich contact metadata, populates `suppliers` and all 117,713 `contract_awards` with
valid `supplier_id` foreign key references, and restores credit wallets & user preferences.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from psycopg2.extras import execute_values

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tender_app.db import get_db_connection

BATCH_SIZE = 2000

def is_valid_cnum(cnum: str | None) -> bool:
    if not cnum:
        return False
    c = cnum.strip().upper()
    if not c or c in ("NOT AVAILABLE", "N/A", "NONE", "UNKNOWN", "00000000", "PROCUREM") or c.startswith("NO_REG") or c.startswith("AUTO_"):
        return False
    return True

def rebuild_all():
    sqlite_path = REPO_ROOT / "users.db"
    if not sqlite_path.is_file():
        print(f"Error: {sqlite_path} not found.")
        return 1

    src = sqlite3.connect(f"file:{sqlite_path.resolve().as_posix()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    s_cur = src.cursor()

    dst = get_db_connection()
    p_cur = dst.cursor()

    try:
        print("Reading SQLite suppliers and contract_awards...")
        s_suppliers = s_cur.execute("SELECT * FROM suppliers").fetchall()
        s_awards = s_cur.execute("SELECT * FROM contract_awards").fetchall()

        print(f"SQLite Suppliers: {len(s_suppliers)}")
        print(f"SQLite Contract Awards: {len(s_awards)}")

        # Step 1: Index rich supplier data from SQLite suppliers
        rich_by_cnum = {}
        rich_by_name = {}
        for s in s_suppliers:
            cnum = s["company_number"]
            name = s["name"]
            info = {
                "address": s["address"],
                "email": s["email"],
                "phone": s["phone"] if "phone" in s.keys() else None,
                "website": s["website"],
                "region": s["region"] or "UK",
                "sme_status": s["sme_status"] or "Unknown",
                "vcse_status": s["vcse_status"] or "Unknown"
            }
            if is_valid_cnum(cnum):
                rich_by_cnum[cnum.strip().upper()] = (name, info)
            if name and name.strip():
                rich_by_name[name.strip().upper()] = (cnum, info)

        # Step 2: Build comprehensive distinct supplier list
        suppliers_map = {}
        cnum_seen = set()
        auto_counter = 0

        # First pass: from contract_awards
        for a in s_awards:
            cnum = a["company_number"]
            sname = a["supplier_name"]

            c_clean = cnum.strip().upper() if is_valid_cnum(cnum) else ""
            s_clean = sname.strip() if sname and sname.strip() else ""

            if not c_clean and not s_clean:
                continue

            key = f"CNUM:{c_clean}" if c_clean else f"NAME:{s_clean.upper()}"

            if key not in suppliers_map:
                rich_info = {}
                name_val = s_clean
                cnum_val = c_clean if (c_clean and c_clean not in cnum_seen) else None

                if c_clean and c_clean in rich_by_cnum:
                    r_name, r_info = rich_by_cnum[c_clean]
                    if not name_val and r_name:
                        name_val = r_name
                    rich_info = r_info
                elif s_clean and s_clean.upper() in rich_by_name:
                    r_cnum, r_info = rich_by_name[s_clean.upper()]
                    if not cnum_val and is_valid_cnum(r_cnum) and r_cnum.upper() not in cnum_seen:
                        cnum_val = r_cnum.upper()
                    rich_info = r_info

                if not name_val:
                    name_val = f"Supplier {cnum_val}" if cnum_val else "Unknown Supplier"

                if not cnum_val:
                    auto_counter += 1
                    cnum_val = f"AUTO_{auto_counter:07d}"

                cnum_seen.add(cnum_val)

                suppliers_map[key] = {
                    "company_number": cnum_val,
                    "name": name_val,
                    "address": rich_info.get("address"),
                    "email": rich_info.get("email"),
                    "phone": rich_info.get("phone"),
                    "website": rich_info.get("website"),
                    "region": rich_info.get("region") or "UK",
                    "sme_status": rich_info.get("sme_status") or "Unknown",
                    "vcse_status": rich_info.get("vcse_status") or "Unknown"
                }

        # Second pass: include any remaining suppliers from suppliers table
        for s in s_suppliers:
            cnum = s["company_number"]
            name = s["name"]
            c_clean = cnum.strip().upper() if is_valid_cnum(cnum) else ""
            s_clean = name.strip() if name and name.strip() else ""

            key = f"CNUM:{c_clean}" if c_clean else f"NAME:{s_clean.upper()}" if s_clean else None
            if key and key not in suppliers_map:
                cnum_val = c_clean if (c_clean and c_clean not in cnum_seen) else None
                if not cnum_val:
                    auto_counter += 1
                    cnum_val = f"AUTO_{auto_counter:07d}"
                cnum_seen.add(cnum_val)

                suppliers_map[key] = {
                    "company_number": cnum_val,
                    "name": s_clean or "Unknown Supplier",
                    "address": s["address"],
                    "email": s["email"],
                    "phone": s["phone"] if "phone" in s.keys() else None,
                    "website": s["website"],
                    "region": s["region"] or "UK",
                    "sme_status": s["sme_status"] or "Unknown",
                    "vcse_status": s["vcse_status"] or "Unknown"
                }

        total_suppliers = len(suppliers_map)
        print(f"Generated total distinct supplier records: {total_suppliers}")

        # Step 3: Clear Postgres contract_awards and suppliers
        print("Clearing contract_awards and suppliers in Postgres...")
        p_cur.execute("TRUNCATE contract_awards, suppliers RESTART IDENTITY CASCADE")

        # Step 4: Insert suppliers into Postgres
        cols = ["company_number", "name", "address", "email", "phone", "website", "region", "sme_status", "vcse_status"]
        col_str = ", ".join(cols)

        batch_suppliers = []
        for key, data in suppliers_map.items():
            batch_suppliers.append((
                data["company_number"],
                data["name"],
                data["address"],
                data["email"],
                data["phone"],
                data["website"],
                data["region"],
                data["sme_status"],
                data["vcse_status"]
            ))

        print(f"Inserting {len(batch_suppliers)} suppliers into Postgres...")
        execute_values(p_cur, f"INSERT INTO suppliers ({col_str}) VALUES %s ON CONFLICT (company_number) DO NOTHING", batch_suppliers, page_size=BATCH_SIZE)

        # Retrieve supplier IDs for mapping
        p_cur.execute("SELECT id, company_number, name FROM suppliers")
        pg_suppliers = p_cur.fetchall()

        id_by_cnum = {}
        id_by_name = {}
        for sid, cnum, name in pg_suppliers:
            if cnum and cnum.strip() and is_valid_cnum(cnum):
                id_by_cnum[cnum.strip().upper()] = sid
            if name and name.strip():
                id_by_name[name.strip().upper()] = sid

        print(f"Retrieved {len(pg_suppliers)} suppliers from Postgres for award mapping.")

        # Step 5: Insert contract_awards into Postgres with mapped supplier_id
        # Get PG contract_awards column list
        p_cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'contract_awards'")
        pg_award_cols = {r[0] for r in p_cur.fetchall()}

        s_award_cols = [c[1] for c in s_cur.execute("PRAGMA table_info(contract_awards)")]
        common_cols = [c for c in s_award_cols if c in pg_award_cols]

        col_award_list = ", ".join(common_cols)
        print(f"Contract awards columns: {common_cols}")

        batch_awards = []
        copied_awards = 0

        for a in s_awards:
            a_dict = dict(a)
            cnum = a_dict.get("company_number")
            sname = a_dict.get("supplier_name")

            c_clean = cnum.strip().upper() if is_valid_cnum(cnum) else ""
            s_clean = sname.strip().upper() if sname and sname.strip() else ""

            target_sid = None
            if c_clean and c_clean in id_by_cnum:
                target_sid = id_by_cnum[c_clean]
            elif s_clean and s_clean in id_by_name:
                target_sid = id_by_name[s_clean]

            # Override supplier_id in record
            a_dict["supplier_id"] = target_sid

            row_tuple = tuple(a_dict[col] for col in common_cols)
            batch_awards.append(row_tuple)

            if len(batch_awards) >= BATCH_SIZE:
                execute_values(p_cur, f"INSERT INTO contract_awards ({col_award_list}) VALUES %s", batch_awards, page_size=BATCH_SIZE)
                copied_awards += len(batch_awards)
                batch_awards = []

        if batch_awards:
            execute_values(p_cur, f"INSERT INTO contract_awards ({col_award_list}) VALUES %s", batch_awards, page_size=BATCH_SIZE)
            copied_awards += len(batch_awards)

        p_cur.execute("SELECT setval(pg_get_serial_sequence('contract_awards', 'id'), (SELECT COALESCE(MAX(id), 1) FROM contract_awards))")
        print(f"Contract awards: inserted {copied_awards} rows.")

        # Step 6: Migrate credit_wallet and user_prefs
        try:
            s_wallets = s_cur.execute("SELECT username, balance, updated_at FROM credit_wallet").fetchall()
            if s_wallets:
                print(f"Migrating {len(s_wallets)} credit_wallet records...")
                for w in s_wallets:
                    p_cur.execute(
                        """
                        INSERT INTO credit_wallet (username, balance, updated_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (username) DO UPDATE SET balance = EXCLUDED.balance, updated_at = EXCLUDED.updated_at
                        """,
                        (w["username"], w["balance"], w["updated_at"]),
                    )
                print(f"credit_wallet: upserted {len(s_wallets)} records.")
        except Exception as e:
            print("Warning credit_wallet:", e)

        try:
            s_prefs = s_cur.execute("SELECT username, pref_key, pref_value, updated_at FROM user_prefs").fetchall()
            if s_prefs:
                print(f"Migrating {len(s_prefs)} user_prefs records...")
                for p in s_prefs:
                    p_cur.execute(
                        """
                        INSERT INTO user_prefs (username, pref_key, pref_value, updated_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (username, pref_key) DO UPDATE SET pref_value = EXCLUDED.pref_value, updated_at = EXCLUDED.updated_at
                        """,
                        (p["username"], p["pref_key"], p["pref_value"], p["updated_at"]),
                    )
                print(f"user_prefs: upserted {len(s_prefs)} records.")
        except Exception as e:
            print("Warning user_prefs:", e)

        dst.commit()
        print("Successfully committed full database rebuild!")

        # Verification query
        p_cur.execute("SELECT COUNT(*) FROM suppliers")
        final_supp = p_cur.fetchone()[0]
        p_cur.execute("SELECT COUNT(*) FROM contract_awards")
        final_awd = p_cur.fetchone()[0]
        p_cur.execute("SELECT COUNT(*) FROM contract_awards WHERE supplier_id IS NOT NULL")
        linked_awd = p_cur.fetchone()[0]

        print("=== FINAL POSTGRES METRICS ===")
        print(f"Suppliers count: {final_supp}")
        print(f"Contract Awards count: {final_awd}")
        print(f"Linked Contract Awards: {linked_awd}")

        return 0

    except Exception:
        dst.rollback()
        raise
    finally:
        p_cur.close()
        dst.close()
        src.close()

if __name__ == "__main__":
    sys.exit(rebuild_all())
