# v1.2 continuation

User: `$pm-development` implement `docs/spec/NEWTON_SPATIAL_SYNC_SPEC_V1.2.md`. Scope Phase0/1, P2 A?D, Phase3 interfaces only. Authorized local integration on master; no remote/CI/deployment configured. PM coordinates, independent Developers implement. Root plus at most two active children because earlier account concurrency errors. Chinese concise user progress, continue autonomously.

## Current candidate and open work

Root code `5d6533f67e1fec0cd457d0843eddcafffa74f291`; milestone baseline `633735534aa05376138904582ebb7712a0ea19c9`. All phase implementations integrated but final independent reviewers found four mandatory gaps. See FINAL-REVIEW.md and TRACKER.md. Do not mark complete until repaired and verified.

Active authors:

- `/root/gateway_repair`: `D:/Projects/Robots/spatial-sync-worktrees/review-gateway`, branch issue/v1.2-gateway-review. Auth malformed non-ASCII401/WS1008; failed reload/restart error details/FAILED broadcast/live heartbeat/recovery. Owns _role, Ready no-manifest guard, Session.start/fail/lifecycle/reload/close/task helpers. Initial pre-manifest create stays503/unregistered; never fabricate revision.
- `/root/newton`: review-source worktree/branch issue/v1.2-source-review. Actual subscription-driven GPU gather with truthful full snapshots/recovery and complete Shape/Runner heartbeat diagnostics. Needs bounded design checkpoint/first CPU example PM check before broad execution. Source interest/full snapshot internal local+TLS changes overlap other Session methods; inspect integration carefully.

Reviewers `/root/final_spec` and `/root/final_standards` are parked, non-authors; reuse for affected complete candidate review after repairs. Root+two authors only now. Old protocol/viewer agents are no longer listed.

## Verification and evidence

Before repairs: root165Python passed/1optional skipped14.69s with truly noNewton/Warp,64Web,6TS,generated consistency,build and full Ruff passed. Root final browser and optional affected asset/reliability journeys NOT YET RUN. Final source-interest delta requires CPU/CUDA/runtime and TLS source refresh. Use existing receipts for unaffected comparisons. Preserve failed/skipped runs separately.

Base-only Python: `D:/Projects/Robots/spatial-sync-worktrees/p2-b/.venv/Scripts/python.exe`, cwd root. Run `-m pytest tests/contract tests/unit tests/gateway -q`. Root optional .venv available. Always `uv run --no-sync`; base sync could remove optional libs used by a live preview. Other gates `pnpm protocol:check`, `pnpm test:protocol:ts`, `pnpm test:web`, `pnpm build`, `uv run --no-sync ruff check .`.

E2E target env SPATIAL_API_URL=http://127.0.0.1:8009, SPATIAL_WEB_URL=http://127.0.0.1:5189. `pnpm test:e2e` should run10 core tests with3optional skipped; SPATIAL_NEWTON_E2E=1 enables2asset+1Newton reliability. Sequential isolated API with no manual session. Root starts final demo only after test sessions cleaned.

Prior D:15c2bcc fixed host scene registry for100sparse-ID Synthetic and client read-only query timeout without killing reader/source; real TLS HTTP504 then new WS seq/late reply/pause works. Preserve this. Real16MiB TLS only proves capacity/loss, NOT writer deadline; separate controlled blocked-drain unit proves deadline. Latest combined gather+remoteNewtonCPU passed1/5.16s. All receipts in P2-D report.

Prior C: actualCPU/CUDA gather reorder/lifetime tests, Runner/crash and command/reset passed. Browser uses SwiftShader, including default probes. Full-quality instancing reduces drawcalls without stableFPS improvement. LOD mixed10.27?10.61FPS/2k11.83?12.19FPS.10k two valid4.027/4.028FPS, third1013/noack excluded. Suggested45FPS unmet. Async single-observation readback did not beat sync; no overlap improvement measured. Pipe p95<=.360ms, no shared memory justified. Receipt age is local, not proven end-to-end freshness.

## Resources: preserve ownership

DO NOT stop/reset/close oldAPI8000 or its unknown-owner PAUSED Synthetic session-b469c0e4cf99 (epoch0seq268simTime8.895833333334). API PID36204 exec8139. Frozen B production web5173 PID41600 exec69500, outdir.scratch/preview-b-9a93c83. Foreign5175PID5676 preserved.

Root-owned final candidate API8009 PID40616 exec10943; web5189 PID38344 exec3114. Matching5d6533f before repairs. Rebuild/restart after integration; no manual session created yet. Latest inventory developer8002/5184/8006/5186/8007/5187/8008/5188/8766 absent. Authors announce bounded CPU/GPU execution, root no competing heavy work.

## Finalization

Update SIMPLIFICATION.md for P2D+repairs, ACCEPTANCE.md, specDoD checkboxes only when evidence supports, TRACKER/report/events and generated current/timeline. P2-C-pipeline-decision.md old boundary rejected; supersede after actual interest implementation. Existing P2-C-review.md retained historical evidence but old fullreadback acceptance superseded. Final review/gates, merge and readback required, do not claim externalCI.

Browser file URL opening for offline report was policy rejected earlier; do not serve or render same report via another browser route. Validate JSON/links statically and report offline interactive QA unverified. For actual app CUA after compaction call cua.rewriteDocumentation(), bind fresh tab, preserve unknown user pages. Final app address5189 and owned pausedNewton robot/TCP demo ideally. TCP golden reset[-.07282344,0,1.26703449], handz1.37043449, localTCP[0,0,.1034] pointingdown; joint4=-2,fingers=.02.
