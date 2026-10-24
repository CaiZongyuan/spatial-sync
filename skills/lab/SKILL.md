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
   lw scene ls --launchable --json
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

The following workflows are planned, not included in this release. Check the
shipped skill list before using a newer workflow; report unavailable capabilities
instead of inventing its commands or calling an assumed API.

| Workflow | Skill when delivered |
| --- | --- |
| Run approved training, evaluate and request promotion | lab-train-evaluate |
| Build and calibrate evaluations and their reference baseline | lab-eval-build |
| Improve an Agent's Harness | lab-harness-optimize |

## RSI loop

RSI compares one candidate with a fixed Reference: establish a goal and baseline,
make a scoped change, measure under comparable conditions, then retain or revert
using the acceptance rule and human gates. Agent-layer changes vary the Harness
while model and thinking stay fixed. Embodied changes vary the Controller while
the Embodiment and scene version stay fixed.

Agent train evaluations use external runtime handoff; automatic Harness optimization
and promotion remain later workflows.
Use the shipped interfaces for embodied evaluation and approved training. Imported
runtime messages are operational evidence; platform evaluation results and human
approval decisions retain their own trusted sources.
