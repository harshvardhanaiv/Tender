import sys
sys.path.insert(0, ".")
import psycopg2
import os

conn = psycopg2.connect(host='127.0.0.1', port=5440, dbname='postgres', user='postgres', password=os.getenv('LOCAL_DB_PASSWORD', 'postgres'))
cur = conn.cursor()

from tender_app.blueprints.suppliers_bp import SUPPLIER_NAME_NOT_PLACEHOLDER_SQL

# Let's count how many currently listable suppliers match the new rule
sql = f"""
    SELECT s.id, s.name, count(ca.id) 
    FROM suppliers s
    LEFT JOIN contract_awards ca ON ca.supplier_id = s.id
    WHERE {SUPPLIER_NAME_NOT_PLACEHOLDER_SQL}
      AND (
        s.name ~* '^(please see|please refer|as per|information withheld|awarded supplier details|confidential information|confidential / sensitive)'
        OR s.name ~* 'commercially confidential'
      )
    GROUP BY s.id, s.name
    ORDER BY s.name
"""

cur.execute(sql)
rows = cur.fetchall()
print(f"Currently listable suppliers matching new rule: {len(rows)}")
for r in rows:
    safe_name = r[1].encode('ascii', errors='replace').decode('ascii')
    print(f"  ID {r[0]} ({r[2]} awards): {safe_name}")

conn.close()
