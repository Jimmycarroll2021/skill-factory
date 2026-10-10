"""Read-only readiness diagnostics; no model calls or account changes."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import runtime
import quality
from brief import BriefError, load_yaml, validate_structure


def command(args, cwd):
    # Windows npm/Claude shims are .cmd; Git Bash provides portable resolution.
    if os.name == "nt" and args[0] == "claude":
        import shlex
        args = [quality.bash(), "-c", shlex.join(args)]
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise ValueError(args[0] + " failed: " + p.stderr[-500:])
    return p.stdout


def sandbox_status(root, system=None, which=None):
    """Whether Claude Code's Bash sandbox will wrap agent shell commands here. Informational: the harness stays
    cooperative where the platform cannot sandbox, so this never becomes a preflight error."""
    system = system or ("win32" if os.name == "nt" else sys.platform)
    which = which or shutil.which
    try:
        settings = runtime.load(Path(root) / ".claude/settings.json", {}) or {}
    except ValueError:
        settings = {}
    sandbox = settings.get("sandbox", {}) if isinstance(settings, dict) else {}
    if not isinstance(sandbox, dict) or sandbox.get("enabled") is not True:
        return {"available": False, "reason": "disabled in .claude/settings.json"}
    if system.startswith("win"):
        return {"available": False, "reason": "native Windows runs commands unsandboxed; use WSL2 for the sandbox"}
    if system.startswith("linux"):
        missing = [name for exe, name in (("bwrap", "bubblewrap"), ("socat", "socat")) if not which(exe)]
        if missing:
            return {"available": False, "reason": "install " + " and ".join(missing) + " (for example: apt-get install bubblewrap socat)"}
    return {"available": True, "reason": "shell commands run inside Claude Code's sandbox on this platform"}


def inspect(root):
    root = Path(root).resolve()
    errors = []
    data = {"python": sys.version.split()[0], "errors": errors, "sandbox": sandbox_status(root)}
    if sys.version_info < (3, 10):
        errors.append("Python 3.10+ required")
    for exe in ("git", "gh", "claude"):
        if not shutil.which(exe):
            errors.append(exe + " is missing")
    try:
        version = command([quality.bash(), "--version"], root).splitlines()[0]
        if not re.search(r"version ([4-9]|[1-9][0-9])\.", version):
            errors.append("bash 4+ required")
        data["bash"] = version
        command(["git", "rev-parse", "--show-toplevel"], root)
        try:
            command(["git", "var", "GIT_AUTHOR_IDENT"], root)
        except ValueError:
            errors.append("git author identity is missing; configure repository user.name and user.email")
        auth = json.loads(command(["claude", "auth", "status"], root))
        data["auth"] = {k: auth.get(k) for k in ("loggedIn", "authMethod", "subscriptionType")}
        if not auth.get("loggedIn"):
            errors.append("Claude is not logged in")
        version = command(["claude", "--version"], root).strip()
        data["claude"] = version
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
        if not match or tuple(map(int, match.groups())) < (2, 1, 288):
            errors.append("Claude Code >=2.1.288 required")
        help_text = command(["claude", "--help"], root)
        for flag in ("--permission-prompts", "--strict-mcp-config", "--allowedTools", "--max-budget-usd"):
            if flag not in help_text:
                errors.append("Claude lacks " + flag)
        command(["gh", "auth", "status"], root)
        origin = command(["git", "remote", "get-url", "origin"], root).strip()
        repo = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?", origin)
        if not repo:
            errors.append("origin must use a canonical github.com HTTPS or SSH repository URL")
        else:
            data["repository"] = repo[1]
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        errors.append(str(exc))
    try:
        b = validate_structure(load_yaml((root / "BRIEF.yaml").read_text(encoding="utf-8")))
        data["brief"] = b
        stack, commands, _ = quality.defaults(root)
        commands.update({k: b["quality"][k] for k in commands if k in b["quality"]})
        if b["quality"]["profile"] != "docs":
            for key, value in commands.items():
                if not value:
                    errors.append("missing quality." + key + " for " + stack)
        elif not b["mission"].get("base"):
            # The docs profile diffs against mission.base or origin/HEAD; a repository made with `git init` has no
            # origin/HEAD, and the gate would only discover that after the model has run.
            try:
                command(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], root)
            except (ValueError, OSError, subprocess.SubprocessError):
                errors.append("docs profile needs mission.base or a resolvable origin/HEAD: push the default branch, then run `git remote set-head origin -a`")
        if (root / ".claude/state/session_id").exists() and not runtime.load(root / ".claude/state/controller.json"):
            errors.append("v0.2 active state: preserve/archive it before starting v0.3; automatic migration refused")
        if (root / ".quickship/conflicts").exists():
            errors.append("installation conflicts: resolve .quickship/conflicts before running")
        manifest = root / ".quickship/manifest.sha256"
        if manifest.is_file():
            managed = {line.split("  ", 1)[1] for line in manifest.read_text(encoding="utf-8").splitlines() if "  " in line}
            managed.update(("BRIEF.yaml", ".quickship/manifest.sha256", ".quickship/VERSION"))
            tracked = set(command(["git", "ls-files"], root).splitlines())
            missing = sorted(managed - tracked)
            if missing:
                errors.append("commit installed harness and BRIEF.yaml before running: " + str(len(missing)) + " required files are untracked")
            if not runtime.load(root / ".claude/state/controller.json"):
                dirty = command(["git", "diff", "--name-only", "HEAD", "--", *sorted(managed)], root).splitlines()
                if dirty:
                    errors.append("commit installed harness and BRIEF.yaml before running: " + str(len(dirty)) + " required files have uncommitted changes")
    except (BriefError, ValueError, OSError) as exc:
        errors.append("brief: " + str(exc))
    return data


if __name__ == "__main__":
    result = inspect(runtime.root())
    result.pop("brief", None)
    print(json.dumps(result, indent=2))
    sys.exit(2 if result["errors"] else 0)
