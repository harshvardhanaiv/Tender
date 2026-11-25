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


def main(argv: list[str]) -> int:
    pattern = argv[0] if argv else ""
    files = sorted(p for p in (ROOT / "tests").glob("*.py")
                   if (p.name.endswith("_test.py") or p.name.startswith("test_")) and pattern in p.name)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "ENABLE_SCHEDULERS": "0"}
    failed: list[str] = []
    for f in files:
        start = time.time()
        try:
            r = subprocess.run([sys.executable, str(f)], cwd=ROOT, env=env, capture_output=True,
                               text=True, timeout=TIMEOUT)
            ok, out = r.returncode == 0, (r.stdout + r.stderr)
        except subprocess.TimeoutExpired:
            ok, out = False, f"timed out after {TIMEOUT}s"
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
