import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(".").resolve()))

from etenders_scraper.sources.progressive_search import start_progressive_search, job_snapshot, get_job

print("=== Running progressive search locally for PCS 'construction' ===")
job = start_progressive_search(keyword="construction", scope="pcs")
t0 = time.time()
while True:
    time.sleep(1)
    rows, meta, status = job_snapshot(job)
    print(f"[{time.time()-t0:.1f}s] status={status}, rows={len(rows)}, progress={meta.get('source_progress')}")
    if status in ("complete", "error") or time.time() - t0 > 120:
        break

print(f"\nFinal status: {status}")
print(f"Total rows: {len(rows)}")
for r in rows:
    print(f"  Tender: {r.get('title')[:60]} | auth: {r.get('contracting_authority')} | url: {r.get('detail_url')}")
