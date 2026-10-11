## Traceability

**Mission:** `<mission-branch>`

**Requirements:**
- `<REQ-xxx>`

<!-- Use stable requirement IDs from docs/PRD.md when applicable. -->


## Scope

**Reviewable changed lines:** `<reviewable-lines>`
**Files changed:** `<file-count>`

- [ ] One logical outcome
- [ ] Within the normal ≤500 reviewable lines / ≤10 files target

If outside the target:

<!-- Explain why the change cannot reasonably be split without reducing safety, testability or reviewability. -->


## Problem

<!-- What failed, was missing, or needed to change?
Describe how the problem can be reproduced or observed. -->


## Change

<!-- What behaviour does this PR introduce or change?
Explain the outcome, not just the files changed. -->


## Regression evidence

<!-- Behaviour changes should prove the problem existed before the fix. -->

- [ ] Regression test added or updated
- [ ] Test verified to fail without the fix
- [ ] Test passes with this change

Evidence:

```text
<relevant failing/passing output>
```


## Validation

### Local gate

- [ ] `bash scripts/gate.sh` passed

Result:

```text
<gate result / relevant summary>
```

### CI — latest PR commit

Commit: `<SHA>`

- [ ] Ubuntu / Python 3.10
- [ ] Ubuntu / Python 3.12
- [ ] Windows / Python 3.10
- [ ] Windows / Python 3.12
- [ ] `ci-required` passed

<!-- Do not rely on CI from an earlier commit. -->


## Contract / operational impact

- [ ] No script input/output/state/hook contract changed
- [ ] `docs/design/contracts.md` updated where required
- [ ] Operational/runbook/reviewer documentation updated where required
- [ ] No guardrail has been weakened

If an action that was previously denied is now allowed:

<!-- Explain why it is safe and identify its regression test. -->


## Security and evidence

- [ ] No credentials, `.env` contents, runtime databases or private transcripts included
- [ ] Evidence has been sanitised
- [ ] Controller/security verification completed

Security findings / disposition:

```text
None / details
```


## Risks and remaining work

**Risk:** Low / Medium / High

<!-- Known limitations, compatibility concerns, follow-up work,
migration requirements, or acceptance that has NOT been performed. -->


## Publication status

- [ ] Controller independently verified the final commit
- [ ] PR represents the controller-published result
- [ ] Latest CI is green
- [ ] Review conversations resolved

**Live acceptance:** Passed / Failed / Not run

<!-- An open PR is not evidence of release readiness. -->
