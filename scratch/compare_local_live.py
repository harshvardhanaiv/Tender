"""Compare local vs live table row counts."""
import psycopg2

conn_loc = psycopg2.connect(host='127.0.0.1', port=5440, dbname='postgres', user='postgres', password='postgres')
conn_live = psycopg2.connect(host='62.171.162.135', port=5440, dbname='postgres', user='postgres', password='root')

cur_loc = conn_loc.cursor()
cur_live = conn_live.cursor()

tables = [
    'suppliers',
    'contract_awards',
    'planning_applications',
    'buyer_locations',
    'buyer_stats',
    'supplier_stats',
    'tenders_master',
]

print(f"{'Table':<25} {'Local Count':<15} {'Live Count':<15} {'Diff (Local - Live)':<20}")
print("-" * 75)
for t in tables:
    try:
        cur_loc.execute(f"SELECT count(*) FROM {t};")
        c_loc = cur_loc.fetchone()[0]
    except Exception as e:
        c_loc = f"ERR: {e}"
        conn_loc.rollback()

    try:
        cur_live.execute(f"SELECT count(*) FROM {t};")
        c_live = cur_live.fetchone()[0]
    except Exception as e:
        c_live = f"ERR: {e}"
        conn_live.rollback()

    if isinstance(c_loc, int) and isinstance(c_live, int):
        diff = c_loc - c_live
        diff_str = f"+{diff:,}" if diff > 0 else f"{diff:,}"
        print(f"{t:<25} {c_loc:<15,} {c_live:<15,} {diff_str:<20}")
    else:
        print(f"{t:<25} {str(c_loc):<15} {str(c_live):<15}")

conn_loc.close()
conn_live.close()
