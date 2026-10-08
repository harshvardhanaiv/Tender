"""Runner script to execute all test files in tests/"""
import os
import sys
import subprocess
import glob

def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    tests_dir = os.path.join(root, "tests")
    
    py_files = sorted(glob.glob(os.path.join(tests_dir, "test_*.py")))
    js_files = sorted(glob.glob(os.path.join(tests_dir, "*_test.js")))
    
    passed = 0
    failed = 0
    failed_names = []

    print(f"Running {len(py_files)} Python test suites...", flush=True)
    for f in py_files:
        name = os.path.basename(f)
        cmd = ["docker", "compose", "exec", "-T", "app", "python", f"tests/{name}"]
        res = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        if res.returncode != 0 and any(err in (res.stderr + res.stdout) for err in ("service \"app\" is not running", "is not running", "No such container")):
            env = os.environ.copy()
            env.setdefault("DB_HOST", "127.0.0.1")
            env.setdefault("DB_PORT", "5440")
            env.setdefault("DB_NAME", "postgres")
            env.setdefault("DB_USER", "postgres")
            env.setdefault("DB_PASSWORD", env.get("LOCAL_DB_PASSWORD", "postgres"))
            cmd = [sys.executable, f]
            res = subprocess.run(cmd, cwd=root, capture_output=True, text=True, env=env)

        if res.returncode == 0:
            passed += 1
            last_line = [l for l in res.stdout.strip().splitlines() if l.strip()][-1] if res.stdout.strip() else "PASSED"
            print(f"PASS  {name:<44} {last_line}", flush=True)
        else:
            failed += 1
            failed_names.append(name)
            print(f"FAIL  {name:<44}", flush=True)
            print(res.stdout)
            print(res.stderr)

    print(f"\nRunning {len(js_files)} JS test suites with Node...")
    for f in js_files:
        name = os.path.basename(f)
        cmd = ["node", f"tests/{name}"]
        res = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        if res.returncode == 0:
            passed += 1
            last_line = [l for l in res.stdout.strip().splitlines() if l.strip()][-1] if res.stdout.strip() else "PASSED"
            print(f"PASS  {name:<44} {last_line}")
        else:
            failed += 1
            failed_names.append(name)
            print(f"FAIL  {name:<44}")
            print(res.stdout)
            print(res.stderr)

    print(f"\nSummary: {passed} passed, {failed} failed")
    if failed > 0:
        print(f"Failed test suites: {', '.join(failed_names)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
