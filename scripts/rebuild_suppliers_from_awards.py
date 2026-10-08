#!/usr/bin/env python3
"""Rebuild the suppliers table in Postgres by combining existing rich metadata
and all distinct suppliers extracted from contract_awards.
"""
import sys
from pathlib import Path
from psycopg2.extras import execute_values

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tender_app.db import get_db_connection

def is_valid_cnum(cnum: str | None) -> bool:
    if not cnum:
        return False
    c = cnum.strip().upper()
    if not c or c in ("NOT AVAILABLE", "N/A", "NONE", "UNKNOWN", "00000000", "PROCUREM") or c.startswith("NO_REG") or c.startswith("AUTO_"):
        return False
    return True

def rebuild_suppliers():
    pg_conn = get_db_connection()
    pg_cur = pg_conn.cursor()

    print("Fetching existing suppliers for rich metadata (email, website, address, etc.)...")
    pg_cur.execute("SELECT id, company_number, name, address, email, phone, website, region, sme_status, vcse_status FROM suppliers")
    existing_rows = pg_cur.fetchall()
    
    # Store existing rich supplier info
    rich_by_cnum = {}
    rich_by_name = {}
    for r in existing_rows:
        sid, cnum, name, addr, email, phone, web, reg, sme, vcse = r
        info = {
            "address": addr,
            "email": email,
            "phone": phone,
            "website": web,
            "region": reg or "UK",
            "sme_status": sme or "Unknown",
            "vcse_status": vcse or "Unknown"
        }
        if is_valid_cnum(cnum):
            rich_by_cnum[cnum.strip().upper()] = (name, info)
        if name and name.strip():
            rich_by_name[name.strip().upper()] = (cnum, info)

    print("Fetching all contract awards to extract distinct suppliers...")
    pg_cur.execute("SELECT id, company_number, supplier_name FROM contract_awards")
    awards = pg_cur.fetchall()
    print(f"Total contract awards: {len(awards)}")

    # Extract distinct supplier entities
    suppliers_map = {}
    cnum_seen = set()
    auto_counter = 0

    for aid, cnum, sname in awards:
        c_clean = cnum.strip().upper() if is_valid_cnum(cnum) else ""
        s_clean = sname.strip() if sname and sname.strip() else ""
        
        if not c_clean and not s_clean:
            continue

        if c_clean:
            key = f"CNUM:{c_clean}"
        else:
            key = f"NAME:{s_clean.upper()}"

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

    # Also include any existing suppliers that didn't appear in contract_awards
    for r in existing_rows:
        sid, cnum, name, addr, email, phone, web, reg, sme, vcse = r
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
                "address": addr,
                "email": email,
                "phone": phone,
                "website": web,
                "region": reg or "UK",
                "sme_status": sme or "Unknown",
                "vcse_status": vcse or "Unknown"
            }

    total_distinct = len(suppliers_map)
    print(f"Extracted total distinct supplier records: {total_distinct}")

    # Step 2: Clear old suppliers table and insert new suppliers
    print("Clearing old suppliers table in Postgres...")
    pg_cur.execute("TRUNCATE suppliers RESTART IDENTITY CASCADE")

    cols = ["company_number", "name", "address", "email", "phone", "website", "region", "sme_status", "vcse_status"]
    col_str = ", ".join(cols)

    batch_values = []
    for key, data in suppliers_map.items():
        batch_values.append((
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

    print(f"Inserting {len(batch_values)} suppliers into Postgres...")
    insert_query = f"INSERT INTO suppliers ({col_str}) VALUES %s ON CONFLICT (company_number) DO NOTHING"
    execute_values(pg_cur, insert_query, batch_values, page_size=2000)

    # Step 3: Map supplier IDs back to contract_awards
    print("Mapping new supplier IDs back to contract_awards...")
    pg_cur.execute("SELECT id, company_number, name FROM suppliers")
    new_suppliers = pg_cur.fetchall()

    id_by_cnum = {}
    id_by_name = {}
    for sid, cnum, name in new_suppliers:
        if cnum and cnum.strip():
            id_by_cnum[cnum.strip().upper()] = sid
        if name and name.strip():
            id_by_name[name.strip().upper()] = sid

    # Update contract_awards supplier_id in batch
    pg_cur.execute("SELECT id, company_number, supplier_name FROM contract_awards")
    all_awards = pg_cur.fetchall()

    award_updates = []
    for aid, cnum, sname in all_awards:
        c_clean = cnum.strip().upper() if is_valid_cnum(cnum) else ""
        s_clean = sname.strip().upper() if sname and sname.strip() else ""

        target_sid = None
        if c_clean and c_clean in id_by_cnum:
            target_sid = id_by_cnum[c_clean]
        elif s_clean and s_clean in id_by_name:
            target_sid = id_by_name[s_clean]

        if target_sid:
            award_updates.append((target_sid, aid))

    print(f"Updating supplier_id on {len(award_updates)} contract_awards...")
    execute_values(pg_cur, "UPDATE contract_awards SET supplier_id = data.sid FROM (VALUES %s) AS data(sid, aid) WHERE contract_awards.id = data.aid", award_updates, page_size=5000)

    pg_conn.commit()
    print("Commit successful!")

    # Verify counts
    pg_cur.execute("SELECT COUNT(*) FROM suppliers")
    final_supplier_count = pg_cur.fetchone()[0]
    print(f"Final Postgres suppliers count: {final_supplier_count}")

    pg_cur.execute("SELECT COUNT(*) FROM contract_awards WHERE supplier_id IS NOT NULL")
    linked_awards = pg_cur.fetchone()[0]
    print(f"Contract awards linked to supplier_id: {linked_awards}")

    pg_cur.close()
    pg_conn.close()

if __name__ == "__main__":
    rebuild_suppliers()
