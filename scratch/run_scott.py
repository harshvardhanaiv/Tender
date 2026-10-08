import os
import psycopg2
import psycopg2.extras

conn = psycopg2.connect(
    host=os.environ["LIVE_DB_HOST"],
    port=os.environ["LIVE_DB_PORT"],
    dbname=os.environ["LIVE_DB_NAME"],
    user=os.environ["LIVE_DB_USER"],
    password=os.environ["LIVE_DB_PASSWORD"],
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

print("=== Large awards (> £1m) for Scott Aerospace ===")
cur.execute("""
    SELECT id, supplier_id, authority_name, supplier_name, tender_title, notice_url, 
           contract_value, date_signed, is_framework, source_portal, cpv_code, cpv_description
    FROM contract_awards
    WHERE supplier_name ILIKE '%Scott Aerospace%' AND contract_value > 1000000
    ORDER BY contract_value DESC NULLS LAST, id;
""")
rows = cur.fetchall()
print(f"Total large award rows: {len(rows)}")
for r in rows:
    print(f"ID: {r['id']} | SuppID: {r['supplier_id']} | Value: £{r['contract_value']:,.2f} | Signed: {r['date_signed']} | Fw: {r['is_framework']}")
    print(f"  Supplier: {r['supplier_name']} | Authority: {r['authority_name']}")
    print(f"  Title: {r['tender_title']}")
    print(f"  URL: {r['notice_url']}")
    print("-" * 60)

cur.close()
conn.close()
