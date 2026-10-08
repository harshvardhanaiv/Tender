"""Scratch: prove the new test fails on the OLD ordering, by editing the SQL text in memory (no file edit)."""
import sys
sys.path.insert(0, "/app")
from etenders_scraper.awards import DEDUPED_AWARDS_CTE_SQL, deduped_awards_cte_sql
from tender_app.db import get_db_connection

BUYER = "ZZ Tie Old"
conn = get_db_connection()
cur = conn.cursor()
try:
    ids = []
    for n in range(8):
        cur.execute("INSERT INTO contract_awards (authority_name, supplier_name, tender_title, cpv_code, date_signed, contract_value, is_framework, notice_url, source_portal, currency) "
                    "VALUES (%s,'zz_tie_supplier','zz tie','72000000','2026-03-01',1000000,0,%s,'Contracts Finder','GBP') RETURNING id", (BUYER, f"https://zz.invalid/tie/{n}"))
        ids.append(cur.fetchone()[0])
    for i in ids:   # rewrite oldest-first: physical order is now the same as id order again, then reverse it
        cur.execute("UPDATE contract_awards SET tender_title = 'again' WHERE id = %s", (i,))
    for i in reversed(ids):
        cur.execute("UPDATE contract_awards SET tender_title = 'again2' WHERE id = %s", (i,))
    conn.commit()
    old_all = DEDUPED_AWARDS_CTE_SQL.replace("a.contract_value DESC NULLS LAST, a.id", "a.contract_value DESC NULLS LAST")
    assert old_all != DEDUPED_AWARDS_CTE_SQL
    cur.execute(f"WITH {old_all} SELECT id FROM deduped_awards WHERE authority_name = %s", (BUYER,))
    print("OLD ordering keeps id", [r[0] for r in cur.fetchall()], "- lowest is", ids[0])
    cur.execute(f"WITH {DEDUPED_AWARDS_CTE_SQL} SELECT id FROM deduped_awards WHERE authority_name = %s", (BUYER,))
    print("NEW ordering keeps id", [r[0] for r in cur.fetchall()])
finally:
    conn.rollback()
    cur.execute("DELETE FROM contract_awards WHERE authority_name = %s", (BUYER,))
    conn.commit()
    conn.close()
