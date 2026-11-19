---
status: accepted
date: 2026-11-19
---

# User-owned Compute Nodes, safetensors Checkpoints and per-attempt accounting

Lab World uses a self-hosted gateway with authenticated, user-provided `Compute Node`s so teams can attach local GPUs, remote Linux servers and ephemeral cloud notebooks. Training `Job`s return intermediate `Checkpoint`s as tensor-only `safetensors` with JSON metadata so a lost lease can suspend training and a compatible node can resume it. Account for actual GPU time per lease attempt and reuse the over-budget `Approval Gate` when rescheduling exceeds the approved `Training Plan` budget.

## Considered Options

- **One hardcoded `local` node with terminal `node_lost`** loses training progress when an ephemeral session disconnects and prevents remote GPU access.
- **Multi-tenant untrusted node sandboxing and privacy consent tiers** add complexity outside the self-hosting scope.

## Consequences

- Explicit authentication enables non-loopback node connections using revocable per-node credentials. The local CUDA lock covers only colocated GPUs, and timing profiles remain usable across host renames.
- At lease expiry the gateway fences the old `leaseId` before a compatible node can resume. Cross-architecture resumption guarantees statistical continuation only.
- Model export and 256-sample `NativeObservationCapture` verification run on a `Compute Node`. A `checkpoint_finalize` `Job` can prepare an intermediate `Checkpoint` for evaluation on a compatible node. [Job and Checkpoint contracts](../spec/LAB_SELF_SERVICE_EMBODIED_SPEC_V1.md#implementation-decisions)
