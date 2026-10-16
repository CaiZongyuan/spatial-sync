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
| P2-B Subscriptions/keyframes | Protocol developer (parked) | `issue/p2-b-subscriptions` | Stream integrated | `930aaca` via `9a93c83`; real JSON/binary loss/recovery/Inspector/camera verified; C actual visibility/raycast pending |
| P2-C Extraction/scaling | Viewer developer | `issue/p2-c-scaling` / `../spatial-sync-worktrees/p2-c` | In progress | Baseline `4bb6217`; actual stress scenes, instancing/LOD/culling and gather/readback evidence |
| P2-D Remote transport | Newton developer | `issue/p2-d-remote` / `../spatial-sync-worktrees/p2-d` | In progress | Baseline9a93c83; typed bounded TLS host/RemoteSource, receive clock, failure/cleanup; no network pickle |

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

Phase0/1 and P2-A are integrated. P2-B complete source/evidence930aaca is reviewed and integrated at9a93c83b5cdb47607afacb752d242ad322aa1267. Root refreshed128Python/63Web/regeneration; author final6TS/build/Ruff and4actualE2E pass. Generated LF checkout and optional Warp collection repairs are integrated. P2-B still has an explicit C-owned overall acceptance condition: actual mesh/instance/Site visibility and raycast exclusion, not just Inspector state.

C owns scalable rendering/gather and that shared visibility condition. Actual SwiftShader baselines exist at100/500/mixed/2k with all bodies/triangles checked. Original10k timed out before valid sampling; no fabricated FPS. Critical comparisons will repeat with fixed fixtures/camera/format/delta and no competing heavy browser load. Shared memory remains conditional on a proven Pipe bottleneck.

D has started in a separate worktree from9a93c83: authenticated TLS host reuses NewtonSource, Gateway RemoteSource replaces only transport. No network pickle; Gateway receive clock, typed bounds and failure/cleanup/public journeys are required. B is parked after cleaning8007/5187. Keep root plus two active developers after earlier account concurrency failures. Final epic survey, full integrated gates and fresh independent Standards/Spec reviews remain pending.

Root API8000 PID36204 exec8139 and web5173 PID13676 exec1257 are retained; the API was restarted after B schema changes. No active manual Session is owned. Use uv --no-sync or explicit extras. C owns8006/5186 and reconciles old8002/5184; D owns suggested8008/5188 plus a dynamic or8766 loopback TLS listener. Announce bounded CPU/GPU/browser benchmark windows and release them. Foreign5175 is preserved. No public deployment is configured.
