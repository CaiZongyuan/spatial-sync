# v1.2 delivery tracker

Authority: [Technical specification v1.2](../../spec/NEWTON_SPATIAL_SYNC_SPEC_V1.2.md).
Baseline: `633735534aa05376138904582ebb7712a0ea19c9`. Delivery target: local integrated application on `master`.

The repository has no remote tracker or remote Git origin. This file is the task tracker. The user authorized development against the specification; no additional product clarification is required because section 17 supplies defaults. Phase 3 requires interfaces only. Phase 2 starts after the MVP works, and evidence governs optimizations and shared memory.

| Issue | Owner | Branch / worktree | Status | Acceptance / next |
|---|---|---|---|---|
| P0-A Newton baseline | Newton developer | `issue/p0-a-newton` / `../spatial-sync-worktrees/p0-a` | In progress | Pin real Newton APIs; CPU/GPU smoke; labels, FK, reference viewer |
| P0-B Protocol | Protocol developer | `issue/p0-b-protocol` / `../spatial-sync-worktrees/p0-b` | In progress | Pydantic authority, generated JSON Schema/TS, cross-language fixtures |
| P0-C Gateway + SyntheticSource | Unassigned | Pending P0-B | Blocked | Ordered reliable commands, sessions, heartbeats, two clients, slow-client isolation |
| P0-D Viewer | Unassigned | Pending P0-B | Blocked | TanStack file routing, R3F, inspector, interpolation and visible controls |
| P0-E Newton integration | Newton developer | To be created after P0-A | Blocked | Spawned single runner, two pipes, reliable command frames, failure isolation |
| P1-A Robot | Unassigned | Pending P0 | Not started | Imported 7-DOF arm, site anchors, solver comparison, pose/joint queries |
| P1-B USD/assets | Unassigned | Pending P0 | Not started | USD path labels, GLB export/binding/fallback, compatibility evidence |
| P1-C Reliability | Unassigned | Pending P0 | Not started | Diagnostics, scene replacement, restart/reconnect, entity state |
| P2-A..D Scaling/transport | Unassigned | Pending verified MVP/profiling | Not started | See spec section 12; record all unmet items explicitly |

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

Integrate P0-B when generated contracts pass; dispatch gateway and viewer on that baseline. Integrate P0-A independently, then complete source adapter and end-to-end Newton journeys. Preserve phase ordering for later work, simplify before final validation and independent review, and generate the offline timeline from the same report/journal facts.
