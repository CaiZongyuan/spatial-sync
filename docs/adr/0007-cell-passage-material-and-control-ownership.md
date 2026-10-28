---
status: accepted
date: 2026-10-28
---

# Keep Cell Passage material and control state under one Device Program

The proposed Cell Passage workcell program controls the pipetting mechanism and owns the public material snapshot for all configured vessels, compartments, tips and waste. A transfer updates source, tip and destination within that one snapshot; other device programs own their mechanical facts or observations and read the shared material state. We choose this over independent vessel writers, which would require a cross-entity transaction mechanism that the current Program API does not provide, and over an external ledger that would lose automatic recording and action-pipeline guarantees. This ownership is specific to the current single liquid-handling workcell; an additional independent liquid handler requires an explicit ownership design rather than a second material balance.

The same owner records material reservations and control generations. New scene device actions validate the current generation in their source-side callbacks, so a delayed automatic command cannot act after manual handoff. Control requests commit their intent in `accept`, which runs while the simulation is paused; their later `start` only acknowledges the recorded result. Old motion targets must be replaced with safe holding targets, and in-flight material must be retained before handoff completes. Client executors continue to schedule Plans; the gateway does not become a workflow engine.

This uses existing entity properties, declared actions and events. It introduces no new public PlanStatus, InvokeRequest fields or recording message shape. The user approved implementation on 2026-10-28 by invoking pm-development and saying “开始做吧”. The concrete requirements and validation boundaries are in [Cell Passage Simulation](../spec/LAB_CELL_PASSAGE_SPEC_V1.md).
