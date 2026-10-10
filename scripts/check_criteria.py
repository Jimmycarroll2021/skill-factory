#!/usr/bin/env python3
"""Run success_criteria from brief.json and write docs/ledgers/criteria.json.

Stdlib only. See docs/design/contracts.md "Criteria".

Exit 0 when no criterion failed, else 2. `judge` criteria are "deferred" by
a plain run; the reviewer's grade is recorded afterwards with
`--judge <index> PASS|FAIL --evidence "<quoted command output>"`, which
rewrites that entry, recounts, and exits 2 if any criterion is then failed.
"""
import runtime
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TEST_TIMEOUT_S = 600
# Resolve via PATH once: on Windows, CreateProcess checks System32 (which may hold a
# WSL-relay bash.exe stub) before honoring PATH order, so a bare "bash" can silently
# resolve to the wrong interpreter. shutil.which does a real PATH search first.
BASH = shutil.which("bash") or "bash"


def find_root():
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.replace("\r", "").strip()
    return Path(out) if out else None


def load_brief(root):
    brief_path = root / ".claude" / "state" / "brief.json"
    if not brief_path.is_file():
        print(f"check_criteria: brief.json not found at {brief_path}", file=sys.stderr)
        sys.exit(2)
    with brief_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def run_test(criterion, cwd):
    cmd = criterion["cmd"]
    expect = criterion.get("expect", 0)
    try:
        proc = subprocess.Popen(
            [BASH, "-c", cmd],
            cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
            start_new_session=os.name != "nt",
        )
        try:
            proc.communicate(timeout=TEST_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            from runner import terminate
            terminate(proc)
            proc.communicate(timeout=10)
            raise
    except subprocess.TimeoutExpired:
        return "fail", "timeout"
    except OSError as e:
        return "fail", f"could not run: {e}"
    if proc.returncode == expect:
        return "pass", f"exit {proc.returncode}"
    return "fail", f"exit {proc.returncode}, expected {expect}"


def run_file(criterion, base):
    path = base / criterion["path"]
    must_contain = criterion.get("must_contain")
    if not path.is_file():
        return "fail", f"missing file: {criterion['path']}"
    if must_contain:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return "fail", f"read error: {e}"
        if not re.search(must_contain, content):
            return "fail", f"pattern not found: {must_contain}"
    return "pass", f"exists: {criterion['path']}"


def run_grep(criterion, base):
    path = base / criterion["path"]
    pattern = criterion["pattern"]
    if not path.is_file():
        return "fail", f"missing file: {criterion['path']}"
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return "fail", f"read error: {e}"
    if re.search(pattern, content):
        return "pass", f"matched: {pattern}"
    return "fail", f"no match: {pattern}"


def run_judge(criterion):
    return "deferred", criterion.get("rubric", "")


def evaluate(criterion, base):
    kind = criterion.get("kind")
    if kind == "test":
        return run_test(criterion, base)
    if kind == "file":
        return run_file(criterion, base)
    if kind == "grep":
        return run_grep(criterion, base)
    if kind == "judge":
        return run_judge(criterion)
    return "fail", f"unknown kind: {kind}"


def write_ledger(root, payload):
    ledger_dir = root / "docs" / "ledgers"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_dir / "criteria.json"
    runtime.atomic(ledger_path, payload)


def recount(results):
    counts = {"pass": 0, "fail": 0, "deferred": 0}
    for r in results:
        counts[r.get("status", "fail")] = counts.get(r.get("status", "fail"), 0) + 1
    return {"results": results, "passed": counts["pass"], "failed": counts["fail"], "deferred": counts["deferred"]}


def record_judge(root, index, grade, evidence):
    """Record the reviewer's grade for the judge criterion at `index` in docs/ledgers/criteria.json."""
    if not evidence or not evidence.strip():
        print("check_criteria: --judge needs --evidence with the quoted command output the grade rests on", file=sys.stderr)
        return 2
    ledger_path = root / "docs" / "ledgers" / "criteria.json"
    if not ledger_path.is_file():
        print(f"check_criteria: {ledger_path} missing; run check_criteria.py first", file=sys.stderr)
        return 2
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    results = payload.get("results", [])
    if index < 0 or index >= len(results):
        print(f"check_criteria: --judge index {index} out of range (0..{len(results) - 1})", file=sys.stderr)
        return 2
    if results[index].get("kind") != "judge":
        print(f"check_criteria: criterion {index} is kind {results[index].get('kind')!r}, not judge", file=sys.stderr)
        return 2
    results[index]["status"] = "pass" if grade == "PASS" else "fail"
    results[index]["detail"] = f"{grade} by reviewer; evidence: {evidence.strip()}"
    payload = recount(results)
    write_ledger(root, payload)
    print(f"criteria: passed={payload['passed']} failed={payload['failed']} deferred={payload['deferred']}")
    return 0 if payload["failed"] == 0 else 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=None, help="working dir for test cmds and base for relative file/grep paths")
    parser.add_argument("--judge", nargs=2, metavar=("INDEX", "GRADE"), default=None,
                        help="record the reviewer's PASS or FAIL for the judge criterion at INDEX (0-based)")
    parser.add_argument("--evidence", default="", help="quoted command output the judge grade rests on")
    args = parser.parse_args()

    root = find_root()
    if root is None:
        print("check_criteria: could not resolve project root (no CLAUDE_PROJECT_DIR and not a git repo)", file=sys.stderr)
        sys.exit(2)

    if args.judge:
        idx, grade = args.judge
        if grade not in ("PASS", "FAIL") or not idx.isdigit():
            print("check_criteria: --judge takes <index> PASS|FAIL", file=sys.stderr)
            sys.exit(2)
        sys.exit(record_judge(root, int(idx), grade, args.evidence))

    brief = load_brief(root)
    base = Path(args.cwd) if args.cwd else root

    results = []
    passed = failed = deferred = 0
    for criterion in brief.get("success_criteria", []):
        try:
            status, detail = evaluate(criterion, base)
        except KeyError as e:
            status, detail = "fail", f"malformed criterion, missing key {e}"

        results.append({"kind": criterion.get("kind"), "status": status, "detail": detail})
        if status == "pass":
            passed += 1
        elif status == "fail":
            failed += 1
        elif status == "deferred":
            deferred += 1

    write_ledger(root, {"results": results, "passed": passed, "failed": failed, "deferred": deferred})

    print(f"criteria: passed={passed} failed={failed} deferred={deferred}")
    sys.exit(0 if failed == 0 else 2)


if __name__ == "__main__":
    main()
