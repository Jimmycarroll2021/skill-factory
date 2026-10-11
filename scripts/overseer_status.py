#!/usr/bin/env python3
"""One-shot status snapshot for the overseer (contract: docs/design/contracts.md "Overseer").

Usage: overseer_status.py
Prints exactly one JSON object and exits 0, whatever is or is not on disk. The overseer
session reads this instead of chaining shell commands (pipes, `||` fallbacks, `date` maths),
which an unattended `claude -p` session refuses. Missing or malformed files never crash the
script: the matching fields become null/0/false and a line is added to "warnings".

Root = $CLAUDE_PROJECT_DIR, else `git rev-parse --show-toplevel`, else ".".
Sources: docs/ledgers/task.json, docs/ledgers/progress.jsonl, docs/RUN_STATE,
.claude/state/brief.json (limits), .claude/state/{cancel,force_replan}, .claude/state/hook_log,
and scripts/budget.py (run in a subprocess with this interpreter).

Output keys: goal, run_state, tasks{pending,dispatched,merged,failed,skipped}, replan_count,
replan_limit, stall_count, stall_limit, newest_progress_ts, newest_progress_age_min,
last3_same_slug_and_hash, repeated_denials{reason,count}, budget, flags{cancel,force_replan},
progress_tail (last 5 lines as objects), warnings.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys

STATUSES = ("pending", "dispatched", "merged", "failed", "skipped")
DEFAULT_REPLAN_LIMIT = 5
DEFAULT_STALL_LIMIT = 3
TAIL = 5
STALE_AFTER_MIN = 45


def project_root():
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    if root:
        return root
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             check=True, timeout=10)
        return out.stdout.strip() or "."
    except (OSError, subprocess.SubprocessError):
        return "."


def read_json(path, warnings, label):
    """Parse a JSON file; None (plus a warning) when it is missing or malformed."""
    if not os.path.isfile(path):
        warnings.append(f"{label} missing")
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        warnings.append(f"{label} unreadable ({e.__class__.__name__})")
        return None


def read_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [ln.rstrip("\r\n") for ln in f]
    except OSError:
        return None


def parse_ts(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t


def task_summary(task, warnings):
    goal, counts = None, {s: 0 for s in STATUSES}
    replan_count, stall_count = 0, 0
    if isinstance(task, dict):
        goal = task.get("goal") if isinstance(task.get("goal"), str) else None
        for item in task.get("plan") or []:
            status = item.get("status") if isinstance(item, dict) else None
            if status in counts:
                counts[status] += 1
            else:
                warnings.append(f"task.json: unknown status {status!r}")
        try:
            replan_count = int(task.get("replan_count") or 0)
            stall_count = int(task.get("stall_count") or 0)
        except (TypeError, ValueError):
            warnings.append("task.json: replan_count/stall_count not integers")
    elif task is not None:
        warnings.append("task.json: not an object")
    return goal, counts, replan_count, stall_count


def limits(brief, warnings):
    replan_limit, stall_limit = DEFAULT_REPLAN_LIMIT, DEFAULT_STALL_LIMIT
    if isinstance(brief, dict):
        budgets = brief.get("budgets") if isinstance(brief.get("budgets"), dict) else {}
        try:
            replan_limit = int(budgets.get("replan_limit", DEFAULT_REPLAN_LIMIT))
            stall_limit = int(budgets.get("stall_limit", DEFAULT_STALL_LIMIT))
        except (TypeError, ValueError):
            warnings.append("brief.json: replan_limit/stall_limit not integers; defaults used")
    return replan_limit, stall_limit


WORK_EVENTS = {"dispatched", "failed", "timeout", "retry", "replan"}
FLAGS = ("cancel", "force_replan")


def set_flag(root, name, reason):
    """Create .claude/state/<name>; the overseer calls this because the Write tool is refused on .claude paths
    in an unattended session. Exit 0 whether created or already present, 2 for an unknown flag."""
    if name not in FLAGS:
        print(f"overseer_status: unknown flag {name!r}; use one of {', '.join(FLAGS)}", file=sys.stderr)
        return 2
    state = os.path.join(root, ".claude", "state")
    os.makedirs(state, exist_ok=True)
    path = os.path.join(state, name)
    if os.path.exists(path):
        print(f"flag {name} already set")
        return 0
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write((reason or "1").strip().replace("\n", " ") + "\n")
    os.replace(tmp, path)
    print(f"flag {name} set")
    return 0


def progress_summary(path, warnings):
    """Returns (tail, newest_ts, age_min, last3_same)."""
    raw = read_lines(path)
    if raw is None:
        warnings.append("progress.jsonl missing")
        return [], None, None, False
    lines, bad = [], 0
    for ln in raw:
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except ValueError:
            bad += 1
            continue
        if isinstance(obj, dict):
            lines.append(obj)
        else:
            bad += 1
    if bad:
        warnings.append(f"progress.jsonl: {bad} unparseable line(s) skipped")
    if not lines:
        warnings.append("progress.jsonl: no progress lines")
        return [], None, None, False
    stamped = [(parse_ts(ln.get("ts")), ln.get("ts")) for ln in lines]
    stamped = [(t, s) for t, s in stamped if t is not None]
    newest_ts, age_min = None, None
    if stamped:
        t, newest_ts = max(stamped, key=lambda p: p[0])
        age_min = round(max(0.0, (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 60), 1)
    else:
        warnings.append("progress.jsonl: no parseable ts")
    # Stall signal: the last three WORK events share slug and state hash. Assumption, note, blocked, merged and
    # criteria lines are bookkeeping that legitimately repeats a hash, so they are ignored here.
    last3_same = False
    work = [ln for ln in lines if ln.get("event") in WORK_EVENTS]
    if len(work) >= 3:
        tail3 = work[-3:]
        slugs = {ln.get("slug") for ln in tail3}
        hashes = {ln.get("state_hash") for ln in tail3}
        last3_same = len(slugs) == 1 and len(hashes) == 1 and None not in slugs and None not in hashes
    return lines[-TAIL:], newest_ts, age_min, last3_same


def repeated_denials(path):
    """Trailing run of consecutive DENY lines sharing one reason, from the end of hook_log.
    Line formats: `<ts>\\tDENY\\t<tool>\\t<reason>\\t<arg>` (denied) and `<ts>\\t<tool>\\t<arg>` (allowed)."""
    raw = read_lines(path)
    if not raw:
        return {"reason": None, "count": 0}
    reason, count = None, 0
    for ln in reversed(raw):
        if not ln.strip():
            continue
        parts = ln.split("\t")
        if len(parts) < 4 or parts[1] != "DENY":
            break
        this_reason = parts[3]
        if reason is None:
            reason = this_reason
        elif this_reason != reason:
            break
        count += 1
    return {"reason": reason, "count": count}


def budget_snapshot(root, warnings):
    script = os.path.join(root, "scripts", "budget.py")
    if not os.path.isfile(script):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "budget.py")
    if not os.path.isfile(script):
        warnings.append("budget: scripts/budget.py not found")
        return None
    env = dict(os.environ, CLAUDE_PROJECT_DIR=root)
    try:
        r = subprocess.run([sys.executable, script], capture_output=True, text=True, cwd=root, env=env,
                           timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        warnings.append(f"budget: could not run budget.py ({e.__class__.__name__})")
        return None
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        warnings.append(f"budget: budget.py exit {r.returncode}" + (f": {err[-1]}" if err else ""))
        return None
    try:
        data = json.loads(r.stdout.strip())
    except ValueError:
        warnings.append("budget: budget.py printed no JSON")
        return None
    if not isinstance(data, dict):
        warnings.append("budget: budget.py JSON is not an object")
        return None
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 epilog="Prints one JSON object; always exits 0.")
    ap.add_argument("--set-flag", choices=list(FLAGS), metavar="NAME",
                    help="create .claude/state/NAME (cancel or force_replan) instead of printing status")
    ap.add_argument("--reason", default="", help="one-line reason stored in the flag file (with --set-flag)")
    args = ap.parse_args()
    root = project_root()
    if args.set_flag:
        return set_flag(root, args.set_flag, args.reason)
    docs = os.path.join(root, "docs")
    state = os.path.join(root, ".claude", "state")
    warnings = []

    task = read_json(os.path.join(docs, "ledgers", "task.json"), warnings, "task.json")
    goal, counts, replan_count, stall_count = task_summary(task, warnings)
    brief = read_json(os.path.join(state, "brief.json"), warnings, "brief.json")
    replan_limit, stall_limit = limits(brief, warnings)

    run_state = None
    rs_path = os.path.join(docs, "RUN_STATE")
    if os.path.isfile(rs_path):
        rs = read_json(rs_path, warnings, "RUN_STATE")
        if isinstance(rs, dict):
            run_state = {"state": rs.get("state"), "reason": rs.get("reason"), "at": rs.get("at")}
        elif rs is not None:
            warnings.append("RUN_STATE: not an object")

    tail, newest_ts, age_min, last3_same = progress_summary(os.path.join(docs, "ledgers", "progress.jsonl"),
                                                             warnings)
    if age_min is not None and age_min > STALE_AFTER_MIN:
        warnings.append(f"no progress line newer than {STALE_AFTER_MIN} minutes (newest is {age_min} min old)")

    out = {
        "goal": goal,
        "run_state": run_state,
        "tasks": counts,
        "replan_count": replan_count,
        "replan_limit": replan_limit,
        "stall_count": stall_count,
        "stall_limit": stall_limit,
        "newest_progress_ts": newest_ts,
        "newest_progress_age_min": age_min,
        "last3_same_slug_and_hash": last3_same,
        "repeated_denials": repeated_denials(os.path.join(state, "hook_log")),
        "budget": budget_snapshot(root, warnings),
        "flags": {
            "cancel": os.path.exists(os.path.join(state, "cancel")),
            "force_replan": os.path.exists(os.path.join(state, "force_replan")),
        },
        "progress_tail": tail,
        "warnings": warnings,
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
