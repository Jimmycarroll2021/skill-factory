#!/usr/bin/env bash
# PreToolUse/PostToolUse guard. Enforces the CLAUDE.md hard rules from inside the repo, so they hold in cloud
# sessions and fresh clones where ~/.claude settings do not exist. Fails closed: malformed input -> exit 2.
# Input: hook JSON on stdin. Exit 0 = allow, exit 2 = deny (reason on stderr).
#
# Clauses, in order:  1 hard rules (always)   1b read-only subagents (agent_type reviewer or security)   1c overseer role
# (QS_ROLE=overseer; exempt from the rest)   2 overseer cancel flag   3 plan/act tier   4 lethal-trifecta legs.
# Clauses 2-4 act only when their state file exists (.claude/state/{cancel,tier,current_step.json}), i.e. during a run;
# clause 4 is off in a v0.3 controller run (runtime.active(): controller.json with schema 3), where policy.py governs.
# PostToolUse never denies; it only records the leg a tool call used (clause 4).
# Every PreToolUse call leaves one line in .claude/state/hook_log:  allowed  <utc ts>\t<tool>\t<arg>
#                                                                   denied   <utc ts>\tDENY\t<tool>\t<reason>\t<arg>
set -u
PY="${QS_PYTHON:-$(command -v python3 || command -v python)}"
in="$(cat)"
printf '%s' "$in" | "$PY" "$(dirname "$0")/../policy.py" || exit 2
parsed="$(printf '%s' "$in" | "$PY" -c '
import json, sys
sys.path.insert(0, sys.argv[1]); import runtime
d = json.load(sys.stdin); e = d.get("hook_event_name", "PreToolUse"); t = d["tool_name"]; i = d["tool_input"]
a = d.get("agent_type") or "lead"   # subagent type; absent for the lead session
if t == "Bash": v = i["command"]
elif t in ("Write", "Edit", "MultiEdit", "Read"): v = i["file_path"]
elif t.startswith("mcp__"): v = " ".join(f"{k}={i[k]}" for k in ("head", "base", "title", "url") if i.get(k))  # log summary only
else: v = i.get("url") or i.get("query") or ""
c = 1 if runtime.active() else 0   # a controller run: the same schema-3 test policy.py and the runner use
print(e); print(t); print(c); print(str(a)); print(str(v))
' "$(dirname "$0")/.." 2>/dev/null)" || { echo "guard: malformed hook input or runtime state, denied" >&2; exit 2; }
parsed="${parsed//$'\r'/}"   # python on Windows emits CRLF
event="${parsed%%$'\n'*}"; rest="${parsed#*$'\n'}"
tool="${rest%%$'\n'*}"; rest="${rest#*$'\n'}"
controlled="${rest%%$'\n'*}"; rest="${rest#*$'\n'}"
agent="${rest%%$'\n'*}"; raw="${rest#*$'\n'}"; arg="${raw//$'\n'/ }"   # raw keeps newlines for the split
case "$tool" in Write|Edit|MultiEdit|Read) arg="${arg//\\//}";; esac   # Windows tools pass backslash paths
S="${CLAUDE_PROJECT_DIR:-.}/.claude/state"; mkdir -p "$S" 2>/dev/null
# Subcommands of a Bash line, one per element of SEGS: split on && || ; | |& and newlines (as Claude Code matches
# permission rules), leading space trimmed, and git's -C <dir> / -c <k=v> / --git-dir / --work-tree options dropped
# so `git -C wt push` is matched as `git push`.
SEGS=()
split_subcmds() {
  local seg out q='("[^"]*"|'"'"'[^'"'"']*'"'"'|[^[:space:]]+)'
  local opt_re="(^|[[:space:]])git[[:space:]]+(-C[[:space:]]+$q|-c[[:space:]]+$q|--(git-dir|work-tree)(=|[[:space:]]+)$q)[[:space:]]*"
  # policy.py splits like Bash (quotes respected; unquoted newlines separate commands in every mode). If it cannot,
  # deny: an empty SEGS would silently skip every per-subcommand rule below.
  out="$(printf '%s' "$in" | "$PY" "$(dirname "$0")/../policy.py" --segments)" || { echo "guard: cannot split command, denied" >&2; exit 2; }
  while IFS= read -r seg; do
    seg="${seg#"${seg%%[![:space:]]*}"}"; [ -z "$seg" ] && continue
    while [[ "$seg" =~ $opt_re ]]; do seg="${seg/"${BASH_REMATCH[0]}"/${BASH_REMATCH[1]}git }"; done
    SEGS+=("$seg")
  done <<< "${out//$'\r'/}"
}
[ "$tool" = Bash ] && split_subcmds
deny() {
  printf '%s\tDENY\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$tool" "$1" "$arg" >> "$S/hook_log" 2>/dev/null
  echo "guard: denied ($1): $tool $arg" >&2; exit 2
}
allow() { printf '%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$tool" "$arg" >> "$S/hook_log" 2>/dev/null; exit 0; }
# Read-only subcommands: inspection, the gate and tests, the state CLIs. Shared by the plan tier, the reviewer, security and the overseer.
ro_re='^(git[[:space:]]+(status|log|diff|ls-files|rev-parse|show|branch([[:space:]]+--list)?|worktree[[:space:]]+list)|ls|cat|head|tail|wc|grep|rg|find|pwd|echo|tr|sort|uniq|cut|awk|sed[[:space:]]+-n|bash[[:space:]]+scripts/(gate|diffbase)\.sh|bash[[:space:]]+tests/|python3?[[:space:]]+scripts/(ledger|budget|brief|check_criteria)\.py)([[:space:]]|$)'
has_redirect() { # a > redirect (other than into .claude/state/) or a tee anywhere in the line
  { [[ "$arg" =~ \> ]] && ! [[ "$arg" =~ \>[[:space:]]*\.claude/state/ ]]; } || [[ "$arg" =~ [[:space:]]tee[[:space:]] ]]
}
is_env_file() { # basename is .env or .env.<x>, except *.example|sample|template
  local b="${1##*/}"
  [[ "$b" =~ ^\.env(\..+)?$ ]] && ! [[ "$b" =~ \.(example|sample|template)$ ]]
}
leg_of() { # untrusted_content | outbound | none, for this tool call
  case "$tool" in
    WebFetch|WebSearch) echo untrusted_content;;
    Read) [[ "$arg" =~ (^|/)work/_untrusted/ ]] && echo untrusted_content || echo none;;
    mcp__*) # MCP tools: write-side GitHub tools send data out; get/list/search/read/fetch/download tools bring untrusted content in
      if [[ "$tool" =~ ^mcp__.*__(create_pull_request|update_pull_request|merge_pull_request|create_or_update_file|push_files|create_issue|add_issue_comment|create_release|create_branch)$ ]]; then echo outbound
      elif [[ "$tool" =~ ^mcp__.*__(get|list|search|read|fetch|download)_ ]]; then echo untrusted_content
      else echo none; fi;;
    Bash) # per subcommand; outbound in any subcommand wins over untrusted_content
      local seg l=none
      for seg in "${SEGS[@]}"; do
        if [[ "$seg" =~ git[[:space:]]+push|gh[[:space:]]+(pr|issue|release)[[:space:]]+(create|comment|edit|merge|close)|npm[[:space:]]+publish ]]; then echo outbound; return
        elif [[ "$seg" =~ (^|[[:space:]])curl[[:space:]] ]] && [[ "$seg" =~ -X[[:space:]]*(POST|PUT|PATCH|DELETE)|--data|[[:space:]]-d[[:space:]]|--upload-file|[[:space:]]-T[[:space:]] ]]; then echo outbound; return
        elif [[ "$seg" =~ (^|[[:space:]])(curl|wget)[[:space:]] || "$seg" =~ work/_untrusted/ ]]; then l=untrusted_content; fi
      done
      echo "$l";;
    *) echo none;;
  esac
}

# --- PostToolUse: record the leg for the current step, never deny ---
if [ "$event" = PostToolUse ]; then
  if [ "$controlled" != 1 ] && [ -f "$S/current_step.json" ]; then
    sid="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id","nostep"))' "$S/current_step.json" 2>/dev/null)"; sid="${sid//$'\r'/}"
    leg="$(leg_of)"; mkdir -p "$S/legs"
    [ "$leg" != none ] && [ -n "$sid" ] && echo "$leg" >> "$S/legs/$sid"
  fi
  exit 0
fi

# --- 1. hard rules ---
case "$tool" in
  Bash) # each rule sees one subcommand, so a token in another subcommand can neither trip nor mask it
    for seg in "${SEGS[@]}"; do
      if [[ "$seg" =~ git[[:space:]]+push ]]; then
        [[ "$seg" =~ [[:space:]](--force|--force-with-lease|-[A-Za-z]*f[A-Za-z]*)([[:space:]]|$) || "$seg" =~ [[:space:]]\+[^[:space:]] ]] && deny "force push"   # -f also inside -uf
        [[ "$seg" =~ ([[:space:]]|:)(main|master)([[:space:]]|$) ]] && deny "push to main"
      fi
      [[ "$seg" =~ git[[:space:]]+reset[[:space:]]+--hard ]] && deny "git reset --hard"
      [[ "$seg" =~ git[[:space:]]+branch[[:space:]]+(-D|--delete[[:space:]]+--force) ]] && deny "git branch -D"
      [[ "$seg" =~ git[[:space:]]+checkout[[:space:]]+--[[:space:]] ]] && deny "git checkout -- discards work"
      [[ "$seg" =~ git[[:space:]]+(filter-branch|filter-repo) ]] && deny "history rewrite"
      [[ "$seg" =~ (^|[[:space:]])pip3?[[:space:]]+install ]] && deny "pip install (use uv add)"
      [[ "$seg" =~ (^|[[:space:]])rm[[:space:]]+(-[A-Za-z]*r[A-Za-z]*f|-[A-Za-z]*f[A-Za-z]*r|-r[[:space:]]+-f|-f[[:space:]]+-r) ]] && deny "rm -rf"
    done
    for tok in $arg; do tok="${tok#[\"\']}"; tok="${tok%[\"\']}"; is_env_file "$tok" && deny ".env access"; done
    # Claude Code's permission layer treats a cd before any git command as needing approval, which an unattended
    # run cannot give; deny it here with the fix in the reason instead of an opaque permission failure.
    seen_cd=0
    for seg in "${SEGS[@]}"; do
      [[ "$seg" =~ ^cd([[:space:]]|$) ]] && seen_cd=1
      [ "$seen_cd" = 1 ] && [[ "$seg" =~ ^git([[:space:]]|$) ]] && deny "cd before git is auto-denied in an unattended run; use git -C <dir> ... instead"
    done
    ;;
  Write|Edit|MultiEdit|Read)
    is_env_file "$arg" && deny ".env access"
    if [ "$tool" != Read ]; then
      b="${arg##*/}"
      [[ "$b" =~ ^(vercel\.json|fly\.toml|netlify\.toml)$ ]] && deny "hosting config"
      [[ "$b" =~ ^Dockerfile.*(deploy|prod) ]] && deny "deploy Dockerfile"
      [[ "$arg" =~ (^|/)(infra|terraform|k8s)/ ]] && deny "infra config"
      [[ "$arg" =~ \.github/workflows/[^/]*deploy ]] && deny "deploy workflow"
    fi
    ;;
esac

# --- 1b. read-only subagents (reviewer, security): audit only. They may inspect, run the gate, tests and evals, and the brief's own test
# criteria (verbatim), and never change git state or write a file. Deny reasons name the agent. ---
if [ "$agent" = reviewer ] || [ "$agent" = security ]; then
  case "$tool" in
    Write|Edit|MultiEdit) deny "$agent is read-only";;
    Bash)
      for seg in "${SEGS[@]}"; do
        [[ "$seg" =~ (^|[[:space:]])git[[:space:]]+(add|commit|merge|checkout|worktree|push)([[:space:]]|$) ]] && deny "$agent never changes git state"
        [[ "$seg" =~ (^|[[:space:]])gh[[:space:]] ]] && deny "$agent never changes git state"
      done
      has_redirect && deny "$agent may only run read-only, test and eval commands (no redirects)"
      # success_criteria of kind test from the brief, one per line; no brief -> no extra allowances
      brief_cmds="$("$PY" -c '
import json, sys
try: b = json.load(open(sys.argv[1]))
except Exception: sys.exit(0)
for c in b.get("success_criteria", []):
    if isinstance(c, dict) and c.get("kind") == "test" and c.get("cmd"): print(c["cmd"])
' "$S/brief.json" 2>/dev/null)"; brief_cmds="${brief_cmds//$'\r'/}"
      runner_re='^(pytest|npm[[:space:]]+test|npm[[:space:]]+run[[:space:]]+[A-Za-z0-9:_-]+|pnpm[[:space:]]+test|yarn[[:space:]]+test|uv[[:space:]]+run[[:space:]]+[^[:space:]]+|make[[:space:]]+test|cargo[[:space:]]+test|go[[:space:]]+test)([[:space:]]|$)'
      for seg in "${SEGS[@]}"; do
        s="${seg%"${seg##*[![:space:]]}"}"                                    # trailing space from the split
        [[ "$s" =~ ^[A-Za-z_][A-Za-z0-9_]*=\$\((.*)\)$ ]] && s="${BASH_REMATCH[1]}"   # base=$(bash scripts/diffbase.sh)
        [[ "$s" =~ $ro_re ]] && continue
        [[ "$s" =~ ^python3?[[:space:]]+scripts/check_criteria\.py([[:space:]]|$) ]] && continue
        [[ "$s" =~ $runner_re ]] && continue
        hit=0; while IFS= read -r c; do [ -n "$c" ] && [ "$c" = "$s" ] && hit=1; done <<< "$brief_cmds"
        [ "$hit" = 1 ] || deny "$agent may only run read-only, test and eval commands ($seg)"
      done
      ;;
  esac
fi

# --- 1c. overseer session (QS_ROLE=overseer): writes only its note and the two flag files, runs only its status scripts
# and read-only commands; exempt from clauses 2-4, which are mission-run policy for the lead and its subagents. ---
if [ "${QS_ROLE:-lead}" = overseer ]; then
  case "$tool" in
    Write|Edit|MultiEdit) [[ "$arg" =~ (^|/)(docs/overseer\.md|\.claude/state/(force_replan|cancel))$ ]] || deny "overseer may only write docs/overseer.md and the two flag files";;
    Bash) for seg in "${SEGS[@]}"; do
            [[ "$seg" =~ ^python3?[[:space:]]+scripts/(overseer_status|budget)\.py([[:space:]]|$) || "$seg" =~ $ro_re ]] || deny "overseer may only run its status scripts and read-only commands ($seg)"
          done;;
  esac
  allow
fi

# --- 2. overseer cancel: only reads and the final report may proceed ---
if [ -f "$S/cancel" ]; then
  case "$tool" in
    Read|Glob|Grep) ;;
    Write|Edit|MultiEdit) [[ "$arg" =~ docs/(REPORT\.md|RUN_STATE|RESULT\.json)$ ]] || deny "overseer cancel: only docs/REPORT.md, docs/RESULT.json and docs/RUN_STATE may be written";;
    *) deny "overseer cancel: the run is stopping";;
  esac
fi

# --- 3. plan tier: read-only, except the plan, the ledgers and the state dir ---
tier="$(cat "$S/tier" 2>/dev/null)"; tier="${tier//[$'\r\n ']/}"
if [ "$tier" = plan ]; then
  case "$tool" in
    Agent) ;;
    Read|Glob|Grep) ;;
    Write|Edit|MultiEdit) [[ "$arg" =~ (^|/)(docs/plan\.md|docs/ledgers/|\.claude/state/) ]] || deny "plan tier: cannot write $arg";;
    Bash)
      has_redirect && deny "plan tier: no redirects"
      for seg in "${SEGS[@]}"; do [[ "$seg" =~ $ro_re ]] || deny "plan tier: read-only commands only ($seg)"; done
      ;;
    *) deny "plan tier: $tool not allowed";;
  esac
fi

# --- 4. lethal trifecta: one step never both reads untrusted content and sends data out ---
if [ "$controlled" != 1 ] && [ -f "$S/current_step.json" ]; then
  stepinfo="$("$PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("id","nostep")); print(",".join(d.get("legs",[])))' "$S/current_step.json" 2>/dev/null)"
  stepinfo="${stepinfo//$'\r'/}"; sid="${stepinfo%%$'\n'*}"; declared="${stepinfo#*$'\n'}"
  leg="$(leg_of)"
  if [ "$leg" != none ]; then
    have=",$declared,$(tr '\n' ',' < "$S/legs/$sid" 2>/dev/null),"
    other=untrusted_content; [ "$leg" = untrusted_content ] && other=outbound
    [[ "$have" == *",$other,"* ]] && deny "trifecta: step $sid already used $other, so $leg is not allowed in the same step"
  fi
fi
allow
