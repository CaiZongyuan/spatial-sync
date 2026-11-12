---
status: accepted
date: 2026-11-12
---

# Dispatch per-action Policies and guide Waypoint locomotion hierarchically

For the planned embodied training pipeline, multi-action `Embodiment`s will map each declarative `EntityType` action (such as `walkTo`, `standUp` or `bow`) to its own trained `Policy` inside a `Device Program` and `Controller` composition, rather than exposing manual policy-slot switching. For `Waypoint` locomotion, the `Device Program` computes bounded body-frame velocity commands from the current pose and target `Waypoint`, while the `walkTo` `Policy` tracks body-frame velocity commands at control rate; `ObservationSpec` exposes a bounded 1D `command` vector shared across parameterized actions (such as velocity targets and posture targets).

We chose per-action `Policy` dispatch to preserve the platform's single declarative HTTP action pipeline for `Agent`s, Web users, CLI and MCP. We chose hierarchical velocity-guided `Waypoint` locomotion over scene-specific coordinate inputs because velocity-tracking `Policy` packages transfer zero-shot to arbitrary `Scene` packages and new `Waypoint`s without retraining, matching established legged-robotics practice.

This records an accepted design; multi-policy `Controller` composition, `Waypoint` schemas and bipedal guidance are not yet implemented. The complete scope and verification targets are in [Lab Embodied Training](../spec/LAB_EMBODIED_TRAINING_SPEC_V1.md).
