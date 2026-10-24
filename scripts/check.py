#!/usr/bin/env python3
"""check.py - the only thing in skillz-factory that says PASS.

Runs every repo gate over skills/<name>/ and prints one line per finding, then
PASS or FAIL as the last line. Stdlib only. Each check's docstring names the
failure (from docs/research/claude-skills-study.md) that it exists to prevent.

Usage:
  python3 scripts/check.py                 # all checks over this repo
  python3 scripts/check.py --root DIR      # another tree (used by the self-tests)
  python3 scripts/check.py --only contract,cases
  python3 scripts/check.py --json          # findings as JSON on stdout

Exit codes and last line: 0 PASS · 1 FAIL (at least one finding) · 2 ERROR,
could not run (bad arguments, missing root, a check that crashed).

Allowlist: scripts/allowlist.txt, one entry per line,
`<check> <path> <text in the finding's message>  # reason`. An entry suppresses
only findings of that check, on that path, whose message contains the text. An
entry without a reason, with an unknown check, or that suppresses nothing is
itself a finding.
"""
import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

# Claude Code skill frontmatter keys (code.claude.com/docs/en/skills, 2026-10-24).
# Claude Code ignores unknown keys silently, so a typo would vanish without this.
ALLOWED_KEYS = {
    "name", "description", "when_to_use", "argument-hint", "arguments",
    "disable-model-invocation", "user-invocable", "allowed-tools", "disallowed-tools",
    "model", "effort", "context", "agent", "background", "hooks", "paths", "shell",
    "metadata", "license", "compatibility",
}
LIST_KEYS = {"allowed-tools", "disallowed-tools", "paths", "arguments"}
LISTING_CAP = 1536  # description + when_to_use are truncated here in the skill listing

# A project skill or command replaces a bundled one of the same name. Bundled
# names as of Claude Code 2.1.302 (installed binary and session skill list);
# extend this set when new built-ins land.
RESERVED = {
    "add-dir", "agents", "bug", "claude-api", "clear", "code-review", "commit",
    "compact", "config", "context", "cost", "doctor", "export",
    "fewer-permission-prompts", "go", "help", "hooks", "init", "keybindings-help",
    "login", "logout", "loop", "mcp", "memory", "model", "permissions", "plugin",
    "pr", "resume", "review", "rewind", "run", "security-review", "settings",
    "simplify", "status", "terminal", "todos", "update-config", "usage", "verify",
    "vim",
}
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# Capital NOT is case-sensitive so an incidental lowercase "not" does not count.
BOUNDARY_RE = re.compile(
    r"\bNOT\b|(?i:\bnot for\b|\bdo not use\b|\bdon't use\b|\brefuses?\b|\bdistinct from\b"
    r"|\b(?:use|see) [a-z0-9-]+ instead\b)"
)
BROCHURE_H2 = {"overview", "core capabilities", "best practices", "common pitfalls",
               "installation", "features", "description", "introduction"}
PERSONA_RE = re.compile(r"^\s*You are an? (expert|seasoned|world-class)", re.IGNORECASE | re.MULTILINE)
COUNT_RE = re.compile(r"\b\d+\s+(skills|agents|commands|scripts)\b", re.IGNORECASE)
NEGATIVE_RE = re.compile(r"\b(NOT|never|must not|does not|doesn't|should not)\b")
SKILL_DIR = "${CLAUDE_SKILL_DIR}/"
_REL_START = r"(?:(?<![\w/.-])|(?<=\$\{CLAUDE_SKILL_DIR\}/))"
PATH_RE = re.compile(_REL_START + r"((?:scripts|references|assets|evals)/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+)")
# A script reference inside a command: at a token start, or right after ${CLAUDE_SKILL_DIR}/.
# `../other-skill/scripts/x.py` belongs to another skill and is not resolved here.
SCRIPT_REF_RE = re.compile(r"(?:(?<![^\s\"'(=])|(?<=\$\{CLAUDE_SKILL_DIR\}/))(scripts/[A-Za-z0-9_./-]+\.py)((?:\s+[^\s|;&>#]+)*)")
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n(.*?)^[ \t]*\1[ \t]*$", re.MULTILINE | re.DOTALL)
MAX_LINES = 500  # Claude Code's stated ceiling for SKILL.md
RUN_TIMEOUT = 30

ALL_CHECKS = ["found", "frontmatter", "reserved-name", "boundary", "contract",
              "entrypoint", "reachability", "cases", "evals", "cruft", "duplicates"]


class Findings:
    def __init__(self):
        self.items = []

    def add(self, check, path, message):
        self.items.append({"check": check, "path": str(path).replace("\\", "/"), "message": message})


# ---------------------------------------------------------------- parsing helpers

class FrontmatterError(ValueError):
    pass


def split_frontmatter(text):
    if not text.startswith("---\n"):
        raise FrontmatterError("file must start with a '---' frontmatter block")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise FrontmatterError("frontmatter block is not closed with '---'")
    return text[4:end], text[end + 5:]


def _scalar(raw):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    if raw[:1] in "[{&*!%@`":
        raise FrontmatterError(f"unsupported YAML value: {raw[:30]!r}")
    return raw


def parse_frontmatter(block):
    """Strict YAML subset: scalars, quoted strings, >/| block scalars, `- item`
    lists for list keys, and one level of `key: value` under `metadata`."""
    data, lines, i = {}, block.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[0] in " \t":
            raise FrontmatterError(f"unexpected indentation: {line.strip()[:40]!r}")
        m = re.match(r"^([A-Za-z_][\w-]*):(.*)$", line)
        if not m:
            raise FrontmatterError(f"not a 'key: value' line: {line[:40]!r}")
        key, rest = m.group(1), m.group(2).strip()
        if key in data:
            raise FrontmatterError(f"duplicate key: {key}")
        i += 1
        body = []
        while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
            body.append(lines[i])
            i += 1
        while body and not body[-1].strip():
            body.pop()
        if rest in (">", ">-", "|", "|-"):
            parts = [b.strip() for b in body]
            data[key] = (" " if rest.startswith(">") else "\n").join(parts).strip()
        elif rest == "":
            if key == "metadata":
                sub = {}
                for b in body:
                    if not b.strip():
                        continue
                    sm = re.match(r"^\s+([A-Za-z_][\w-]*):(.*)$", b)
                    if not sm:
                        raise FrontmatterError(f"metadata line is not 'key: value': {b.strip()[:40]!r}")
                    sub[sm.group(1)] = _scalar(sm.group(2))
                data[key] = sub
            elif key in LIST_KEYS:
                items = []
                for b in body:
                    if not b.strip():
                        continue
                    lm = re.match(r"^\s+-\s+(.*)$", b)
                    if not lm:
                        raise FrontmatterError(f"{key} list item must be '- value'")
                    items.append(_scalar(lm.group(1)))
                data[key] = items
            else:
                raise FrontmatterError(f"{key}: empty value (or a nested map this parser does not accept)")
        else:
            if body:
                raise FrontmatterError(f"{key}: continuation lines need a '>' or '|' block scalar")
            data[key] = _scalar(rest)
    return data


def fenced_blocks(text):
    """Bodies of ``` and ~~~ fences, including fences indented under list items."""
    return [m.group(2) for m in FENCE_RE.finditer(text)]


def parse_script(path):
    """AST of a script; raises ValueError (incl. UnicodeDecodeError) or SyntaxError."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def script_options(path):
    """Option strings declared via add_argument(...) in a Python script."""
    tree = parse_script(path)
    opts = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("-"):
                    opts.add(a.value)
    return opts | {"-h", "--help"}


def has_entrypoint(path):
    tree = parse_script(path)
    imports_argparse = any(
        (isinstance(n, ast.Import) and any(a.name == "argparse" for a in n.names))
        or (isinstance(n, ast.ImportFrom) and n.module == "argparse")
        for n in ast.walk(tree))
    main_guard = any(
        isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
        and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__"
        for n in tree.body)
    return imports_argparse, main_guard


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def documented_exit_codes(text):
    """{'scripts/x.py': {0, 2, ...}} from the exit-code column of markdown tables.
    Only tables whose header has a column mentioning "exit" count, so a script
    named in a references table or a cell with other numbers is not misread."""
    out, lines, i = {}, text.splitlines(), 0
    while i < len(lines) - 1:
        head, sep = lines[i].strip(), lines[i + 1].strip()
        if head.startswith("|") and re.match(r"^\|?\s*:?-{3,}", sep):
            cols = [c.lower() for c in _cells(head)]
            exit_col = next((n for n, c in enumerate(cols) if "exit" in c), None)
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = _cells(lines[i])
                m = re.search(r"(scripts/[A-Za-z0-9_./-]+\.py)", cells[0])
                if m and exit_col is not None and exit_col < len(cells):
                    codes = {int(n) for n in re.findall(r"\b(\d{1,3})\b", cells[exit_col])}
                    out.setdefault(m.group(1), set()).update(codes)
                i += 1
        else:
            i += 1
    return out


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=RUN_TIMEOUT)


# ---------------------------------------------------------------- the checks

class Skill:
    def __init__(self, root, folder):
        self.root, self.dir = root, folder
        self.rel = folder.relative_to(root).as_posix()
        self.md_path = folder / "SKILL.md"
        raw = self.md_path.read_bytes()
        self.fm, self.fm_error = {}, None
        try:
            self.text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            self.text = raw.decode("utf-8", "replace")
            self.fm_error = f"SKILL.md is not valid UTF-8 ({e.reason} at byte {e.start})"
        self.text = self.text.replace("\r\n", "\n")  # Windows line endings are not a defect
        if self.text.startswith("\ufeff"):
            self.text = self.text[1:]
            self.fm_error = self.fm_error or "SKILL.md starts with a UTF-8 BOM; save it as UTF-8 without a BOM"
        self.body = self.text
        if not self.fm_error:
            try:
                block, self.body = split_frontmatter(self.text)
                self.fm = parse_frontmatter(block)
            except FrontmatterError as e:
                self.fm_error = str(e)
        self.scripts = sorted(p for p in (folder / "scripts").glob("*.py")) if (folder / "scripts").is_dir() else []

    def path(self, sub=""):
        return f"{self.rel}/{sub}" if sub else f"{self.rel}/SKILL.md"


def check_found(root, skills, f):
    """Refuse to pass a tree with nothing in it. A gate that scans zero files
    reports green while checking nothing (the source repo's compileall step and
    its PR audits, which found 7 of 398 skills)."""
    if not skills:
        f.add("found", "skills/", "no skills/<name>/SKILL.md found; refusing to pass with nothing to check")


def check_frontmatter(root, skills, f):
    """Parse frontmatter strictly. Claude Code loads a skill with broken YAML as
    empty metadata and silently ignores unknown keys, so neither fails loudly."""
    for s in skills:
        if s.fm_error:
            f.add("frontmatter", s.path(), s.fm_error)
            continue
        for k in s.fm:
            if k not in ALLOWED_KEYS:
                f.add("frontmatter", s.path(), f"unknown key '{k}' (Claude Code would ignore it)")
        name = s.fm.get("name", "")
        if not name:
            f.add("frontmatter", s.path(), "missing 'name'")
        elif not NAME_RE.match(name):
            f.add("frontmatter", s.path(), f"name '{name}' is not kebab-case")
        elif name != s.dir.name:
            f.add("frontmatter", s.path(), f"name '{name}' does not match folder '{s.dir.name}'")
        desc = s.fm.get("description", "")
        if not isinstance(desc, str) or not desc.strip():
            f.add("frontmatter", s.path(), "missing 'description'")
        listing = len(str(desc)) + len(str(s.fm.get("when_to_use", "")))
        if listing > LISTING_CAP:
            f.add("frontmatter", s.path(), f"description + when_to_use is {listing} chars; the listing truncates at {LISTING_CAP}")


def check_reserved_name(root, skills, f):
    """A project skill named like a bundled command replaces it for everyone who
    installs this repo (a skill named `verify` replaces Claude Code's own /verify)."""
    for s in skills:
        for n in {s.dir.name, s.fm.get("name", "")} - {""}:
            if n in RESERVED:
                f.add("reserved-name", s.path(), f"'{n}' is a bundled Claude Code command or skill; namespace it")


def check_boundary(root, skills, f):
    """The description must say what the skill refuses or is not for. Strongest
    single separator in the study (73% vs 56% KEEP, p=0.024; 8 of 8 exemplars)."""
    for s in skills:
        desc = str(s.fm.get("description", ""))
        if desc and not BOUNDARY_RE.search(desc):
            f.add("boundary", s.path(), "description has no refusal or NOT-for clause naming what the skill does not do")


def check_contract(root, skills, f):
    """Every command in a fenced block must name a real script and only flags
    that script declares. 11 of 36 engineering-team skills and 11 of 29
    compliance-os calls documented flags their scripts did not have."""
    for s in skills:
        for block in fenced_blocks(s.body):
            for line in block.splitlines():
                for m in SCRIPT_REF_RE.finditer(line):
                    rel, args = m.group(1), m.group(2).split()
                    if not line[:m.start()].endswith(SKILL_DIR):
                        f.add("contract", s.path(), f"use {SKILL_DIR}{rel}: a bare {rel} only works from the skill folder, not once installed")
                    target = s.dir / rel
                    if not target.is_file():
                        f.add("contract", s.path(), f"command names {rel}, which does not exist")
                        continue
                    try:
                        opts = script_options(target)
                    except (SyntaxError, ValueError) as e:
                        f.add("contract", s.path(rel), f"cannot parse: {getattr(e, 'msg', e)}")
                        continue
                    for a in args:
                        if a.startswith("--") or re.match(r"^-[A-Za-z]$", a):
                            flag = a.split("=", 1)[0]
                            if flag not in opts:
                                f.add("contract", s.path(), f"{rel} has no option {flag} (documented: {line.strip()[:70]})")


def check_entrypoint(root, skills, f):
    """Each script must parse arguments and run as a program. tdd-guide shipped
    8 scripts with no entrypoint while its audit called it harness-ready."""
    for s in skills:
        for p in s.scripts:
            rel = p.relative_to(s.dir).as_posix()
            try:
                uses_argparse, guard = has_entrypoint(p)
            except (SyntaxError, ValueError) as e:
                f.add("entrypoint", s.path(rel), f"cannot parse: {getattr(e, 'msg', e)}")
                continue
            if not uses_argparse:
                f.add("entrypoint", s.path(rel), "does not use argparse")
            if not guard:
                f.add("entrypoint", s.path(rel), "has no `if __name__ == \"__main__\":` entrypoint")
            if uses_argparse and guard:
                r = run([sys.executable, rel, "--help"], s.dir)
                if r.returncode != 0 or "usage:" not in r.stdout:
                    f.add("entrypoint", s.path(rel), f"--help exited {r.returncode} or printed no 'usage:'")


def check_reachability(root, skills, f):
    """No orphans and no phantoms. ship-gate's 1,231-line scanner was never named
    in its SKILL.md, so the model wrote the verdict from prose."""
    for s in skills:
        cases_file = s.dir / "evals" / "cases.json"
        cases_text = cases_file.read_bytes().decode("utf-8", "replace") if cases_file.is_file() else ""
        named = set(PATH_RE.findall(s.body))
        for rel in sorted(named):
            if rel.startswith("evals/"):
                continue
            if not (s.dir / rel).exists():
                f.add("reachability", s.path(), f"mentions {rel}, which does not exist")
        for sub in ("scripts", "references", "assets"):
            d = s.dir / sub
            if not d.is_dir():
                continue
            for p in sorted(d.rglob("*")):
                if not p.is_file() or "__pycache__" in p.parts:
                    continue
                rel = p.relative_to(s.dir).as_posix()
                if rel not in named and rel not in cases_text:
                    f.add("reachability", s.path(rel), "not named in SKILL.md (orphan)")


def load_json(path, f, check):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        f.add(check, path, f"unreadable JSON: {e}")
        return None


def check_cases(root, skills, f):
    """Run every script: `--sample --output json` must exit 0 and print a JSON
    object, each case in evals/cases.json must exit as declared, and the exit
    codes in the SKILL.md scripts table must equal the codes the cases exercise.
    Catches verdict scripts that exit 0 on a failing verdict (aeo_audit,
    phase_gate_scorer) and gates proven only by `--help`."""
    for s in skills:
        if not s.scripts:
            continue
        documented = documented_exit_codes(s.body)
        cases_path = s.dir / "evals" / "cases.json"
        cases = load_json(cases_path, f, "cases") if cases_path.is_file() else None
        if cases is None and not cases_path.is_file():
            f.add("cases", s.path("evals/cases.json"), "missing; every script needs declared cases with expected exit codes")
        if cases is not None and not isinstance(cases, list):
            f.add("cases", s.path("evals/cases.json"), "must be a list of cases")
            cases = None
        cases = cases or []
        for p in s.scripts:
            rel = p.relative_to(s.dir).as_posix()
            if rel not in documented:
                f.add("cases", s.path(), f"no scripts-table row with exit codes for {rel}")
            r = run([sys.executable, rel, "--sample", "--output", "json"], s.dir)
            if r.returncode != 0:
                f.add("cases", s.path(rel), f"--sample --output json exited {r.returncode}, expected 0")
            else:
                try:
                    if not isinstance(json.loads(r.stdout), dict):
                        raise ValueError("not an object")
                except ValueError:
                    f.add("cases", s.path(rel), "--sample --output json did not print a JSON object")
            mine = [c for c in cases if isinstance(c, dict) and c.get("script") == rel]
            exercised = {0} if r.returncode == 0 else set()
            for c in mine:
                if not c.get("why"):
                    f.add("cases", s.path("evals/cases.json"), f"a case for {rel} has no 'why'")
                args, want = c.get("args", []), c.get("expect_exit")
                if not isinstance(args, list) or not isinstance(want, int):
                    f.add("cases", s.path("evals/cases.json"), f"a case for {rel} needs 'args' (list) and 'expect_exit' (int)")
                    continue
                got = run([sys.executable, rel, *[str(a) for a in args]], s.dir).returncode
                exercised.add(want)
                if got != want:
                    f.add("cases", s.path("evals/cases.json"), f"{rel} {' '.join(map(str, args))} exited {got}, expected {want}")
            if not any(c.get("expect_exit", 0) != 0 for c in mine):
                f.add("cases", s.path("evals/cases.json"), f"{rel} has no case that must fail (negative control)")
            if rel in documented and documented[rel] != exercised:
                f.add("cases", s.path(), f"{rel}: documented exit codes {sorted(documented[rel])} != exercised {sorted(exercised)}")


def check_evals(root, skills, f):
    """Structural check only (a model is needed to run them): at least 3 prompts,
    each with assertions, and at least one negative assertion. The source repo had
    7 eval files for 398 skills, most of them unassertable prose."""
    for s in skills:
        p = s.dir / "evals" / "evals.json"
        if not p.is_file():
            f.add("evals", s.path("evals/evals.json"), "missing")
            continue
        data = load_json(p, f, "evals")
        if data is None:
            continue
        if not isinstance(data, list) or len(data) < 3:
            f.add("evals", s.path("evals/evals.json"), "needs a list of at least 3 prompts")
            continue
        negatives = 0
        for i, e in enumerate(data):
            asserts = e.get("assertions") if isinstance(e, dict) else None
            if not isinstance(e, dict) or not e.get("prompt") or not asserts:
                f.add("evals", s.path("evals/evals.json"), f"entry {i} needs 'prompt' and non-empty 'assertions'")
                continue
            negatives += sum(1 for a in asserts if NEGATIVE_RE.search(str(a)))
        if negatives == 0:
            f.add("evals", s.path("evals/evals.json"), "no negative assertion (one that says what must NOT happen)")


def check_cruft(root, skills, f):
    """Patterns that predicted worse skills: persona openers (41% vs 62% KEEP,
    p=0.010), brochure headings (0 of 9 good, 4 of 4 bad), oversized files, and
    hand-written counts that drift (READMEs still saying 205 or 348 skills)."""
    for s in skills:
        if PERSONA_RE.search(s.body):
            f.add("cruft", s.path(), "persona opener ('You are an expert...'); state the job and the refusals instead")
        for h in re.findall(r"^##\s+(.+?)\s*$", s.body, re.MULTILINE):
            if h.strip().lower() in BROCHURE_H2:
                f.add("cruft", s.path(), f"brochure heading '## {h}'")
        n = s.text.count("\n")
        if n > MAX_LINES:
            f.add("cruft", s.path(), f"{n} lines; Claude Code's guidance is under {MAX_LINES}")
    for doc in ("README.md", "CLAUDE.md", "CONTRIBUTING.md"):
        p = root / doc
        if p.is_file():
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if COUNT_RE.search(line):
                    f.add("cruft", f"{doc}:{i}", "hand-written count; counts drift, derive or drop them")


def check_duplicates(root, skills, f):
    """Byte-identical skills or scripts drift apart silently (6 identical pairs in
    the source repo's engineering domain alone)."""
    seen = {}
    for s in skills:
        for p in [s.md_path, *s.scripts]:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            rel = p.relative_to(root).as_posix()
            if digest in seen:
                f.add("duplicates", rel, f"identical to {seen[digest]}")
            else:
                seen[digest] = rel


CHECKS = {
    "found": check_found, "frontmatter": check_frontmatter, "reserved-name": check_reserved_name,
    "boundary": check_boundary, "contract": check_contract, "entrypoint": check_entrypoint,
    "reachability": check_reachability, "cases": check_cases, "evals": check_evals,
    "cruft": check_cruft, "duplicates": check_duplicates,
}


# ---------------------------------------------------------------- allowlist

def load_allowlist(path):
    entries = []
    if not path.is_file():
        return entries
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        body, _, reason = line.partition("#")
        parts = body.split(None, 2)
        entries.append({"line": n, "check": parts[0] if parts else "",
                        "path": parts[1] if len(parts) > 1 else "",
                        "text": parts[2].strip() if len(parts) > 2 else "", "reason": reason.strip()})
    return entries


def apply_allowlist(findings, entries, f_out):
    kept, used = [], set()
    for item in findings:
        hit = next((i for i, e in enumerate(entries)
                    if e["check"] == item["check"] and e["path"] == item["path"] and e["reason"]
                    and e["text"] and e["text"] in item["message"]), None)
        if hit is None:
            kept.append(item)
        else:
            used.add(hit)
    for i, e in enumerate(entries):
        where = f"scripts/allowlist.txt:{e['line']}"
        if e["check"] not in CHECKS:
            f_out.add("allowlist", where, f"unknown check '{e['check']}'")
        elif not e["reason"] or not e["path"] or not e["text"]:
            f_out.add("allowlist", where, "entry needs '<check> <path> <text in the message>  # reason'")
        elif i not in used:
            f_out.add("allowlist", where, f"stale entry: '{e['check']} {e['path']}' suppresses nothing; delete it")
    return kept


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the skillz-factory gates. Exit 0 PASS, 1 FAIL, 2 could not run.")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help="repo root (default: this repo)")
    ap.add_argument("--only", default="", help="comma-separated subset of: " + ", ".join(ALL_CHECKS))
    ap.add_argument("--json", action="store_true", help="print findings as JSON")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows pipes default to cp1252
        stream.reconfigure(encoding="utf-8", errors="replace")

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"check: could not run: root {root} is not a directory", file=sys.stderr)
        return 2
    selected = [c.strip() for c in args.only.split(",") if c.strip()] or ALL_CHECKS
    unknown = [c for c in selected if c not in CHECKS]
    if unknown:
        print(f"check: could not run: unknown check(s) {', '.join(unknown)}", file=sys.stderr)
        return 2
    try:
        entries = load_allowlist(root / "scripts" / "allowlist.txt")
    except OSError as e:
        print(f"check: could not run: allowlist unreadable: {e}", file=sys.stderr)
        return 2

    skills_dir = root / "skills"
    folders = sorted(p.parent for p in skills_dir.glob("*/SKILL.md")) if skills_dir.is_dir() else []
    skills = [Skill(root, p) for p in folders]

    raw, crashed = Findings(), []
    for name in ["found", *[c for c in selected if c != "found"]]:
        try:
            CHECKS[name](root, skills, raw)
        except subprocess.TimeoutExpired as e:
            raw.add(name, " ".join(map(str, e.cmd))[:80], f"timed out after {RUN_TIMEOUT}s")
        except Exception as e:  # a gate bug must not look like a verdict
            crashed.append(f"{name}: {type(e).__name__}: {e}")
    # Judge allowlist staleness only for the checks that ran.
    entries = [e for e in entries if not (e["check"] in CHECKS and e["check"] not in selected and e["check"] != "found")]
    final = Findings()
    kept = apply_allowlist(raw.items, entries, final)
    final.items = kept + final.items

    verdict = "ERROR" if crashed else ("FAIL" if final.items else "PASS")
    if args.json:
        print(json.dumps({"verdict": verdict, "skills": len(skills), "findings": final.items,
                          "crashed": crashed}, indent=2))
    else:
        for item in final.items:
            print(f"{item['check']}: {item['path']}: {item['message']}")
        for c in crashed:
            print(f"check crashed, could not run: {c}")
        print(f"checked {len(skills)} skill(s) with: {', '.join(['found'] + [c for c in selected if c != 'found'])}")
        print(verdict)
    if crashed:
        return 2
    return 1 if final.items else 0


if __name__ == "__main__":
    sys.exit(main())
