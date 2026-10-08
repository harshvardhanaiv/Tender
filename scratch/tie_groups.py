"""Scratch: groups that tie on (supplier, buyer, date, value) but differ in another way. LOCAL db."""
import sys
sys.path.insert(0, "/app")
from tender_app.db import get_db_connection

conn = get_db_connection()
cur = conn.cursor()
cur.execute("""
WITH k AS (
  SELECT *, COALESCE(NULLIF(supplier_id,0)::text, NULLIF(company_number,''), supplier_name) AS sk,
         UPPER(TRIM(REGEXP_REPLACE(authority_name, '\\s+', ' ', 'g'))) AS ak
  FROM contract_awards WHERE date_signed IS NOT NULL),
g AS (
  SELECT sk, ak, date_signed, contract_value, COUNT(*) n, COUNT(DISTINCT COALESCE(is_framework,0)) fw_kinds,
         COUNT(DISTINCT COALESCE(notice_url,'')) urls, COUNT(DISTINCT source_portal) portals
  FROM k GROUP BY 1,2,3,4 HAVING COUNT(*) > 1)
SELECT COUNT(*) AS tie_groups,
       SUM(CASE WHEN fw_kinds > 1 THEN 1 ELSE 0 END) AS differ_in_framework_flag,
       SUM(CASE WHEN urls > 1 THEN 1 ELSE 0 END) AS differ_in_notice_url,
       SUM(CASE WHEN portals > 1 THEN 1 ELSE 0 END) AS differ_in_portal,
       SUM(CASE WHEN fw_kinds > 1 THEN contract_value ELSE 0 END) AS value_in_flag_conflicts
FROM g
""")
print(cur.fetchone())
cur.execute("""
WITH k AS (
  SELECT *, COALESCE(NULLIF(supplier_id,0)::text, NULLIF(company_number,''), supplier_name) AS sk,
         UPPER(TRIM(REGEXP_REPLACE(authority_name, '\\s+', ' ', 'g'))) AS ak
  FROM contract_awards WHERE date_signed IS NOT NULL AND authority_name ILIKE 'ministry of defence')
SELECT supplier_name, date_signed, contract_value, array_agg(id ORDER BY id) ids, array_agg(COALESCE(is_framework,0) ORDER BY id) fw,
       array_agg(source_portal ORDER BY id) portals
FROM k GROUP BY sk, ak, supplier_name, date_signed, contract_value
HAVING COUNT(*) > 1 AND COUNT(DISTINCT COALESCE(is_framework,0)) > 1 AND contract_value > 0
ORDER BY contract_value DESC NULLS LAST LIMIT 8
""")
for r in cur.fetchall():
    print(r)
