"""PreToolUse guard for the hard rules in CLAUDE.md.

The deny list in settings.json matches command prefixes, so it cannot see `git -C x push -uf`,
`push origin HEAD:main`, `rm -r -f` or `cat .env`. This hook parses the call and denies those.
Reads the hook JSON on stdin. Exit 0 allows; exit 2 denies, with the reason on stderr.
Standard library only.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import PurePath

SEPARATORS = set(";&|\n()")
SHELLS = {"bash", "sh", "zsh", "dash"}
PROTECTED = {"main", "master"}
_ENV_NAME = re.compile(r"^\.env(rc|\..+)?$")
_SAFE_ENV = (".example", ".sample", ".template")
_FILE_KEYS = ("file_path", "notebook_path", "path")


def is_env_file(token: str) -> bool:
    name = PurePath(token.strip("\"'<>").replace("\\", "/")).name
    return bool(_ENV_NAME.match(name)) and not name.endswith(_SAFE_ENV)


def segments(command: str) -> list[list[str]]:
    """Split a shell command into simple commands, each a list of words."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:  # an unbalanced quote, e.g. in an unquoted heredoc body: fall back to words
        tokens = [t.strip("\"'") for t in re.split(r"[ \t\r]+|(?=[;&|\n])|(?<=[;&|\n])", command)]
    out: list[list[str]] = [[]]
    for token in tokens:
        if token and set(token) <= SEPARATORS:
            out.append([])
        elif token:
            out[-1].append(token)
    return [seg for seg in out if seg]


def _command(seg: list[str]) -> tuple[str, list[str]]:
    """The program a segment runs and its arguments, past `VAR=x` assignments and `sudo`/`env`."""
    i = 0
    while i < len(seg) and (re.match(r"^\w+=", seg[i]) or seg[i] in {"sudo", "env", "command"}):
        i += 1
    if i == len(seg):
        return "", []
    return PurePath(seg[i].replace("\\", "/")).name, seg[i + 1 :]


def _git_subcommand(args: list[str]) -> tuple[str, list[str]]:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in {"-C", "-c"} else 1
    return (args[i], args[i + 1 :]) if i < len(args) else ("", [])


def _short_flags(args: list[str]) -> str:
    return "".join(a[1:] for a in args if re.match(r"^-[A-Za-z]+$", a))


def _push_reason(args: list[str]) -> str | None:
    if any(a.startswith(("--force", "--mirror")) for a in args) or "f" in _short_flags(args):
        return "force push"
    refspecs = [a for a in args if not a.startswith("-")][1:]  # the first is the remote
    if any(r.startswith("+") for r in refspecs):
        return "force push"
    for ref in refspecs:
        target = ref.rsplit(":", 1)[-1].removeprefix("refs/heads/")
        if target in PROTECTED:
            return "push to main"
    if "--all" in args:
        return "push to main"
    return None


def _git_reason(args: list[str]) -> str | None:
    sub, rest = _git_subcommand(args)
    if sub == "push":
        return _push_reason(rest)
    if sub == "reset" and "--hard" in rest:
        return "git reset --hard"
    if sub == "branch" and ("D" in _short_flags(rest) or {"--delete", "--force"} <= set(rest)):
        return "git branch -D"
    if sub == "checkout" and "--" in rest:
        return "git checkout -- discards work"
    if sub in {"filter-branch", "filter-repo"}:
        return "history rewrite"
    return None


def _segment_reason(seg: list[str], depth: int) -> str | None:
    prog, args = _command(seg)
    if prog == "git":
        return _git_reason(args)
    if prog == "rm":
        flags = _short_flags(args)
        recursive = "r" in flags.lower() or "--recursive" in args
        if recursive and ("f" in flags or "--force" in args):
            return "rm -rf"
    if prog in {"pip", "pip3"} and args[:1] == ["install"]:
        return "pip install (use uv add for dev tools; no runtime dependencies)"
    if prog.startswith("python") and args[:3] == ["-m", "pip", "install"]:
        return "pip install (use uv add for dev tools; no runtime dependencies)"
    if prog == "gh" and args[:2] == ["pr", "merge"]:
        return "merging a pull request is the human's decision"
    if depth < 3 and prog == "eval":
        return bash_reason(" ".join(args), depth + 1)
    if depth < 3 and prog in SHELLS and "-c" in args[:-1]:
        return bash_reason(args[args.index("-c") + 1], depth + 1)
    return None


def bash_reason(command: str, depth: int = 0) -> str | None:
    segs = segments(command)
    if any(is_env_file(word) for seg in segs for part in seg for word in part.split("=")):
        return ".env access"
    seen_cd = False
    for seg in segs:
        reason = _segment_reason(seg, depth)
        if reason:
            return reason
        prog, _ = _command(seg)
        seen_cd = seen_cd or prog == "cd"
        if seen_cd and prog == "git":
            return "cd before git; use git -C <dir> instead"
    return None


def reason_for(event: dict) -> str | None:
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    if tool == "Bash":
        return bash_reason(str(tool_input.get("command", "")))
    for key in _FILE_KEYS:
        if is_env_file(str(tool_input.get(key, ""))):
            return ".env access"
    return None


def main() -> int:
    try:
        event = json.loads(sys.stdin.read())
        reason = reason_for(event)
    except (ValueError, AttributeError, TypeError):
        print("guard: malformed hook input, denied", file=sys.stderr)
        return 2
    if reason:
        print(f"guard: {reason} is denied by CLAUDE.md's hard rules", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
