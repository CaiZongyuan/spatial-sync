---
status: accepted
date: 2026-11-19
---

# Compiler-rooted Task qualification, bounded rewards and Goal-bound Evaluation Suites

Use the platform-owned declarative `mjlab` environment compiler as the sole observation-qualification trust root for self-service `Task`s, and reserve Python `Task` archives for built-in or human-audited operator use. Combine built-in MDP primitives with a bounded mathematical expression DSL over versioned `lw.reward-state/1` tensors so users can define rewards and terminations as data. To preserve RSI comparability across reward revisions, the server generates embodied `Evaluation Suite`s by `(EntityType action, Goal)` and freezes their success criteria, perturbations and selected episode count at the first approved `Training Plan` for the `Goal`.

## Considered Options

- **Per-Task repository observation profiles** block a user-created `Task` with `observation_source_unverified` until an engineer commits a profile.
- **Python AST whitelisting for custom rewards** exposes attribute and module escape paths when `torch` objects are reachable.
- **Per-Task auto-generated `Evaluation Suite`s** change `suiteContentHash` on reward edits, making `Candidate` and `Reference` incomparable under Wilson lower-bound rules.

## Consequences

- Self-service trust follows the verified compiler and trainer versions. Python archives require a trusted channel because the platform has no OS-level sandbox for them. [Task and observation contracts](../spec/LAB_SELF_SERVICE_EMBODIED_SPEC_V1.md#implementation-decisions)
- A `plan_validation` `Job` steps the environment at the target `num_envs` before the human gate, checking reward finiteness and memory use and attaching a measured GPU-hour estimate to the approval request.
- Agents can run `split: train` independently. The gateway generates and seals `split: test` seeds for human `Controller` promotion, when the first qualified `Candidate` establishes the initial `Reference`.
