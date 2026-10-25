"""Static validation of a canonical skill directory.

The validator only reads files as bytes and matches compiled regexes against them. It never
imports, executes or spawns anything from the skill and does not follow symlinks.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import stat
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from skill_factory import yamlish
from skill_factory.capture import (
    MAX_DESCRIPTION,
    MAX_NAME,
    NAME_RE,
    NEGATIVE_RE,
    RESOURCE_DIRS,
)

# Size limits. Module-level so tests can lower them instead of writing megabytes.
MAX_FILE_BYTES = 1_048_576  # a single file over this is an error
MAX_SKILL_BYTES = 5 * 1_048_576  # the whole skill (excluding .factory/) over this is an error
MAX_BODY_LINES = 500  # a SKILL.md body longer than this is a warning

FACTORY_DIR = ".factory"
EVALS_DIR = "evals"  # committed eval prompts for scripts/check.py; render never ships it
ALLOWED_TOP_LEVEL = frozenset({"SKILL.md", FACTORY_DIR, EVALS_DIR, *RESOURCE_DIRS})
_MAX_FINDINGS_PER_FILE = 20
_BINARY_SNIFF = 8192

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("API key (sk- prefix)", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("private key header", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")),
    (
        "credentials in a URL",
        re.compile(r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*://[^\s:/@]+:[^\s/@]+@"),
    ),
    (
        "hard-coded credential assignment",
        re.compile(
            r"\b(?:api[_-]?key|secret|token|passw(?:or)?d)\b\s*[:=]\s*[\"']?[A-Za-z0-9/+_-]{16,}",
            re.IGNORECASE,
        ),
    ),
)

_NETWORK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("network tool", re.compile(r"\b(?:curl|wget|nc|ncat|netcat|ssh|scp|sftp|telnet|ftp)\b")),
    (
        "network library",
        re.compile(
            r"\b(?:import|from)\s+(?:urllib\d?|urllib2|requests|httpx|aiohttp|http\.client|httplib"
            r"|ftplib|smtplib|telnetlib|websockets?|socket)\b"
            r"|\burllib\d?\.|\brequests\.(?:get|post|put|patch|delete|head|request|Session)\b"
            r"|\bsocket\.socket\b|\brequire\(\s*[\"']https?[\"']\s*\)"
        ),
    ),
    (
        "PowerShell web request",
        re.compile(r"\b(?:Invoke-WebRequest|Invoke-RestMethod)\b", re.IGNORECASE),
    ),
    ("browser fetch", re.compile(r"\b(?:fetch\s*\(|XMLHttpRequest)")),
    ("/dev/tcp redirect", re.compile(r"/dev/(?:tcp|udp)/")),
)

_DESTRUCTIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "recursive or forced rm",
        re.compile(r"\brm\s+(?:-\S+\s+)*(?:-\S*[rRf]\S*|--(?:recursive|force)\b)"),
    ),
    (
        "recursive delete (PowerShell/cmd)",
        re.compile(r"\bRemove-Item\b[^\n]*-Recurse|\b(?:del|rmdir|rd)\s+/s\b", re.IGNORECASE),
    ),
    ("shutil.rmtree", re.compile(r"\bshutil\.rmtree\b")),
    ("filesystem wipe", re.compile(r"\b(?:mkfs(?:\.\w+)?|shred|wipefs)\b|\bdd\s[^\n]*\bof=/dev/")),
    ("redirect onto a block device", re.compile(r">\s*/dev/(?:sd|nvme|hd)")),
    ("fork bomb", re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")),
    (
        "git history or work-tree destruction",
        re.compile(r"\bgit\s+(?:reset\s+--hard|clean\s+-\S*f|push\s+(?:\S+\s+)*--force)"),
    ),
    ("world-writable chmod on root", re.compile(r"\bchmod\s+(?:-\S+\s+)*[0-7]*777\s+/")),
)


# Quality rules from a study of a 398-skill public collection (see CHANGELOG): signals that
# predicted weaker skills are warnings; a skill that cannot work as written is an error.

# A project skill named like a bundled Claude Code command replaces that command.
# Bundled names as of Claude Code 2.1.302; extend when new built-ins land.
RESERVED_NAMES = frozenset({
    "add-dir", "agents", "bug", "claude-api", "clear", "code-review", "commit", "compact",
    "config", "context", "cost", "doctor", "export", "fewer-permission-prompts", "go", "help",
    "hooks", "init", "keybindings-help", "login", "logout", "loop", "mcp", "memory", "model",
    "permissions", "plugin", "pr", "resume", "review", "rewind", "run", "security-review",
    "settings", "simplify", "status", "terminal", "todos", "update-config", "usage", "verify",
    "vim",
})
_KEYWORD_LIST_RE = re.compile(
    r"\b(?:mentions?|keywords?)\s*:\s*[\w.-]+(?:\s*,\s*[\w.-]+){2,}", re.IGNORECASE
)
_PERSONA_RE = re.compile(
    r"^\s*You are an? (?:expert|seasoned|world-class|senior)\b", re.IGNORECASE | re.MULTILINE
)
_BROCHURE_HEADINGS = frozenset({
    "overview", "core capabilities", "best practices", "common pitfalls", "installation",
    "features", "introduction",
})
# A resource path at a token start, after ${CLAUDE_SKILL_DIR}/, or after a leading ./
_REL_START = r"(?:(?<![\w/.-])|(?<=\$\{CLAUDE_SKILL_DIR\}/)|(?<=(?<![\w/.-])\./))"
_IGNORED_FILES = frozenset({".DS_Store", "Thumbs.db"})
_RESOURCE_PATH_RE = re.compile(
    _REL_START + r"((?:scripts|references|assets)/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+)"
)
_FENCE_RE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})[^\n]*\n(.*?)^[ \t]*\1[ \t]*$", re.MULTILINE | re.DOTALL
)
_SCRIPT_CALL_RE = re.compile(
    _REL_START + r"(scripts/[A-Za-z0-9_./-]+\.py)((?:\s+[^\s|;&>#]+)*)"
)
# What may precede a script path for the line to run it: a command start, an optional
# `uv run` and Python interpreter, and the skill-dir prefix. A path in a string literal
# (`Path("scripts/x.py")`) is only named, so it gets the path checks, not missing-script.
_INVOCATION_PREFIX_RE = re.compile(
    r"(?:.*(?:[;|]|&&)\s*)?(?:\$\s+)?(?:uv\s+run\s+)?(?:(?:python3?|py)(?:\s+-\S+)*\s*)?"
    r"[\"']?(?:\$\{CLAUDE_SKILL_DIR\}/|\./)?\s*"
)


@dataclass
class Finding:
    rule: str
    severity: str  # "error" or "warning"
    path: str  # POSIX, relative to the skill directory
    message: str
    line: int | None = None


@dataclass
class Report:
    skill: str
    skill_dir: Path
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    def to_dict(self) -> dict:
        return {
            "skill": self.skill,
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "findings": [asdict(f) for f in self.findings],
        }

    def to_markdown(self) -> str:
        verdict = "PASS" if self.ok else "FAIL"
        lines = [
            f"# Validation: {self.skill}",
            "",
            f"Result: **{verdict}** ({self.errors} errors, {self.warnings} warnings)",
            "",
        ]
        if not self.findings:
            lines.append("No findings.")
        for f in self.findings:
            where = f.path if f.line is None else f"{f.path}:{f.line}"
            message = " ".join(f.message.split())
            lines.append(f"- **{f.severity}** `{f.rule}` {where}: {message}")
        return "\n".join(lines) + "\n"

    def write(self, factory_dir: Path) -> tuple[Path, Path]:
        """Write validate.json and validate.md into factory_dir and return their paths."""
        factory_dir = Path(factory_dir)
        factory_dir.mkdir(parents=True, exist_ok=True)
        json_path = factory_dir / "validate.json"
        md_path = factory_dir / "validate.md"
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        with json_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        with md_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(self.to_markdown())
        return json_path, md_path


def _read_capped(path: Path) -> bytes:
    """Read at most MAX_FILE_BYTES + 1 bytes so a huge file is never fully loaded."""
    with path.open("rb") as handle:
        return handle.read(MAX_FILE_BYTES + 1)


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    """Return (frontmatter block, body), or None when SKILL.md has no opening delimiter."""
    lines = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].rstrip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].rstrip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise ValueError("frontmatter is not closed by a '---' line")


def _check_skill_md(skill_dir: Path, findings: list[Finding]) -> None:
    path = skill_dir / "SKILL.md"
    if path.is_symlink() or not path.is_file():
        return  # reported by the tree walk (symlink) or absent
    try:
        text = _read_capped(path).decode("utf-8")
    except UnicodeDecodeError:
        findings.append(
            Finding("frontmatter-invalid", "error", "SKILL.md", "SKILL.md is not valid UTF-8")
        )
        return
    try:
        parts = _split_frontmatter(text)
    except ValueError as exc:
        findings.append(Finding("frontmatter-invalid", "error", "SKILL.md", str(exc)))
        return
    if parts is None:
        findings.append(
            Finding(
                "frontmatter-missing",
                "error",
                "SKILL.md",
                "SKILL.md must start with a '---' frontmatter block",
            )
        )
        return
    block, body = parts
    try:
        meta = yamlish.loads(block)
    except ValueError as exc:
        findings.append(
            Finding("frontmatter-invalid", "error", "SKILL.md", f"cannot parse frontmatter: {exc}")
        )
        return

    def add(rule: str, message: str, severity: str = "error") -> None:
        findings.append(Finding(rule, severity, "SKILL.md", message))

    name = meta.get("name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        add("name-invalid", f"name {name!r} must be kebab-case (a-z, 0-9, hyphens)")
    elif len(name) > MAX_NAME:
        add("name-too-long", f"name is {len(name)} characters, the limit is {MAX_NAME}")
    if isinstance(name, str) and name and name != skill_dir.name:
        add("name-mismatch", f"name {name!r} does not match the directory name {skill_dir.name!r}")
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        add("description-empty", "description must be a non-empty string")
    elif len(description) > MAX_DESCRIPTION:
        add(
            "description-too-long",
            f"description is {len(description)} characters, the limit is {MAX_DESCRIPTION}",
        )
    body_lines = len(body.strip("\n").splitlines())
    if body_lines > MAX_BODY_LINES:
        add(
            "body-too-long",
            f"body is {body_lines} lines, the recommended limit is {MAX_BODY_LINES}",
            "warning",
        )
    _check_quality(skill_dir, name, description, body, add)


def _resource_files(skill_dir: Path) -> dict[str, list[str]]:
    """Regular files under each existing resource directory, as POSIX paths; no symlinks."""
    found: dict[str, list[str]] = {}
    for resource in RESOURCE_DIRS:
        root = skill_dir / resource
        if root.is_symlink() or not root.is_dir():
            continue
        files: list[str] = []
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not (Path(current) / d).is_symlink())
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for entry in sorted(filenames):
                path = Path(current) / entry
                if entry in _IGNORED_FILES or entry.endswith(".pyc"):
                    continue
                if path.is_file() and not path.is_symlink():
                    files.append(path.relative_to(skill_dir).as_posix())
        found[resource] = files
    return found


def _command_args(rest: str) -> list[str]:
    """The arguments after a script path, quoted the way the shell reads them, up to the first
    operator (`|`, `;`, `&&`, `>`) or comment. A closing quote around the path is dropped."""
    if rest[:1] in ("\"", "'"):
        rest = rest[1:]
    lexer = shlex.shlex(rest, posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    args: list[str] = []
    try:
        for token in lexer:
            if set(token) <= set(";&|<>"):
                break
            args.append(token)
    except ValueError:  # an unbalanced quote: there is no reliable argument list to check
        return []
    return args


def _script_options(path: Path) -> set[str] | None:
    """Option strings a Python script declares with add_argument; parsed, never executed."""
    try:
        tree = ast.parse(_read_capped(path).decode("utf-8"), filename=str(path))
    except (SyntaxError, ValueError):
        return None
    options = {"-h", "--help"}
    declared = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            declared = True
            if any(not isinstance(arg, ast.Constant) for arg in node.args):
                return None  # a name set at run time (`add_argument(flag)`): unknown, not absent
            if any(
                kw.arg == "nargs" and ast.unparse(kw.value) in ("argparse.REMAINDER", "REMAINDER", "'...'")
                for kw in node.keywords
            ):
                return None  # REMAINDER takes option-looking words too; nothing can be flagged
            flags = [
                arg.value
                for arg in node.args
                if isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and arg.value.startswith("-")
            ]
            options.update(flags)
            if any(
                kw.arg == "action" and ast.unparse(kw.value).endswith("BooleanOptionalAction")
                for kw in node.keywords
            ):
                options.update(f"--no-{f[2:]}" for f in flags if f.startswith("--"))
        if (
            isinstance(node, ast.Call)
            and any(kw.arg == "parents" for kw in node.keywords)
        ):
            return None  # options inherited from another parser cannot be listed here
    # A script that parses its arguments another way (sys.argv, getopt, click) is not checked.
    return options if declared else None


def _inside(skill_dir: Path, rel: str) -> Path | None:
    """The path for ``rel`` if it stays inside the skill with no symlinked part, else None."""
    parts = Path(rel).parts
    if not parts or ".." in parts or Path(rel).is_absolute():
        return None
    current = skill_dir
    for part in parts:
        current = current / part
        if current.is_symlink():
            return None
    return current


def _imported_modules(path: Path) -> set[str]:
    """Top-level module names a Python script imports; parsed, never executed."""
    try:
        tree = ast.parse(_read_capped(path).decode("utf-8"), filename=str(path))
    except (SyntaxError, ValueError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


def _check_quality(skill_dir: Path, name, description, body: str, add) -> None:
    if isinstance(name, str) and name in RESERVED_NAMES:
        add("reserved-name", f"{name!r} is a bundled Claude Code command; a skill with this "
            "name replaces it for everyone who installs it")
    # A missing or over-long description is already an error; one finding per problem.
    if isinstance(description, str) and description.strip() and len(description) <= MAX_DESCRIPTION:
        if not NEGATIVE_RE.search(description):
            add("description-no-boundary", "description does not say what the skill is NOT "
                "for or refuses; Claude reads only the description when choosing a skill",
                "warning")
        if _KEYWORD_LIST_RE.search(description):
            add("description-keyword-list", "description ends in a list of loose keywords, which "
                "makes the skill load on unrelated prompts; describe whole requests instead",
                "warning")
    if _PERSONA_RE.search(body):
        add("persona-opener", "'You are an expert...' openers predicted weaker skills; state the "
            "job and what the skill refuses", "warning")
    for heading in re.findall(r"^##\s+(.+?)\s*$", body, re.MULTILINE):
        if heading.strip().lower() in _BROCHURE_HEADINGS:
            add("brochure-heading", f"'## {heading}' is a brochure heading; the strongest skills "
                "use workflow, rules and done-when sections instead", "warning")

    files = _resource_files(skill_dir)
    named = {rel for rel in _RESOURCE_PATH_RE.findall(body) if _inside(skill_dir, rel)}
    commands: list[tuple[str, list[str]]] = []
    for block in _FENCE_RE.finditer(body):
        for line in block.group(2).splitlines():
            for match in _SCRIPT_CALL_RE.finditer(line):
                if _INVOCATION_PREFIX_RE.fullmatch(line[: match.start(1)].strip()):
                    commands.append((match.group(1), _command_args(line[match.end(1) :])))
    run_scripts = {rel for rel, _ in commands}
    for rel in sorted(named - run_scripts):
        if not (skill_dir / rel).is_file():
            # A prose path may be a file the skill writes at run time.
            add("phantom-path", f"SKILL.md mentions {rel}, which does not exist; fine if the "
                "skill creates it, otherwise fix the path", "warning")
    imported: dict[str, set[str]] = {}
    for rel in sorted(named):
        path = _inside(skill_dir, rel)
        if rel.endswith(".py") and path is not None and path.is_file():
            imported.setdefault(str(Path(rel).parent.as_posix()), set()).update(
                _imported_modules(path)
            )
    for resource, paths in files.items():
        if not paths:
            add("empty-resource-dir", f"{resource}/ is empty; add the files the skill needs or "
                "remove the directory", "warning")
        for rel in paths:
            helper = Path(rel)
            if rel in named or (
                helper.suffix == ".py" and helper.stem in imported.get(helper.parent.as_posix(), ())
            ):
                continue
            add("orphan-file", f"{rel} is never named in SKILL.md, so the skill "
                "cannot tell the model to use it", "warning")
    reported: set[str] = set()
    for rel, args in commands:
        script = _inside(skill_dir, rel)
        if script is None:
            continue  # outside the skill or through a symlink: never read
        if not script.is_file():
            if rel not in reported:
                reported.add(rel)
                add("missing-script", f"a documented command runs {rel}, which does not exist")
            continue
        options = _script_options(script)
        if options is None:
            continue
        for arg in args:
            if arg == "--":
                break  # argparse: every word after a standalone -- is positional
            if arg.startswith("--") or re.fullmatch(r"-[A-Za-z]", arg):
                flag = arg.split("=", 1)[0]
                # argparse accepts any unambiguous prefix of a long option by default.
                abbreviates = flag.startswith("--") and any(
                    o.startswith(flag) for o in options if o.startswith("--")
                )
                if flag not in options and not abbreviates:
                    add("script-flag-missing", f"{rel} declares no option {flag}, so "
                        "the documented command fails")


def _redacted(label: str) -> str:
    return f"possible {label}; matched value redacted"


def _scan_text(
    rel: str, text: str, patterns, rule: str, findings: list[Finding], budget: list[int]
) -> None:
    """Match each pattern per line; budget[0] counts findings left for this file."""
    for number, line in enumerate(text.splitlines(), start=1):
        for label, pattern in patterns:
            if budget[0] <= 0:
                return
            if pattern.search(line):
                budget[0] -= 1
                message = (
                    _redacted(label)
                    if rule == "secret"
                    else f"{label} is not allowed in skill scripts"
                )
                findings.append(Finding(rule, "error", rel, message, number))


def _check_file(path: Path, rel: str, top: str, findings: list[Finding]) -> int:
    """Check one regular file and return its size in bytes."""
    size = path.lstat().st_size
    if size > MAX_FILE_BYTES:
        findings.append(
            Finding(
                "file-too-large",
                "error",
                rel,
                f"file is {size} bytes, the limit is {MAX_FILE_BYTES}",
            )
        )
        return size
    data = _read_capped(path)
    if b"\0" in data[:_BINARY_SNIFF]:
        return size
    text = data.decode("utf-8", errors="replace")
    budget = [_MAX_FINDINGS_PER_FILE]
    _scan_text(rel, text, _SECRET_PATTERNS, "secret", findings, budget)
    if top == "scripts":
        _scan_text(rel, text, _NETWORK_PATTERNS, "network-call", findings, budget)
        _scan_text(rel, text, _DESTRUCTIVE_PATTERNS, "destructive-shell", findings, budget)
    return size


def _walk(skill_dir: Path, findings: list[Finding]) -> None:
    total = 0
    for current, dirnames, filenames in os.walk(skill_dir, followlinks=False):
        here = Path(current)
        if here == skill_dir:
            dirnames[:] = [d for d in dirnames if d != FACTORY_DIR or (here / d).is_symlink()]
        dirnames.sort()
        for entry in sorted(dirnames) + sorted(filenames):
            path = here / entry
            rel = path.relative_to(skill_dir).as_posix()
            top = rel.split("/", 1)[0]
            if top not in ALLOWED_TOP_LEVEL and "/" not in rel:
                findings.append(
                    Finding(
                        "path-not-allowed",
                        "error",
                        rel,
                        f"top-level entry {rel!r} is not SKILL.md, scripts/, references/, "
                        "assets/ or evals/",
                    )
                )
            if path.is_symlink():
                findings.append(
                    Finding("path-not-allowed", "error", rel, "symlinks are not allowed in a skill")
                )
            elif not stat.S_ISDIR(path.lstat().st_mode):
                if not stat.S_ISREG(path.lstat().st_mode):
                    findings.append(Finding("path-not-allowed", "error", rel, "not a regular file"))
                else:
                    total += _check_file(path, rel, top, findings)
        # os.walk lists symlinked directories in dirnames without descending into them.
    if total > MAX_SKILL_BYTES:
        findings.append(
            Finding(
                "skill-too-large",
                "error",
                ".",
                f"skill is {total} bytes, the limit is {MAX_SKILL_BYTES}",
            )
        )


def validate_skill(skill_dir: Path) -> Report:
    """Statically validate a skill directory. Raises FileNotFoundError if it is missing."""
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir():
        raise FileNotFoundError(f"skill directory not found: {skill_dir}")
    findings: list[Finding] = []
    _check_skill_md(skill_dir, findings)
    _walk(skill_dir, findings)
    return Report(skill=skill_dir.name, skill_dir=skill_dir, findings=findings)


def add_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "validate",
        help="statically validate skills/<name>/ and write a report",
        description="Check frontmatter, naming, paths, sizes and forbidden patterns without executing anything.",
    )
    parser.add_argument("name", help="skill name (the directory under skills/)")
    parser.add_argument(
        "--out", type=Path, default=None, help="root directory containing skills/ (default: cwd)"
    )


def _fail(message: str) -> int:
    print(f"skillz: error: {message}", file=sys.stderr)
    return 2


def run(args: argparse.Namespace) -> int:
    name = args.name
    if not name or name in (".", "..") or Path(name).name != name or "\\" in name:
        return _fail(f"invalid skill name {name!r}")
    root = args.out if args.out is not None else Path.cwd()
    skill_dir = Path(root) / "skills" / name
    if not skill_dir.is_dir():
        return _fail(f"skill directory not found: {skill_dir}")
    if not (skill_dir / "SKILL.md").exists():
        return _fail(f"SKILL.md not found in {skill_dir}")
    factory_dir = skill_dir / FACTORY_DIR
    if factory_dir.is_symlink():
        return _fail(f"{factory_dir} must not be a symlink")
    report = validate_skill(skill_dir)
    json_path, md_path = report.write(factory_dir)
    verdict = "ok" if report.ok else "FAILED"
    print(f"validate {name}: {verdict} ({report.errors} errors, {report.warnings} warnings)")
    print(json_path)
    print(md_path)
    return 0 if report.ok else 1
