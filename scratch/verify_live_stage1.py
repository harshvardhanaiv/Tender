import os
import psycopg2

db_host = os.environ["LIVE_DB_HOST"]
db_port = os.environ["LIVE_DB_PORT"]
db_name = os.environ["LIVE_DB_NAME"]
db_user = os.environ["LIVE_DB_USER"]
db_password = os.environ["LIVE_DB_PASSWORD"]

conn = psycopg2.connect(
    host=db_host,
    port=db_port,
    dbname=db_name,
    user=db_user,
    password=db_password
)
cur = conn.cursor()

# 1. count of suppliers
cur.execute("SELECT COUNT(*) FROM suppliers;")
suppliers_count = cur.fetchone()[0]

# 2. count of suppliers matching junk pattern
cur.execute("""
    SELECT COUNT(*) FROM suppliers 
    WHERE name ~* '(please see|please refer|^as per|^various |^multiple providers|^all successful bidders)';
""")
matching_junk_count = cur.fetchone()[0]

# 3. count of contract_awards whose supplier_id is not in suppliers
cur.execute("""
    SELECT COUNT(*) FROM contract_awards 
    WHERE supplier_id IS NOT NULL 
      AND supplier_id NOT IN (SELECT id FROM suppliers);
""")
orphan_awards_count = cur.fetchone()[0]

# 4. contract_awards total
cur.execute("SELECT COUNT(*) FROM contract_awards;")
awards_total = cur.fetchone()[0]

print(f"METRIC_1_SUPPLIERS_COUNT: {suppliers_count}")
print(f"METRIC_2_MATCHING_JUNK_COUNT: {matching_junk_count}")
print(f"METRIC_3_ORPHAN_AWARDS_COUNT: {orphan_awards_count}")
print(f"METRIC_4_AWARDS_TOTAL: {awards_total}")

conn.close()
