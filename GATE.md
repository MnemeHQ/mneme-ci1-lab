# CI1.3 trusted merge gate and lab protection rules (proposal)

**Status (2026-10-17).**

- **The gate runs on every PR to `main`** (merged in PR #2, `a0eaff92`; failure reporting fixed in PR #5, `051d21ee`).
- **The ruleset** `policy/ruleset-main.proposed.json` is **applied unchanged** as ruleset `25313704` (active), so the gate is a **required, blocking** check.
- **Approved and run:** V0 (compliant change merged, PR #3), V1 (violation refused with HTTP 405, PR #4), and the reporting regression test (PR #6). Evidence is on MnemeHQ/mneme#540.
- **Not authorized:** V2–V13, and the Actions event-policy exception below (spec only).

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
| `policy/ruleset-main.proposed.json` | Protection for `main`, **applied unchanged as ruleset `25313704`** |
| `policy/actions-policy-gate-event.proposed.json` | Actions event-policy exception for the gate (proposed, **not applied**) |
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

**Applied (historical record).** On 2026-10-17, with approval under charter §9 item 3, this spec (blob `e9b0c1dc`) was applied **unchanged** as ruleset **`25313704`** (`ci1-lab-main`, active), using `POST /repos/MnemeHQ/mneme-ci1-lab/rulesets`. The live ruleset was verified field by field against the spec with no mismatches; details are on MnemeHQ/mneme#540.

**Do not repeat that POST.** It would create a second, duplicate ruleset. Any change to the protection must be reviewed as an update to ruleset `25313704` (`PUT .../rulesets/25313704`) under a separate approval. Read the live state with `gh api repos/MnemeHQ/mneme-ci1-lab/rulesets/25313704`.

## Proposed Actions event policy (`policy/actions-policy-gate-event.proposed.json`)

**Status: PROPOSED, NOT APPLIED.** Applying it is a separate approval: it changes a security trust boundary.

**Why.** GitHub adds a default policy that blocks `pull_request_target` in public repositories without an applicable Actions event policy. It currently runs in evaluate mode, and every gate run so far shows the warning. GitHub's docs give **2026-11-02** for enforcement. One page limits that date to repositories using the default policy "before general availability"; another does not. Read-only inspection on 2026-10-17 found **no** repository or inherited Actions policy on this repo (`GET /repos/MnemeHQ/mneme-ci1-lab/actions/policies?has_parents=true` returned 0), so the default applies.

If the gate's trigger is blocked, `CI1 trusted gate` never reports, and ruleset `25313704` keeps every PR to `main` blocked. That is fail-closed, but the experiment stops.

**What it allows, and nothing more:**

| Field | Value |
|---|---|
| Scope | repository-level policy on `MnemeHQ/mneme-ci1-lab` only |
| `conditions.workflow_path` | include **only** `.github/workflows/ci1-trusted-gate.yml`; exclude nothing |
| Rule | `restrict_action_events`, `allowed_events: ["pull_request_target"]`, an allowlist for that one workflow, which triggers on nothing else |
| `enforcement` | `active` (`evaluate` is Enterprise-only) |

Other workflows, including the manual P2 preflight, are not targeted. The policy does not **authorize** `pull_request_target` for any other file, so a new `pull_request_target` workflow under a different name is not covered by this exception. Whether such a workflow is actually **blocked** depends on GitHub's effective policies: Actions policies layer across repository, organization and enterprise levels, and the default policy may also apply. This policy alone does not determine it.

**Schema.** The body was validated against GitHub's OpenAPI description (`github/rest-api-description`, `POST /repos/{owner}/{repo}/actions/policies`): `actions-policy-repo-conditions` → `actions-policy-workflow-path-condition`, and `actions-rule` → `actions-rule-restrict-action-events`. A misspelled event and a missing `exclude` are both rejected by the same validator.

**Why the exception is defensible.** It rests on how the gate is built, per GitHub's [pull_request_target hardening guidance](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target):

- The workflow and gate script come from the base branch, and `actions/checkout` checks out only the base.
- The token is `contents: read` at workflow and job level, and there are no secrets.
- The PR head is fetched as objects and read only as git blobs. It is never checked out, built, installed or executed. GitHub's listed "pwn request" shapes all require running fetched PR code.
- PR-controlled text (title, body, branch) is not used.

**Known residual risk (unchanged by this policy).** A separate, compliant PR could weaken the gate files and merge, because the gate does not protect itself (see "Limits"). The exception does not widen that: it only keeps the existing gate workflow runnable.

**How it would be applied** (**do not run** until approved):

```bash
gh api -X POST -H "X-GitHub-Api-Version: 2026-03-10" repos/MnemeHQ/mneme-ci1-lab/actions/policies --input policy/actions-policy-gate-event.proposed.json
```

**Verification after applying:**

1. `GET .../actions/policies` lists exactly this policy, with its `workflow_path` and rule as sent.
2. GitHub accepts the path format `.github/workflows/ci1-trusted-gate.yml`. It is validated server-side; the docs give no worked example.
3. The next gate run no longer carries the default-policy warning. If it still does, the path-scoped policy is not treated as "applicable", and this design must be revisited.
4. A V0-style compliant PR still passes and a V1-style violation still blocks.

Rollback: `DELETE .../actions/policies/{id}`.

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
