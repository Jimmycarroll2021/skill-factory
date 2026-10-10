#!/usr/bin/env bash
# Lints .claude/settings.json against rules Claude Code silently ignores (verified against the permissions docs):
#  - an allow rule with a wildcard inside its first argument word (e.g. "Bash(bash tests/*)") is dropped
#  - Write(path)/MultiEdit(path)/Glob(path) rules are never consulted; use Edit(path)/Read(path)
#  - every hard rule in CLAUDE.md must have a deny entry, and guard.sh must be wired as a PreToolUse hook
source "$(dirname "$0")/lib.sh"
lint() { "$QS_PYTHON" - "$1" <<'EOF'
import json, re, sys
s = json.load(open(sys.argv[1])); p = s.get("permissions", {}); errs = []
for r in p.get("allow", []):
    m = re.match(r"Bash\((\S+)\s+([^\s)]+)", r)
    if m and "*" in m.group(2) and m.group(2) != "*":
        errs.append(f"allow rule ignored by Claude Code (wildcard in first arg): {r}")
for key in ("allow", "deny"):
    for r in p.get(key, []):
        if re.match(r"(Write|MultiEdit|Glob|NotebookEdit)\(", r):
            errs.append(f"{key} rule never consulted (use Edit/Read): {r}")
deny = " ".join(p.get("deny", []))
for must in ("git push --force", "git reset --hard", "pip install", "rm -rf", "Read(./.env)", "Edit(./.env)"):
    if must not in deny: errs.append(f"missing deny for: {must}")
hooks = s.get("hooks", {})
if not any("guard.sh" in h.get("command", "") for m in hooks.get("PreToolUse", []) for h in m.get("hooks", [])):
    errs.append("PreToolUse guard.sh hook not wired")
sb = s.get("sandbox", {})
if sb.get("enabled") is not True:
    errs.append("sandbox.enabled must be true: agent shell commands run inside Claude Code's OS sandbox where the platform supports it")
if sb.get("allowUnsandboxedCommands") is not False:
    errs.append("sandbox.allowUnsandboxedCommands must be false: no unsandboxed retry in an unattended run")
if "cdn.playwright.dev" not in (sb.get("network", {}) or {}).get("allowedDomains", []):
    errs.append("sandbox.network.allowedDomains must include cdn.playwright.dev: UI missions verify through Playwright, whose browser download is otherwise blocked")
print("\n".join(errs)); sys.exit(2 if errs else 0)
EOF
}
expect_exit "repo settings.json passes the lint" 0 lint "$ROOT/.claude/settings.json"
bad="$(tmpdir)/settings.json"
printf '%s' '{"permissions":{"allow":["Bash(bash tests/*)"],"deny":["Write(./.env)"]},"hooks":{}}' > "$bad"
expect_exit "negative control: ignored rules and missing denies fail the lint" 2 lint "$bad"
expect_contains "negative control names the wildcard rule" "bash tests/*" "$OUT"
expect_contains "negative control names the Write rule" "Write(./.env)" "$OUT"
expect_contains "negative control names the missing hook" "guard.sh" "$OUT"
expect_contains "negative control names the sandbox" "sandbox.enabled" "$OUT"
expect_contains "negative control names the sandbox retry" "allowUnsandboxedCommands" "$OUT"
expect_contains "negative control names the Playwright host" "cdn.playwright.dev" "$OUT"
finish
