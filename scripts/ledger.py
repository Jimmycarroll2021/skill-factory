#!/usr/bin/env python3
"""Task and progress ledgers for a quickship run (see docs/design/contracts.md, "Ledgers").

Exit codes: 0 ok, 1 stall detected (stall-check), 2 bad args or missing files, 3 replan budget exceeded.
Stdlib only, Python 3.10+.
"""
from __future__ import annotations

import runtime
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ("pending", "dispatched", "merged", "failed", "skipped")
EVENTS = ("dispatched", "merged", "failed", "timeout", "assumption", "blocked", "replan", "stall", "criteria", "note")
TIERS = ("plan", "act")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
HASH_EXCLUDED = {"replan_count", "stall_count", "ts"}
DEFAULT_REPLAN_LIMIT = 5


class LedgerError(Exception):
    """Bad arguments or missing files: exit 2."""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LedgerError("cannot resolve project root (set CLAUDE_PROJECT_DIR or run inside git)") from exc
    return Path(out.stdout.strip())


def ledger_dir() -> Path:
    return root() / "docs" / "ledgers"


def state_dir() -> Path:
    return root() / ".claude" / "state"


def task_path() -> Path:
    return ledger_dir() / "task.json"


def tier_path() -> Path:
    return state_dir() / "tier"


def progress_path() -> Path:
    return ledger_dir() / "progress.jsonl"


def handoff_path() -> Path:
    return ledger_dir() / "handoff.md"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    runtime.atomic(path, text)


def load_task() -> dict:
    p = task_path()
    if not p.is_file():
        raise LedgerError(f"missing {p} (run: ledger.py init --goal ...)")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LedgerError(f"malformed {p}: {exc}") from exc


def save_task(task: dict) -> None:
    write_atomic(task_path(), json.dumps(task, indent=2) + "\n")


def read_progress() -> list[dict]:
    p = progress_path()
    if not p.is_file():
        return []
    lines = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return lines


def next_step_id() -> str:
    if runtime.active():
        with runtime.transaction() as db:
            return f"s{runtime.increment(db, 'step-sequence'):03d}"
    return f"s{len(read_progress()) + 1:03d}"


def strip_excluded(obj):
    if isinstance(obj, dict):
        return {k: strip_excluded(v) for k, v in obj.items() if k not in HASH_EXCLUDED}
    if isinstance(obj, list):
        return [strip_excluded(v) for v in obj]
    return obj


def state_hash(task: dict) -> str:
    canon = json.dumps(strip_excluded(task), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def find_task(task: dict, slug: str) -> dict:
    for item in task.get("plan", []):
        if item.get("slug") == slug:
            return item
    raise LedgerError(f"unknown slug: {slug}")


def split_list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def write_progress_line(event: str, slug: str, detail: str, tokens: int = 0, cost: float = 0.0) -> dict:
    task = load_task()
    line = {
        "ts": now(),
        "step": next_step_id(),
        "slug": slug,
        "event": event,
        "detail": detail,
        "state_hash": state_hash(task),
        "tokens": tokens,
        "cost_usd": cost,
    }
    p = progress_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(line) + "\n")
    return line


# --- subcommands -------------------------------------------------------------------------------

def cmd_init(a) -> int:
    if task_path().exists() and not a.force:
        raise LedgerError(f"{task_path()} exists (use --force to overwrite)")
    task = {"goal": a.goal, "plan": [], "facts": [], "assumptions": [], "blocked": [],
            "replan_count": 0, "stall_count": 0, "is_complete": False}
    save_task(task)
    write_atomic(progress_path(), "")
    print(f"initialised {ledger_dir()}")
    return 0


def cmd_task_add(a) -> int:
    if not SLUG_RE.match(a.slug) or len(a.slug) > 24:
        raise LedgerError(f"slug must be kebab-case, max 24 chars: {a.slug}")
    task = load_task()
    if any(t.get("slug") == a.slug for t in task.get("plan", [])):
        raise LedgerError(f"slug already exists: {a.slug}")
    task.setdefault("plan", []).append({"slug": a.slug, "goal": a.goal, "owns": split_list(a.owns),
                                        "status": "pending", "branch": "", "commit": ""})
    save_task(task)
    print(f"added {a.slug}")
    return 0


def cmd_task_set(a) -> int:
    if a.status not in STATUSES:
        raise LedgerError(f"status must be one of {'|'.join(STATUSES)}: {a.status}")
    task = load_task()
    item = find_task(task, a.slug)
    item["status"] = a.status
    if a.branch is not None:
        item["branch"] = a.branch
    if a.commit is not None:
        item["commit"] = a.commit
    save_task(task)
    print(f"{a.slug} -> {a.status}")
    return 0


def cmd_append(a) -> int:
    if a.event not in EVENTS:
        raise LedgerError(f"event must be one of {'|'.join(EVENTS)}: {a.event}")
    line = write_progress_line(a.event, a.slug, a.detail, a.tokens, a.cost)
    print(line["step"])
    return 0


def cmd_hash(a) -> int:
    print(state_hash(load_task()))
    return 0


def stall_reasons(task: dict, lines: list[dict]) -> list[str]:
    reasons = []
    dispatched = [ln for ln in lines if ln.get("event") == "dispatched"]
    if len(dispatched) >= 2:
        prev, last = dispatched[-2], dispatched[-1]
        if prev.get("slug") == last.get("slug"):
            goals = {t.get("slug"): t.get("goal") for t in task.get("plan", [])}
            # The goal is looked up per slug; a slug missing from the plan compares as None == None.
            if goals.get(prev.get("slug")) == goals.get(last.get("slug")):
                reasons.append(f"last two dispatched lines repeat slug '{last.get('slug')}' with the same goal")
    if len(lines) >= 2 and lines[-1].get("state_hash") and lines[-1].get("state_hash") == lines[-2].get("state_hash"):
        reasons.append(f"state_hash unchanged between {lines[-2].get('step')} and {lines[-1].get('step')}")
    failed = [ln for ln in lines if ln.get("event") == "failed"]
    if len(failed) >= 3 and len({ln.get("detail") for ln in failed[-3:]}) == 1:
        reasons.append(f"last three failed lines share detail '{failed[-1].get('detail')}'")
    if lines and lines[-1].get("event") == "timeout":
        reasons.append(f"last line {lines[-1].get('step')} is a timeout")
    return reasons


def cmd_stall_check(a) -> int:
    task = load_task()
    reasons = stall_reasons(task, read_progress())
    if not reasons:
        print("no stall")
        return 0
    task["stall_count"] = int(task.get("stall_count", 0)) + 1
    save_task(task)
    for r in reasons:
        print(f"stall: {r}")
    print(f"stall_count={task['stall_count']}")
    return 1


def replan_limit() -> int:
    p = state_dir() / "brief.json"
    if not p.is_file():
        return DEFAULT_REPLAN_LIMIT
    try:
        brief = json.loads(p.read_text(encoding="utf-8"))
        return int(brief.get("budgets", {}).get("replan_limit", DEFAULT_REPLAN_LIMIT))
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError) as exc:
        raise LedgerError(f"malformed {p}: {exc}") from exc


def cmd_handoff(a) -> int:
    """Append a dated note for the next context window: what is done, what comes next, what to watch.
    Prose for the model; the machine state stays in task.json and progress.jsonl."""
    p = handoff_path()
    text = p.read_text(encoding="utf-8") if p.is_file() else "# Handoff notes\n\nNewest section last. Read the last `Next:` first.\n"
    section = [f"## {now()}", f"Done: {a.done.strip()}", f"Next: {a.next.strip()}"]
    for note in a.note or []:
        section.append(f"Note: {note.strip()}")
    write_atomic(p, text.rstrip("\n") + "\n\n" + "\n".join(section) + "\n")
    print(p.relative_to(root()).as_posix())
    return 0


def last_handoff() -> str:
    """The newest `## ` section of handoff.md, or an empty string."""
    p = handoff_path()
    if not p.is_file():
        return ""
    parts = p.read_text(encoding="utf-8").split("\n## ")
    return ("## " + parts[-1]).strip() if len(parts) > 1 else ""


def cmd_replan(a) -> int:
    limit = replan_limit()
    task = load_task()
    task["replan_count"] = int(task.get("replan_count", 0)) + 1
    task["stall_count"] = 0
    save_task(task)
    write_progress_line("replan", a.slug, a.detail or f"replan {task['replan_count']}/{limit}")
    flag = state_dir() / "force_replan"
    if flag.is_file():
        # The overseer's request is answered by this replan; the lead itself may not delete .claude/state files.
        flag.unlink()
        print("cleared force_replan")
    print(f"replan_count={task['replan_count']} limit={limit}")
    if task["replan_count"] > limit:
        print(f"replan budget exceeded ({task['replan_count']} > {limit})", file=sys.stderr)
        return 3
    return 0


def cmd_step_start(a) -> int:
    legs = split_list(a.legs)
    if "untrusted_content" in legs and "outbound" in legs:
        raise LedgerError("a step may not combine untrusted_content and outbound legs")
    step = {"id": next_step_id(), "slug": a.slug, "legs": legs, "ts": now()}
    if runtime.active():
        with runtime.transaction() as db:
            runtime.put(db, "step:" + step["id"], step)
    write_atomic(state_dir() / "current_step.json", json.dumps(step) + "\n")
    print(step["id"])
    return 0


def cmd_tier(a) -> int:
    p = tier_path()
    if a.value is None:
        if p.is_file():
            print(p.read_text(encoding="utf-8").strip() or "act")
        else:
            print("act")
        return 0
    if a.value not in TIERS:
        raise LedgerError(f"tier must be one of {'|'.join(TIERS)}: {a.value}")
    write_atomic(p, a.value + "\n")
    print(a.value)
    return 0


TERMINAL_STATES = ("DONE", "DONE_PARTIAL", "SAFE_STOP", "HALT", "ERROR")
RUN_ARTIFACTS = ("RUN_STATE", "REPORT.md", "plan.md", "overseer.md", "RESULT.json", "COMPLETION.json")
RUNTIME_STATE = ("session_id", "steps", "restarts", "stop_attempts", "idem.jsonl", "current_step.json", "tier",
                 "cancel", "force_replan", "transcript_path", "last_run.json", "controller.json", "runtime.sqlite3", "runtime.sqlite3-journal")


def cmd_archive_stale(a) -> int:
    """A merged mission PR carries docs/RUN_STATE and the ledgers into the next mission's checkout. When that
    terminal state belongs to a different goal than the current brief, move the old run under docs/runs/ and
    reset the runtime state so the new mission starts fresh. --force archives any terminal run regardless of goal
    (the operator's `run.sh --archive`, which holds the controller lock). Prints `current` or `archived <dir>`."""
    import re
    import shutil
    docs = root() / "docs"
    rs = docs / "RUN_STATE"
    brief_p = state_dir() / "brief.json"
    if not rs.is_file() or not (brief_p.is_file() or a.force):
        print("current")
        return 0
    try:
        state = json.loads(rs.read_text(encoding="utf-8").strip() or "{}")
        goal = json.loads(brief_p.read_text(encoding="utf-8"))["mission"]["goal"] if brief_p.is_file() else None
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise LedgerError(f"cannot read RUN_STATE or brief.json: {exc}") from exc
    if state.get("state") not in TERMINAL_STATES:
        print("current")
        return 0
    old_goal = None
    if task_path().is_file():
        try:
            old_goal = json.loads(task_path().read_text(encoding="utf-8")).get("goal")
        except json.JSONDecodeError:
            old_goal = None
    run_goal = None
    try:
        run_goal = json.loads((state_dir() / "controller.json").read_text(encoding="utf-8"))["brief"]["mission"]["goal"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        run_goal = None
    # Same goal with its ledgers = the run that just finished; leave it. The controller's frozen brief also
    # identifies the current run when the lead failed before `ledger.py init`: archiving that would reset its
    # deadline, launch counter and budget on every rerun. A different goal, or a terminal state with neither
    # ledgers nor controller state (a legacy run that ended before planning), cannot be resumed and is archived.
    if not a.force and goal is not None and goal in (old_goal, run_goal):
        print("current")
        return 0
    slug = re.sub(r"[^a-z0-9]+", "-", (old_goal or run_goal or "run").lower()).strip("-")[:24].rstrip("-") or "run"
    at = re.sub(r"[^0-9A-Za-z]+", "-", str(state.get("at") or now())).strip("-")
    dest = docs / "runs" / f"{at}-{slug}"
    n = 1
    while dest.exists():
        n += 1
        dest = docs / "runs" / f"{at}-{slug}-{n}"
    dest.mkdir(parents=True)
    for name in RUN_ARTIFACTS:
        p = docs / name
        if p.is_file():
            shutil.move(str(p), str(dest / name))
    if ledger_dir().is_dir():
        shutil.move(str(ledger_dir()), str(dest / "ledgers"))
    s = state_dir()
    for name in RUNTIME_STATE:
        p = s / name
        if p.is_file():
            p.unlink()
    if (s / "legs").is_dir():
        shutil.rmtree(s / "legs")
    s.mkdir(parents=True, exist_ok=True)
    write_atomic(s / "started_at", now() + "\n")
    print(f"archived {dest.relative_to(root()).as_posix()}")
    return 0


def cmd_facts_invalidate(a) -> int:
    task = load_task()
    n = 0
    for fact in task.get("facts", []):
        if a.substring in str(fact.get("text", "")) and fact.get("valid", True):
            fact["valid"] = False
            n += 1
    save_task(task)
    print(f"invalidated {n} fact(s)")
    return 0


class Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse already exits 2; keep the one-line reason on stderr
        self.print_usage(sys.stderr)
        print(f"ledger.py: {message}", file=sys.stderr)
        sys.exit(2)


def build_parser() -> argparse.ArgumentParser:
    p = Parser(prog="ledger.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=Parser)

    s = sub.add_parser("init"); s.add_argument("--goal", required=True); s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("task-add"); s.add_argument("slug"); s.add_argument("--goal", required=True)
    s.add_argument("--owns", required=True); s.set_defaults(fn=cmd_task_add)
    s = sub.add_parser("task-set"); s.add_argument("slug"); s.add_argument("status")
    s.add_argument("--branch"); s.add_argument("--commit"); s.set_defaults(fn=cmd_task_set)
    s = sub.add_parser("append"); s.add_argument("event"); s.add_argument("slug"); s.add_argument("detail")
    s.add_argument("--tokens", type=int, default=0); s.add_argument("--cost", type=float, default=0.0)
    s.set_defaults(fn=cmd_append)
    sub.add_parser("hash").set_defaults(fn=cmd_hash)
    sub.add_parser("stall-check").set_defaults(fn=cmd_stall_check)
    s = sub.add_parser("replan"); s.add_argument("--slug", default="-"); s.add_argument("--detail", default="")
    s.set_defaults(fn=cmd_replan)
    s = sub.add_parser("handoff"); s.add_argument("done"); s.add_argument("--next", required=True)
    s.add_argument("--note", action="append"); s.set_defaults(fn=cmd_handoff)
    s = sub.add_parser("step-bind"); s.add_argument("id"); s.set_defaults(fn=lambda a: 0)
    s = sub.add_parser("step-start"); s.add_argument("slug"); s.add_argument("--legs", required=True)
    s.set_defaults(fn=cmd_step_start)
    s = sub.add_parser("facts-invalidate"); s.add_argument("substring"); s.set_defaults(fn=cmd_facts_invalidate)
    s = sub.add_parser("tier"); s.add_argument("value", nargs="?", default=None); s.set_defaults(fn=cmd_tier)
    s = sub.add_parser("archive-stale"); s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_archive_stale)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with runtime.file_lock():
            return args.fn(args)
    except LedgerError as exc:
        print(f"ledger.py: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ledger.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
