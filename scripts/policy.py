"""Cooperative policy checks shared by controller and hooks; not an OS sandbox."""
import fnmatch
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import runtime


class Denied(ValueError):
    pass


def protected(path, reading=False):
    p = str(path).replace("\\", "/").lower()
    b = p.rsplit("/", 1)[-1]
    if (b == ".env" or b.startswith(".env.")) and not b.endswith((".example", ".sample", ".template")):
        return ".env access"
    if reading:
        return None
    if b in ("vercel.json", "fly.toml", "netlify.toml"):
        return "hosting config"
    if b.startswith("dockerfile") and ("deploy" in b or "prod" in b):
        return "deploy Dockerfile"
    if re.search(r"(^|/)(infra|terraform|k8s)/", p):
        return "infra config"
    if re.search(r"(^|/)\.github/workflows/[^/]*deploy", p):
        return "deploy workflow"
    return None


def mark_newlines(command):
    # Bash ends a command at an unquoted newline, exactly like ';'. shlex treats a
    # newline as whitespace, so pad each unquoted one to stand alone as a separator
    # token, drop backslash-newline line continuations and leave quoted newlines alone.
    out, quote, i = [], None, 0
    while i < len(command):
        c = command[i]
        if quote == "'":
            quote = None if c == "'" else quote
        elif c == "\\" and i + 1 < len(command):
            if command[i + 1] != "\n":
                out.append(command[i:i + 2])
            i += 2
            continue
        elif quote == '"':
            quote = None if c == '"' else quote
        elif c in "'\"":
            quote = c
        elif c == "\n":
            c = " \n "
        out.append(c)
        i += 1
    return "".join(out)


def argv_segments(command, strict=False):
    # Reject expansions rather than pretending shlex evaluates Bash. One narrow
    # inspection idiom used by the existing reviewer is explicitly supported.
    if command.strip() == "base=$(bash scripts/diffbase.sh)":
        return [["bash", "scripts/diffbase.sh"]]
    if strict and re.search(r"\$\(|`|<<|<\(|>\(|\$\{|\n", command):
        raise Denied("unsupported shell expansion; use one literal command")
    lex = shlex.shlex(mark_newlines(command), posix=True, punctuation_chars=";&|<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        tokens = list(lex)
    except ValueError as e:
        raise Denied("unparseable shell command") from e
    segments, current = [], []
    for token in tokens:
        if token == "\n":
            # A newline may follow an operator (`a &&` NEWLINE `b`): an empty
            # segment before it is a continuation, not an error.
            if current:
                segments.append(current)
            current = []
        elif token in ("&&", "||", ";", "|", "|&"):
            if not current:
                raise Denied("empty shell subcommand")
            segments.append(current)
            current = []
        elif token == "&":
            raise Denied("background shell commands are unsupported")
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def git_args(tokens):
    args = tokens[1:]
    while args:
        if args[0] in ("-C", "-c", "--git-dir", "--work-tree"):
            if len(args) < 2:
                raise Denied("missing git option value")
            args = args[2:]
        elif args[0].startswith(("--git-dir=", "--work-tree=")):
            args = args[1:]
        else:
            break
    return args


# Executables an agent may run in a controller run without the brief naming them: inspection, the gate and the
# state CLIs, git and gh (their subcommands are checked below), and the package managers and test runners the
# gate itself drives. The brief's quality commands and test criteria extend this per run; nothing else runs.
BASE_COMMANDS = frozenset(
    "ls cat head tail wc grep rg find pwd echo printf tr sort uniq cut awk sed diff true false test [ date sleep "
    "mkdir touch cp mv rm chmod basename dirname env cd tee "
    "git gh bash sh python python3 uv pip pip3 pytest ruff node npm npx pnpm yarn bun make cargo go".split())


def strip_assignments(tokens):
    # `FOO=1 cmd ...` runs cmd; the assignments are not the command.
    i = 0
    while i < len(tokens) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[i], re.S):
        i += 1
    return tokens[i:]


def executable_of(tokens):
    return tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")


def allowed_executables(brief):
    """BASE_COMMANDS plus every executable the brief's quality commands and test criteria name."""
    allowed = set(BASE_COMMANDS)
    commands = []
    quality = brief.get("quality") if isinstance(brief, dict) else None
    if isinstance(quality, dict):
        commands += [v for k, v in quality.items() if k in ("lint", "test", "build") and isinstance(v, str)]
    for c in brief.get("success_criteria", []) if isinstance(brief, dict) else []:
        if isinstance(c, dict) and c.get("kind") == "test" and isinstance(c.get("cmd"), str):
            commands.append(c["cmd"])
    for command in commands:
        try:
            segments = argv_segments(command)
        except Denied:
            continue
        for tokens in segments:
            tokens = strip_assignments(tokens)
            if tokens:
                allowed.add(executable_of(tokens))
    return allowed


def authorize(command, brief):
    # Only the controller invokes this, using shell=False for the actual call.
    perms = brief["permissions"]["irreversible"]
    if perms["default"] == "allow" or any(fnmatch.fnmatchcase(command, p) for p in perms["allow"]):
        return
    raise Denied("brief irreversible policy does not allow " + command)


def inside(path, base):
    try:
        Path(path).resolve().relative_to(Path(base).resolve())
        return True
    except ValueError:
        return False


def resolve_path(path, cwd):
    raw = str(path).replace("\\", "/")
    if os.name == "nt" and raw.startswith(("/c/", "/d/", "/tmp/")):
        cp = subprocess.run(["cygpath", "-w", raw], capture_output=True, text=True, timeout=5)
        if cp.returncode:
            raise Denied("cannot normalize MSYS path")
        raw = cp.stdout.strip()
    p = Path(raw)
    return (p if p.is_absolute() else Path(cwd) / p).resolve()


def bound_step(event):
    agent = event.get("agent_id")
    if not agent:
        return None
    with runtime.transaction() as db:
        sid = runtime.get(db, "agent:" + agent)
        return runtime.get(db, "step:" + sid) if sid else None


def bind(event, sid):
    aid = event.get("agent_id")
    if not aid:
        raise Denied("step-bind is only for a subagent")
    with runtime.transaction() as db:
        step = runtime.get(db, "step:" + sid)
        if not step:
            raise Denied("unregistered step")
        old = runtime.get(db, "agent:" + aid)
        if old and old != sid:
            raise Denied("agent already bound to another step")
        owner = runtime.get(db, "owner:" + sid)
        if owner and owner != aid:
            raise Denied("step already has an agent")
        runtime.put(db, "agent:" + aid, sid)
        runtime.put(db, "owner:" + sid, aid)
        if not runtime.get(db, "agent-head:" + aid):
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=runtime.root(),
                                  capture_output=True, text=True, check=True).stdout.strip()
            runtime.put(db, "agent-head:" + aid, head)


def research_bind(event, inp, cwd):
    # The researcher reads untrusted web content, so it has no shell. It binds with its
    # first Write instead: exactly "step-bind <id>" into its own findings file, which
    # must be work/_untrusted/<slug>.md for that step's slug.
    m = re.fullmatch(r"\s*step-bind\s+(\S+)\s*", str(inp.get("content", "")))
    if not m:
        return False
    with runtime.transaction() as db:
        step = runtime.get(db, "step:" + m[1])
    if not step:
        raise Denied("unregistered step")
    slug = str(step.get("slug", ""))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", slug):
        raise Denied("research step slug is not a plain name")
    expected = (runtime.root() / "work/_untrusted" / (slug + ".md")).resolve()
    if not inside(expected, runtime.root()) or resolve_path(inp.get("file_path", ""), cwd) != expected:
        raise Denied("researcher binds by writing step-bind <id> to its work/_untrusted/<slug>.md")
    bind(event, m[1])
    return True


def check(event):
    tool = event["tool_name"]
    inp = event["tool_input"]
    if not isinstance(inp, dict):
        raise Denied("malformed hook input")
    if event.get("hook_event_name", "PreToolUse") != "PreToolUse":
        return
    active = runtime.active()
    agent = event.get("agent_type", "lead")
    config = runtime.load(runtime.state() / "controller.json", {})
    brief = config.get("brief") or runtime.load(runtime.state() / "brief.json", {})
    cwd = event.get("cwd") or str(runtime.root())
    if active and tool == "Agent" and inp.get("subagent_type") not in ("planner", "worker", "reviewer", "security", "researcher"):
        raise Denied("use a registered Quickship agent type")
    if (active and tool == "Write" and agent == "researcher" and event.get("agent_id") and
            not bound_step(event) and research_bind(event, inp, cwd)):
        return
    if active and tool != "Bash" and event.get("agent_id") and not bound_step(event):
        raise Denied("subagent must step-bind before work")
    if active and tool in ("Read", "Glob", "Grep"):
        target = inp.get("file_path") if tool == "Read" else inp.get("path", ".")
        if not inside(resolve_path(target, cwd), runtime.root()):
            raise Denied("read outside project")
    if tool == "PowerShell":
        raise Denied("PowerShell tool disabled; use Git Bash")
    if tool.startswith("mcp__"):
        if "merge_pull_request" in tool:
            raise Denied("PR merging is prohibited")
        if active:
            raise Denied("external connectors disabled in controlled runs")
    if tool == "Bash":
        cmd = inp["command"]
        if not isinstance(cmd, str):
            raise Denied("command must be a string")
        if agent == "researcher":
            raise Denied("researcher has no shell; it binds by writing step-bind <id> to its findings file")
        segs = argv_segments(cmd, active)
        if (active and len(segs) == 1 and len(segs[0]) == 4 and
                segs[0][:3] in (["python", "scripts/ledger.py", "step-bind"],
                               ["python3", "scripts/ledger.py", "step-bind"])):
            bind(event, segs[0][3])
            return
        if active and event.get("agent_id") and not bound_step(event):
            raise Denied("subagent must step-bind before work")
        allowed = allowed_executables(brief) if active else None
        for ts in segs:
            ts = strip_assignments(ts)
            if not ts:
                continue
            executable = executable_of(ts)
            if executable in ("powershell", "pwsh", "cmd"):
                raise Denied("alternate shell disabled; use Git Bash")
            if executable == "git":
                a = git_args(ts)
                if active and a:
                    if any(x == "-c" or x.startswith(("-c", "--config-env")) for x in ts[1:]):
                        raise Denied("per-command git configuration overrides are disabled")
                    if a[0] in ("reset", "rebase", "update-ref", "symbolic-ref"):
                        raise Denied("history/ref rewriting is prohibited")
                    if a[0] == "remote" and (len(a) < 2 or a[1] not in ("-v", "get-url", "show")):
                        raise Denied("origin configuration is frozen")
                    if a[0] == "config" and not any(x in a for x in ("--get", "--get-all", "--list", "-l")):
                        raise Denied("git configuration is frozen")
                    if a[0] in ("checkout", "switch", "branch") and any(x in ("main", "master", "refs/heads/main", "refs/heads/master") for x in a[1:]) and not any(x in a for x in ("-b", "-c")):
                        raise Denied("protected branch mutation")
                if a and a[0] == "push":
                    if any(x.startswith(("--force", "+")) or re.fullmatch(r"-[A-Za-z]*f[A-Za-z]*", x) for x in a[1:]):
                        raise Denied("force push")
                    for x in a[1:]:
                        dest = x.split(":")[-1].removeprefix("refs/heads/")
                        if dest in ("main", "master"):
                            raise Denied("push to main")
                    if active:
                        raise Denied("publishing belongs to the controller")
                    if brief.get("permissions"):
                        authorize(" ".join(ts), brief)
                if active and any(x.startswith(("--git-dir", "--work-tree")) for x in ts[1:]):
                    raise Denied("alternate git directory is unsupported")
            if executable == "gh":
                a = [x for x in ts[1:] if not x.startswith("--repo=")]
                if "merge" in a or (a and a[0] == "api" and any(x in a for x in ("-X", "--method", "-f", "--field", "-F"))):
                    raise Denied("PR merging and write-side gh api are prohibited")
                if active and not (a[:2] in (["pr", "view"], ["pr", "list"], ["auth", "status"], ["repo", "view"])):
                    raise Denied("publishing belongs to the controller")
            if active and executable in ("curl", "wget", "ssh", "scp", "sftp", "vercel", "netlify", "fly", "terraform", "kubectl", "docker"):
                raise Denied("network/deployment command disabled")
            if active and executable in ("npm", "pnpm", "yarn", "bun") and "publish" in ts:
                raise Denied("package publishing prohibited")
            # Check every literal path for secrets and production destinations, including redirections.
            for i, token in enumerate(ts[1:], 1):
                reason = protected(token, reading=False)
                if reason:
                    # Read-only inspection of deployment config is allowed.
                    if executable in ("cat", "head", "tail", "grep", "rg") and not any(
                            x in ts for x in (">", ">>", "tee")) and reason != ".env access":
                        continue
                    raise Denied(reason)
                if active and token in (">", ">>", "&>", "2>"):
                    if i + 1 >= len(ts):
                        raise Denied("missing redirection target")
                    check_write(ts[i + 1], cwd, agent, event, brief)
            if active:
                if executable in ("cp", "mv", "touch", "tee", "rm"):
                    targets = [x for x in ts[1:] if not x.startswith("-")]
                    if executable in ("cp", "mv"):
                        targets = targets[-1:]
                    for target in targets:
                        check_write(target, cwd, agent, event, brief)
                if executable in ("bash", "sh"):
                    if len(ts) < 2 or ts[1].startswith("-"):
                        raise Denied("nested shell evaluation disabled")
                    p = resolve_path(ts[1], cwd)
                    if not inside(p, runtime.root()):
                        raise Denied("script outside project")
                    if p.parent == (runtime.root() / "scripts/hooks").resolve():
                        raise Denied("hook recorders may only be invoked by the runtime")
                if executable in ("python", "python3"):
                    if len(ts) < 2 or ts[1].startswith("-"):
                        raise Denied("inline interpreter disabled; use a project script")
                    if not inside(resolve_path(ts[1], cwd), runtime.root()):
                        raise Denied("script outside project")
                    if ts[1].replace("\\", "/").endswith("ledger.py") and agent in ("worker", "reviewer", "security"):
                        if len(ts) < 3 or ts[2] not in ("show", "step-bind"):
                            raise Denied(agent + " may not mutate shared ledgers")
                    if ts[1].replace("\\", "/").endswith(("runner.py", "preflight.py", "agent_hook.py", "budget_hook.py", "policy.py")):
                        raise Denied("controller may not be invoked by an agent")
                    if ts[1].replace("\\", "/").endswith("ledger.py"):
                        if "archive-stale" in ts:
                            raise Denied("runtime archival belongs to the controller")
                        if "init" in ts and (event.get("agent_id") or "--goal" not in ts or
                                ts[ts.index("--goal") + 1:ts.index("--goal") + 2] != [brief["mission"]["goal"]]):
                            raise Denied("ledger goal must match the frozen mission")
                    if ts[1].replace("\\", "/").endswith("brief.py") and "validate" in ts:
                        raise Denied("active brief is frozen; use brief.py check")
                if executable == "git" and "-C" in ts:
                    target = resolve_path(ts[ts.index("-C") + 1], cwd)
                    if not inside(target, runtime.root()):
                        raise Denied("git target outside project")
                    if agent == "worker":
                        step = bound_step(event)
                        expected = runtime.root() / ".claude/worktrees" / step["slug"]
                        if target != expected.resolve():
                            raise Denied("worker git target differs from assigned worktree")
                elif executable == "git" and agent == "worker":
                    step = bound_step(event)
                    expected = runtime.root() / ".claude/worktrees" / step["slug"]
                    if resolve_path(".", cwd) != expected.resolve():
                        raise Denied("worker must use git -C for its assigned worktree")
            # Last, so a more specific reason above wins: a controller run only runs what the base set or the
            # brief names. Fail closed on anything else rather than enumerate every dangerous tool.
            if allowed is not None and executable not in allowed:
                raise Denied("command '" + executable + "' is not in this run's allowlist; add it to the brief's "
                             "quality commands or a test criterion")
        return
    if tool in ("Write", "Edit", "MultiEdit", "Read"):
        path = inp["file_path"]
        reason = protected(path, tool == "Read")
        if reason:
            raise Denied(reason)
        if tool != "Read":
            if agent == "researcher" and not active:
                if not re.search(r"(^|/)work/_untrusted/[^/]+\.md$", path.replace("\\", "/")):
                    raise Denied("researcher writes only its untrusted research file")
            if active:
                if event.get("agent_id") and not bound_step(event):
                    raise Denied("subagent must step-bind before work")
                check_write(path, cwd, agent, event, brief)


def check_write(path, cwd, agent, event, brief):
    p = resolve_path(path, cwd)
    r = runtime.root()
    if not inside(p, r):
        raise Denied("write outside project")
    rel = p.relative_to(r).as_posix()
    reason = protected(str(p))
    if reason:
        raise Denied(reason)
    if rel in ("docs/RUN_STATE", "docs/COMPLETION.json"):
        raise Denied("authoritative completion belongs to controller")
    if rel.startswith(".git/") or rel == "BRIEF.yaml":
        raise Denied("git metadata and active brief are protected")
    step = bound_step(event)
    if agent in ("reviewer", "security"):
        raise Denied(agent + " is read-only")
    if agent == "researcher":
        expected = "work/_untrusted/" + (step or {}).get("slug", "") + ".md"
        if rel != expected:
            raise Denied("researcher may only write " + expected)
        return
    if rel.startswith(".claude/state/"):
        raise Denied("runtime state must be changed through its CLI")
    worktree = ".claude/worktrees/" + (step or {}).get("slug", "")
    if agent == "worker":
        if not step or not rel.startswith(worktree + "/"):
            raise Denied("worker must write in its assigned worktree")
        owned = rel[len(worktree) + 1:]
        task = runtime.load(r / "docs/ledgers/task.json", {})
        entries = [t for t in task.get("plan", []) if t["slug"] == step["slug"]]
        if not entries or owned not in entries[0]["owns"]:
            raise Denied("worker file not owned by its task")
        if protected(owned):
            raise Denied(protected(owned))
        if not brief.get("maintenance") and harness_file(owned):
            raise Denied("harness writes require maintenance mode")
        return
    if harness_file(rel):
        raise Denied("active harness is protected")


def harness_file(rel):
    if rel.startswith(".claude/worktrees/"):
        return False
    manifest = runtime.root() / ".quickship/manifest.sha256"
    if manifest.is_file() and rel.startswith(("scripts/", "tests/")):
        managed = {line.split("  ", 1)[1] for line in manifest.read_text(encoding="utf-8").splitlines() if "  " in line}
        return rel in managed
    return rel.startswith(("scripts/", ".claude/", "tests/", ".quickship/")) or rel in (
        "CLAUDE.md", "VERSION", "run.cmd", "program.cmd", "idea.cmd", "init.cmd")


def main():
    try:
        event = json.load(sys.stdin)
        if "--segments" in sys.argv:
            for tokens in argv_segments(event["tool_input"]["command"], runtime.active()):
                # One segment per line: a quoted newline must not start another.
                print(shlex.join(tokens).replace("\n", " ").replace("\r", " "))
            return 0
        check(event)
        return 0
    except (Denied, ValueError, KeyError, TypeError, OSError) as e:
        tool = event.get("tool_name", "unknown") if "event" in locals() else "unknown"
        runtime.state().mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        reason = str(e).replace("\n", " ").replace("\t", " ")
        with (runtime.state() / "hook_log").open("a", encoding="utf-8") as f:
            inp = event.get("tool_input", {}) if "event" in locals() else {}
            arg = str(inp.get("command", inp.get("file_path", "[policy]"))).replace("\n", " ").replace("\t", " ")
            f.write(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") +
                    "\tDENY\t" + tool + "\t" + reason + "\t" + arg + "\n")
        print("guard: denied (" + reason + ")", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
