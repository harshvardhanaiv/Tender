"""Scratch: where does the card (global dedupe, grouped by norm key) differ from the profile (buyer-filtered dedupe)? LOCAL db."""
import sys
sys.path.insert(0, "/app")
from etenders_scraper.awards import DEDUPED_AWARDS_CTE_SQL, deduped_awards_cte_sql, SINGLE_AWARD_STATS_CEILING_GBP as CAP
from tender_app.blueprints.buyers_bp import _norm_auth_sql
from tender_app.db import get_db_connection

NAME = sys.argv[1] if len(sys.argv) > 1 else "Transport For London"
conn = get_db_connection()
cur = conn.cursor()
norm = _norm_auth_sql("authority_name")
flt = f"{norm} = {_norm_auth_sql('%s')}"
glob = f"""WITH {DEDUPED_AWARDS_CTE_SQL}
  SELECT id, authority_name, contract_value, shared_n, is_framework FROM deduped_awards WHERE {norm} = {_norm_auth_sql('%s')}"""
prof = f"""WITH {deduped_awards_cte_sql(flt)}
  SELECT id, authority_name, contract_value, shared_n, is_framework FROM deduped_awards WHERE {norm} = {_norm_auth_sql('%s')}"""
cur.execute(glob, (NAME,))
g = {r[0]: r for r in cur.fetchall()}
cur.execute(prof, (NAME, NAME, NAME))
p = {r[0]: r for r in cur.fetchall()}
print("rows global", len(g), "profile", len(p))
only_g = [g[i] for i in g if i not in p]
only_p = [p[i] for i in p if i not in g]
print("only in global:", len(only_g), "only in profile:", len(only_p))
for r in (only_g + only_p)[:10]:
    print("  ", r)
diff = [(i, g[i], p[i]) for i in g if i in p and (g[i][2] != p[i][2] or g[i][3] != p[i][3])]
print("same id, different value/shared_n:", len(diff))
for d in diff[:10]:
    print("  ", d)
tot = lambda m: sum(float(r[2] or 0) for r in m.values() if not r[4] and float(r[2] or 0) <= CAP)
print("spend global", tot(g), "profile", tot(p))
