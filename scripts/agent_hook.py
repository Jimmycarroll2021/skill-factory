"""Record security-agent evidence independently of the lead."""
import json
import hashlib
import re
import subprocess
import sys
import runtime


def artifacts(brief, missing_ok=False):
    # missing_ok records an absent deliverable as None: a reviewer may legitimately
    # grade FAIL *because* a deliverable is missing, and that grade must be kept.
    result = {}
    root = runtime.root()
    for name in brief["mission"]["deliverables"]:
        path = (root / name).resolve()
        if path.is_relative_to(root) and not path.exists() and missing_ok:
            result[name] = None
            continue
        if not path.is_relative_to(root) or not path.exists():
            raise ValueError("missing or outside-project review artifact")
        if path.is_file():
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            names = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", name],
                                   cwd=root, capture_output=True, check=True).stdout.decode().split("\0")
            contents = {}
            for filename in filter(None, names):
                entry = root / filename
                if not entry.resolve().is_relative_to(root):
                    raise ValueError("review artifact resolves outside project")
                if entry.is_file():
                    contents[filename] = hashlib.sha256(entry.read_bytes()).hexdigest()
            result[name] = hashlib.sha256(json.dumps(contents, sort_keys=True).encode()).hexdigest()
    return result


def handle(event):
    if not runtime.active():
        return 0
    if event.get("hook_event_name") != "SubagentStop":
        return 0
    if event.get("agent_type") == "reviewer":
        brief = runtime.load(runtime.state() / "controller.json")["brief"]
        text = event.get("last_assistant_message", "")
        with runtime.transaction() as db:
            if not runtime.get(db, "agent:" + str(event.get("agent_id"))):
                return 0
            matches = list(re.finditer(r"^[ \t]*judge\s+(\d+):[ \t]*(PASS|FAIL)(?::[ \t]*(.*))?[ \t]*$", text, re.MULTILINE | re.IGNORECASE))
            for position, match in enumerate(matches):
                index = int(match[1])
                criteria = brief["success_criteria"]
                judges = [i for i, c in enumerate(criteria) if c["kind"] == "judge"]
                # A sole judge numbered zero is unambiguous, even when a file/test precedes it.
                if index == 0 and len(judges) == 1 and criteria[0]["kind"] != "judge":
                    index = judges[0]
                end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
                evidence = (match[3] or text[match.end():end]).strip()
                if evidence and index < len(criteria) and criteria[index]["kind"] == "judge":
                    runtime.put(db, "judge:" + str(index), {"verdict": match[2].upper(), "evidence": evidence,
                        "rubric": criteria[index]["rubric"], "artifacts": artifacts(brief, missing_ok=match[2].upper() == "FAIL"),
                        "agent_id": event.get("agent_id")})
        return 0
    if event.get("agent_type") == "security":
        text = event.get("last_assistant_message", "")
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=runtime.root(),
                             capture_output=True, text=True, check=True).stdout.strip()
        with runtime.transaction() as db:
            reviewed = runtime.get(db, "agent-head:" + str(event.get("agent_id")))
            runtime.put(db, "security", {"verdict": "PASS" if text.strip().startswith("PASS") and reviewed == sha else "FAIL",
                "evidence": text, "head": sha, "agent_id": event.get("agent_id")})
    return 0


def main():
    # On SubagentStop exit 2 forces the subagent to continue. Fail closed on any error,
    # but only once: when Claude Code reports stop_hook_active the subagent is already
    # continuing because of a stop hook, and trapping it again could loop forever.
    event = {}
    try:
        event = json.load(sys.stdin)
        return handle(event)
    except Exception as exc:
        print("agent hook: " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 0 if isinstance(event, dict) and event.get("stop_hook_active") is True else 2


if __name__ == "__main__":
    sys.exit(main())
