import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scripts.supplier_sync_live_local import connect, load_suppliers, supplier_key
import scripts.supplier_dedup_cleanup as dedupe_mod
from etenders_scraper.awards import looks_like_description

local_pwd = os.environ.get("LOCAL_DB_PASSWORD", "postgres")
live_host = os.environ["LIVE_DB_HOST"]
live_port = int(os.environ["LIVE_DB_PORT"])
live_db = os.environ["LIVE_DB_NAME"]
live_user = os.environ["LIVE_DB_USER"]
live_pwd = os.environ["LIVE_DB_PASSWORD"]

conn_local = connect("127.0.0.1", 5440, "postgres", "postgres", local_pwd)
conn_live = connect(live_host, live_port, live_db, live_user, live_pwd)

logical_local = dedupe_mod.logical_post_dedupe_rows(load_suppliers(conn_local))
logical_live = dedupe_mod.logical_post_dedupe_rows(load_suppliers(conn_live))

keys_live = {supplier_key(r[1], r[2]) for r in logical_live}
keys_local = {supplier_key(r[1], r[2]) for r in logical_local}

missing_in_live = [r for r in logical_local if supplier_key(r[1], r[2]) not in keys_live]
missing_in_local = [r for r in logical_live if supplier_key(r[1], r[2]) not in keys_local]

print(f"Missing in live (to insert into live): {len(missing_in_live)}")
print(f"Missing in local (to insert into local): {len(missing_in_local)}")

hits_to_live = []
for r in missing_in_live:
    name = r[2]
    if looks_like_description(name):
        hits_to_live.append((r[0], r[1], name))

hits_to_local = []
for r in missing_in_local:
    name = r[2]
    if looks_like_description(name):
        hits_to_local.append((r[0], r[1], name))

print(f"\nHits in missing_in_live ({len(hits_to_live)}):")
for sid, cnum, name in hits_to_live:
    print(f"  [Local ID {sid}] [CH {cnum}] {repr(name)}")

print(f"\nHits in missing_in_local ({len(hits_to_local)}):")
for sid, cnum, name in hits_to_local:
    print(f"  [Live ID {sid}] [CH {cnum}] {repr(name)}")

conn_local.close()
conn_live.close()
