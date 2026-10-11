#!/usr/bin/env python3
"""BRIEF.yaml -> .claude/state/brief.json. Stdlib only; uses PyYAML when importable.

Subcommands:
  validate [--brief BRIEF.yaml]   parse, validate, write brief.json and started_at, exit 0
  show                            print the current brief.json

Contract: docs/design/contracts.md, section "BRIEF.yaml -> brief.json".
Set QS_NO_YAML=1 to force the built-in strict-subset reader even when PyYAML is installed
(both readers must produce identical brief.json for the same input).
"""
import argparse
import json
import os
import subprocess
import re
import sys
from datetime import datetime, timezone


class BriefError(Exception):
    """Carries the dotted key path that failed validation, for a one-line stderr message."""

    def __init__(self, key, msg=None):
        super().__init__(msg or f"missing {key}")
        self.key = key
        self.msg = msg or f"missing {key}"


# --- paths -------------------------------------------------------------------

def repo_root():
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    if root:
        return root
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip().replace("\r", "")
    except Exception:
        return os.getcwd()


def state_dir():
    return os.path.join(repo_root(), ".claude", "state")


def brief_json_path():
    return os.path.join(state_dir(), "brief.json")


def started_at_path():
    return os.path.join(state_dir(), "started_at")


# --- built-in strict-subset YAML reader ---------------------------------------
# Accepts: block mappings (2-space indent), scalar values, block lists of scalars
# ("- item"), block lists of single-line flow maps ("- {k: v, k2: v2}"). Strings
# may be quoted with " or '. No anchors, multi-line scalars or nested flow.

def _parse_scalar(s):
    s = s.strip()
    if s == "":
        return None
    if s.startswith("{") and s.endswith("}"):
        return _parse_flow_map(s)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    if s == "true":
        return True
    if s == "false":
        return False
    if s in ("null", "~"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _split_respecting_quotes(s, sep):
    parts, cur, quote = [], [], None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch == sep:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _parse_flow_map(s):
    s = s.strip()
    if not (s.startswith("{") and s.endswith("}")):
        raise ValueError(f"expected a flow map: {s!r}")
    inner = s[1:-1]
    result = {}
    for part in _split_respecting_quotes(inner, ","):
        part = part.strip()
        if not part:
            continue
        key, _, val = part.partition(":")
        result[key.strip()] = _parse_scalar(val.strip())
    return result


def _strip_comment(line):
    # A comment starts at a '#' that is at the line start or preceded by whitespace, outside quotes.
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _tokenize(text):
    lines = []
    for raw_line in text.splitlines():
        line = _strip_comment(raw_line).rstrip()
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        lines.append((indent, stripped))
    return lines


def _parse_block(lines, pos, indent):
    if pos >= len(lines) or lines[pos][0] < indent:
        return None, pos
    cur_indent, content = lines[pos]
    if content.startswith("- ") or content == "-":
        result = []
        while pos < len(lines):
            cur_indent, content = lines[pos]
            if cur_indent != indent or not (content == "-" or content.startswith("- ")):
                break
            item = content[1:].strip()
            pos += 1
            if item.startswith("{"):
                result.append(_parse_flow_map(item))
            elif item == "":
                value, pos = _parse_block(lines, pos, indent + 2)
                result.append(value)
            else:
                result.append(_parse_scalar(item))
        return result, pos
    else:
        result = {}
        while pos < len(lines):
            cur_indent, content = lines[pos]
            if cur_indent != indent or content.startswith("- "):
                break
            key, sep, val = content.partition(":")
            if not sep:
                pos += 1
                continue
            key = key.strip()
            val = val.strip()
            pos += 1
            if val == "":
                if pos < len(lines) and lines[pos][0] > cur_indent:
                    value, pos = _parse_block(lines, pos, lines[pos][0])
                else:
                    value = None
                result[key] = value
            else:
                result[key] = _parse_scalar(val)
        return result, pos


def builtin_yaml_load(text):
    lines = _tokenize(text)
    if not lines:
        return {}
    value, _ = _parse_block(lines, 0, lines[0][0])
    return value


def load_yaml(text):
    if os.environ.get("QS_NO_YAML") != "1":
        try:
            import yaml
            return yaml.safe_load(text)
        except ImportError:
            pass
    return builtin_yaml_load(text)


# --- validation ----------------------------------------------------------------

_VALID_KINDS = ("test", "file", "grep", "judge")
_BUDGET_REQUIRED = ("tokens", "cost_usd", "wall_clock_min", "steps")
_BUDGET_DEFAULTS = {"stall_limit": 3, "replan_limit": 5, "critic_rounds": 2}


def _num(v, key):
    import math
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise BriefError(key, f"{key} must be a number > 0")
    return v


def validate_structure(raw):
    if not isinstance(raw, dict):
        raise BriefError("mission", "brief must be a mapping")

    mission = raw.get("mission")
    if not isinstance(mission, dict):
        raise BriefError("mission")
    goal = mission.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise BriefError("mission.goal")
    deliverables = mission.get("deliverables")
    if not isinstance(deliverables, list) or not deliverables or any(not isinstance(p, str) or not p.strip() for p in deliverables):
        raise BriefError("mission.deliverables")
    requirements = mission.get("requirements", [])
    if not isinstance(requirements, list) or any(
            not isinstance(req, str) or not re.fullmatch(r"REQ-[0-9]{3,}", req)
            for req in requirements):
        raise BriefError("mission.requirements", "mission.requirements must be a list of REQ-xxx identifiers")
    # optional: the branch a chained mission starts from and opens its PR against (set by scripts/program.sh)
    base = mission.get("base")
    if base is not None and (not isinstance(base, str) or not re.match(r"^[A-Za-z0-9._/-]+$", base)):
        raise BriefError("mission.base", "mission.base must be a branch name")

    raw_criteria = raw.get("success_criteria")
    if not isinstance(raw_criteria, list) or not raw_criteria:
        raise BriefError("success_criteria")
    criteria = []
    for idx, c in enumerate(raw_criteria):
        prefix = f"success_criteria[{idx}]"
        if not isinstance(c, dict):
            raise BriefError(prefix)
        kind = c.get("kind")
        if kind not in _VALID_KINDS:
            raise BriefError(f"{prefix}.kind", f"unknown kind '{kind}' (want one of {_VALID_KINDS})")
        if kind == "test":
            cmd = c.get("cmd")
            if not isinstance(cmd, str) or not cmd:
                raise BriefError(f"{prefix}.cmd")
            expect = c.get("expect", 0)
            if not isinstance(expect, int) or isinstance(expect, bool):
                raise BriefError(f"{prefix}.expect")
            criteria.append({"kind": "test", "cmd": cmd, "expect": expect})
        elif kind == "file":
            path = c.get("path")
            if not isinstance(path, str) or not path:
                raise BriefError(f"{prefix}.path")
            entry = {"kind": "file", "path": path}
            if c.get("must_contain") is not None:
                entry["must_contain"] = c["must_contain"]
            criteria.append(entry)
        elif kind == "grep":
            pattern, path = c.get("pattern"), c.get("path")
            if not isinstance(pattern, str) or not pattern:
                raise BriefError(f"{prefix}.pattern")
            if not isinstance(path, str) or not path:
                raise BriefError(f"{prefix}.path")
            criteria.append({"kind": "grep", "pattern": pattern, "path": path})
        else:  # judge
            rubric = c.get("rubric")
            if not isinstance(rubric, str) or not rubric:
                raise BriefError(f"{prefix}.rubric")
            criteria.append({"kind": "judge", "rubric": rubric})

    raw_budgets = raw.get("budgets")
    if not isinstance(raw_budgets, dict):
        raise BriefError("budgets")
    budgets = {}
    for key in _BUDGET_REQUIRED:
        if key not in raw_budgets:
            raise BriefError(f"budgets.{key}")
        budgets[key] = _num(raw_budgets[key], f"budgets.{key}")
    for key, default in _BUDGET_DEFAULTS.items():
        budgets[key] = _num(raw_budgets.get(key, default), f"budgets.{key}")

    permissions = raw.get("permissions")
    if not isinstance(permissions, dict):
        raise BriefError("permissions")
    irreversible = permissions.get("irreversible")
    if not isinstance(irreversible, dict):
        raise BriefError("permissions.irreversible")
    default = irreversible.get("default")
    if default not in ("skip-and-record", "allow"):
        raise BriefError("permissions.irreversible.default")
    allow = irreversible.get("allow", [])
    if not isinstance(allow, list) or any(not isinstance(p, str) or not p.strip() for p in allow):
        raise BriefError("permissions.irreversible.allow")
    allow = [str(x) for x in allow]

    ambiguity_policy = raw.get("ambiguity_policy")
    if ambiguity_policy != "choose-default-and-record":
        raise BriefError("ambiguity_policy")

    quality = raw.get("quality", {})
    if not isinstance(quality, dict) or quality.get("profile", "code") not in ("code", "docs"):
        raise BriefError("quality.profile")
    for key in ("lint", "test", "build"):
        val = quality.get(key)
        if val is not None and not (isinstance(val, str) and val.strip() or
                isinstance(val, dict) and set(val) == {"skip"} and
                isinstance(val["skip"], str) and val["skip"].strip()):
            raise BriefError("quality." + key)
    if not isinstance(raw.get("maintenance", False), bool):
        raise BriefError("maintenance")
    return {
        "quality": dict({"profile": "code"}, **quality),
        "maintenance": raw.get("maintenance", False),
        "mission": dict(
            {"goal": goal, "deliverables": list(deliverables)},
            **({"requirements": list(requirements)} if requirements else {}),
            **({"base": base} if base else {}),
        ),
        "success_criteria": criteria,
        "budgets": budgets,
        "permissions": {"irreversible": {"default": default, "allow": allow}},
        "ambiguity_policy": ambiguity_policy,
    }


# --- commands --------------------------------------------------------------

RUNTIME_STATE = ("started_at", "session_id", "steps", "restarts", "stop_attempts", "idem.jsonl",
                 "current_step.json", "tier", "cancel", "force_replan", "transcript_path", "last_run.json")


def reset_runtime_state(sdir):
    import shutil
    for name in RUNTIME_STATE + ("controller.json", "runtime.sqlite3", "runtime.sqlite3-journal", "budget-cache.json"):
        path = os.path.join(sdir, name)
        if os.path.isfile(path):
            os.remove(path)
    legs = os.path.join(sdir, "legs")
    if os.path.isdir(legs):
        shutil.rmtree(legs)


def cmd_validate(args):
    brief_path = args.brief or os.path.join(repo_root(), "BRIEF.yaml")
    try:
        with open(brief_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"brief: cannot read {brief_path}: {e.strerror}", file=sys.stderr)
        return 2

    try:
        raw = load_yaml(text)
    except Exception as e:
        print(f"brief: invalid YAML in {brief_path}: {e}", file=sys.stderr)
        return 2

    try:
        normalized = validate_structure(raw)
    except BriefError as e:
        print(f"brief: {e.msg}", file=sys.stderr)
        return 2

    sdir = state_dir()
    os.makedirs(sdir, exist_ok=True)

    # A new goal is a new mission. The runtime state under .claude/state is gitignored, so it survives a branch
    # switch that removed the previous run's RUN_STATE from the tree; left in place it would hand the new run the
    # old clock, step count, session id and flags. Same goal = resume, state untouched.
    out_path = brief_json_path()
    previous_goal = None
    if os.path.isfile(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                previous_goal = (json.load(f).get("mission") or {}).get("goal")
        except (OSError, ValueError, AttributeError):
            previous_goal = None
    if previous_goal is not None and previous_goal != normalized["mission"]["goal"]:
        reset_runtime_state(sdir)
        print("brief: new mission goal; runtime state reset")

    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(normalized, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, out_path)

    sa_path = started_at_path()
    if not os.path.exists(sa_path):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sa_tmp = sa_path + ".tmp"
        with open(sa_tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(ts + "\n")
        os.replace(sa_tmp, sa_path)

    return 0


def cmd_check(args):
    """Validate brief files without touching run state. Exit 0 if all pass, 2 if any fails."""
    status = 0
    for path in args.files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                validate_structure(load_yaml(f.read()))
            print(f"brief: ok {path}")
        except OSError as e:
            print(f"brief: cannot read {path}: {e.strerror}", file=sys.stderr); status = 2
        except BriefError as e:
            print(f"brief: {path}: {e.msg}", file=sys.stderr); status = 2
        except Exception as e:
            print(f"brief: invalid YAML in {path}: {e}", file=sys.stderr); status = 2
    return status


def cmd_show(args):
    path = brief_json_path()
    if not os.path.exists(path):
        print(f"brief: no active run ({path} missing); run 'validate' first", file=sys.stderr)
        return 2
    with open(path, "r", encoding="utf-8") as f:
        sys.stdout.write(f.read())
    return 0


def main():
    parser = argparse.ArgumentParser(prog="brief.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    pv = sub.add_parser("validate")
    pv.add_argument("--brief", default=None)
    sub.add_parser("show")
    pc = sub.add_parser("check")
    pc.add_argument("files", nargs="+")
    args = parser.parse_args()

    if args.cmd == "validate":
        return cmd_validate(args)
    if args.cmd == "check":
        return cmd_check(args)
    return cmd_show(args)


if __name__ == "__main__":
    sys.exit(main())
