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
from etenders_scraper.awards import deduped_awards_cte_sql, SINGLE_AWARD_STATS_CEILING_GBP
from tender_app.blueprints.buyers_bp import _norm_auth_sql

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

print("=== CHECK 1: Target Buyers Profile Comparison ===")
print(f"{'Buyer':<42} | {'Card (stats) Contracts':<22} | {'Card Spend (£m)':<16} | {'Profile Contracts':<18} | {'Profile Spend (£m)':<18} | {'Match?':<6}")
print("-" * 135)

for b in buyers_to_check:
    # From buyer_stats using normalised key
    cur.execute(f"SELECT authority_name, total_contracts, total_spend FROM buyer_stats WHERE authority_key = {_norm_auth_sql('%s')}", (b,))
    row = cur.fetchone()
    if not row:
        print(f"{b:<42} | NOT FOUND in buyer_stats")
        continue
    auth_name, stats_contracts, stats_spend = row[0], row[1], float(row[2] or 0)

    # Live profile query from buyers_bp.py
    norm_filter = f"{_norm_auth_sql('authority_name')} = {_norm_auth_sql('%s')}"
    cte = deduped_awards_cte_sql(norm_filter)
    sql = f"""WITH {cte}
              SELECT
                  COUNT(DISTINCT ca.id) as total_contracts,
                  COALESCE(SUM(CASE WHEN ca.is_framework = 1 OR ca.contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN 0 ELSE ca.contract_value END), 0) as total_spend
              FROM deduped_awards ca
              WHERE {_norm_auth_sql('ca.authority_name')} = {_norm_auth_sql('%s')}"""
    cur.execute(sql, (b, b, b))
    live_contracts, live_spend = cur.fetchone()
    live_spend = float(live_spend or 0)

    diff_contracts = stats_contracts - live_contracts
    diff_spend = stats_spend - live_spend
    match = (diff_contracts == 0 and abs(diff_spend) < 0.01)

    print(f"{b:<42} | {stats_contracts:<22} | £{stats_spend/1e6:>14,.2f}m | {live_contracts:<18} | £{live_spend/1e6:>14,.2f}m | {'YES' if match else 'NO'}")

print("\n=== CHECK 2: Supplier 'Balfour Beatty Civil Engineering Ltd' ===")
cur.execute("""
    SELECT s.id, s.name, ss.total_awards, ss.total_value
    FROM supplier_stats ss
    JOIN suppliers s ON s.id = ss.supplier_id
    WHERE s.name ILIKE 'Balfour Beatty Civil Engineering Ltd'
""")
sup_stats = cur.fetchone()
if sup_stats:
    s_id, s_name, s_awards, s_val = sup_stats[0], sup_stats[1], sup_stats[2], float(sup_stats[3] or 0)
    # The exact profile calculation from suppliers_bp.py:
    # First get the supplier's awards according to the profile page
    from tender_app.stats import _PORTALS, _VALID_COMPANY_NUMBER_SQL
    cte = deduped_awards_cte_sql()
    cur.execute(f"""
        WITH {cte},
        links AS (
            SELECT a.supplier_id AS sid, a.id AS aid, a.contract_value, a.is_framework
            FROM deduped_awards a
            WHERE a.supplier_id = %s AND a.source_portal IN ({_PORTALS})
            UNION ALL
            SELECT s.id, a.id, a.contract_value, a.is_framework
            FROM suppliers s
            JOIN deduped_awards a ON a.company_number = s.company_number
            WHERE s.id = %s AND {_VALID_COMPANY_NUMBER_SQL}
              AND a.supplier_id IS DISTINCT FROM s.id
              AND a.source_portal IN ({_PORTALS})
        )
        SELECT COUNT(aid) AS total_awards,
               COALESCE(SUM(CASE WHEN (is_framework IS NULL OR is_framework = 0) AND contract_value <= {SINGLE_AWARD_STATS_CEILING_GBP} THEN contract_value ELSE 0 END), 0) AS total_value
        FROM links
    """, (s_id, s_id))
    live_sup = cur.fetchone()
    print(f"supplier_stats: awards={s_awards}, total_value=£{s_val/1e6:,.2f}m (£{s_val:,.2f})")
    if live_sup:
        live_val = float(live_sup[1])
        print(f"live profile:   awards={live_sup[0]}, total_value=£{live_val/1e6:,.2f}m (£{live_val:,.2f})")
        match_sup = (s_awards == live_sup[0] and abs(s_val - live_val) < 0.01)
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
cur.execute(f"""
    SELECT authority_key, authority_name, total_contracts, total_spend
    FROM buyer_stats
    ORDER BY total_spend DESC NULLS LAST
    LIMIT 40
""")
top_40 = cur.fetchall()
mismatch_count = 0
for b_key, b_name, b_contracts, b_spend in top_40:
    b_spend = float(b_spend or 0)
    norm_filter = f"{_norm_auth_sql('authority_name')} = %s"
    cte = deduped_awards_cte_sql(norm_filter)
    sql = f"""WITH {cte}
              SELECT
                  COUNT(DISTINCT ca.id) as total_contracts,
                  COALESCE(SUM(CASE WHEN ca.is_framework = 1 OR ca.contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN 0 ELSE ca.contract_value END), 0) as total_spend
              FROM deduped_awards ca
              WHERE {_norm_auth_sql('ca.authority_name')} = %s"""
    cur.execute(sql, (b_key, b_key, b_key))
    l_contracts, l_spend = cur.fetchone()
    l_spend = float(l_spend or 0)
    if b_contracts != l_contracts or abs(b_spend - l_spend) > 0.01:
        mismatch_count += 1
        print(f"MISMATCH: {b_name} | Stats: ({b_contracts}, £{b_spend/1e6:,.2f}m) vs Profile: ({l_contracts}, £{l_spend/1e6:,.2f}m) | Diff: {b_contracts - l_contracts} contracts, £{(b_spend - l_spend)/1e6:,.2f}m")

if mismatch_count == 0:
    print("ALL Top 40 buyers by spend match EXACTLY between buyer_stats and live profile queries!")
else:
    print(f"Found {mismatch_count} mismatches among Top 40.")

conn.close()
