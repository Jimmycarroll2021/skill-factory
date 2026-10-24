"""Static validation of a canonical skill directory.

The validator only reads files as bytes and matches compiled regexes against them. It never
imports, executes or spawns anything from the skill and does not follow symlinks.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from skill_factory import yamlish
from skill_factory.capture import MAX_DESCRIPTION, MAX_NAME, NAME_RE, RESOURCE_DIRS

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
