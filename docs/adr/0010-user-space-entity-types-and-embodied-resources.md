---
status: accepted
date: 2026-11-19
---

# Immutable user-space EntityTypes, append-only embodied resources and unified policy host archetypes

To let users and Agents onboard custom robot morphologies and new actions without editing repository files under `devices/` or `packages/programs/`, user-defined robot `EntityType`s are stored as **immutable, versioned user-library declarations** named `u-<slug>/v<N>`. Keeping the existing `<name>/v<N>` identifier syntax preserves compatibility with `Task` and `Evaluation Suite` `implements` parsers while reserving unprefixed namespaces for built-in repository `EntityType`s. `Scene` device bindings pin the referenced `EntityType` content hash so published `Scene Version`s never drift, and `Evaluation` comparison identity hashes include referenced user `EntityType` digests.

User write APIs allow append-only registration of `Embodiment`s, `Robot Model`s and `Controller` headers (`currentVersion: null`) in the user library without shadowing built-in `Embodiment` or `Robot Model` IDs; concrete `Controller` versions continue to be produced exclusively through human-approved promotion transactions (`deployment_authority`). User robot `EntityType` templates automatically inject the standard telemetry properties required by completion conditions (`phase`, `activeAction`, `lastCompletedAction`, `fallen`, `tiltDeg`, `baseHeight` and optional tracking error). Rather than introducing a parallel program stack, the existing robot-agnostic `embodied.policy` `Device Program` is extended to cover four declarative completion archetypes—`phase_clip` (fixed or looped phase/`Reference Motion` playback), `posture_recovery`, `command_track` and `waypoint_nav` ([ADR-0008](0008-multi-policy-dispatch-and-velocity-guided-waypoint-locomotion.md))—and exposed through the platform's generic HTTP action pipeline and generic MCP catalog discovery tools (never generating per-action MCP tools).

## Considered Options

- **Repository-only `devices/types/*.yaml` and per-morphology `packages/programs/` classes**: preserves a single static directory, but forces platform source changes for every user morphology or custom action.
- **Mutable user `EntityType` definitions**: simpler to edit in place, but silently mutates existing `Scene Version` behavior and invalidates `Evaluation` comparability.

## Consequences

- Partially amends the `AGENTS.md` device-behavior location contract (approved with the user) while keeping `packages/info`, the HTTP API and MCP tools completely device-agnostic.
- Single-robot-per-scene policy deployment constraints remain explicit until multi-agent scene authority is separately specified.
