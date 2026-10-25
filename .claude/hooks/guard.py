"""PreToolUse guard for the hard rules in CLAUDE.md.

The deny list in settings.json matches command prefixes, so it cannot see `git -C x push -uf`,
`push origin HEAD:main`, `rm -r -f`, `if ...; then rm -rf x; fi` or `cat .env`. This hook
tokenises the call the way Bash would (line continuations, comments, quotes) and checks every
word position where a guarded program can start, so wrappers (`env -i`, `sudo`, `xargs`,
`then`, `do`, `find -exec`) do not hide it. It errs towards denying.
Reads the hook JSON on stdin. Exit 0 allows; exit 2 denies, with the reason on stderr.
Standard library only.
"""

from __future__ import annotations

import json
import re
import shlex
import sys

SEPARATORS = {"&&", "||", ";", "|", "|&", "&", "\n", ";;"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
ALTERNATE_SHELLS = {"powershell", "pwsh", "cmd"}
PROTECTED = {"main", "master"}
# Git global options that take the next word as their value.
_GIT_VALUE_OPTIONS = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env",
}
_SAFE_ENV = (".example", ".sample", ".template")
# Tool name -> the input field that holds its path, and whether that field is required.
_FILE_FIELDS = {
    "Read": ("file_path", True),
    "Write": ("file_path", True),
    "Edit": ("file_path", True),
    "MultiEdit": ("file_path", True),
    "NotebookEdit": ("notebook_path", True),
    "Grep": ("path", False),
    "Glob": ("path", False),
}
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
_READ_ONLY = {"cat", "head", "tail", "grep", "rg", "less", "wc", "diff", "ls"}
_SUBST = re.compile(r"\$\(|`")
_GH_WRITE_FLAGS = {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"}
_GH_WRITE_PREFIXES = ("-X", "--method=", "--field=", "--raw-field=", "--input=")
_MCP_MERGE_TOOLS = ("merge_pull_request", "enable_pr_auto_merge")


class Malformed(ValueError):
    pass


def _norm(word: str) -> str:
    """A word as a program name: no quotes, brackets or directory, lower case, no `.exe`."""
    word = word.strip("\"'(){}$`").replace("\\", "/").rsplit("/", 1)[-1]
    return word.lower().removesuffix(".exe")


def is_env_file(token: str) -> bool:
    name = token.strip("\"'<>()`").replace("\\", "/").rsplit("/", 1)[-1].lower()
    is_env = name in (".env", ".envrc") or name.startswith(".env.")
    return is_env and not name.endswith(_SAFE_ENV)


def protected_write(path: str) -> str | None:
    """Production and deployment config the agent never writes (reading it is fine)."""
    p = path.strip("\"'").replace("\\", "/").lower()
    base = p.rsplit("/", 1)[-1]
    if base in ("vercel.json", "fly.toml", "netlify.toml"):
        return "hosting config"
    if base.startswith("dockerfile") and ("deploy" in base or "prod" in base):
        return "deploy Dockerfile"
    if re.search(r"(^|/)(infra|terraform|k8s)/", p):
        return "infra config"
    if re.search(r"(^|/)\.github/workflows/[^/]*deploy", p):
        return "deploy workflow"
    return None


def _mark_newlines(command: str) -> str:
    """Drop backslash-newline continuations, as Bash does, and make each unquoted newline a
    separator token; quoted newlines stay."""
    out, quote, i = [], None, 0
    while i < len(command):
        c = command[i]
        if quote == "'":
            quote = None if c == "'" else quote
        elif c == "\\" and i + 1 < len(command):
            if command[i + 1] != "\n":
                out.append(command[i : i + 2])
            i += 2
            continue
        elif quote == '"':
            quote = None if c == '"' else quote
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or command[i - 1] in " \t\n;&|()"):
            # A comment: Bash ignores it up to the newline, quotes included. A mid-word # is literal.
            end = command.find("\n", i)
            i = len(command) if end == -1 else end
            continue
        elif c == "\n":
            c = " \n "
        out.append(c)
        i += 1
    return "".join(out)


def segments(command: str) -> list[list[str]]:
    """Split a shell command into simple commands, each a list of words."""
    marked = _mark_newlines(command)
    try:
        lexer = shlex.shlex(marked, posix=True, punctuation_chars=";&|<>\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""  # `#` mid-word is literal in Bash; a comment can only add denials
        tokens = list(lexer)
    except ValueError:  # an unbalanced quote, e.g. in an unquoted heredoc body: fall back to words
        tokens = [t.strip("\"'") for t in re.split(r"[ \t\r]+|(?=[;&|\n])|(?<=[;&|\n])", marked)]
    out: list[list[str]] = [[]]
    for token in tokens:
        if token in SEPARATORS:
            out.append([])
        elif token:
            out[-1].append(token)
    return [seg for seg in out if seg]


def _git_subcommand(args: list[str]) -> tuple[str, list[str]]:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in _GIT_VALUE_OPTIONS else 1
    return (args[i], args[i + 1 :]) if i < len(args) else ("", [])


def _short_flags(args: list[str]) -> str:
    return "".join(a[1:] for a in args if re.fullmatch(r"-[A-Za-z]+", a))


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


def _program_reason(prog: str, args: list[str], depth: int) -> str | None:
    """The reason to deny `prog args...`, wherever in a segment it starts."""
    if prog in ALTERNATE_SHELLS:
        return "alternate shell; its commands bypass these checks"
    if prog == "git":
        return _git_reason(args)
    if prog == "rm":
        flags = _short_flags(args)
        recursive = "r" in flags.lower() or "--recursive" in args
        if recursive and ("f" in flags or "--force" in args):
            return "rm -rf"
    if prog in {"pip", "pip3"} and "install" in args[:1]:
        return "pip install (use uv add for dev tools; no runtime dependencies)"
    if prog.startswith("python") and args[:3] == ["-m", "pip", "install"]:
        return "pip install (use uv add for dev tools; no runtime dependencies)"
    if prog == "gh" and (("pr" in args and "merge" in args) or ("api" in args and any(
        "merge" in a for a in args
    ))):
        return "merging a pull request is the human's decision"
    if prog == "gh" and "api" in args and (
        "graphql" in args
        or any(a in _GH_WRITE_FLAGS or a.startswith(_GH_WRITE_PREFIXES) for a in args)
    ):
        # A payload (-f, -F @file, --input) or a mutation can merge a PR; its contents are not visible here.
        return "write-side gh api"
    if depth < 3 and prog == "eval":
        return bash_reason(" ".join(args), depth + 1)
    if depth < 3 and prog in SHELLS:
        # `-c`, or a combined cluster such as `-lc` or `-ec`: the next word is the script.
        for i, arg in enumerate(args[:-1]):
            if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", arg):
                return bash_reason(args[i + 1], depth + 1)
    return None


def _segment_reason(seg: list[str], depth: int) -> str | None:
    for i, word in enumerate(seg):
        reason = _program_reason(_norm(word), seg[i + 1 :], depth)
        if reason:
            return reason
    prog = _norm(seg[0])
    for token in seg[1:]:
        reason = protected_write(token)
        if reason and not (prog in _READ_ONLY and not {">", ">>", "tee"} & set(seg)):
            return reason
    return None


def bash_reason(command: str, depth: int = 0) -> str | None:
    if not isinstance(command, str):
        raise Malformed("command must be a string")
    segs = segments(command)
    words = [word for seg in segs for token in seg for word in re.split(r"[=:]", token)]
    if any(is_env_file(word) for word in words):
        return ".env access"
    # Command substitution runs its contents too, even inside double quotes.
    if depth < 3:
        for seg in segs:
            for token in seg:
                if _SUBST.search(token):
                    reason = bash_reason(re.sub(r"\$\(|\)|`", " ; ", token), depth + 1)
                    if reason:
                        return reason
    seen_cd = False
    for seg in segs:
        reason = _segment_reason(seg, depth)
        if reason:
            return reason
        seen_cd = seen_cd or _norm(seg[0]) == "cd"
        if seen_cd and any(_norm(word) == "git" for word in seg):
            return "cd before git; use git -C <dir> instead"
    return None


def reason_for(event: dict) -> str | None:
    if not isinstance(event, dict):
        raise Malformed("event must be an object")
    tool = event.get("tool_name")
    tool_input = event.get("tool_input")
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        raise Malformed("tool_name and tool_input are required")
    if tool.startswith("mcp__") and tool.endswith(_MCP_MERGE_TOOLS):
        return "merging a pull request is the human's decision"
    if tool == "Bash":
        if "command" not in tool_input:
            raise Malformed("Bash needs a command")
        return bash_reason(tool_input["command"])
    if tool in _FILE_FIELDS:
        key, required = _FILE_FIELDS[tool]
        path = tool_input.get(key)
        if path is None and not required:
            return None
        if not isinstance(path, str):
            raise Malformed(f"{tool} needs a string {key}")
        if is_env_file(path):
            return ".env access"
        if tool in _WRITE_TOOLS:
            return protected_write(path)
    return None


def main() -> int:
    try:
        reason = reason_for(json.loads(sys.stdin.read()))
    except (ValueError, TypeError):  # Malformed is a ValueError, as is bad JSON
        print("guard: malformed hook input, denied", file=sys.stderr)
        return 2
    if reason:
        print(f"guard: {reason} is denied by CLAUDE.md's hard rules", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
