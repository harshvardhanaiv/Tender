import os
import sys
import psycopg2
from pathlib import Path

host = os.environ.get("LIVE_DB_HOST")
port = os.environ.get("LIVE_DB_PORT", "5440")
dbname = os.environ.get("LIVE_DB_NAME", "postgres")
user = os.environ.get("LIVE_DB_USER", "postgres")
password = os.environ.get("LIVE_DB_PASSWORD")

sys.path.insert(0, str(Path(".").resolve()))
from etenders_scraper.awards import deduped_awards_cte_sql

conn = psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password)
cur = conn.cursor()

buyers_to_check = [
    "Ministry of Defence",
    "Home Office",
    "Telford and Wrekin Council",
    "Lancashire County Council",
    "Transport For London",
    "Department For International Development",
    "Leicester City Council"
]

print("=== CHECK 1: Target Buyers Comparison (buyer_stats vs live deduped CTE) ===")
print(f"{'Buyer':<42} | {'Card (stats) Contracts':<22} | {'Card Spend':<16} | {'Profile Contracts':<18} | {'Profile Spend':<16} | {'Match?':<6}")
print("-" * 130)

for b in buyers_to_check:
    cur.execute("SELECT total_contracts, total_spend FROM buyer_stats WHERE authority_name ILIKE %s", (b,))
    row = cur.fetchone()
    if not row:
        print(f"{b:<42} | NOT FOUND in buyer_stats")
        continue
    stats_contracts, stats_spend = row[0], float(row[1] or 0)

    # In deduped_awards_cte_sql: authority_filter is repeated twice (pre and post)
    cte = deduped_awards_cte_sql("authority_name ILIKE %s")
    sql = f"""WITH {cte}
              SELECT COUNT(*), COALESCE(SUM(contract_value), 0)
              FROM deduped_awards
              WHERE authority_name ILIKE %s"""
    cur.execute(sql, (b, b, b))
    live_contracts, live_spend = cur.fetchone()
    live_spend = float(live_spend or 0)

    diff_contracts = stats_contracts - live_contracts
    diff_spend = stats_spend - live_spend
    match = (diff_contracts == 0 and abs(diff_spend) < 0.01)

    print(f"{b:<42} | {stats_contracts:<22} | £{stats_spend:>14,.2f} | {live_contracts:<18} | £{live_spend:>14,.2f} | {'YES' if match else 'NO'}")

print("\n=== CHECK 2: Supplier 'Balfour Beatty Civil Engineering Ltd' ===")
cur.execute("""
    SELECT s.name, ss.total_awards, ss.total_value
    FROM supplier_stats ss
    JOIN suppliers s ON s.id = ss.supplier_id
    WHERE s.name ILIKE 'Balfour Beatty Civil Engineering Ltd'
""")
sup_stats = cur.fetchone()
if sup_stats:
    s_name, s_awards, s_val = sup_stats[0], sup_stats[1], float(sup_stats[2] or 0)
    # Exact de-duplicated figure computed from full CTE
    cte = deduped_awards_cte_sql()
    cur.execute(f"""
        WITH {cte}
        SELECT COUNT(*), COALESCE(SUM(contract_value), 0)
        FROM deduped_awards da
        JOIN suppliers s ON s.id = da.supplier_id
        WHERE s.name ILIKE 'Balfour Beatty Civil Engineering Ltd'
    """)
    live_sup = cur.fetchone()
    print(f"supplier_stats: awards={s_awards}, total_value=£{s_val:,.2f}")
    if live_sup:
        print(f"live deduped:   awards={live_sup[0]}, total_value=£{float(live_sup[1]):,.2f}")
        match_sup = (s_awards == live_sup[0] and abs(s_val - float(live_sup[1])) < 0.01)
        print(f"Match: {'YES' if match_sup else 'NO'}")
else:
    print("Supplier 'Balfour Beatty Civil Engineering Ltd' not found in supplier_stats")

print("\n=== CHECK 3: Arts Council Buyers buyer_type in buyer_stats ===")
cur.execute("""
    SELECT authority_name, buyer_type
    FROM buyer_stats
    WHERE authority_name ILIKE '%arts council%'
    ORDER BY authority_name
""")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

print("\n=== CHECK 4: Top 40 Buyers by Spend Comparison ===")
cur.execute("""
    SELECT authority_name, total_contracts, total_spend
    FROM buyer_stats
    ORDER BY total_spend DESC NULLS LAST
    LIMIT 40
""")
top_40 = cur.fetchall()
mismatch_count = 0
for b_name, b_contracts, b_spend in top_40:
    b_spend = float(b_spend or 0)
    cte = deduped_awards_cte_sql("authority_name = %s")
    sql = f"""WITH {cte}
              SELECT COUNT(*), COALESCE(SUM(contract_value), 0)
              FROM deduped_awards
              WHERE authority_name = %s"""
    cur.execute(sql, (b_name, b_name, b_name))
    l_contracts, l_spend = cur.fetchone()
    l_spend = float(l_spend or 0)
    if b_contracts != l_contracts or abs(b_spend - l_spend) > 0.01:
        mismatch_count += 1
        print(f"MISMATCH: {b_name} | Stats: ({b_contracts}, £{b_spend:,.2f}) vs Live: ({l_contracts}, £{l_spend:,.2f}) | Diff: {b_contracts - l_contracts} contracts, £{b_spend - l_spend:,.2f}")

if mismatch_count == 0:
    print("ALL Top 40 buyers by spend match EXACTLY between buyer_stats and live CTE!")
else:
    print(f"Found {mismatch_count} mismatches among Top 40.")

conn.close()
