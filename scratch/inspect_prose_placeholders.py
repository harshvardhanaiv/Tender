import psycopg2
import os

conn = psycopg2.connect(host='127.0.0.1', port=5440, dbname='postgres', user='postgres', password=os.getenv('LOCAL_DB_PASSWORD', 'postgres'))
cur = conn.cursor()

cur.execute("""
    SELECT id, name FROM suppliers
    WHERE name ~* '^(please see|please refer|as per|information withheld|awarded supplier details|confidential information|confidential / sensitive)'
       OR name ~* 'commercially confidential'
    ORDER BY name
""")
rows = cur.fetchall()
print(f"Total matching suppliers in DB: {len(rows)}")
for r in rows:
    print(f"  [ID {r[0]}] {r[1]}")
conn.close()
