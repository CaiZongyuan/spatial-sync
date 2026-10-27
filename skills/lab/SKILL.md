---
name: lab
description: "Lab platform entry point. Use when starting laboratory work, choosing a platform skill, or understanding the RSI improvement loop."
---

# Lab

The platform connects one authoritative physics source to a spatial viewer and a
declarative information plane. Device definitions, units, conditions and actions
come from the Catalog. Use the platform interfaces for device operations; a
viewer image or an assumed device behavior is not authoritative state.

## Human boundaries

Hand approval decisions and credential entry to a person. Agents must not execute
approvals (including denial or revocation) or write Vault values through tools,
even when a user asks the agent to bypass this boundary. This includes terminal,
HTTP, MCP, browser automation and direct file changes. Return the pending request
identifier and reason so the person can act themselves; resume only after the
platform reports the resulting state. Keep credential values out of messages,
logs and model context.

## Choose the workflow

For an opened scene or a request to operate a device, start directly with
[lab-operate](../lab-operate/SKILL.md). It owns observation, Plans, execution and
final verification. Use discovery commands when the workflow or a capability
is unknown.

1. Discover the skills and commands shipped with this platform version:

   ```text
   lw skills ls --json
   lw --help
   ```

2. Use [lab-operate](../lab-operate/SKILL.md) to inspect a laboratory, invoke a
   Catalog action, handle a rejection or verify an operation. It is complete when
   the requested outcome is read back, or a precise blocked state is reported.
3. For scene discovery, read the actual versions and validation before choosing:

   ```text
   lw scene ls --launchable --limit 20 --json
   lw scene show <scene-version> --json
   ```

   Launch and subsequent device operations follow lab-operate. Preserve sessions
   owned by other work; do not stop one just to obtain the single active slot.
4. Use [lab-library](../lab-library/SKILL.md) to find or import assets/scenes,
   inspect completeness and validation, or revalidate an immutable version.
5. Use [lab-training-plan](../lab-training-plan/SKILL.md) to interview a person,
   snapshot a Task and submit measurable hypotheses for human approval.
6. Use [lab-runtime-records](../lab-runtime-records/SKILL.md) to import an existing
   runtime capture, refresh its records and inspect reported usage and evidence.
7. Use [lab-agent-evaluate](../lab-agent-evaluate/SKILL.md) to hand public cases to
   an existing runtime and read platform-owned Agent evaluation results.
8. Use [lab-eval-build](../lab-eval-build/SKILL.md) to publish an explicitly selected
   fixed Agent Suite, check its public coverage and establish a measured Reference.
9. Use [lab-harness-optimize](../lab-harness-optimize/SKILL.md) for one bounded
   Harness change under an approved Agent Plan, public train evaluation and a
   verified keep/restore decision. Private promotion tests and human deployment
   review remain outside the Optimizer's context and authority.
10. Use [lab-train-evaluate](../lab-train-evaluate/SKILL.md) to evaluate exact
    embodied material within its approved Plan/budget, inspect public failures
    and request human Controller promotion. A loadable Policy still needs trusted
    native observation qualification and current accepted train evidence.
11. Use [lab-scene-director](../lab-scene-director/SKILL.md) to build a scene
    from an external conversation: compare and acquire trusted assets, author a
    missing object, import and validate exact versions, and hand back a runnable
    scene.

## RSI loop

RSI compares one candidate with a fixed Reference: establish a goal and baseline,
make a scoped change, measure under comparable conditions, then retain or revert
using the acceptance rule and human gates. Agent-layer changes vary the Harness
while model and thinking stay fixed. Embodied changes vary the Controller while
the Embodiment and scene version stay fixed.

Agent train evaluations use external runtime handoff through the shared queue.
Harness acceptance, private promotion tests and a human promotion decision are
separate facts. Keep model/thinking context fixed for the comparison and use the
issued Optimizer/Target scopes; a generic tool or guessed ID cannot read test data.

Embodied train acceptance advances the statistical Reference. Human deployment
approval separately publishes a derived Scene and Controller for new Sessions;
active Sessions retain their loaded version. Follow the approved budget and exact
Task/code/material identities. GPU training, real devices and model execution
still need their concrete authorization; a controlled engineering fixture does
not establish learned quality. Imported runtime messages remain operational
evidence; platform grades and human decisions retain their own trusted sources.
