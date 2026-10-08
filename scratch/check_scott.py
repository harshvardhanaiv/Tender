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

print("=== 1. Summary of Scott Aerospace suppliers in suppliers table ===")
cur.execute("SELECT * FROM suppliers WHERE name ILIKE '%Scott Aerospace%';")
for r in cur.fetchall():
    print(r)

print("\n=== 2. supplier_stats for Scott Aerospace ===")
cur.execute("""
    SELECT s.id, s.name, ss.total_awards, ss.total_value, ROUND(ss.total_value/1e6, 2) AS value_m
    FROM suppliers s
    JOIN supplier_stats ss ON ss.supplier_id = s.id
    WHERE s.name ILIKE '%Scott Aerospace%';
""")
for r in cur.fetchall():
    print(r)

print("\n=== 3. Contract awards for Scott Aerospace (all rows) ===")
cur.execute("""
    SELECT id, supplier_id, authority_name, supplier_name, tender_title, notice_url, 
           contract_value, date_signed, is_framework, source_portal, cpv_code, cpv_description
    FROM contract_awards
    WHERE supplier_name ILIKE '%Scott Aerospace%'
    ORDER BY contract_value DESC NULLS LAST, id;
""")
rows = cur.fetchall()
print(f"Total award rows: {len(rows)}")
for r in rows:
    print(f"ID: {r['id']} | SuppID: {r['supplier_id']} | Value: {r['contract_value']} | Signed: {r['date_signed']} | Fw: {r['is_framework']} | Portal: {r['source_portal']}")
    print(f"  Supplier: {r['supplier_name']} | Authority: {r['authority_name']}")
    print(f"  Title: {r['tender_title']}")
    print(f"  URL: {r['notice_url']}")
    print(f"  CPV: {r['cpv_code']} - {r['cpv_description']}")
    print("-" * 60)

cur.close()
conn.close()
