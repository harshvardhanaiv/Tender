import os
import psycopg2

local_pwd = os.environ.get("LOCAL_DB_PASSWORD", "postgres")
live_host = os.environ["LIVE_DB_HOST"]
live_port = int(os.environ["LIVE_DB_PORT"])
live_db = os.environ["LIVE_DB_NAME"]
live_user = os.environ["LIVE_DB_USER"]
live_pwd = os.environ["LIVE_DB_PASSWORD"]

def check_db(name, conn):
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM suppliers;")
    sup_cnt = cur.fetchone()[0]
    
    cur.execute("SELECT count(*) FROM contract_awards;")
    awd_cnt = cur.fetchone()[0]
    
    cur.execute("SELECT count(*) FROM suppliers WHERE name ~* '(please see|please refer|^as per|^various |^multiple providers|^all successful bidders)';")
    junk_cnt = cur.fetchone()[0]
    
    cur.execute("SELECT count(*) FROM contract_awards WHERE supplier_id IS NOT NULL AND supplier_id NOT IN (SELECT id FROM suppliers);")
    orphan_cnt = cur.fetchone()[0]
    
    print(f"[{name}] suppliers: {sup_cnt:,}")
    print(f"[{name}] contract_awards: {awd_cnt:,}")
    print(f"[{name}] junk pattern suppliers: {junk_cnt}")
    print(f"[{name}] orphan awards: {orphan_cnt}")

try:
    conn_live = psycopg2.connect(host=live_host, port=live_port, dbname=live_db, user=live_user, password=live_pwd, connect_timeout=10)
    check_db("LIVE", conn_live)
    conn_live.close()
except Exception as e:
    print("LIVE check error:", e)

try:
    conn_local = psycopg2.connect(host="127.0.0.1", port=5440, dbname="postgres", user="postgres", password=local_pwd, connect_timeout=5)
    check_db("LOCAL", conn_local)
    conn_local.close()
except Exception as e:
    print("LOCAL check error:", e)
