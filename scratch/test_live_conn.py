"""Test read-only connection to live production DB."""
import psycopg2
import os

host = "62.171.162.135"
port = 5440
dbname = "postgres"
user = "postgres"
password = os.environ.get("LIVE_DB_PASSWORD", "root")

try:
    conn = psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        connect_timeout=5
    )
    cur = conn.cursor()
    cur.execute("SELECT version();")
    ver = cur.fetchone()[0]
    print("SUCCESS: Connected to live database!")
    print("PostgreSQL Version:", ver)

    # Check row counts of suppliers, contract_awards, planning_applications
    cur.execute("SELECT count(*) FROM suppliers;")
    sup_count = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM contract_awards;")
    awards_count = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM planning_applications;")
    planning_count = cur.fetchone()[0]

    print(f"Live row counts: suppliers={sup_count:,}, contract_awards={awards_count:,}, planning_applications={planning_count:,}")
    conn.close()
except Exception as e:
    print("FAILED to connect to live DB:", type(e).__name__, e)
