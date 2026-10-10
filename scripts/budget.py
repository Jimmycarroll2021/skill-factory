#!/usr/bin/env python3
"""Budget status for the active run (contract: docs/design/contracts.md "Budgets").

Usage: budget.py [--transcript <path>] [--exhausted-only]
Reads $S/brief.json (exit 2 if absent), $S/started_at, $S/steps, and token usage from the
transcript JSONL plus sibling <stem>*.jsonl files (subagent transcripts, best effort).
When --transcript is omitted, falls back to the path remembered in $S/transcript_path
(written by scripts/hooks/budget.sh, the only caller that is ever told the path).
Prints the budget JSON, or with --exhausted-only the comma-separated exhausted names. Exit 0.

Token definition (the `tokens` dimension checked against budgets.tokens):
    tokens = input_tokens + output_tokens + cache_creation_input_tokens
cache_read_input_tokens are excluded: a cache read re-serves context that was already paid for and
counted when it was created, and it is billed at 10% of the input rate. Counting it made a real run
show 7.5M of an 8M token budget at $3.40 of cost, so the token budget tripped on re-reads rather than
on new work. Cache reads are still reported as `cache_read_tokens`, and `tokens_total` (all four
counters summed, the pre-change meaning of `tokens`) is kept for the report. cost_usd is unchanged:
reads at 10% and creation at 125% of the model's input rate.
"""
import argparse
import datetime as dt
import glob
import json
import os
import subprocess
import sys
import runtime

# All transcript lines are counted. The incremental controller cache is serialized separately.
MAX_BYTES = None
# USD per million tokens. Cache read = 10% of input; cache creation = 125% of input.
RATES = {"opus": (15.0, 75.0), "sonnet": (3.0, 15.0), "haiku": (1.0, 5.0)}
DIMS = (("tokens", "tokens"), ("cost_usd", "cost_usd"), ("elapsed_min", "wall_clock_min"), ("steps", "steps"))
# The single wrap-up threshold: CLAUDE.md step 3, the controller's "reserve 25%" prompt and the BUDGET hook line all
# mean this number. Past it the lead stops new work and keeps the rest for review, handoff and the report.
NEAR_FRACTION = 0.75


def state_dir():
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    if not root:
        try:
            root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                                  text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            root = "."
    return os.path.join(root, ".claude", "state")


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def normalize_path(p):
    """A path written to a state file (e.g. by the bash hook) may be in MSYS/git-bash mount
    form ("/tmp/...", "/c/Users/..."). A CLI arg gets this auto-translated to a native Windows
    path by the MSYS runtime on exec, but a path read back from a file here gets no such help,
    so it must stay byte-exact on disk. Resolve it via cygpath (ships with Git for Windows) if
    the raw path isn't directly openable."""
    if not p or os.name != "nt" or os.path.exists(p):
        return p
    try:
        r = subprocess.run(["cygpath", "-w", p], capture_output=True, text=True, timeout=5)
        w = r.stdout.strip() if r.returncode == 0 else ""
        return w or p
    except (OSError, subprocess.SubprocessError):
        return p


def rates_for(model):
    m = (model or "").lower()
    for key, r in RATES.items():
        if key in m:
            return r
    # not in the table (a newer or renamed model): price it at the highest known rate so the cost cap errs high
    return max(RATES.values())


def transcript_files(path):
    if not path:
        return []
    files = [path] if os.path.isfile(path) else []
    d = os.path.dirname(path) or "."
    stem = os.path.splitext(os.path.basename(path))[0]
    for p in sorted(glob.glob(os.path.join(glob.escape(d), glob.escape(stem) + "*.jsonl"))):
        if os.path.normcase(os.path.abspath(p)) != os.path.normcase(os.path.abspath(path)):
            files.append(p)
    # current Claude Code layout: each subagent (worker, reviewer, planner) writes <session>/subagents/*.jsonl
    files.extend(sorted(glob.glob(os.path.join(glob.escape(d), glob.escape(stem), "subagents", "*.jsonl"))))
    return files


def usage(path):
    """Return (tokens, cache_read_tokens, cost_usd); see the module docstring for what `tokens` counts."""
    tokens, cache_read, cost, seen = 0, 0, 0.0, set()
    for p in transcript_files(path):
        try:
            f = open(p, "rb")
        except OSError:
            continue
        with f:
            for raw in f:
                try:
                    msg = json.loads(raw).get("message") or {}
                    u = msg.get("usage")
                    if not isinstance(u, dict):
                        continue
                    # one API response is logged once per content block; count it once
                    mid = msg.get("id")
                    if mid:
                        if mid in seen:
                            continue
                        seen.add(mid)
                    i, o = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
                    cr = int(u.get("cache_read_input_tokens") or 0)
                    cc = int(u.get("cache_creation_input_tokens") or 0)
                except (ValueError, TypeError, AttributeError):
                    continue
                rin, rout = rates_for(msg.get("model"))
                tokens += i + o + cc   # cache reads excluded from the budgeted dimension
                cache_read += cr
                cost += (i * rin + o * rout + cr * rin * 0.1 + cc * rin * 1.25) / 1e6
    return tokens, cache_read, cost


def incremental_usage(path):
    """Read only complete new lines, but aggregate all sessions in this run."""
    import hashlib
    with runtime.transaction() as db:
        paths = runtime.get(db, "transcripts", [])
        path = normalize_path(path)
        if path and path not in paths:
            paths.append(path)
            runtime.put(db, "transcripts", paths)
        files = sorted({p for t in paths for p in transcript_files(normalize_path(t))})
        for file in files:
            with open(file, "rb") as f:
                fingerprint = hashlib.sha256(f.read(256)).hexdigest()
                size = os.path.getsize(file)
                key = "usage:" + os.path.abspath(file)
                record = runtime.get(db, key, {"offset": 0, "messages": {}, "fingerprint": fingerprint})
                # Prefix can grow while a small file is being written. Only compare
                # fingerprints once the previous prefix was at least 256 bytes.
                if size < record["offset"] or (record["offset"] >= 256 and record["fingerprint"] != fingerprint):
                    record["offset"] = 0
                    record["fingerprint"] = fingerprint
                    record["generation"] = record.get("generation", 0) + 1
                f.seek(record["offset"])
                while True:
                    at = f.tell()
                    raw = f.readline()
                    if not raw or not raw.endswith(b"\n"):
                        f.seek(at)
                        break
                    try:
                        message = json.loads(raw).get("message") or {}
                        u = message.get("usage")
                        if not isinstance(u, dict):
                            continue
                        mid = message.get("id") or file + ":" + str(record.get("generation", 0)) + ":" + str(at)
                        i, o = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
                        cc, cr = int(u.get("cache_creation_input_tokens") or 0), int(u.get("cache_read_input_tokens") or 0)
                        rin, rout = rates_for(message.get("model"))
                        values = [i + o + cc, cr, (i * rin + o * rout + cc * rin * 1.25 + cr * rin * .1) / 1e6]
                        previous = record["messages"].get(mid, [0, 0, 0])
                        record["messages"][mid] = [max(a, b) for a, b in zip(previous, values)]
                    except (ValueError, TypeError, AttributeError):
                        continue
                record["offset"] = f.tell()
                record["fingerprint"] = fingerprint
                runtime.put(db, key, record)
        # Keep charges from cached files that have been rotated or removed. A replayed
        # message ID counts once; later usage updates can increase, never lower it.
        charged = {}
        for row in db.execute("SELECT value FROM kv WHERE key LIKE 'usage:%'"):
            for mid, values in json.loads(row[0])["messages"].items():
                prior = charged.get(mid, [0, 0, 0])
                charged[mid] = [max(a, b) for a, b in zip(prior, values)]
        return tuple(sum(values[i] for values in charged.values()) for i in range(3))


def elapsed_min(started_at):
    if not started_at:
        return 0.0
    try:
        t = dt.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return max(0.0, (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 60)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--transcript")
    ap.add_argument("--exhausted-only", action="store_true")
    a = ap.parse_args()
    s = state_dir()
    try:
        with open(os.path.join(s, "brief.json"), encoding="utf-8") as f:
            budgets = json.load(f).get("budgets") or {}
    except (OSError, ValueError, AttributeError) as e:
        print(f"budget: no readable brief.json in {s} ({e.__class__.__name__})", file=sys.stderr)
        return 2
    try:
        steps = int(read_text(os.path.join(s, "steps")) or 0)
    except ValueError:
        steps = 0
    controller_path = os.path.join(s, "controller.json")
    if os.path.isfile(controller_path):
        with open(controller_path, encoding="utf-8") as f:
            controller = json.load(f)
        budgets = controller["brief"]["budgets"]
    transcript = normalize_path(a.transcript or read_text(os.path.join(s, "transcript_path"))) or None
    if os.path.isfile(controller_path) and (not transcript or not os.path.isfile(transcript)):
        print("budget: transcript unavailable; accounting degraded", file=sys.stderr)
        return 2
    tokens, cache_read, cost = incremental_usage(transcript) if os.path.isfile(controller_path) else usage(transcript)
    # existing fields keep their names: budget.sh reads tokens/cost_usd/elapsed_min/steps by name
    vals = {"tokens": tokens, "cache_read_tokens": cache_read, "tokens_total": tokens + cache_read,
            "cost_usd": round(cost, 4),
            "elapsed_min": round(elapsed_min(read_text(os.path.join(s, "started_at"))), 1), "steps": steps}
    limits = {lim: budgets.get(lim, 0) for _, lim in DIMS}
    exhausted = [lim for key, lim in DIMS
                 if isinstance(limits[lim], (int, float)) and limits[lim] > 0 and vals[key] >= limits[lim]]
    # near: at or past NEAR_FRACTION of a limit but not over it; the lead's one cue to stop dispatching, finish the
    # current task, commit, write a handoff note and go to synthesis while the budget hook still lets commands through
    near = [lim for key, lim in DIMS
            if isinstance(limits[lim], (int, float)) and limits[lim] > 0
            and vals[key] < limits[lim] and vals[key] >= NEAR_FRACTION * limits[lim]]
    if a.exhausted_only:
        if exhausted:
            print(",".join(exhausted))
    else:
        print(json.dumps({**vals, "limits": limits, "exhausted": exhausted, "near": near}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
