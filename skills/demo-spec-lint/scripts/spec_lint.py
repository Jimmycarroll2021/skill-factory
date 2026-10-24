#!/usr/bin/env python3
"""spec_lint.py - check that a demo spec can be tested before anything is built.

Reads a markdown demo spec (pitch/run/health lines, ## seed, ## golden path,
## out of scope) and reports what would make
the golden path untestable.

Blocks (exit 4):
  - a `pitch:`, `run:` or `health:` line is missing
  - `health:` is not an HTTP path starting with "/"
  - there is no `## golden path` section, or it has no numbered steps
  - a golden-path step has no observable result (no "->" or "→" with text after it)
Warns (exit 2, only when nothing blocks):
  - `## seed` is missing or does not hold 5-10 records
  - `## out of scope` is missing or empty
  - an observable result is only a vague word ("works", "succeeds", "is correct")

What it cannot check: whether a result is the right one, whether the demo
produces it, or whether the idea is worth building. It reads text; it runs nothing.

Exit codes: 0 PASS · 2 WARN · 3 bad input (a usage error, neither or both of
--input and --sample, an unreadable or empty file) · 4 BLOCK. Usage errors exit
3, not argparse's default 2, so a typo can never read as WARN. Stdlib only.
`--sample` is opt-in and cannot be combined with --input.
"""
import argparse
import json
import re
import sys
from pathlib import Path

SAMPLE = """# Cafe prep planner

pitch: a cafe owner drops in a week of till exports and sees tomorrow's prep list
run: ./demo.sh
health: /healthz
stack: FastAPI and SQLite with a small HTML front end

## seed
- Almond croissant, baked 24, sold out 11:20-11:55 every day
- Plain croissant, baked 30, sold 22-27
- Blueberry muffin, baked 30, sold 12-16
- Banana bread slice, baked 16, sold out 14:00-14:45
- Cheese scone, baked 12, sold 0-8

## golden path
1. Open / → the prep list for tomorrow shows all five items with quantities
2. Click "Almond croissant" → a chart shows it sold out before noon on 7 of 7 days
3. Change the almond quantity to 32 and save → the list shows 32 and a "saved" toast

## out of scope
- accounts and login
- uploading files from a phone
"""

KEY_RE = re.compile(r"^(pitch|run|health):[ \t]*(\S.*)?$", re.MULTILINE)
STEP_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
ARROW_RE = re.compile(r"(->|→)")
VAGUE = {"works", "it works", "works fine", "succeeds", "success", "is correct", "correct",
         "ok", "done", "as expected", "fine", "is shown", "it loads", "loads"}


def sections(text):
    """{'golden path': [lines], ...} keyed by lower-case H2 title."""
    out, current = {}, None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            current = m.group(1).strip().lower()
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return out


def lint(text):
    blocking, warnings = [], []
    keys = {m.group(1): (m.group(2) or "").strip() for m in KEY_RE.finditer(text)}
    for k in ("pitch", "run", "health"):
        if not keys.get(k):
            blocking.append(f"missing '{k}:' line")
    if keys.get("health") and not keys["health"].strip("`'\"").startswith("/"):
        blocking.append(f"health '{keys['health']}' is not an HTTP path starting with '/'")

    secs = sections(text)
    steps = [STEP_RE.match(l).group(1) for l in secs.get("golden path", []) if STEP_RE.match(l)]
    if "golden path" not in secs:
        blocking.append("no '## golden path' section")
    elif not steps:
        blocking.append("'## golden path' has no numbered steps")
    for i, step in enumerate(steps, 1):
        parts = ARROW_RE.split(step, maxsplit=1)
        result = parts[2].strip() if len(parts) == 3 else ""
        if not result:
            blocking.append(f"step {i} has no observable result ('→ what must be visible'): {step[:60]}")
        elif result.lower().rstrip(".") in VAGUE:
            warnings.append(f"step {i} result '{result}' is too vague to assert")

    seed = [l for l in secs.get("seed", []) if l.lstrip().startswith(("-", "*"))]
    if "seed" not in secs:
        warnings.append("no '## seed' section; the first screen may be empty")
    elif not 5 <= len(seed) <= 10:
        warnings.append(f"'## seed' has {len(seed)} records; aim for 5-10")
    oos = [l for l in secs.get("out of scope", []) if l.strip()]
    if not oos:
        warnings.append("'## out of scope' is missing or empty; cut items get re-added")

    verdict, code = ("BLOCK", 4) if blocking else (("WARN", 2) if warnings else ("PASS", 0))
    return {"verdict": verdict, "exit": code, "steps": len(steps),
            "blocking": blocking, "warnings": warnings}


class Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse would exit 2, which means WARN here
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        sys.exit(3)


def main(argv=None):
    ap = Parser(description="Lint a demo spec for testability. Exit 0 PASS, 2 WARN, 3 bad input, 4 BLOCK.")
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--input", help="path to the spec, e.g. DEMO_SPEC.md")
    source.add_argument("--sample", action="store_true", help="lint the embedded sample spec instead of a file")
    ap.add_argument("--output", choices=["text", "json"], default="text")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows pipes default to cp1252
        stream.reconfigure(encoding="utf-8", errors="replace")

    if args.sample:
        text = SAMPLE
    elif args.input:
        try:
            text = Path(args.input).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"error: cannot read {args.input}: {e}", file=sys.stderr)
            return 3
    else:
        print("error: give --input FILE or --sample; refusing to lint nothing", file=sys.stderr)
        return 3
    if not text.strip():
        print("error: the spec is empty", file=sys.stderr)
        return 3

    result = lint(text)
    if args.output == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for b in result["blocking"]:
            print(f"BLOCK: {b}")
        for w in result["warnings"]:
            print(f"WARN: {w}")
        print(f"{result['verdict']} ({result['steps']} golden-path step(s))")
    return result["exit"]


if __name__ == "__main__":
    sys.exit(main())
