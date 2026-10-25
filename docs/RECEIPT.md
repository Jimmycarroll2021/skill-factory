# Skill receipts

Schema id: `skill-receipt/1`. A receipt is the evidence that travels with a packaged skill version.

## What a receipt is

Every version that `skillz package` writes under `skills/<name>/.factory/versions/<semver>/` carries
two files, `receipt.json` and `receipt.md`. They record what was checked, how it was checked, and
how this version compares with the previous approved one: which cases it newly fails (regressions)
and which it newly passes (gains). Both files are listed in the version's `manifest.json`, so a
receipt cannot be edited after packaging without `approve`, `install` and `rollback` refusing the
version. A receipt is a record of what ran, not a certificate that the skill is good. The human at
`skillz approve` reads it and decides.

## Schema

The JSON object in `receipt.json` has these fields and no others. `receipt.md` is the same
information rendered for a human.

| Field | Type | Meaning |
| --- | --- | --- |
| `schema` | string | Always `skill-receipt/1`. |
| `skill` | string | The skill's `name` from its frontmatter. |
| `version` | string | The semantic version this receipt belongs to. |
| `created_at` | string | UTC timestamp of packaging, ISO 8601. |
| `skill_sha256` | string | SHA-256 of `SKILL.md` as packaged. |
| `evidence` | string | `verified`, `self-judged` or `unverified`. See below. |
| `runner` | string or null | `claude` or `codex`: the CLI that ran the behaviour cases; `null` when none ran. |
| `judge` | string or null | `claude` or `codex`: the model that scored the transcripts; `null` when none did. |
| `baseline` | string or null | The previous approved version the comparison is against; `null` for a first version. |
| `trigger` | object | `{"method": "lexical-overlap", "threshold", "checked", "passed", "failed"}`: the offline trigger grading. |
| `behaviour` | object or null | `{"cases", "skill_better", "skill_worse", "tie", "errors"}` from the judge; `null` without a runner. |
| `regressions` | list | Objects `{id, kind, prompt, before, after}`: cases that got worse since the baseline. |
| `gains` | list | Objects `{id, kind, prompt, before, after}`: cases that got better since the baseline. |
| `status` | string | `clean`, `regressed` or `no-baseline`. |

`status` is `no-baseline` when there is no earlier approved version, `regressed` when `regressions`
is non-empty, and `clean` otherwise.

## Evidence levels and how each is earned

- `unverified`: only the lexical trigger grading ran. No model saw the skill. This is what
  `skillz package <name>` produces with no `--runner`. It is a smoke check on the description's
  wording, not evidence about behaviour.
- `self-judged`: a model ran the behaviour cases with and without the skill and the same model
  scored the transcripts. This happens with `--runner` when only one of `claude` and `codex` is
  on PATH, or when `--judge` names the runner's own model.
- `verified`: a model ran the behaviour cases and a different model judged them (cross-model).
  This is the default when both `claude` and `codex` are installed and `--runner` is given.

A higher level means more was checked, not that the skill is correct. A judge is a model and can
be wrong.

## Regressions and gains

A regression is a case that passed in the previous approved version and fails now, or a behaviour
case whose judge score dropped. A gain is the reverse: a case that failed before and passes now, or
a behaviour case whose judge score rose. Both lists carry the case id, its kind (`positive`,
`negative` or `behaviour`), the prompt, and the before and after results, so a reader can see what
changed without re-running anything.

The comparison covers exactly the cases the baseline version recorded in its own reports, re-graded
against the new version. A case the baseline never saw (an `evals.json` entry or a captured failure
added since) is graded in this version's `trigger` and `behaviour` counts but can be neither a gain
nor a regression, so those counts and the two lists can legitimately disagree. A generated case whose
prompt changed with the description (for example `pos-001`) appears in the lists under the baseline's
prompt.

The pipeline's regression line sums them up, for example
`pass (2 gains, 0 regressions vs 0.1.0; 7 cases compared)`.

## What refuses what

- `skillz package` stops when the comparison finds a regression, unless `--allow-regression` is
  given. The version is still written with `status: regressed` when it is allowed.
- `skillz approve <name> <version>` refuses a version whose receipt status is `regressed`, unless
  `--accept-regressions "<reason>"` is given. The reason and the regressed case ids are recorded in
  the version's `approval.json`.
- `skillz promote <name> <candidate>` refuses a candidate whose challenge verdict is
  `candidate-worse` or `candidate-unsafe`. Nothing stable changes.

Every refusal exits non-zero and writes no approval, no install and no canonical file.

## Where receipts live

- `skills/<name>/.factory/versions/<semver>/receipt.json` and `receipt.md`, covered by the
  version's `manifest.json` checksums alongside `skill/`, `dist/` and `reports/`.
- `skillz receipt <name> [version] [--json]` prints the receipt for a version (default: the highest).
- `skills/registry.json` records the evidence level of every installed version, and `skillz list`
  shows it in an evidence column.

## Why

"The Regression Tax: Decomposing Why Skills Help and Hurt LLM Agents" (Tank and Nama, 2026,
[arXiv:2607.22520](https://arxiv.org/abs/2607.22520)) reports, over about 6,000 runs, that a
skill's average gain hides the cases where it makes the agent worse, that the best skills win mainly
by regressing less rather than by gaining more, and recommends evaluating a skill by splitting its
net effect into gains and regressions. That is their finding, not ours; the receipt is the shape it
suggests: a per-case comparison against the last approved version, with the two lists kept apart.

## Versioning of the schema

The `schema` field names the version of this document. A field is never removed or redefined within
`skill-receipt/1`; a new field, or a changed meaning, is a new schema id, and readers must refuse an
id they do not know rather than guess. `skillz receipt` prints the receipt as stored, whatever its
schema id.
