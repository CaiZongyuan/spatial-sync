# Launch and verification

The final integrated product candidate is `3896875`; [the final receipt](v1.2/FINAL-VALIDATION.md) records its exact checks and retained preview. Earlier milestone reports preserve their original input revisions. Desktop web is the supported verification scope. API and web must use one checkout's source and frozen locks.

## Start

```powershell
uv sync --frozen
pnpm install --frozen-lockfile
uv run --frozen python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

In a second terminal run `pnpm dev`, then open `http://127.0.0.1:5173`. Health is `GET /api/v1/health`; the homepage must contain a registered scene before Launch is enabled. Select Synthetic Laboratory for a path without Newton/Warp/GPU. API health remains live when a Newton source fails.

For Newton, install and select extras on the launch command:

```powershell
uv sync --frozen --extra simulation --extra usd
uv run --frozen --extra simulation --extra usd python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

An ordinary `uv run` without those extras can remove optional packages. Use explicit extras consistently, or `uv run --no-sync` after the exact environment is prepared. Do not resynchronize an environment while a preview using it is running. Use one API worker and one authoritative Runner.

## Drive and observe

Choose a scene/source, launch the workbench, select a Body and then a Site, Pause, Step, Reset and Resume. The Inspector shows the latest authoritative seq/simTime, while the canvas may intentionally interpolate. Paused Inspector values can be compared with `GET /sessions/{id}/pose?key=...` at the same seq. A reset Newton falling-boxes anchor is `(1,0,1.5)`; its child Body origin is different. All endpoint paths start with `/api/v1`.

Use two browsers on the same session. Closing the session through the UI releases it before selecting a different scene/source. Keep process/PID ownership explicit before a failure test; terminate only the Runner created by that test. Scene reload keeps the Session ID, changes revision, and requires a new Manifest and complete snapshot.

## Checks

```powershell
uv run --frozen python -m pytest tests/contract tests/unit tests/gateway -q
pnpm protocol:check
pnpm test:protocol:ts
pnpm test:web
pnpm build
pnpm --filter @spatial-sync/web exec playwright install chromium
pnpm test:e2e
```

The E2E API target defaults to 8000 and its isolated web server to 5184. Set `SPATIAL_API_URL` for another owned API; `SPATIAL_WEB_URL` can select an already running web target. Tests must not share an active manual session because the MVP permits one active session. Chromium's HTTP offline toggle may leave existing WebSockets connected; the demonstrated recovery probe closes a real upstream WebSocket relay and reconnects to the actual gateway.

Newton/import/GPU checks live under `tests/sim_runtime` and the `tools/smoke_newton*.py` commands. Run with explicit simulation/USD extras; these are non-gating. Quantized JSON positions use six decimals, so same-sample comparisons allow the corresponding rounding tolerance. GPU solver submission timing is not GPU execution time; reports identify that measurement scope.

## Evidence and cleanup

Per-issue reports, receipts and browser images are under `docs/development/v1.2`; the tracker and offline timeline distinguish integrated results from pending work. No GitHub CI result exists without an actual remote run. Close owned sessions, stop API/web/reference servers with their creating terminal or known process tree, and verify listeners/children are gone. Preserve the final preview only when its owner and retained address are recorded. Ignored `.scratch` logs are local evidence, not committed delivery artifacts.

## Optional final journeys

For a fresh isolated owned API with the simulation and USD extras installed, set both flags to run all optional browser journeys:

```powershell
$env:SPATIAL_NEWTON_E2E='1'
$env:SPATIAL_TEST_NEWTON='1'
$env:SPATIAL_API_URL='http://127.0.0.1:8009'
$env:SPATIAL_WEB_URL='http://127.0.0.1:5189'
pnpm test:e2e
```

The final integrated run executed15tests without skips. With both flags unset, four optional Newton/asset scenarios are skipped and11Synthetic/core tests remain. The diagnostics test terminates only the Runner belonging to its own created session. Do not run that suite against the retained manual demonstration; use an isolated API or close only a session you own first. A custom preview origin must be included in `SPATIAL_ALLOWED_ORIGINS` when launching its API. Final preview5189 uses origin `http://127.0.0.1:5189`.

Ordinary HTTP Pose reads the most recent complete published cache without a source RPC. Its seq/simTime/staleMs belong to that sample. Explicit snapshot and subscription recovery may request a full capture; narrow periodic interests never justify merging stale rows into a new complete sample.
