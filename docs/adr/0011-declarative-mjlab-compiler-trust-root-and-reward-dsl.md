---
status: accepted
date: 2026-11-19
---

# Compiler-rooted Task qualification, bounded reward expression DSL and Goal-bound Evaluation Suites

Self-service `Task` creation uses a **declarative `mjlab` environment compiler** as the sole unreviewed authoring channel for users and Agents; arbitrary Python `Task` archives (`pyproject.toml` entry points) are restricted to built-in or human-audited operator packages because the platform does not provide an OS-level sandbox. Correspondingly, observation-qualification trust moves from per-`taskContentHash` files under `library/observation-profiles/` to a **platform-owned compiler observation profile** that pins the declarative compiler implementation hash and verified `mjlab`/`rsl_rl` package versions while validating each `Task` purely as structured data (extending `lw.obs-spec` and `lw.policy-native-observation-samples` backward-compatibly for phase and reference-frame channels).

Each declarative `Task` locks its `Scene Version`, robot entity, `EntityType` action, control rate, action space, episode randomization/curriculum and `ObservationSpec`, while combining built-in MDP primitives (Layer A) with an optional **non-Python expression DSL** (Layer B) evaluated over versioned `lw.reward-state/1` tensors (`xyzw` quaternions, canonical `Z-up` SI units, bounded AST depth/node count, no imports/attribute traversal/unbounded tensor allocations, plus runtime `nan_to_num` and magnitude clamping). Before a `Training Plan` reaches its human `Approval Gate`, a compute-node **plan validation and profiling `Job`** compiles and steps the environment at the target `num_envs` to verify DSL finiteness/memory and attach a measured GPU-hour estimate directly to the approval request.

To preserve RSI comparability across iterative reward revisions, server-generated embodied `Evaluation Suite`s bind to `(EntityType action, Goal)` rather than regenerating on every `Task` edit: success predicates, perturbation dimensions and minimum sample count (at least 30 perturbed episodes) freeze at the `Goal`'s first `Training Plan` `Approval Gate`, with `split: test` seeds generated and sealed exclusively by the gateway. Agents may trigger `split: train` `Evaluation`s freely; `split: test` runs only during human `Controller` promotion, where the first qualified `Candidate` establishes the initial `Reference`.

## Considered Options

- **Per-Task repository observation profiles**: blocks every user-created `Task` with `observation_source_unverified` unless an engineer commits a profile file.
- **Python AST whitelisting for custom rewards**: vulnerable to attribute/module escape paths whenever `torch` objects are reachable.
- **Per-Task auto-generated `Evaluation Suite`s**: changes `suiteContentHash` on every reward tweak, making `Candidate` and `Reference` non-comparable under Wilson lower-bound rules.
