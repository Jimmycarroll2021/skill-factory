#!/usr/bin/env bash
# SessionStart hook. After /compact or a resume, re-injects the mission brief, plan, the last handoff note,
# the progress tail, assumptions, blocked items, budget summary and control flags into context so the lead
# loop can pick up cleanly from files rather than from whatever compaction kept.
# Input: hook JSON on stdin. Fails closed: malformed input -> exit 2. No active run -> exit 0, no output.
set -u
PY="${QS_PYTHON:-$(command -v python3 || command -v python)}"
in="$(cat)"
source_val="$(printf '%s' "$in" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
print(d.get("source", ""))
' 2>/dev/null)" || { echo "anchor: malformed hook input" >&2; exit 2; }
source_val="${source_val//$'\r'/}"   # python on Windows emits CRLF
case "$source_val" in
  compact|resume) ;;
  *) exit 0 ;;
esac

ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
S="$ROOT/.claude/state"
[ -f "$S/brief.json" ] || exit 0

budget_line="budget: unavailable"
if [ -f "$ROOT/scripts/budget.py" ]; then
  b_out="$("$PY" "$ROOT/scripts/budget.py" 2>/dev/null)"
  b_rc=$?
  b_out="${b_out//$'\r'/}"
  first_line="${b_out%%$'\n'*}"
  if [ "$b_rc" = 0 ] && [ -n "$first_line" ]; then
    budget_line="budget: $first_line"
  fi
fi

cancel_flag="no"; [ -f "$S/cancel" ] && cancel_flag="yes"
replan_flag="no"; [ -f "$S/force_replan" ] && replan_flag="yes"

# The files the previous context touched last: uncommitted changes first, then the last three commits. A fresh
# context reads these before anything else in the code, the way Claude Code's own compaction hands over the
# summary plus the most recently accessed files.
recent="$( { git -C "$ROOT" status --porcelain 2>/dev/null | cut -c4-; git -C "$ROOT" log -n 3 --name-only --pretty=format: 2>/dev/null; } | awk 'NF && !seen[$0]++' | head -n 5 )"
recent="${recent//$'\r'/}"

"$PY" - "$ROOT" "$budget_line" "$cancel_flag" "$replan_flag" "$recent" <<'PYEOF'
import json
import os
import sys

root, budget_line, cancel_flag, replan_flag, recent = sys.argv[1:6]
state_dir = os.path.join(root, ".claude", "state")
brief_path = os.path.join(state_dir, "brief.json")
task_path = os.path.join(root, "docs", "ledgers", "task.json")
handoff_path = os.path.join(root, "docs", "ledgers", "handoff.md")
progress_path = os.path.join(root, "docs", "ledgers", "progress.jsonl")


def last_handoff(path):
    """The newest `## ` section of handoff.md (ledger.py handoff), or an empty string."""
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        parts = f.read().split("\n## ")
    return ("## " + parts[-1]).strip() if len(parts) > 1 else ""


def progress_tail(path, n):
    """The last n progress events as `ts step slug event: detail`, oldest first; malformed lines are skipped."""
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f.read().splitlines():
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            out.append("{} {} {} {}: {}".format(e.get("ts", ""), e.get("step", ""), e.get("slug", ""),
                                                e.get("event", ""), str(e.get("detail", ""))[:200]))
    return out[-n:]


def build():
    with open(brief_path, "r", encoding="utf-8") as f:
        brief = json.load(f)

    lines = []
    mission = brief.get("mission", {})
    lines.append("<mission-brief>")
    lines.append("goal: {}".format(mission.get("goal", "")))
    lines.append("deliverables:")
    for d in mission.get("deliverables", []):
        lines.append("- {}".format(d))
    lines.append("success_criteria:")
    for c in brief.get("success_criteria", []):
        kind = c.get("kind", "")
        rest = ", ".join("{}={}".format(k, v) for k, v in c.items() if k != "kind")
        lines.append("- {}: {}".format(kind, rest))
    budgets = brief.get("budgets", {})
    lines.append("budgets: " + " ".join("{}={}".format(k, v) for k, v in budgets.items()))
    perms = brief.get("permissions", {}).get("irreversible", {})
    lines.append("permissions: default={} allow={}".format(perms.get("default", ""), perms.get("allow", [])))
    lines.append("ambiguity_policy: {}".format(brief.get("ambiguity_policy", "")))
    lines.append("</mission-brief>")
    lines.append("")

    if os.path.isfile(task_path):
        try:
            with open(task_path, "r", encoding="utf-8") as f:
                task = json.load(f)
        except Exception:
            task = {}

        lines.append("<plan>")
        lines.append("slug|status|goal")
        for t in task.get("plan", []):
            lines.append("{}|{}|{}".format(t.get("slug", ""), t.get("status", ""), t.get("goal", "")))
        lines.append("</plan>")
        lines.append("")

        # What the previous context left for this one: its last handoff note and the newest progress events.
        # Prose and a short tail, bounded so the budget line and the closing instruction below survive the cap.
        lines.append("<handoff>")
        lines.append(last_handoff(handoff_path)[:1200] or "none yet")
        lines.append("</handoff>")
        lines.append("")
        lines.append("<progress>")
        tail = progress_tail(progress_path, 5)
        lines.extend(tail or ["none yet"])
        lines.append("</progress>")
        lines.append("")
        lines.append("<recent-files>")
        lines.extend([p for p in recent.splitlines() if p.strip()] or ["none yet"])
        lines.append("</recent-files>")
        lines.append("")

        lines.append("<assumptions>")
        for a in task.get("assumptions", []):
            lines.append("- {} ({})".format(a.get("text", ""), a.get("ts", "")))
        lines.append("</assumptions>")
        lines.append("")

        lines.append("<blocked>")
        for b in task.get("blocked", []):
            lines.append("- {}: {} ({})".format(b.get("step", ""), b.get("rule", ""), b.get("ts", "")))
        lines.append("</blocked>")
        lines.append("")
    else:
        lines.append("plan: none yet")
        lines.append("")

    lines.append(budget_line)
    lines.append("flags: cancel={} force_replan={}".format(cancel_flag, replan_flag))
    lines.append("")
    lines.append("You never ask a question. This is a fresh context: trust the files above, not memory. "
                 "Read <handoff> and <recent-files> before anything else in the code; fetch the rest only as you need it. "
                 "Continue the lead loop in CLAUDE.md from step 1: run the gate before dispatching anything new, "
                 "then pick up the <handoff> Next line.")

    text = "\n".join(lines)
    return text[:9000]


try:
    context_text = build()
except Exception as exc:
    print("anchor: failed to build context: {}".format(exc), file=sys.stderr)
    sys.exit(2)

out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context_text}}
print(json.dumps(out))
PYEOF
