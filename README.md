# mneme-ci1-lab

Temporary, public test repository for the **CI1 Enforcement Assurance**
workstream of [MnemeHQ/mneme](https://github.com/MnemeHQ/mneme)
(tracker: [MnemeHQ/mneme#540](https://github.com/MnemeHQ/mneme/issues/540)).

## Current scope

Approved so far under the
[CI1.3 charter](https://github.com/MnemeHQ/mneme/blob/main/docs/plans/2026-10-17-ci1-3-isolated-merge-blocking-experiment-charter.md)
§9:

- **P2** (accepted): validating the pinned Mneme evaluator on a GitHub-hosted
  Linux runner, meaning wheel integrity and full CI1.2 fixture parity. See
  §4 and §4a and the
  [P2 validation record](https://github.com/MnemeHQ/mneme/blob/main/docs/validation/ci1-3-p2-pinned-evaluator-preflight-2026-10-17.md).
- **The trusted merge gate, blocking**: see [`GATE.md`](GATE.md). The ruleset
  `policy/ruleset-main.proposed.json` is applied unchanged as ruleset
  `25313704`, so `CI1 trusted gate` is a required check on `main`.
- **V0, V1 and the reporting regression test** (run; evidence on
  MnemeHQ/mneme#540).
- **The Actions event-policy exception** for the gate only
  (`policy/actions-policy-gate-event.proposed.json`), applied unchanged as
  repository Actions policy `7806`, so `pull_request_target` stays allowed
  for `ci1-trusted-gate.yml` if GitHub enforces its default block.

**Not authorized here yet:**

- V2–V13 (adversarial PRs beyond V1)

Each needs its own approval under charter §9.

## Workflows

| Workflow | Trigger | Effect |
|---|---|---|
| CI1.3 P2 evaluator preflight | manual dispatch only | validates the pinned evaluator |
| CI1 trusted merge gate | **every PR to `main`** (`pull_request_target`) | reports a pass or block verdict as the `CI1 trusted gate` check, **required** by ruleset `25313704`: a red check blocks the merge |

Both use read-only tokens and never execute pull-request code. The gate runs
the workflow and gate script from the base branch and reads PR files only as
data.

## Lifetime

This repository is **archived after CI1.4**, once the evidence packet and its
promote or defer recommendation are recorded in `MnemeHQ/mneme`. It is not a
product, a supported integration or a template.
