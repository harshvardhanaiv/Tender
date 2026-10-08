from etenders_scraper.sources.progressive_search import start_progressive_search, stop_all_search, job_snapshot
import time

j = start_progressive_search("Construction", "find_tender,contracts_finder")
print("Job:", j.job_id[:8])
for tick in [5, 10, 15, 20]:
    time.sleep(5)
    rows, meta, phase = job_snapshot(j)
    prog = meta.get("source_progress", {})
    tim = meta.get("source_timings_sec", {})
    print("T+" + str(tick) + "s: phase=" + phase + ", rows=" + str(len(rows)) + ", prog=" + str(prog) + ", tim=" + str(tim))
    if phase == "complete":
        break

stop_all_search(j.job_id)
time.sleep(1)
rows, meta, phase = job_snapshot(j)
print("After stop: rows=" + str(len(rows)) + ", phase=" + phase)
for r in rows[:5]:
    print(" - " + str(r.get("title", ""))[:80])
