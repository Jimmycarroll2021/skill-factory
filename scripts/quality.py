"""Quality gate with explicit skip reasons and machine-readable evidence."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import runtime
from policy import inside


def bash():
    if os.name == "nt":
        candidates = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe",
                      Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Git/bin/bash.exe"]
        for p in candidates:
            if p.is_file():
                return str(p)
    return shutil.which("bash") or "bash"


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


def brief_for(root):
    controller = runtime.load(runtime.state() / "controller.json", {})
    if controller.get("schema") == 3:
        return controller["brief"]
    b = runtime.load(runtime.state() / "brief.json")
    if b:
        return b
    if (root / "BRIEF.yaml").exists():
        from brief import load_yaml
        return load_yaml((root / "BRIEF.yaml").read_text(encoding="utf-8"))
    return {}


def defaults(root):
    if (root / "package.json").exists():
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        pm = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() else "bun" if any(root.glob("bun.lock*")) else "npm"
        commands = {k: pm + " run " + k if package.get("scripts", {}).get(k) else None for k in ("lint", "test", "build")}
        dependencies = any(package.get(k) for k in ("dependencies", "devDependencies", "optionalDependencies"))
        install = None
        if dependencies:
            install = pm + " install"
            if pm == "npm":
                install = "npm ci" if (root / "package-lock.json").exists() else "npm install --package-lock=false"
        return "node", commands, install
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
        prefix = "uv run " if (root / "uv.lock").exists() else ""
        build = (root / "pyproject.toml").exists() and "[build-system]" in (root / "pyproject.toml").read_text(encoding="utf-8")
        return "python", {"lint": prefix + "ruff check .", "test": prefix + "pytest -q",
                          "build": "uv build" if build and prefix else "python -m build" if build else {"skip": "no Python build system declared"}}, None
    if (root / "scripts/manifest.txt").exists() and (root / "VERSION").exists() and not (root / ".quickship/VERSION").exists():
        return "harness", {"lint": "python -m compileall -q scripts", "test": "bash tests/run.sh",
                           "build": {"skip": "stdlib scripts; no build artifact"}}, None
    return "unknown", {k: None for k in ("lint", "test", "build")}, None


def docs_only(root, brief):
    base = brief.get("mission", {}).get("base")
    if not base:
        try:
            base = git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        except subprocess.CalledProcessError:
            return False
    try:
        changed = set(git(root, "diff", "--name-only", base + "...HEAD").splitlines())
    except subprocess.CalledProcessError:
        return False
    changed.update(git(root, "diff", "--name-only", "HEAD").splitlines())
    changed.update(git(root, "ls-files", "--others", "--exclude-standard").splitlines())
    for name in changed:
        parts = Path(name).parts
        if parts[:2] == ("docs", "runs") and len(parts) >= 4 and parts[-1] in (
                "RESULT.json", "COMPLETION.json", "RUN_STATE", "task.json", "facts.json", "progress.jsonl", "criteria.json"):
            continue
        if name in ("docs/RESULT.json", "docs/COMPLETION.json", "docs/RUN_STATE",
                    "docs/ledgers/task.json", "docs/ledgers/facts.json", "docs/ledgers/progress.jsonl",
                    "docs/ledgers/criteria.json") or name == "BRIEF.yaml":
            continue
        if Path(name).suffix.lower() in (".md", ".rst", ".txt"):
            continue
        return False
    return True


def gate(root, brief=None, deadline=None):
    root = Path(root).resolve()
    brief = brief if brief is not None else brief_for(root)
    evidence = {"head": git(root, "rev-parse", "HEAD"), "checks": [], "failures": []}
    def run(label, command, selftest=False):
        print("gate: " + label + " -> " + command, file=sys.stderr, flush=True)
        timeout = deadline - time.time() if deadline is not None else (5400 if selftest else 900)
        # Shell code comes only from the operator's brief/project, not constructed strings.
        try:
            if timeout <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            p = subprocess.Popen([bash(), "-c", command], cwd=root, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                 start_new_session=os.name != "nt")
            try:
                stdout, stderr = p.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                from runner import terminate
                terminate(p)
                p.communicate(timeout=10)
                raise
            record = {"name": label, "command": command, "exit": p.returncode, "output": (stdout + stderr)[-20000:]}
        except subprocess.TimeoutExpired:
            record = {"name": label, "command": command, "exit": 124, "output": "deadline exceeded"}
        evidence["checks"].append(record)
        if record["exit"]:
            evidence["failures"].append(label + " failed")
            print(record["output"], file=sys.stderr)
    # Scan actual files, not only the diff. Report paths, never credential values.
    names = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root,
                           capture_output=True, check=True).stdout.decode().split("\0")
    import re
    pattern = re.compile(rb"AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36,}|sk-(?:ant-|proj-)[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----")
    for name in filter(None, names):
        p = root / name
        if not inside(p, root):
            evidence["failures"].append("file resolves outside project: " + name)
            continue
        env = p.name == ".env" or p.name.startswith(".env.")
        if env and not p.name.endswith((".example", ".sample", ".template")):
            evidence["failures"].append("secrets: env file: " + name)
        if p.is_file() and pattern.search(p.read_bytes()):
            evidence["failures"].append("secrets: possible credential in: " + name)
    stack, commands, install = defaults(root)
    print("gate: detected " + stack, file=sys.stderr)
    quality = brief.get("quality", {})
    if quality.get("profile") == "docs":
        if not docs_only(root, brief):
            evidence["failures"].append("docs profile cannot validate application code changes")
        commands = {k: {"skip": "explicit documentation-only profile"} for k in commands}
    commands.update({k: quality[k] for k in commands if k in quality})
    if install and not (root / "node_modules").exists():
        run("install", install)
    for label, command in commands.items():
        if isinstance(command, dict) and command.get("skip"):
            evidence["checks"].append({"name": label, "skipped": command["skip"]})
            print("gate: " + label + " skipped: " + command["skip"], file=sys.stderr)
        elif command:
            run(label, command, selftest=stack == "harness" and label == "test")
        else:
            evidence["failures"].append("missing required " + label + " check; configure quality." + label)
    if (root / "tests/run.sh").exists() and stack != "harness":
        if not (root / ".quickship/VERSION").exists() or os.environ.get("QS_SELFTEST") == "1":
            run("harness self-tests", "bash tests/run.sh", selftest=True)
        else:
            print("gate: harness self-tests skipped in an installed copy (QS_SELFTEST=1 runs them)", file=sys.stderr)
    evidence["pass"] = not evidence["failures"]
    print("gate: " + ("PASS" if evidence["pass"] else "FAIL"), file=sys.stderr)
    for failure in evidence["failures"]:
        print("  " + failure, file=sys.stderr)
    return evidence


def main():
    try:
        root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip())
        evidence = gate(root)
        return 0 if evidence["pass"] else 2
    except Exception as exc:
        # gate.sh and the Stop hook exec this; any exit other than 2 would let a stop
        # through without a gate verdict, so every failure is a gate FAIL.
        print("gate: FAIL " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
