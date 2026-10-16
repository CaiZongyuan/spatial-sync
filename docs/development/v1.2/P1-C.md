# P1-C — Reliability and measurement checkpoint

Status: coherent implementation checkpoint. Actual CPU Newton recovery browser journey passed. Bound-GLB/native-mesh browser bounds, production baseline, bounded simplification and independent final review remain pending paired P1-B integration.

Baseline `b794d0f9a7e0f2e2e88b865afc19550f51d1cfda`, branch `issue/p1-c-reliability`. Consumes P1-B generated fallback contract `6362d65d29abe86ebb05287163647b3117801903` (local cherry-pick `b6cf0bd`); generated contracts were not hand edited.

## Implemented

- Strict `continueWithoutObservers` create option, default true, exposed on the homepage and in diagnostics. New Sessions configured false pause the actual source without subscribers. First subscription resumes only an automatic pause; last subscriber removal pauses again. Explicit user pause survives reconnect. Policy operations share `command_lock`, send reliable frames/lifecycle state, and do not synthesize user acknowledgements. Existing Session reuse preserves its existing options.
- Public Reload scene and Restart source controls consume real lifecycle results and the normal revision/snapshot path.
- Atomic simultaneous visual tiers: primary visuals, native fallbackVisuals, colliders, placeholder. Failed partial loads are disposed; successful groups remain exclusive. Mesh fallback, local pose/scale/up-axis, normal generation and separate debug colliders are retained. On-demand inspection reads actual Three bounds/counts/normals without per-frame bounding-box work.
- Opt-in bounded recording of actual R3F callbacks and application time. The profiling CLI checks useful output/counts, requires isolated API and production viewer by default, excludes startup/warmup, records raw samples/repeats/environment/hashes, and closes only Sessions it created.

## Evidence

- Real CPU Newton Playwright: **1 passed, 33.5 s** (34.6 s with harness). Normal homepage selected falling-boxes; Body/Site pose matched the same seq/simTime with wire-precision tolerance; two observers shared one Runner PID. Killing only that newly created Runner produced FAILED in both UIs while API health stayed 200. Restart retained Session ID with new PID/revision and increasing sequence. Public reload replaced the Runner/manifest/mapping, and page refresh retained the current Runner. Owned Session cleanup completed.
- [Site Inspector](viewer-p1-evidence/newton-site-inspector.png), [Runner failure](viewer-p1-evidence/newton-runner-failed.png), [reloaded scene](viewer-p1-evidence/newton-reloaded.png).
- Gateway **25 passed**, including 4 observer-policy tests: zero/one/two/zero subscribers, explicit pause, default continue, reload/close and HTTP strict boolean validation. Scoped Ruff passes.
- Visual tiers **5 passed**: whole native mesh+primitive group, exclusive primary group, partial-load disposal, mesh collider fallback/placeholder, late cancellation cleanup.
- Recorder **2 passed**; profile aggregation **2 Node tests passed**, including independent 50 FPS completed-window expectations and rejection of empty/nonfinite/duplicate-clock/truncated samples. Affected frontend boundary suite **21 passed**; TypeScript passed.

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

No performance result or optimization claim exists yet. The prepared CLI reports actual completed one-second RAF windows (nearest-rank p05/p50/p95), per-frame apply time, draw calls/objects, received frames, raw source polls, actual WebGL renderer/resolution, hardware, versions and source-tree/lock/scene hashes. It rejects an already active API, missing useful output, failed/disconnected source, empty/truncated sampling and nonfinite output.

Source solver_ms/readback_ms/publish_ms distributions are explicitly polled latest values, not per-operation p95. CUDA solve timing is labeled by its source solverTimingMode (host submission where applicable), not GPU execution. P2 changes require actual workload measurements; no instancing optimization is included.

## Ownership and next steps

Own API8002 PID26672 and viewer5184 retained for paired integration. Intended5175 was occupied by foreignPID5676 and left untouched. Old owned P0 viewer5174 PID35688 stopped after PM viewer5173 became available. Root8000/5173 and other developers' services remain untouched.

P1-B owns exporter/schema/assets/routes. This checkpoint owns only coordinated CreateSession/WS hook hunks in main.py and observer policy in session_manager.py. Next: merge the paired asset checkpoint, verify real GLB/native mesh bounds/group exclusivity, measure a production baseline, simplify, refresh affected checks, then independent review/local integration.
