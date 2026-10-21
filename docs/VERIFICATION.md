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
The approval browser journey uses `approval_gateway:app --app-dir tests/fixtures`
with `SPATIAL_TEST_APPROVAL=1`: it declares `closeLid` high-risk in the test Catalog
before the real Synthetic host starts. Production device definitions are unchanged.
Set `SPATIAL_STATE_ROOT` to an owned test directory alongside recordings.
CLI tests use the installed `lw` entrypoint, actual HTTP, official SDK clients
and stdio; run `uv sync --frozen --group dev` before the environment serves any
other consumer. Each test and benchmark owns its recording directory.

## Information and recording checks

The latency budgets in information specification section12 use this development
machine's Linux/WSL2 environment, as used for the I3 acceptance, as the reference.
Windows results are diagnostic references and do not gate delivery. Before
optimizing a performance issue, compare the candidate and acceptance baseline
under the same environment, workload and measurement boundary; use
`check-benchmark` to assess the comparison. #70 tracks the Windows difference
and does not block I5 or another ticket. Record the I5 MCP journey on Linux.

The I5 runner gives Claude Code an empty working directory, no built-in tools and
only the `lab` MCP server, against an owned Newton-capable API and web server:

```powershell
uv run --no-sync python tools/agent_journey.py --api http://127.0.0.1:8000 --web http://127.0.0.1:5184 --recordings-root <owned recordings root> --output .scratch/agent-journey
```

The agent launches `robot-and-tubes` with Newton itself. While it works, every
settled tube Site location is checked against physics whenever the rotor is at
rest, and a browser observer compares the viewer's locations and resting poses
with the API. The invocation log must complete the I3 order with agent actors
only. The output holds `receipt.json`, the transcript, checkpoint screenshots
and the zipped recording; publish the recording and transcript as release
assets, never in the repository.

The browser suite includes real format1 and format2 recording fixtures. Format1
retains ten pose/location/joint samples; format2 pins a selected scene version
and compares the final paused state with independent public HTTP reads. Live
device actions and replay share the same stores
and reducers. Replay must make no live session requests or WebSocket connection,
and its action, approval, cancellation and lifecycle controls must be disabled.

```powershell
uv run --no-sync python tools/bench_info.py --entities 1000 --rate-hz 15 --generation-hz 15 --http-hz 10 --seconds 12 --repeats 3
uv run --no-sync python tools/bench_info.py --entities 1000 --rate-hz 15 --generation-hz 15 --http-hz 10 --seconds 12 --repeats 3 --include-newton
uv run --no-sync python tools/bench_recording.py
```

Run performance commands with an exclusive measurement window. Inspect actual
changes/s, completed HTTP rate, both readers' exact counts/digests, transform
cadence and every budget in specification section12. A `measured` receipt only
means measurements completed; it does not declare the latency budgets passed.
`--generation-hz` controls real property writes and defaults to `--rate-hz`,
which independently limits publication. Inspect generated writes, Source
publication and each reader's actual received rows separately. Every loaded
measurement window must deliver at least10,000 changes/s to both readers.
The earlier publication-only stress workload is reproducible with
`--rate-hz 20 --generation-hz 60`; its results are a different workload and
cannot establish a before/after speedup against15Hz generation.
The log benchmark measures admission through actual drain, buffered flush and
metadata close, validates every written frame, and reports queue depth and loop
delay separately. It does not measure fsync durability. Keep benchmark output
outside Git and summarize final results in the PR description.
With the simulation extra installed, `--include-newton` adds a separate CPU FR3
phase after the Synthetic gateway closes. It uses real alternating gripper actions,
two WebSocket readers, registered waits and Runner control timing. The final log
phase measures real output after both gateways close. A default run without this
option explicitly marks Native timing as unavailable; it does not estimate it.

For crash recovery, force-stop only an owned gateway after a session shorter
than60s has written samples, restart against the same recording root and verify
`incomplete`, recovered duration and browser playback. Also cover an empty or
partial first header: it must become incomplete without inventing samples.

## Real physics and browser checks

```powershell
uv run --no-sync python tools/smoke_newton.py --device cpu
uv run --no-sync python tools/smoke_newton_source.py --scene robot-and-tubes --device cpu --crash
uv run --no-sync python tools/smoke_info_newton.py
uv run --no-sync python -m pytest tests/sim_runtime -m "not gpu" -q
```

These require installed simulation/USD extras and use the actual Newton engine.
All four commands above are required before merging changes to
`packages/sim_runtime/**`, `packages/programs/**`, `library/scenes/**`, or a
recaptured Newton preview under `assets/scene-previews/**`. Run the commands
against the candidate with owned state/library/recording roots, retain their
results, and add a `Newton CPU` paragraph with commands, results and tick P95
to the PR. Use Linux/WSL2 for the latency gate; keep Windows observations
separate. A focused native test is additional evidence, not a replacement for
these four checks.
Optional browser journeys use `SPATIAL_NEWTON_E2E=1` and `SPATIAL_TEST_NEWTON=1`.
Failure probes can terminate only the Runner belonging to their own session.

Static previews verify the actual source files used for their capture. After an
input changes, regenerate the affected version/source, for example:
`uv run --no-sync python tools/capture_scene_preview.py robot-and-tubes@2 --source newton --cache-root .scratch/scene-previews`.
Copy the generated capture directory into `assets/scene-previews/`; keep published
scene and asset package bytes unchanged.

Launch each registered scene/source, select Body and Site, Pause, Step, Reset
and Resume. Two browsers must share the authoritative sample and control results.
Compare paused Inspector and HTTP pose at matching seq/simTime; Body and Site
origins can differ. Close your session before changing scenes. Reload changes
sceneRevision. Quantized JSON poses allow documented rounding tolerance.
GPU submission timing must not be reported as GPU execution time.

## Platform operations and native viewer

`GET /api/v1/platform` reads a background cache of managed sessions and gateway
host resources. The GPU-free gateway tests inject a sampler; they do not prove
actual driver telemetry. Verify real CPU/RAM/NVIDIA values separately on a host
with `nvidia-smi`. Unknown values remain null with a reason. An initial CPU
sample needs a second measurement before a percentage is meaningful.

```powershell
uv run --no-sync python -m pytest tests/unit/test_platform_monitor.py tests/gateway/test_platform.py tests/unit/test_native_viewer.py -q
uv run --no-sync python -m pytest tests/sim_runtime/test_native_viewer.py -q
$env:SPATIAL_PLATFORM_NEWTON_E2E = '1'
pnpm --filter @spatial-sync/web exec playwright test platform.spec.ts --grep 'real native'
```

The last two checks need the frozen simulation extras and an owned API. The
browser case opens the actual robot scene's native viewer through the platform
button, checks its Runner identity and repeated URL, reloads the session, opens
the replacement viewer, then stops it and checks backend counts. Ordinary CI
explicitly skips this optional native case. Other operations browser cases
separate contract mocks from the real Synthetic connection/pause/stop journey.

## Ownership and cleanup

Use isolated ports, processes, worktrees, build directories and evidence.
Foundation integration reserves API8009/web5189. Operations integration reserves
API8010/web5190 while the older user session remains active and protected.
Developer ports8011/8012/8013
and web5191 are separate. Local evidence lives in `.scratch/lab-foundation`;
prior process evidence is retained in Git history.

Close owned sessions, stop owned processes and verify listeners and Runner
children are gone. Preserve unrelated services. A retained preview needs an
explicit owner and address. Passing local checks does not claim remote CI.
