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
| P2-B Subscriptions/keyframes | Protocol developer (parked) | `issue/p2-b-subscriptions` | Integrated | `930aaca` via `9a93c83`; real JSON/binary stream and C renderer/raycast verification retained; GPU interest repair below |
| P2-C Extraction/scaling | Viewer developer (parked) | `issue/p2-c-scaling` / `../spatial-sync-worktrees/p2-c` | Integrated with review gap | `0cf3e25` integrated; stress/render/readback evidence retained; active-observer GPU target repair below |
| P2-D Remote transport | Newton developer | `issue/p2-d-remote` / `../spatial-sync-worktrees/p2-d` | Integrated | `15c2bcc` via `5d6533f`; TLS auth, shared scene registry, read-only deadline recovery, real CPU/browser evidence |
| FINAL-G Gateway repair | `/root/gateway_repair` | `issue/v1.2-gateway-review` / `../spatial-sync-worktrees/review-gateway` | Integrated | `40e1a8e` via `465a152`; PM5public regressions and author170base checks pass; complete final review pending |
| FINAL-R TLS repair | `/root/gateway_repair` | `issue/v1.2-remote-review` / `../spatial-sync-worktrees/review-remote` | In progress | Typed interest/snapshot barrier exchange, exact membership and relative heartbeat age across real TLS |
| FINAL-S Source repair | `/root/newton` | `issue/v1.2-source-review` / `../spatial-sync-worktrees/review-source` | In progress | Subscription-driven GPU gather plus complete snapshot recovery; actual Shape/Runner heartbeat diagnostics |

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

Integrated candidate is `465a152518437051e637a3213212c7f083eb8414` including Gateway repair. Phase0/1 and P2 A?D code are integrated; final acceptance remains open because independent Standards/Spec reviews found four mandatory gaps. See [findings and repair acceptance](FINAL-REVIEW.md). No scope or specification relaxation was accepted.

Gateway repair is integrated; its author now owns the isolated TLS subissue, while Newton owns local source/Session/UI integration. Root plus at most two children only. Gateway owns authentication and source-failure lifecycle; Newton owns source interests/snapshots/local+TLS transport and diagnostics. Their shared session-manager changes require integration inspection. The first GPU-interest safety example needs a bounded PM check before broad validation. After integration, refresh affected independent review, complete GPU-free gates, actual browser journeys, and affected CPU/CUDA/TLS paths.

Before repairs, final root checks passed165Python (1optional skipped) without Newton/Warp,64Web,6TS,regeneration,build and full Ruff. Final integrated browser/optional source journeys are pending. SwiftShader render measurements do not prove a hardware FPS result; suggested45FPS remains unmet, including two valid10k runs near4.03FPS. No shared-memory, solver-overlap, WAN or end-to-end freshness improvement is claimed.

Root final preview is reserved at8009/5189: API PID40616 exec10943, web PID38344 exec3114, matching candidate before repairs. Rebuild/restart for latest source. Preserve old8000 PID36204 and frozen5173 PID41600, including unknown-owner paused `session-b469c0e4cf99`; do not reset/close it. Foreign5175 PID5676 is outside task ownership. Latest inventory shows developer ports8002/5184/8006/5186/8007/5187/8008/5188/8766 absent. Use `uv run --no-sync` to preserve optional extras. No Git remote, external CI or deployment is configured.
