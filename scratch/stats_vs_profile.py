"""Scratch: compare buyer_stats (card) with the live-computed profile for the same buyers, on the LOCAL database."""
import os, sys, time
sys.path.insert(0, "/app")
from urllib.parse import quote
from server import create_app
from tender_app.db import get_db_connection

NAMES = ["Ministry of Defence", "NHS England", "Transport For London", "Birmingham City Council", "Manchester City Council", "Kent County Council"]
conn = get_db_connection()
cur = conn.cursor()
c = create_app().test_client()
with c.session_transaction() as s:
    s.update(logged_in=True, username="zz_scratch", email="zz_scratch@test.invalid", last_activity=time.time(), csrf_token="x")
for n in NAMES:
    cur.execute("SELECT authority_name, total_contracts, total_spend, avg_contract_value, refreshed_at FROM buyer_stats WHERE authority_name = %s", (n,))
    row = cur.fetchone()
    r = c.get("/api/buyers/" + quote(n))
    j = r.get_json() or {}
    s = j.get("stats") or {}
    print(n, "| stats:", row, "| profile:", s.get("total_contracts"), s.get("total_spend"), s.get("avg_contract_value"), r.status_code)
