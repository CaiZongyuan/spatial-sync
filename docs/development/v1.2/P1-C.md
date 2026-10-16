# P1-C — Reliability and verified asset consumption

Status: implementation and author validation complete. Actual CPU Newton recovery, real USD/Franka asset consumption, default-launch JSON logging and a functional production baseline are verified. Independent final review and actual local integration remain PM-owned.

Baseline `b794d0f9a7e0f2e2e88b865afc19550f51d1cfda`, branch `issue/p1-c-reliability`. Consumes P1-B generated fallback contract `6362d65d29abe86ebb05287163647b3117801903` (local cherry-pick `b6cf0bd`); generated contracts were not hand edited.

## Implemented

- Strict `continueWithoutObservers` create option, default true, exposed on the homepage and in diagnostics. New Sessions configured false pause the actual source without subscribers. First subscription resumes only an automatic pause; last subscriber removal pauses again. Explicit user pause survives reconnect. Policy operations share `command_lock`, send reliable frames/lifecycle state, and do not synthesize user acknowledgements. Existing Session reuse preserves its existing options.
- Public Reload scene and Restart source controls consume real lifecycle results and the normal revision/snapshot path.
- Atomic simultaneous visual tiers: primary visuals, native fallbackVisuals, colliders, placeholder. Failed partial loads are disposed; successful groups remain exclusive. Mesh fallback, local pose/scale/up-axis, normal generation and separate debug colliders are retained. On-demand inspection reads actual Three bounds/counts/normals without per-frame bounding-box work.
- Opt-in bounded recording of actual R3F callbacks and application time. The profiling CLI checks useful output/counts, requires isolated API and production viewer by default, excludes startup/warmup, records raw samples/repeats/environment/hashes, and closes only Sessions it created.
- Default Uvicorn launch configures an idempotent, dedicated `spatial.gateway` JSON handler at INFO. Reliable command frames, acknowledgements, lifecycle changes and source errors carry Session/revision/epoch/seq, command/request ID and Runner PID where known. Parameters/authentication headers/tokens are excluded. Upstream logger configuration is untouched.

## Evidence

- Real CPU Newton Playwright: **1 passed, 33.5 s** (34.6 s with harness). Normal homepage selected falling-boxes; Body/Site pose matched the same seq/simTime with wire-precision tolerance; two observers shared one Runner PID. Killing only that newly created Runner produced FAILED in both UIs while API health stayed 200. Restart retained Session ID with new PID/revision and increasing sequence. Public reload replaced the Runner/manifest/mapping, and page refresh retained the current Runner. Owned Session cleanup completed.
- [Site Inspector](viewer-p1-evidence/newton-site-inspector.png), [Runner failure](viewer-p1-evidence/newton-runner-failed.png), [reloaded scene](viewer-p1-evidence/newton-reloaded.png).
- Gateway **28 passed**, including observer-policy, asset-route and structured-log paths. Scoped Ruff passes. The default-continue test now waits on actual first published motion; its original fixed 30 ms sleep was shorter than one 30 Hz publish period and was corrected after a concrete false failure.
- Visual tiers **5 passed**: whole native mesh+primitive group, exclusive primary group, partial-load disposal, mesh collider fallback/placeholder, late cancellation cleanup.
- Recorder **2 passed**; profile aggregation **2 Node tests passed**, including independent 50 FPS completed-window expectations and rejection of empty/nonfinite/duplicate-clock/truncated samples. Complete frontend **50 passed**, TypeScript and production build passed.
- Paired real asset browser tests **2 passed** (10.6 s USD, 14.9 s Franka; total 28 s). Housing Y-up GLB and native mesh world bounds match independent golden values within 1e-4 m. Missing binding returns 404 then native mesh loads via real GLB/ETag/304; forcing only the native mesh request to fail chooses the exclusive collider tier. Nine Franka bodies exercise 2–8 simultaneous visuals; TCP world pose matches the same HTTP sample. [USD mesh fallback](viewer-p1-evidence/usd-native-mesh-fallback.png), [collider warnings](viewer-p1-evidence/usd-collider-fallback-warnings.png), [Franka TCP](viewer-p1-evidence/franka-tcp-inspector.png).
- P1-B's inside-bounds Indicator fixture was then consumed from `4e51d81fce3d3cb4f2bc01ff1d99dd9c22c96539` (local `4fb92ad`). Only the affected USD journey was repeated: **1 passed, 7.7 s**, proving the complete two-item mesh+primitive native group, unchanged golden outer bounds, and atomic fallback when the mesh fails.
- Final normal SyntheticSource browser gate against the production viewer and current API: **1 passed, 11.4 s** (12.1 s with harness), covering exact poses, two observers, applied controls, heartbeat, reset, real WS recovery, device state, asset fallback and close.
- Default-launch logging regression starts from WARN/no owned handler, invokes the real app factory, sends real commands and lifecycle transitions, parses emitted JSON and rejects accidental argument/auth content. A separate actual Uvicorn + CPU Newton probe applied pause, killed only newly created Runner 17784, observed FAILED/health200, and closed it; [emitted JSONL](viewer-p1-evidence/gateway-default-logging.jsonl) preserves frame → ack → state order and source failure correlation.

The first Newton browser launch used a gateway process that had imported the old schema before the shared fallback schema update, while its new Runner imported the new schema. Restarting own API aligned the actual target; the full journey then passed. Failed trace: `.scratch/viewer-e2e/newton-launch-attempt-1`. No product compatibility claim is inferred from that stale-process prerequisite.

## Reproduce

```powershell
uv sync --frozen --extra simulation --extra usd
pnpm install --frozen-lockfile
# Start own API with SPATIAL_ALLOWED_ORIGINS including the viewer origin:
uv run --frozen --extra simulation --extra usd uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8002
# In another shell with the viewer already running:
$env:SPATIAL_API_URL = 'http://127.0.0.1:8002'
$env:SPATIAL_WEB_URL = 'http://127.0.0.1:5184'
$env:SPATIAL_NEWTON_E2E = '1'
pnpm --filter @spatial-sync/web exec playwright test newton-reliability.spec.ts
uv run --frozen --extra simulation --extra usd pytest tests/gateway -q
pnpm exec tsx --test tests/perf/viewer_profile_stats.test.ts
pnpm exec tsx tools/profile_viewer.ts --help
```

Newton browser integration skips unless explicitly enabled; SyntheticSource remains the GPU/Newton-free CI gate.

## Measurement contract and limits

The [production functional baseline](viewer-p1-evidence/synthetic-production-baseline.json) records three 10-second windows after 3-second warmups: 598/595/587 actual RAF samples, 24 moving bodies, render FPS p50 59.99/59.98/58.91, transform application p95 approximately 0.1 ms (the browser clock's observed quantization), received frame rate 19.96/19.54/20.16 Hz. Actual source RTF medians remained approximately 1.0. These are observations for this useful workload, not a target-rate or scaling guarantee.

The actual WebGL renderer was **SwiftShader**, despite the machine also containing an RTX 3070 Ti Laptop GPU. Hardware inventory: i7-12700H, 20 logical processors, approximately 32 GiB RAM; browser viewport 1440×960. Raw samples, versions, source-tree/lock/scene hashes and actual canvas dimensions are retained. The baseline preceded the logging-only API delta; the render/source workload and steady measurement path are unchanged. This 24-body result cannot justify instancing for 500/2000/10000 bodies; unoptimized stress measurements remain required in P2-C.

Source solver_ms/readback_ms/publish_ms distributions are explicitly polled latest values, not per-operation p95. CUDA solve timing is labeled by its source solverTimingMode (host submission where applicable), not GPU execution. P2 changes require actual workload measurements; no instancing optimization is included.

## Change impact and bounded simplification

Reviewed the full P1-C scope from its fixed baseline, including uncommitted/new files and immediate consumers. Traced create option → SessionOptions → serialized policy commands → source command result → reliable gateway delivery → existing browser lifecycle. The option defaults true, and existing clients retain the original behavior. Traced optional generated fallback metadata → registered GLB/mesh HTTP → atomic renderer tiers → actual Three geometry; both CPU producer golden values and real browser bounds prove that seam.

The bounded pass retains a separate asset resolver (async loading/resource ownership), frame recorder (opt-in measurement), and source controller (authority/ordering), because they have distinct cancellation and timing obligations. It reuses one cleanup hook for primary/debug groups, avoids per-frame bounds work and React frame-state updates, and logs through one gateway-owned allowlist. No framework switch, extra collector, protocol hand edit or premature instancing change was introduced. Unit/build, affected browser cases and the real default logging probe passed; independent review remains separate from this author pass.

## Ownership and next steps

Own API8002 PID29272 and production viewer5184 PID19904 retained for PM inspection. All test/profiling Sessions are STOPPED and their Runner resources were closed; only the explicitly owned crash probes were terminated. Intended5175 was occupied by foreignPID5676 and left untouched. Old owned P0 viewer5174 PID35688 stopped after PM viewer5173 became available. Root8000/5173 and other developers' services remain untouched.

P1-B owns exporter/schema/assets/routes. This implementation owns only coordinated CreateSession/WS/logging setup hunks in main.py and observer/logging behavior in session_manager.py. Paired asset core `2d09c011` was merged cleanly before its actual browser checks. Next: PM independent review/local integration, then a new P2-C branch for real unoptimized stress workload measurements before optimization. P2-A owns stream files; no concurrent stream edits or heavy render stress runs occurred during its benchmark reservation.
