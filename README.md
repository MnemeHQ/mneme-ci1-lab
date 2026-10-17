# mneme-ci1-lab

Temporary, public test repository for the **CI1 Enforcement Assurance**
workstream of [MnemeHQ/mneme](https://github.com/MnemeHQ/mneme)
(tracker: [MnemeHQ/mneme#540](https://github.com/MnemeHQ/mneme/issues/540)).

## Current scope: P2 only

This repository is approved **only** for CI1.3 precondition P2: validating
the pinned Mneme evaluator on a GitHub-hosted Linux runner. That means
integrity (the approved wheel sha256) and full CI1.2 fixture parity. See the
[CI1.3 charter](https://github.com/MnemeHQ/mneme/blob/main/docs/plans/2026-10-17-ci1-3-isolated-merge-blocking-experiment-charter.md)
§4 and §4a, and the
[P2 validation record](https://github.com/MnemeHQ/mneme/blob/main/docs/validation/ci1-3-p2-pinned-evaluator-preflight-2026-10-17.md).

**Not authorized here yet:**

- rulesets or branch protection
- the trusted merge gate (`pull_request_target`) workflow
- adversarial PRs or the V0–V13 merge-blocking experiment

Each needs its own approval under charter §9. The trusted gate and the
proposed protection rules are described in [`GATE.md`](GATE.md). The ruleset
there is a spec only, until approved.

Workflows run only on manual dispatch and with read-only permissions. They
never execute pull-request code.

## Lifetime

This repository is **archived after CI1.4**, once the evidence packet and its
promote or defer recommendation are recorded in `MnemeHQ/mneme`. It is not a
product, a supported integration or a template.
