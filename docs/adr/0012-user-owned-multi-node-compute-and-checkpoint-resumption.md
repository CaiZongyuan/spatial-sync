---
status: accepted
date: 2026-11-19
---

# User-owned Compute Nodes, safetensors Checkpoints and per-attempt accounting

The approved design extends Lab World's self-hosted gateway to accept authenticated remote connections from user-provided `Compute Node`s alongside the existing local node, so teams can use local GPUs, remote Linux servers and ephemeral cloud notebooks. Transfer intermediate `Checkpoint`s as `safetensors` tensors and JSON metadata so a `Job` can suspend after lease loss and resume on a compatible node. Sum actual GPU time across lease attempts and reuse the over-budget `Approval Gate` when the projected additional GPU cost of resuming exceeds the `Training Plan`'s remaining approved budget after prior charges and existing reservations. [Remote node registration and authentication #292](https://github.com/CaiZongyuan/spatial-sync/issues/292) · [Checkpoint transfer, suspension, resumption and per-attempt billing #293](https://github.com/CaiZongyuan/spatial-sync/issues/293)

## Considered Options

- **One hardcoded `local` node with terminal `node_lost`** loses training progress when an ephemeral session disconnects and prevents remote GPU access.
- **Multi-tenant untrusted node sandboxing and privacy consent tiers** add complexity outside the self-hosting scope.

## Consequences

- Explicit authentication enables non-loopback node connections using revocable per-node credentials. The local CUDA lock covers only colocated GPUs, and timing profiles remain usable across host renames.
- At lease expiry the gateway fences the old `leaseId` before a compatible node can resume. Cross-architecture resumption guarantees statistical continuation only.
- The `Compute Node` exports ONNX and captures 256 native observation witnesses. The gateway independently verifies observation qualification using the exported model and `NativeObservationCapture` evidence. A `checkpoint_finalize` `Job` can produce those artifacts from an intermediate `Checkpoint` on a compatible node. [Job and Checkpoint contracts](../spec/LAB_SELF_SERVICE_EMBODIED_SPEC_V1.md#implementation-decisions)
