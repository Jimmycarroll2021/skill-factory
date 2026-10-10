"""Serialized budget hooks. Legacy events without tool IDs remain supported."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import runtime

WRAP_UP = re.compile(r"^\s*(?:git\s+(?:-C\s+\S+\s+)?(?:add|commit)\s|python3?\s+scripts/ledger\.py\s+handoff\s)")
CHAIN = re.compile(r"[;&|`\n]|\$\(")


def handle(event):
    s = runtime.state()
    if not (s / "brief.json").exists():
        return 0
    role = os.environ.get("QS_ROLE", "lead")
    if role == "strategist":
        return 0
    e, tool = event["hook_event_name"], event["tool_name"]
    inp = event.get("tool_input") or {}
    active = runtime.active()
    tp = event.get("transcript_path")
    with runtime.transaction() as db:
        if tp:
            if role != "overseer" and not event.get("agent_id"):
                runtime.atomic(s / "transcript_path", tp)
            paths = runtime.get(db, "transcripts", [])
            if tp not in paths:
                runtime.put(db, "transcripts", paths + [tp])
    if role == "overseer":
        return 0
    parts = str(inp.get("file_path", "")).replace("\\", "/").split("/")
    final = tool in ("Write", "Edit") and parts[-2:] in (["docs", "RESULT.json"], ["docs", "REPORT.md"])
    # Once a budget is exhausted the lead may still save what it has: commit, and leave a handoff note. Nothing
    # else, and no chaining, so an exhausted run cannot smuggle work past the limit on the back of a commit.
    final = final or (tool == "Bash" and bool(WRAP_UP.match(str(inp.get("command", ""))))
                      and not CHAIN.search(str(inp.get("command", ""))))
    reads = tool in ("Read", "Glob", "Grep")
    # In v3 count permitted attempts at PreToolUse, including subsequent tool failures.
    if (active and e == "PreToolUse") or (not active and e == "PostToolUse"):
        if not final:
            key = str(event.get("session_id", "")) + ":" + str(event.get("tool_use_id", ""))
            with runtime.transaction() as db:
                # A stable hook ID counts once, including repeated hook delivery.
                if event.get("tool_use_id") and runtime.get(db, "counted:" + key):
                    pass
                else:
                    old = int((s / "steps").read_text().strip()) if (s / "steps").exists() else 0
                    limit = runtime.load(s / "controller.json", {}).get("brief", runtime.load(s / "brief.json"))["budgets"]["steps"]
                    if active and old >= limit and not reads:
                        print("budget exhausted (steps): submit RESULT and REPORT", file=sys.stderr)
                        return 2
                    runtime.atomic(s / "steps", str(old + 1) + "\n")
                    if event.get("tool_use_id"):
                        runtime.put(db, "counted:" + key, True)
    args = [sys.executable, str(Path(__file__).with_name("budget.py"))]
    if tp:
        args += ["--transcript", tp]
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode:
        # Unknown spend is not "within budget": deny mutations in every mode (main's
        # budget.sh did the same), while reads and the final report stay possible.
        if (active or e == "PreToolUse") and not reads and not final:
            print("budget: cannot read budget state, denied", file=sys.stderr)
            return 2
        return 0
    b = json.loads(p.stdout)
    if e == "PreToolUse":
        if b["exhausted"] and not reads and not final:
            # Keep legacy terminal writes valid for old direct-hook tests.
            fp = str(inp.get("file_path", "")).replace("\\", "/")
            if not active and tool in ("Write", "Edit") and fp in ("docs/REPORT.md", "docs/RUN_STATE"):
                return 0
            print("budget exhausted (" + ",".join(b["exhausted"]) + "): write docs/RESULT.json and docs/REPORT.md, then stop", file=sys.stderr)
            return 2
    # The BUDGET line is context the lead pays for on every call. It is printed every tenth step and whenever a
    # budget is near or exhausted; a quiet step adds nothing, which over a 400-step mission is a few thousand tokens
    # of identical readouts the model never needed.
    if e in ("PostToolUse", "PostToolUseFailure") and (b.get("near") or b.get("exhausted") or b["steps"] % 10 == 0):
        limits = b["limits"]
        ctx = "BUDGET tokens=%d/%s cost=%.2f/%s min=%.1f/%s steps=%d/%s" % (
            b["tokens"], limits["tokens"], b["cost_usd"], limits["cost_usd"],
            b["elapsed_min"], limits["wall_clock_min"], b["steps"], limits["steps"])
        if b.get("near"):
            ctx += " near=%s: stop dispatching; finish the current task, commit, write a handoff note, go to synthesis" % (
                ",".join(b["near"]))
        print(json.dumps({"hookSpecificOutput": {"hookEventName": e, "additionalContext": ctx}}))
    return 0


def main():
    # Claude Code treats any exit code other than 0 and 2 as a non-blocking error and
    # lets the call proceed, so every failure (a 15 s sqlite lock timeout, malformed
    # input of an unexpected type) must surface as exit 2: PreToolUse denies, and a
    # PostToolUse failure to record the step is shown rather than silently dropped.
    try:
        return handle(json.load(sys.stdin))
    except Exception as exc:
        print("budget: malformed input/state, denied: " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
