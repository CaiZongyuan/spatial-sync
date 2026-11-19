---
status: accepted
date: 2026-11-19
---

# Immutable user-space EntityTypes, append-only embodied resources and unified policy host

To let users and Agents register new robot morphologies and actions without editing repository code, store user robot `EntityType`s as immutable `u-<slug>/v<N>` library declarations pinned by `Scene` bindings and `Evaluation` comparison identities. Expose append-only registration of `Embodiment`s, `Robot Model`s and `Controller` headers, with `Controller` versions issued through human-approved promotion. Extend the generic `embodied.policy` `Device Program` with `phase_clip`, `posture_recovery`, `command_track` and `waypoint_nav` so new morphologies share the HTTP action pipeline and MCP Catalog discovery. [ADR-0008](0008-multi-policy-dispatch-and-velocity-guided-waypoint-locomotion.md)

## Considered Options

- **Repository-only device types and per-morphology programs** require platform source changes for every user morphology or custom action.
- **Mutable user `EntityType`s** make in-place edits easier but allow published `Scene Version` behavior and `Evaluation` comparability to drift.

## Consequences

- User IDs preserve the existing `Task` and `Evaluation Suite` `implements` grammar and reserve unprefixed IDs for built-ins. The device-behavior contract covers user declarations, while `packages/info`, the HTTP API and MCP tools stay device-agnostic. [AGENTS.md](../../AGENTS.md)
- User registration preserves built-in `Embodiment` and `Robot Model` IDs. A new `Controller` header has `currentVersion: null` until promotion issues a version under `deployment_authority`.
- User robot `EntityType`s automatically declare the shared completion telemetry `phase`, `activeAction`, `lastCompletedAction`, `fallen`, `tiltDeg`, `baseHeight` and optional tracking error.
- Policy deployment authorizes a single robot per `Scene`.
