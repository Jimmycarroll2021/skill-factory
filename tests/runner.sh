#!/usr/bin/env bash
# tests/runner.sh: verifies tests/run.sh runs test files concurrently with deterministic, filename-ordered
# output, unchanged pass/fail semantics and exit codes, and that QS_TEST_JOBS=1 matches the default.
source "$(dirname "$0")/lib.sh"

mkfixture() { # -> fresh <dir>/tests/ containing the run.sh + lib.sh under test
  local d; d="$(tmpdir)"
  mkdir -p "$d/tests"
  cp "$ROOT/tests/run.sh" "$d/tests/run.sh"
  cp "$ROOT/tests/lib.sh" "$d/tests/lib.sh"
  printf '%s\n' "$d"
}

write_test() { # write_test <dir> <filename> <exit> <line> [sleep-secs]
  cat > "$1/tests/$2" <<EOF
#!/usr/bin/env bash
${5:+sleep $5}
echo "$4"
exit $3
EOF
  chmod +x "$1/tests/$2"
}

# --- mixed pass/fail: exit code, order preserved, both outputs present, final line names the failure ---
for jobs_label in "default:" "QS_TEST_JOBS=1:1"; do
  label="${jobs_label%%:*}"; jobs="${jobs_label#*:}"
  d="$(mkfixture)"
  write_test "$d" aa.sh 0 "aa ok"
  write_test "$d" bb.sh 1 "bb FAIL"
  if [ -n "$jobs" ]; then OUT="$(cd "$d" && QS_TEST_JOBS="$jobs" bash tests/run.sh 2>&1)"; code=$?
  else OUT="$(cd "$d" && bash tests/run.sh 2>&1)"; code=$?; fi
  [ "$code" = 2 ] && ok "$label exit 2" || bad "$label exit 2 (got $code): $(printf '%s' "$OUT" | tail -n 3)"
  aa_line="$(printf '%s\n' "$OUT" | grep -n '^== tests/aa.sh$' | head -1 | cut -d: -f1)"
  bb_line="$(printf '%s\n' "$OUT" | grep -n '^== tests/bb.sh$' | head -1 | cut -d: -f1)"
  if [ -n "$aa_line" ] && [ -n "$bb_line" ] && [ "$aa_line" -lt "$bb_line" ]; then ok "$label aa.sh printed before bb.sh"
  else bad "$label aa.sh printed before bb.sh (aa=$aa_line bb=$bb_line)"; fi
  expect_contains "$label output contains aa.sh's output" "aa ok" "$OUT"
  expect_contains "$label output contains bb.sh's output" "bb FAIL" "$OUT"
  last="$(printf '%s\n' "$OUT" | tail -n 1)"
  expect_contains "$label final line names tests/bb.sh" "tests/bb.sh" "$last"
done

# --- all passing: exit 0, last line is tests: PASS ---
d="$(mkfixture)"
write_test "$d" aa.sh 0 "aa ok"
write_test "$d" cc.sh 0 "cc ok"
expect_exit "all passing: exit 0" 0 bash -c "cd '$d' && bash tests/run.sh"
expect_contains "all passing: last line is tests: PASS" "tests: PASS" "$(printf '%s\n' "$OUT" | tail -n 1)"

# --- an inherited CLAUDE_PROJECT_DIR (the Stop hook exports it when its gate runs this suite) must not reach
# tests that source lib.sh: fixtures would read the real repo's .claude/state instead of their own ---
d="$(mkfixture)"
cat > "$d/tests/env.sh" <<'EOF'
#!/usr/bin/env bash
source "$(dirname "$0")/lib.sh"
[ -z "${CLAUDE_PROJECT_DIR:-}" ] && ok "CLAUDE_PROJECT_DIR is unset" || bad "CLAUDE_PROJECT_DIR leaked: $CLAUDE_PROJECT_DIR"
finish
EOF
chmod +x "$d/tests/env.sh"
expect_exit "lib.sh clears an inherited CLAUDE_PROJECT_DIR" 0 bash -c "cd '$d' && CLAUDE_PROJECT_DIR='$ROOT' bash tests/run.sh"

# --- real concurrency: under QS_TEST_JOBS=2 two tests overlap in time. Each test records when it starts and
# ends; the proof is that the second starts before the first ends. No wall-clock thresholds, so machine load
# (the gate runs sixteen test files at once, one of which runs a nested full suite) cannot make this flaky ---
d="$(mkfixture)"
write_stamp_test() { # write_stamp_test <dir> <name>: records start/end nanoseconds in <dir>/<name>.start|.end
  printf '#!/usr/bin/env bash\ndate +%%s%%N > "%s/%s.start"; sleep 2; date +%%s%%N > "%s/%s.end"; echo "%s ok"; exit 0\n' \
    "$1" "$2" "$1" "$2" "$2" > "$1/tests/$2.sh"
  chmod +x "$1/tests/$2.sh"
}
write_stamp_test "$d" s1; write_stamp_test "$d" s2
(cd "$d" && QS_TEST_JOBS=2 bash tests/run.sh) >/dev/null 2>&1
s1s=$(cat "$d/s1.start" 2>/dev/null); s1e=$(cat "$d/s1.end" 2>/dev/null); s2s=$(cat "$d/s2.start" 2>/dev/null); s2e=$(cat "$d/s2.end" 2>/dev/null)
if [ -n "$s1e" ] && [ -n "$s2s" ] && [ -n "$s2e" ] && [ "$s2s" -lt "$s1e" ] && [ "$s1s" -lt "$s2e" ]; then
  ok "QS_TEST_JOBS=2 runs two tests concurrently (their intervals overlap)"
else
  bad "QS_TEST_JOBS=2 runs two tests concurrently (s1 $s1s-$s1e, s2 $s2s-$s2e)"
fi
rm -f "$d/s1.start" "$d/s1.end" "$d/s2.start" "$d/s2.end"
(cd "$d" && QS_TEST_JOBS=1 bash tests/run.sh) >/dev/null 2>&1
s1e=$(cat "$d/s1.end" 2>/dev/null); s2s=$(cat "$d/s2.start" 2>/dev/null)
if [ -n "$s1e" ] && [ -n "$s2s" ] && [ "$s2s" -ge "$s1e" ]; then
  ok "QS_TEST_JOBS=1 runs tests one at a time (s2 started after s1 ended)"
else
  bad "QS_TEST_JOBS=1 runs tests one at a time (s1 end $s1e, s2 start $s2s)"
fi

# --- invalid limits fail promptly, rather than launching an unbounded run ---
for invalid in 0 -1 nope 1.5 "" 1025 99999999999999999999999999999999999999; do
  expect_exit "invalid QS_TEST_JOBS '$invalid' exits 2" 2 env QS_TEST_JOBS="$invalid" bash -c "cd '$d' && bash tests/run.sh"
done

# --- requested logs survive successful and failed runs, with separate nested directories ---
logs="$(tmpdir)/logs with spaces"
expect_exit "retained logs: first run" 0 env QS_TEST_JOBS=1 QS_TEST_LOG_DIR="$logs" bash -c "cd '$d' && bash tests/run.sh"
expect_exit "retained logs: second run" 0 env QS_TEST_JOBS=1 QS_TEST_LOG_DIR="$logs" bash -c "cd '$d' && bash tests/run.sh"
count="$(find "$logs" -name s1.sh.rc -type f | wc -l | tr -d ' ')"
[ "$count" = 2 ] && ok "runs retain separate log directories" || bad "runs retain separate log directories (got $count)"
write_test "$d" fail.sh 1 "deliberate fixture failure"
expect_exit "retained logs: failed run exits 2" 2 env QS_TEST_JOBS=1 QS_TEST_LOG_DIR="$logs" bash -c "cd '$d' && bash tests/run.sh"
failure_rc="$(find "$logs" -name fail.sh.rc -type f -exec cat {} \;)"
[ "$failure_rc" = 1 ] && ok "failed script's exit code retained" || bad "failed script's exit code retained"

# --- defaults are bounded on both supported platform families ---
bin="$(tmpdir)/bin"; mkdir -p "$bin"
printf '#!/usr/bin/env bash\necho 64\n' > "$bin/nproc"; chmod +x "$bin/nproc"
for platform in MINGW64_NT Linux; do
  printf '#!/usr/bin/env bash\necho %s\n' "$platform" > "$bin/uname"; chmod +x "$bin/uname"
  defaults_logs="$(tmpdir)/defaults"
  defaults_fixture="$(mkfixture)"; write_test "$defaults_fixture" pass.sh 0 pass
  expect_exit "$platform default passes" 0 env -u QS_TEST_JOBS PATH="$bin:$PATH" QS_TEST_LOG_DIR="$defaults_logs" bash -c "cd '$defaults_fixture' && bash tests/run.sh"
  got_jobs="$(find "$defaults_logs" -name jobs -type f -exec cat {} \;)"
  want_jobs=4; [ "$platform" != MINGW64_NT ] || want_jobs=1
  [ "$got_jobs" = "$want_jobs" ] && ok "$platform default job limit $want_jobs" || bad "$platform default job limit (got $got_jobs)"
done

finish
