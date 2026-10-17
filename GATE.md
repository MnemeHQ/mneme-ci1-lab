# CI1.3 trusted merge gate and lab protection rules (proposal)

**Status.** This document arrives with this repository's PR #2.

- **Before PR #2 merges,** nothing here runs. `pull_request_target` always uses the base branch's copy of the workflow, and `main` has none.
- **Once PR #2 merges, the gate runs on every PR to `main`** and reports the `CI1 trusted gate` check. It is **advisory**: no ruleset is applied, so a red gate does not block a merge.
- **The ruleset** in `policy/ruleset-main.proposed.json` is a spec only. Applying it, which is what makes the gate blocking, is a separate approval (charter §9 item 3).
- **The V0–V13 experiment** is not authorized.

Charter: MnemeHQ/mneme
[`docs/plans/2026-10-17-ci1-3-isolated-merge-blocking-experiment-charter.md`](https://github.com/MnemeHQ/mneme/blob/main/docs/plans/2026-10-17-ci1-3-isolated-merge-blocking-experiment-charter.md).
Tracker: [MnemeHQ/mneme#540](https://github.com/MnemeHQ/mneme/issues/540).

## What is proposed

| File | Role |
|---|---|
| `.github/workflows/ci1-trusted-gate.yml` | T1 gate: `pull_request_target`, read-only token, base checkout only |
| `gate/ci1_gate.py` | Trusted workspace, materialization, evaluation, classification (stdlib only) |
| `gate/evaluator-requirements.txt` | Hash-locked runtime dependencies of the P2-approved evaluator |
| `.mneme/project_memory.json` | Trusted policy: the CI1.2.1 corpus memory (blob `dd760bf0`, charter §5) |
| `policy/ruleset-main.proposed.json` | Proposed protection for `main` (not applied) |
| `tests/test_ci1_gate.py` | Offline tests |

## How a PR is judged

1. The workflow and `gate/ci1_gate.py` come from the **base** commit (`GITHUB_SHA`), and that is verified.
2. The PR head is fetched as `refs/pull/<n>/head` into the object store, and must equal the event's `head.sha`. Otherwise the run is UNEVALUATED, and the newer push gets its own run. The head is **never checked out**.
3. The evaluator is rebuilt from MnemeHQ/mneme `0b74d410` exactly as in P2:
   - shallow checkout, then `git archive`
   - `tarfile` with `filter="data"`
   - umask `0002`
   - `SOURCE_DATE_EPOCH` set to the commit time
   - `setuptools==84.0.0`

   It is installed only if its sha256 is **`e3fcc769…`**, the value accepted in P2. Its runtime dependencies are installed with `--require-hashes --no-deps`. This procedure reproduces `e3fcc769…` from a shallow checkout (checked on Cloud Shell).
4. A fresh workspace `ws/` is built:
   - the trusted memory is written to `ws/.mneme/project_memory.json` from the base commit's blob
   - every file the PR adds or modifies (merge-base..head) is written as a new regular file from its git blob, with exclusive create and no symlink following
   - deleted files are not evaluated
5. **Refused, and therefore blocking:**
   - symlinks (`120000`) and submodules (`160000`)
   - absolute paths, `..`, `.`, empty components or backslashes
   - anything under `.mneme/` that the PR adds or modifies
   - **any deletion under `.mneme/`**, even when it is the PR's only change. Without this, a deletion-only PR would have nothing to evaluate, would pass, and could remove the trusted memory every later PR is judged against. Other deletions are not evaluated
   - collisions with an entry the gate already wrote
6. Each materialized file is checked with the pinned evaluator:
   - command: `python -I -m mneme.cli check --mode strict --json`
   - a fixed query, so the PR title is never used (CI1.1 S7)
   - an empty working directory and ADR directory
7. Classification follows the CI1.2 contract and the C1 `assess_verdict` rules:
   - **UNEVALUATED** when the output is not a single `mneme.check/v1` object, `mode` is not the requested `strict`, `evaluation_complete` is not exactly `true`, the verdict is unknown, violations contradict the verdict, or the exit code contradicts the verdict
   - otherwise **VERIFIED_PASS**, **VIOLATION** or **WARN**
8. **Lab policy:** the gate passes only if every evaluated file is VERIFIED_PASS and nothing was refused. Blocking on UNEVALUATED is a measurement choice for this lab, not product policy (ADR-033 / MnemeHQ/mneme#483 is undecided).
9. `gate-report.json` (base, head, merge-base, memory blob, per-file payloads, refusals) is uploaded as an artifact, and a job summary is written.

## Proposed ruleset (`policy/ruleset-main.proposed.json`)

| Rule | Value | Why |
|---|---|---|
| Target | `~DEFAULT_BRANCH` (`main`) | the protected branch |
| `bypass_actors` | `[]` | no one bypasses, admins included |
| `deletion`, `non_fast_forward` | on | `main` cannot be deleted or force-pushed |
| `pull_request` | squash only, **0 approvals** | mirrors MnemeHQ/mneme `main-pr-only`, so the result speaks to that setup. The gate, not a reviewer, is what is measured |
| Required check | `CI1 trusted gate`, `integration_id` 15368 (GitHub Actions) | the gate job's name |
| `strict_required_status_checks_policy` | **false** | mirrors MnemeHQ/mneme, so V11 measures base freshness instead of assuming it |

It would be applied with the command below. **Do not run it until approved** (charter §9 item 3):

```bash
gh api -X POST repos/MnemeHQ/mneme-ci1-lab/rulesets --input policy/ruleset-main.proposed.json
```

## How each V-row is meant to be handled

| Row | Scenario | Mechanism | Expected |
|---|---|---|---|
| V0 | compliant `docs/` change | VERIFIED_PASS | merge allowed |
| V1 | literal in `docs/` | VIOLATION | blocked |
| V2 | V1, plus the rule removed from the PR's memory | memory comes from base; the `.mneme/` path is refused | blocked |
| V3 | V1, plus the PR's memory deleted | memory comes from base; the deletion is refused | blocked |
| V3a | the PR **only** deletes `.mneme/project_memory.json` | the deletion is refused (regression test `test_deletion_only_pr_removing_the_trusted_memory_cannot_pass`) | blocked |
| V4 | V1, plus evaluator or packaging edits | the evaluator is the pinned wheel; PR code never executes | blocked |
| V5 | V1, plus gate workflow or `gate/` edits | `pull_request_target` runs the base copies | blocked |
| V6 | V1, plus an added permissive `pull_request` workflow | the trusted gate still fails | blocked |
| V7 | V1, plus a look-alike job named `CI1 trusted gate` | same name and same app (15368) | **measured** |
| V8 | literal in `src/` (no include match) | EXCLUDED, so VERIFIED_PASS | merge allowed, recorded as a **coverage limit** |
| V9 | unevaluable input | UNEVALUATED | blocked (lab policy) |
| V10 | green head, then a violating push | new run on the new head; the race guard covers the gap | **measured**: the old result must not authorize the new head |
| V11 | green, then base gains a stricter rule | strict status policy is off | **measured**: may merge on a stale base |
| V12 | requested-mode mismatch | `mode` must equal `strict` | blocked (unit-tested; live only on a lab branch) |
| V13 | symlink at `.mneme` or under `docs/`, or traversal | refused, never followed | blocked; git/GitHub input rejections recorded separately |

## Limits this experiment does not remove

These stay explicit in the CI1.4 evidence. They are not guarantees.

- **Check ownership and revision.** Whether GitHub enforces the genuine `pull_request_target` result for the correct PR head, and rejects a look-alike check, is measured by V0, V1, V7 and V10. It is not assumed.
- **Staged changes across separate PRs.** The matrix tests a PR that tries to change the gate **and** violate policy at once (V5, V6). It does not prove protection against two separate PRs, where a first, compliant PR weakens `gate/` or the workflow and merges, and a second PR then introduces a violation. A gate-weakening PR contains no policy violation, so this gate passes it. Protecting the gate definition itself needs a control outside the gate (for example review of `.github/workflows/**` and `gate/**`, or T4). That is out of scope here.
- **Partial mirror of production rules.** The lab ruleset copies the two `MnemeHQ/mneme` `main-pr-only` settings that matter for this measurement: 0 approvals and the non-strict status policy. It does not replicate every production rule.

## Open questions the live experiment must answer (not assumed here)

- **Check-run association.** Whether a `pull_request_target` check run attaches to the PR head SHA, so that the required check gates each head, is the key question for V0, V1 and V10. If it does not, the gate cannot block through a required check, and the result is **NOT PROVEN — T1**.
- **V7.** Whether a same-name check from the same app can satisfy the requirement while the real gate fails.
- **V11.** Base freshness with `strict_required_status_checks_policy: false`.

## What merging PR #2 means

Merging puts the gate on `main`, so **it runs on every later PR**. Until the ruleset is approved and applied, it is advisory: a red gate does not block a merge, and its runs are not V0–V13 evidence.

Merging therefore needs its own explicit approval of this design. Applying the ruleset and running V0–V13 remain separate approvals.

## Local tests

```bash
python -m pytest tests/test_ci1_gate.py -q
```

The 5 end-to-end tests run only when `CI1_GATE_EVALUATOR_PYTHON` points at a Python that can `import mneme.cli` under `-I`, for example a venv with the approved `e3fcc769` wheel. Otherwise they skip.
