# v1.2 delivery tracker

Authority: [Technical specification v1.2](../../spec/NEWTON_SPATIAL_SYNC_SPEC_V1.2.md).
Baseline: `633735534aa05376138904582ebb7712a0ea19c9`. Delivery target: local integrated application on `master`.

The repository has no remote tracker or remote Git origin. This file is the task tracker. The user authorized development against the specification; no additional product clarification is required because section 17 supplies defaults. Phase 3 requires interfaces only. Phase 2 starts after the MVP works, and evidence governs optimizations and shared memory.

| Issue | Owner | Branch / worktree | Status | Acceptance / next |
|---|---|---|---|---|
| P0-A Newton baseline | Newton developer | `issue/p0-a-newton` | Integrated | CPU/CUDA/FK/import/Site evidence and review repairs |
| P0-B Protocol | Protocol developer | `issue/p0-b-protocol` | Integrated | Pydantic authority, generated TS/schema, contract tests |
| P0-C Gateway/Synthetic | Protocol developer | `issue/p0-c-gateway` | Integrated | Commands, reliable pause final sample, global reload sequence, step bound |
| P0-D Viewer | Viewer developer | `issue/p0-d-viewer` | Integrated | Real Synthetic/Newton browser and exact Inspector/Site acceptance |
| P0-E Newton integration | Newton developer | `issue/p0-e-newton-source` | Integrated | Spawn, dual Pipe, real CPU/CUDA, failure/restart and cleanup |
| P1-A Robot | Newton developer | `issue/p1-a-robot` | Integrated | `d60804f`; official FR3, MuJoCo CPU/CUDA, exact TCP golden; Featherstone contact limitation |
| P1-B USD/assets | Protocol developer | `issue/p1-b-assets` | Integrated | `aadf7d6`; real Y-up USD, content-hashed GLB/assets, complete fallback groups/browser |
| P1-C Reliability | Viewer developer | `issue/p1-c-reliability` | Integrated | `fc6bf49`; actual Newton recovery, observer policy, fallback, default correlated logs |
| P2-A Binary | Newton developer | `issue/p2-a-binary` / `../spatial-sync-worktrees/p2-a` | Integrated | `ec32d8a` via `4bb6217`; non-author review, actual mixed-format browser and comparable profiling |
| P2-B Subscriptions/keyframes | Protocol developer | `issue/p2-b-subscriptions` / `../spatial-sync-worktrees/p2-b` | In progress | Scope/delta/recovery/UI; real-source high-water regression `8f33d1a` oracle checked |
| P2-C Extraction/scaling | Viewer developer | `issue/p2-c-scaling` / `../spatial-sync-worktrees/p2-c` | In progress | Baseline `4bb6217`; actual stress scenes, instancing/LOD/culling and gather/readback evidence |
| P2-D Remote transport | Newton developer (idle) | Create after capacity release | Not started | Typed bounded transport, auth/TLS, Gateway receive clock, failure/cleanup; no network pickle |

PM owns integration, tracker, reports and resource allocation. Independent Developers own source implementation. Final review uses non-authors, with Standards and Spec reported separately. The installed Matt implementation/retro entrypoints have no runtime-specific tracker configuration; direct developer dispatch and the installed thin methods are the host equivalent.

## Resources and verification risks

- Root worktree: PM and local integration only. Every implementation issue has its own branch/worktree and evidence under `.scratch/<issue>`; dependencies/build output stay within that worktree.
- Python root project and `uv.lock`: Newton developer during P0-A. Protocol developer owns `packages/protocol`, root pnpm configuration and protocol contract tests. Shared contract changes require PM coordination.
- GPU: RTX 3070 Ti Laptop, 8 GiB, driver 610.62; reserved by Newton developer for bounded smoke/benchmark execution. API/UI tests use SyntheticSource and require no GPU.
- Ports: API `127.0.0.1:8000`, web `127.0.0.1:5173`, reference viewer `127.0.0.1:8080`; reserve only when launched. Integration PM owns final preview processes. Developer tests must use distinct ports.
- No database, accounts, cloud services, remote publication or shared memory are introduced for the MVP.
- High-risk failures: wrong quaternion/axis or doubled hierarchy transforms; anchor confused with body origin; reset seq rollback; interpolation over pause gaps; command frames overwritten; duplicate commands executed twice; concurrent websocket writers; slow observers blocking solver; CUDA staging reused before copy completion; failed Runner killing API.
- Verification entrypoints are new: developers must document and execute the smallest public entrypoints, then supply commands, actual versions, exit results and remaining gaps. No passing-test or independence claims without execution evidence.

## Current continuation

Phase 0 and Phase 1 are integrated at `b76a782`. P2-A is independently reviewed and integrated at `4bb62179999667fcd596d216082bff8a5d23ab02`. Author evidence is 115 Python, 55 Web, 6 TS, two real E2E and build; PM independently ran four affected Python tests on the merged source. Initial root TS byte determinism failed because Windows checkout introduced CRLF while the generator writes LF. The protocol owner is assigned a persistent checkout fix, with the byte oracle retained.

P2-B and P2-C are the two active developers. Keep root plus two active children after account concurrency failures; A remains idle until B frees capacity for P2-D. All Phase 2 items remain required. Shared memory is conditional on an actual Pipe bottleneck, and Phase 3 requires extension interfaces only. Each frozen candidate gets bounded simplification, affected validation and non-author review before integration. Final independent Standards/Spec milestone reviews and full integrated gates remain pending.

Root web `127.0.0.1:5173` PID13676 is retained; API8000 is stopped and owned FR3 session is STOPPED. Restart API from integrated source before further actual browser evidence. Root environment has simulation/USD extras: use `uv run --no-sync`, or explicit extras, and never sync the live environment.

P1-C API8002/web5184 remain the viewer owner's resources with all sessions STOPPED; reconcile cleanup/reuse for P2-C (suggested8006/5186). P2-A8005/5185 and benchmark children are stopped, CPU/network reservation released. Old API8001 and web5174 are stopped with exact owned process/listener receipts. Foreign5175 is outside this task and preserved. Announce bounded heavy CPU/GPU/network execution and release it promptly.
