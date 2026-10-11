#!/usr/bin/env bash
# Install (or upgrade) the quickship harness into a target project.
#   bash scripts/init.sh <target-dir> [--force] [--upgrade]
# Copies every file listed in scripts/manifest.txt, git-inits the target if needed, seeds .gitignore,
# docs/decisions.md and BRIEF.yaml, and records what was installed under .quickship/ so a later --upgrade
# can tell files you edited (kept) from files you never touched (refreshed). Needs only git and coreutils.
#   plain:     new files are added; a target file that differs from the source is kept (skip (modified))
#   --force:   differing files are overwritten (CLAUDE.md never is)
#   --upgrade: a differing file is overwritten only if it still matches the sha recorded at install time
# Exit 0 on success, 2 on bad usage or when the target is the harness itself.
set -u

usage() { echo "usage: bash scripts/init.sh <target-dir> [--force] [--upgrade]" >&2; exit 2; }
target=""; force=0; upgrade=0
for a in "$@"; do
  case "$a" in
    --force) force=1;; --upgrade) upgrade=1;; -h|--help) usage;;
    -*) echo "init: unknown option $a" >&2; usage;;
    *) [ -n "$target" ] && usage; target="$a";;
  esac
done
[ -n "$target" ] || usage

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(git -C "$here" rev-parse --show-toplevel 2>/dev/null)" || SRC="$(cd "$here/.." && pwd)"
[ -f "$SRC/scripts/manifest.txt" ] || { echo "init: no scripts/manifest.txt under $SRC" >&2; exit 2; }

# sha_all <dir> <paths...> prints "<sha256>  <path>" per file, one process for the whole tree
# (Git Bash hashes in binary mode and prefixes the path with "*"; sed strips it)
if command -v sha256sum >/dev/null 2>&1; then sha_all() { ( cd "$1" && shift && sha256sum -- "$@" | sed 's/ \*/  /' ); }
elif command -v shasum >/dev/null 2>&1; then sha_all() { ( cd "$1" && shift && shasum -a 256 -- "$@" | sed 's/ \*/  /' ); }
else echo "init: need sha256sum or shasum" >&2; exit 2; fi

mkdir -p "$target" || exit 2
T="$(cd "$target" && pwd -P)"
if [ "$T" = "$(cd "$SRC" && pwd -P)" ]; then
  echo "init: target is the harness itself ($SRC); pick the project you want to install into" >&2; exit 2
fi

changed=0; skipped=0
git -C "$T" rev-parse --git-dir >/dev/null 2>&1 || { git init -q -b main "$T" && echo "add: .git (git init)" && changed=$((changed+1)); }

# expand the manifest against the source tree (globstar), deduplicated, in manifest order
files=()
while IFS= read -r f; do
  case " ${files[*]:-} " in *" $f "*) ;; *) files+=("$f");; esac
done < <( cd "$SRC" && shopt -s globstar nullglob && while IFS= read -r line || [ -n "$line" ]; do
  line="${line%%#*}"; line="${line//[$'\r\t ']/}"; [ -z "$line" ] && continue
  for g in $line; do [ -f "$g" ] && echo "$g"; done; done < scripts/manifest.txt )
[ ${#files[@]} -gt 0 ] || { echo "init: manifest matched no files under $SRC" >&2; exit 2; }

declare -A SSHA TSHA REC   # source sha, target sha (existing files only), sha recorded at install time
while read -r s p; do SSHA[$p]=$s; done < <(sha_all "$SRC" "${files[@]}")
existing=(); for f in "${files[@]}"; do [ -f "$T/$f" ] && existing+=("$f"); done
[ ${#existing[@]} -gt 0 ] && while read -r s p; do TSHA[$p]=$s; done < <(sha_all "$T" "${existing[@]}")
[ -f "$T/.quickship/manifest.sha256" ] && while read -r s p; do REC[$p]=$s; done < "$T/.quickship/manifest.sha256"

install() { mkdir -p "$(dirname "$T/$1")" && cp "$SRC/$1" "$T/$1"; }
new_manifest=""; conflicts=""
mkdir -p "$T/.quickship"
[ -f "$T/.quickship/unmanaged" ] || touch "$T/.quickship/unmanaged"
for f in "${files[@]}"; do
  new_manifest="$new_manifest${SSHA[$f]}  $f"$'\n'
  if [ ! -f "$T/$f" ]; then install "$f" && echo "add: $f"; changed=$((changed+1)); continue; fi
  [ "${TSHA[$f]}" = "${SSHA[$f]}" ] && continue
  untouched=0; [ "$upgrade" = 1 ] && ! grep -qxF "$f" "$T/.quickship/unmanaged" && [ "${TSHA[$f]}" = "${REC[$f]:-}" ] && untouched=1
  if [ "$f" = CLAUDE.md ] && [ "$untouched" = 0 ]; then
    echo "notice: CLAUDE.md exists; merge the \"PR sizing and evidence\", \"Hard rules\" and \"Lead loop\" sections from $SRC/CLAUDE.md"; skipped=$((skipped+1))
  elif [ "$force" = 1 ]; then install "$f" && echo "overwrite: $f"; changed=$((changed+1))
  elif [ "$untouched" = 1 ]; then install "$f" && echo "upgrade: $f"; changed=$((changed+1))
  else echo "skip (modified): $f"; skipped=$((skipped+1)); fi
  if [ "$(sha_all "$T" "$f" | cut -d' ' -f1)" != "${SSHA[$f]}" ]; then
    conflicts="$conflicts$f"$'\n'
    grep -qxF "$f" "$T/.quickship/unmanaged" || echo "$f" >> "$T/.quickship/unmanaged"
  fi
done

# .gitignore entries the harness relies on
gi="$T/.gitignore"
for line in ".claude/worktrees/" ".claude/state/" "work/_untrusted/" "__pycache__/"; do
  grep -qxF -- "$line" "$gi" 2>/dev/null && continue
  [ -s "$gi" ] && [ -n "$(tail -c1 "$gi")" ] && echo >> "$gi"
  echo "$line" >> "$gi"; echo "add: .gitignore <- $line"; changed=$((changed+1))
done

if [ ! -f "$T/docs/decisions.md" ]; then
  mkdir -p "$T/docs"
  printf '# Decision log\n\nOne line per merged task. Append only; never edit past entries.\n\n| date | task | change | why |\n|---|---|---|---|\n' > "$T/docs/decisions.md"
  echo "add: docs/decisions.md"; changed=$((changed+1))
fi
if [ ! -f "$T/BRIEF.yaml" ] && [ -f "$SRC/BRIEF.example.yaml" ]; then
  cp "$SRC/BRIEF.example.yaml" "$T/BRIEF.yaml"; echo "add: BRIEF.yaml (from BRIEF.example.yaml)"; changed=$((changed+1))
fi

mkdir -p "$T/.quickship"
if [ -z "$conflicts" ]; then
  [ -f "$SRC/VERSION" ] && cp "$SRC/VERSION" "$T/.quickship/VERSION"
  rm -f "$T/.quickship/conflicts"
else
  printf '%s' "$conflicts" > "$T/.quickship/conflicts"
  echo "notice: incomplete installation; resolve .quickship/conflicts before running"
fi
sha_all "$T" "${files[@]}" > "$T/.quickship/manifest.sha256"
printf '%s' "$conflicts" > "$T/.quickship/unmanaged"

echo "next: edit BRIEF.yaml, commit it, then: bash scripts/run.sh  (Windows PowerShell: .\run.cmd)"
echo "  or, from a raw idea: write IDEA.md (see IDEA.example.md), then: bash scripts/idea.sh, then: bash scripts/program.sh"
[ "$changed" = 0 ] && [ "$skipped" = 0 ] && echo "unchanged"
exit 0
