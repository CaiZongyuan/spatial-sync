# Launch and verification

Desktop web is the approved scope. Use one checkout's source and frozen locks.
Only one session can be active; tests must not share a manual session.

## Launch

Follow the root README. Health is `GET /api/v1/health`. Use Synthetic Laboratory
for GPU-free checks. Newton requires `--extra simulation --extra usd`.
Do not resynchronize an environment while it serves another process;
`uv run --no-sync` preserves installed extras. Use one API worker and one Runner.

## Mandatory checks

```powershell
uv run --no-sync python -m pytest tests/contract tests/unit tests/gateway tests/cli -q
uv run --no-sync ruff check apps packages tools tests
pnpm protocol:check
pnpm test:protocol:ts
pnpm test:web
pnpm build
pnpm --filter @spatial-sync/web exec playwright install chromium
pnpm test:e2e
```

Start an owned API first. E2E defaults to API8000 and isolated web5184.
`SPATIAL_API_URL` selects another API; `SPATIAL_WEB_URL` selects an existing web
server. Both must serve the candidate checkout. Include custom browser origins
in `SPATIAL_ALLOWED_ORIGINS`. CI installs base Python dependencies and Chromium.
CLI tests use the installed `lw` entrypoint, actual HTTP, official SDK clients
and stdio; run `uv sync --frozen --group dev` before the environment serves any
other consumer. Each test and benchmark owns its recording directory.

## Information and recording checks

The browser suite includes a real format1 recording fixture with ten independent
pose/location/joint samples. Live device actions and replay share the same stores
and reducers. Replay must make no live session requests or WebSocket connection,
and its action, approval, cancellation and lifecycle controls must be disabled.

```powershell
uv run --no-sync python tools/bench_info.py --entities 1000 --rate-hz 20 --http-hz 10 --seconds 10 --repeats 3
uv run --no-sync python tools/bench_recording.py
```

Run performance commands with an exclusive measurement window. Inspect actual
changes/s, completed HTTP rate, both readers' exact counts/digests, transform
cadence and every budget in specification section12. A `measured` receipt only
means measurements completed; it does not declare the latency budgets passed.
The log benchmark measures admission through actual drain, buffered flush and
metadata close, validates every written frame, and reports queue depth and loop
delay separately. It does not measure fsync durability. Keep benchmark output
outside Git and summarize final results in the PR description.

For crash recovery, force-stop only an owned gateway after a session shorter
than60s has written samples, restart against the same recording root and verify
`incomplete`, recovered duration and browser playback. Also cover an empty or
partial first header: it must become incomplete without inventing samples.

## Real physics and browser checks

```powershell
uv run --no-sync python tools/smoke_newton.py --device cpu
uv run --no-sync python tools/smoke_newton_source.py --scene robot-and-tubes --device cpu --crash
uv run --no-sync python -m pytest tests/sim_runtime -m "not gpu" -q
```

These require installed simulation/USD extras and use the actual Newton engine.
Optional browser journeys use `SPATIAL_NEWTON_E2E=1` and `SPATIAL_TEST_NEWTON=1`.
Failure probes can terminate only the Runner belonging to their own session.

Launch each registered scene/source, select Body and Site, Pause, Step, Reset
and Resume. Two browsers must share the authoritative sample and control results.
Compare paused Inspector and HTTP pose at matching seq/simTime; Body and Site
origins can differ. Close your session before changing scenes. Reload changes
sceneRevision. Quantized JSON poses allow documented rounding tolerance.
GPU submission timing must not be reported as GPU execution time.

## Ownership and cleanup

Use isolated ports, processes, worktrees, build directories and evidence.
Foundation integration reserves API8009/web5189. Developer ports8011/8012/8013
and web5191 are separate. Local evidence lives in `.scratch/lab-foundation`;
prior process evidence is retained in Git history.

Close owned sessions, stop owned processes and verify listeners and Runner
children are gone. Preserve unrelated services. A retained preview needs an
explicit owner and address. Passing local checks does not claim remote CI.
