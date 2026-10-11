#!/usr/bin/env bash
# PreToolUse/PostToolUse idempotency guard. Handles two tool shapes:
#   Bash: `git push` and `gh pr|issue|release create` subcommands (keyed on the normalised text).
#   MCP:  `mcp__<server>__create_pull_request|create_issue|create_release` (keyed on the tool
#         name plus the `head`, `base`, `title` fields of tool_input), which is how a cloud
#         session without `gh` opens a PR.
# Stops the lead from retrying a push/create that already ran to completion for the current step.
# The settings.json matcher is `Bash|mcp__.*`, so any tool_name may arrive: anything not handled
# above is a no-op (exit 0, no ledger line). A `done` key is denied regardless of its recorded
# exit code: the recorded result stands even when the call errored, because retrying a create
# that may have half-succeeded is exactly the duplicate this hook exists to prevent.
# Input: hook JSON on stdin. Fails closed: malformed input -> exit 2. See
# docs/design/contracts.md "Idempotency".
set -u
PY="${QS_PYTHON:-$(command -v python3 || command -v python)}"
in="$(cat)"

S="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}/.claude/state"

# One python call does everything that can shift fields if split across multiple prints:
# parse the hook JSON, split the command into subcommands on unquoted &&, ||, ;, |, and
# newlines, and match only a subcommand that itself STARTS WITH git push / git -C <dir>
# push / gh pr|issue|release create (text inside quotes never triggers a match, since a
# quoted argument becomes a single token, not a run of bare words). Emits exactly one line
# of JSON, so the bash side can never misalign cmd/step/status the way multi-line output did.
result="$(printf '%s' "$in" | IDEM_STATE="$S" "$PY" -c '
import hashlib, json, os, re, shlex, sys

try:
    d = json.load(sys.stdin)
    event = d["hook_event_name"]
    tool = d.get("tool_name", "Bash")
except Exception:
    sys.exit(1)
if not isinstance(tool, str):
    sys.exit(1)

MCP_CREATE = re.compile(r"^mcp__[^_].*__(create_pull_request|create_issue|create_release)$")

def read_step():
    try:
        with open(os.path.join(os.environ["IDEM_STATE"], "current_step.json"), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("id"):
            return str(data["id"])
    except (FileNotFoundError, ValueError, OSError):
        pass
    return "nostep"

if tool != "Bash":
    if not MCP_CREATE.match(tool):
        # Any other tool (Write, Read, a read-only MCP tool, ...) is a pure no-op.
        print(json.dumps({"match": False}))
        sys.exit(0)
    ti = d.get("tool_input")
    if not isinstance(ti, dict):
        sys.exit(1)
    fields = {}
    for k in ("head", "base", "title"):
        v = ti.get(k, "")
        fields[k] = v if isinstance(v, str) else ("" if v is None else str(v))
    code = 0
    if event == "PostToolUse":
        resp = d.get("tool_response")
        if isinstance(resp, dict) and (resp.get("isError") is True or resp.get("is_error") is True):
            code = 1
    step = read_step()
    key = hashlib.sha256((step + "\n" + tool + ":" + json.dumps(fields, sort_keys=True)).encode("utf-8")).hexdigest()
    cmd = tool + " head=" + fields["head"] + " base=" + fields["base"]
    print(json.dumps({"match": True, "event": event, "key": key, "step": step, "cmd": cmd, "exit": code}))
    sys.exit(0)

try:
    cmd = d["tool_input"]["command"]
except Exception:
    sys.exit(1)

if not isinstance(cmd, str):
    cmd = str(cmd)

code = 0
if event == "PostToolUse":
    resp = d.get("tool_response")
    if isinstance(resp, dict):
        for k in ("exit_code", "exitCode", "code"):
            v = resp.get(k)
            if isinstance(v, int):
                code = v
                break
    elif isinstance(resp, int):
        code = resp

SQ = chr(39)
DQ = chr(34)

def split_subcommands(s):
    subs = []
    buf = []
    i = 0
    n = len(s)
    quote = None
    while i < n:
        c = s[i]
        if quote:
            buf.append(c)
            if quote == DQ and c == chr(92) and i + 1 < n:
                buf.append(s[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c == DQ or c == SQ:
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == chr(92) and i + 1 < n:
            buf.append(c)
            buf.append(s[i + 1])
            i += 2
            continue
        two = s[i:i + 2]
        if two in ("&&", "||"):
            subs.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in (";", "|", "\n"):
            subs.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    subs.append("".join(buf))
    return subs

def sub_tokens(sub):
    try:
        return shlex.split(sub, posix=True)
    except ValueError:
        return sub.split()

def matches(tokens):
    if not tokens:
        return False
    if tokens[0] == "git":
        if len(tokens) >= 2 and tokens[1] == "push":
            return True
        if len(tokens) >= 4 and tokens[1] == "-C" and tokens[3] == "push":
            return True
        return False
    if tokens[0] == "gh":
        if len(tokens) >= 3 and tokens[1] in ("pr", "issue", "release") and tokens[2] == "create":
            return True
    return False

matched_norm = None
if event in ("PreToolUse", "PostToolUse"):
    for sub in split_subcommands(cmd):
        raw = sub.strip()
        if not raw:
            continue
        if matches(sub_tokens(raw)):
            matched_norm = re.sub(r"\s+", " ", raw).strip()
            break

if matched_norm is None:
    print(json.dumps({"match": False}))
    sys.exit(0)

step = read_step()
key = hashlib.sha256((step + "\n" + matched_norm).encode("utf-8")).hexdigest()
print(json.dumps({"match": True, "event": event, "key": key, "step": step, "cmd": matched_norm, "exit": code}))
' 2>/dev/null)"
rc=$?
if [ $rc -ne 0 ] || [ -z "$result" ]; then
  echo "idem: malformed hook input, denied" >&2
  exit 2
fi
result="${result//$'\r'/}"

# field <name> -> reads one field back out of the single JSON line above.
field() {
  printf '%s' "$result" | IDEM_FIELD="$1" "$PY" -c '
import json, os, sys

d = json.loads(sys.stdin.read())
v = d.get(os.environ["IDEM_FIELD"], "")
sys.stdout.write(str(v))
'
}

# Non-matching commands are a pure no-op: exit before touching the filesystem.
[ "$(field match)" = "True" ] || exit 0

event="$(field event)"
key="$(field key)"
step="$(field step)"
cmd="$(field cmd)"
code="$(field exit)"

mkdir -p "$S" 2>/dev/null
ledger="$S/idem.jsonl"

if [ "$event" = PreToolUse ]; then
  done_info="$(IDEM_KEY="$key" IDEM_FILE="$ledger" "$PY" -c '
import json, os

key = os.environ["IDEM_KEY"]
path = os.environ["IDEM_FILE"]
found = ""
try:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("key") == key and obj.get("status") == "done":
                ts = obj.get("ts", "")
                ex = obj.get("exit", 0)
                found = f"{ts}\t{ex}"
except FileNotFoundError:
    pass
print(found)
')"
  done_info="${done_info//$'\r'/}"
  if [ -n "$done_info" ]; then
    ts="${done_info%%$'\t'*}"; ex="${done_info#*$'\t'}"
    echo "idem: already executed at ${ts} (exit ${ex}); use the recorded result, do not retry" >&2
    exit 2
  fi
  IDEM_KEY="$key" IDEM_STEP="$step" IDEM_CMD="$cmd" IDEM_FILE="$ledger" "$PY" -c '
import datetime, json, os

line = {
    "key": os.environ["IDEM_KEY"],
    "step": os.environ["IDEM_STEP"],
    "cmd": os.environ["IDEM_CMD"],
    "status": "pending",
    "exit": 0,
    "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
with open(os.environ["IDEM_FILE"], "a", encoding="utf-8") as f:
    f.write(json.dumps(line) + "\n")
'
  exit 0
fi

# PostToolUse: record the outcome.
IDEM_KEY="$key" IDEM_STEP="$step" IDEM_CMD="$cmd" IDEM_CODE="$code" IDEM_FILE="$ledger" "$PY" -c '
import datetime, json, os

try:
    code = int(os.environ.get("IDEM_CODE", "0"))
except ValueError:
    code = 0

line = {
    "key": os.environ["IDEM_KEY"],
    "step": os.environ["IDEM_STEP"],
    "cmd": os.environ["IDEM_CMD"],
    "status": "done",
    "exit": code,
    "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
with open(os.environ["IDEM_FILE"], "a", encoding="utf-8") as f:
    f.write(json.dumps(line) + "\n")
'
exit 0
