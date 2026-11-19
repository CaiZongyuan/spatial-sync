---
status: accepted
date: 2026-11-19
---

# User-attached multi-node Compute Nodes, safetensors Checkpoints and per-attempt resumption

Lab World operates under a **self-hosted / user-supplied compute model**: users run their own gateway and attach their own `Compute Node`s—including gateway-colocated GPUs, user-owned remote Linux GPU servers, and user-operated ephemeral cloud notebooks such as Google Colab—rather than sharing a multi-tenant public compute pool. To support remote and NAT-restricted user nodes cleanly, the gateway adds an authenticated remote-node mode (lifting the loopback-only `node_gateway_url` restriction when explicit auth is configured), issues one-time registration codes exchanged for scoped, revocable per-node tokens, separates persistent `nodeId` from per-connection `instanceId`, decouples hardware timing profiles from ephemeral hostnames, and scopes local-session CUDA locks solely to colocated GPUs.

Both RL training `Job`s, plan-validation/profiling `Job`s and motion-preprocessing `Job`s schedule across compatible registered `Compute Node`s (verifying pinned trainer dependency versions on claim). Running training `Job`s periodically upload intermediate `Checkpoint`s in **tensor-only `safetensors` plus JSON metadata format** (never unpickling untrusted `.pt` code on the gateway or peer nodes; ONNX export and 256-sample `NativeObservationCapture` verification always execute on a `Compute Node`, including via a dedicated checkpoint-finalize `Job` when evaluating an intermediate `Checkpoint`). When a `Compute Node` disconnects past lease TTL, its monotonic `leaseId` epoch is fenced and the `Job` transitions to `suspended` rather than terminal `failed/node_lost`, allowing resumption from the latest `Checkpoint` on another compatible `Compute Node` (statistical continuation across GPU architectures, not bitwise identity). Resource accounting records additive GPU-hours per lease attempt, and retains the existing over-budget `Approval Gate` whenever re-estimated remaining cost on a slower replacement GPU exceeds the approved `Training Plan` budget.

## Considered Options

- **Single hardcoded `"local"` node with terminal `node_lost`**: loses all elapsed progress when ephemeral Colab sessions disconnect and prevents attaching remote user GPU servers cleanly.
- **Multi-tenant untrusted node sandboxing and privacy consent tiers**: unnecessary complexity while all attached `Compute Node`s belong to the self-hosting user or team; revisited only if a hosted multi-tenant SaaS offering is introduced later.
