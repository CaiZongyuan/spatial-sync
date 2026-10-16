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
uv run --no-sync python -m pytest tests/contract tests/unit tests/gateway -q
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
