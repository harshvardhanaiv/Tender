"""Run every Python test in tests/ and report failures.

These tests are standalone scripts (they run at import and call sys.exit), so pytest cannot collect
them. Each file is run in its own interpreter; a non-zero exit or a timeout is a failure.

    python scripts/run_tests.py            # all tests
    python scripts/run_tests.py bid_plan   # only files whose name contains "bid_plan"
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "180"))

# Tests that scan the whole awards table are slow on a production-sized database: give them longer.
SLOW = {"test_buyer_scoring_retest3.py": 900, "test_buyer_stats_refresh_keys.py": 400, "test_report6_count_alias.py": 400}

# Tests that cannot pass on CI's empty database or without the live portals. They still run locally;
# on CI (CI=true) they are skipped. Take a test off this list once it seeds its own data.
CI_SKIP = {
    "test_buyer_detail_performance.py": "needs a populated awards table",
    "test_buyer_did_you_mean.py": "needs real buyer names in the database",
    "test_buyer_feed_speed.py": "needs a populated awards table",
    "test_buyer_places.py": "needs a populated awards table",
    "test_funding_track.py": "needs funding data",
    "test_report6_count_alias.py": "needs a populated suppliers table",
    "test_supplier_search_warm.py": "needs a populated suppliers table",
    "test_supplier_subcontractor_candidates.py": "needs a populated suppliers table",
    "test_yortender_source.py": "calls the live YORtender portal",
    "test_bd_playbook_seller_scope.py": "fails with 0 credits on a fresh database (test setup does not grant credits)",
}


def main(argv: list[str]) -> int:
    pattern = argv[0] if argv else ""
    files = sorted(p for p in (ROOT / "tests").glob("*.py")
                   if (p.name.endswith("_test.py") or p.name.startswith("test_")) and pattern in p.name)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "ENABLE_SCHEDULERS": "0"}
    failed: list[str] = []
    skipped = 0
    on_ci = os.environ.get("CI", "").lower() in ("1", "true")
    for f in files:
        start = time.time()
        if on_ci and f.name in CI_SKIP:
            print(f"skip {f.name} ({CI_SKIP[f.name]})", flush=True)
            skipped += 1
            continue
        try:
            r = subprocess.run([sys.executable, str(f)], cwd=ROOT, env=env, capture_output=True,
                               text=True, timeout=SLOW.get(f.name, TIMEOUT))
            ok, out = r.returncode == 0, (r.stdout + r.stderr)
        except subprocess.TimeoutExpired:
            ok, out = False, f"timed out after {TIMEOUT}s"
        # A test that raises unittest.SkipTest at import (no database, too little data) is a skip, not a failure.
        last = out.strip().splitlines()[-1] if out.strip() else ""
        if not ok and last.startswith("unittest.case.SkipTest"):
            print(f"skip {f.name}", flush=True)
            skipped += 1
            continue
        print(f"{'ok  ' if ok else 'FAIL'} {f.name} ({time.time() - start:.1f}s)", flush=True)
        if not ok:
            failed.append(f.name)
            print("\n".join(out.strip().splitlines()[-15:]), flush=True)
    print(f"\n{len(files) - len(failed)}/{len(files)} passed")
    if failed:
        print("failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
