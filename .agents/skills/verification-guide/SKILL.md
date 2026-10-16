---
name: verification-guide
description: Discover and validate an existing project's launch and public verification entrypoints, then record a reusable guide when agents repeatedly rediscover the environment.
---

# Verification Guide

Make the existing harness usable without repeated environment exploration. Apply when onboarding to an unfamiliar verification path or when its setup is repeatedly rediscovered. Update the project's existing guide; a new ticket does not need a new skill or harness.

## Discover the Existing Path

Read host workflow/testing docs and inspect actual scripts, fixtures, runner help, and CI. Identify what currently exists, what it covers, and what is still planned. Follow the approved scope and public test interfaces; use accepted journeys and an existing actor's task.

Map the relevant user behavior to the smallest existing representative verification path. Identify prerequisites, setup cost, current owner of shared services, isolated ports/data/accounts/artifacts, and cleanup responsibility. Credentials are references to approved setup, not values to copy into the guide. Missing entrypoints are gaps to report, not commands to invent.

## Walk It and Record What Works

Prefer inexpensive discovery and health checks before heavy execution. Run a representative authorized path using actual commands:

- **Launch:** required setup, existing startup/fixture entrypoint, target build and environment.
- **Ready:** observable health, identity, data, or geometry predicate that makes the path runnable.
- **Drive:** public action or test entrypoint and the behavior it verifies, including a relevant failure boundary when needed.
- **Evidence:** command/exit, source/build identity, actual coverage and result, raw evidence location, and limitations. Preserve a failed attempt as failed.
- **Cleanup:** owned resources, active consumers/creators, interruption recovery, and reconciliation outcome according to host policy.

When temporary resources are needed, use the host fixture and ownership ledger, register them before use, and reconcile afterward. Remove only verified owned disposable resources after creators and consumers stop; retain persistent/shared services with owner and purpose. Preserve evidence in the task record. An abnormal exit needs reconciliation before retry.

Example: a browser guide might use the existing development server and smoke-test runner, wait for the seeded user and target scene, exercise save and rejection recovery, keep screenshots/receipts, then clean only its fixture. If the save path is not implemented, state that limitation instead of documenting a fictional passing command.

## Result

Place the concise guide in the existing testing/developer-doc location, or the user-specified path. Point to authoritative scripts and describe operational prerequisites, behavior coverage, evidence, and cleanup that the scripts alone do not explain. Record the revision/environment actually walked and mark unexecuted parts as unverified or blocked.

The guide is usable when another actor can follow its demonstrated path and understand its limits. Report the walkthrough outcome and any missing capability. Reuse it while inputs remain valid; update affected sections when commands, contracts, or environment change. A guide adds navigation and evidence, not another delivery gate.

Method adapted from [pstack create-verification-skill at df581122](https://github.com/cursor/plugins/blob/df581122cde17e6e27686b5a448bde23e4ad4318/pstack/skills/create-verification-skill/SKILL.md); this adaptation produces project documentation rather than a per-feature skill.
