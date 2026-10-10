#!/usr/bin/env bash
# Runs every tests/*.sh (except itself and lib.sh) concurrently, up to QS_TEST_JOBS at a time
# (default: 1 on Windows; at most 4 elsewhere). Each test's combined output is captured and printed
# in filename order once all tests finish, so output stays deterministic. Exit 0 all green, 2 otherwise.
cd "$(dirname "$0")/.." || exit 2

if [ "${QS_TEST_JOBS+x}" = x ]; then
  jobs="$QS_TEST_JOBS"
else
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) jobs=1;;
    *) jobs="$(command -v nproc >/dev/null 2>&1 && nproc || echo 4)"
       [[ "$jobs" =~ ^[1-9][0-9]{0,3}$ ]] || jobs=4
       [ "$jobs" -le 4 ] || jobs=4;;
  esac
fi
if ! [[ "$jobs" =~ ^[1-9][0-9]{0,3}$ ]] || [ "$jobs" -gt 1024 ]; then
  echo "tests: QS_TEST_JOBS must be an integer from 1 to 1024" >&2
  exit 2
fi
echo "tests: claude $(claude --version 2>/dev/null | head -1 || echo unavailable)"

files=()
for t in tests/*.sh; do
  case "$t" in tests/run.sh|tests/lib.sh) continue;; esac
  files+=("$t")
done

retain=0
if [ -n "${QS_TEST_LOG_DIR:-}" ]; then
  mkdir -p "$QS_TEST_LOG_DIR" || exit 2
  logs="$(cd "$QS_TEST_LOG_DIR" && pwd -P)" || exit 2
  workdir="$(mktemp -d "$logs/run.XXXXXXXXXX")" || exit 2
  retain=1
  printf '%s\n' "$jobs" > "$workdir/jobs"
  echo "tests: retained logs $workdir" >&2
else
  workdir="$(mktemp -d)" || exit 2
fi
trap 'if [ "$retain" = 0 ]; then rm -rf "$workdir"; fi' EXIT

running=0
for t in "${files[@]}"; do
  out="$workdir/$(basename "$t").out"
  rc="$workdir/$(basename "$t").rc"
  (
    bash "$t" >"$out" 2>&1
    echo $? >"$rc"
  ) &
  running=$((running + 1))
  if [ "$running" -ge "$jobs" ]; then wait -n; running=$((running - 1)); fi
done
wait

failed=()
for t in "${files[@]}"; do
  echo "== $t"
  cat "$workdir/$(basename "$t").out"
  [ "$(cat "$workdir/$(basename "$t").rc")" = 0 ] || failed+=("$t")
done

if [ ${#failed[@]} -gt 0 ]; then echo "tests: FAIL ${failed[*]}" >&2; exit 2; fi
echo "tests: PASS"
