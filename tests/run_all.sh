#!/usr/bin/env bash
# Runs every test in tests/ and prints one line per file, then a total. Exit status is 1 if any failed.
#
#   tests/run_all.sh            everything (the full regression pass: run it before finishing any change)
#   tests/run_all.sh growth     only the files whose name contains "growth"
#
# Python tests run as one-off containers on the real bind mount: no reloader, no published port and
# both schedulers off, so they cannot send mail or collide with the dev server. The database
# container must be up (docker compose up -d db). Node tests run on the host and need no packages.
cd "$(dirname "$0")/.." || exit 2
export MSYS_NO_PATHCONV=1
filter="${1:-}"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

pass=0
fail=0
failed_names=()

report() {  # name status log
  local last
  last="$(grep -E '^[0-9]+ (of [0-9]+ )?passed' "$3" | tail -1)"
  if [ "$2" -eq 0 ]; then
    pass=$((pass + 1))
    printf 'PASS  %-44s %s\n' "$1" "$last"
  else
    fail=$((fail + 1))
    failed_names+=("$1")
    printf 'FAIL  %-44s %s\n' "$1" "$last"
    sed 's/^/        /' "$3" | tail -40
  fi
}

for f in tests/test_*.py; do
  [ -e "$f" ] || continue
  name="$(basename "$f")"
  case "$name" in *"$filter"*) ;; *) continue ;; esac
  docker compose run --rm --no-deps -T -e ENABLE_EMAIL_SCHEDULER=0 -e ENABLE_SCHEDULERS=0 app python "tests/$name" >"$out/$name.log" 2>&1
  report "$name" $? "$out/$name.log"
done

for f in tests/*_test.js; do
  [ -e "$f" ] || continue
  name="$(basename "$f")"
  case "$name" in *"$filter"*) ;; *) continue ;; esac
  node "tests/$name" >"$out/$name.log" 2>&1
  report "$name" $? "$out/$name.log"
done

echo
echo "$pass passed, $fail failed"
if [ "$fail" -gt 0 ]; then
  echo "failed: ${failed_names[*]}"
  exit 1
fi
