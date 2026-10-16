# P0-E — Isolated NewtonSource

Status: source ready for independent review and PM integration. Issue baseline:
`78f07c6e709c5a294cb431be4ffe2c60594cea48`; shared gateway/guard dependency:
`384d712e04e0ba71833aeea17c81dbac4f4570e5`. This candidate owns
`packages/transport`, Newton runner/pacer/extractor, the single gateway adapter
`apps/api/app/sources/newton_source.py`, its tests, and the source smoke CLI.

## Result and contracts

`NewtonSource(session_id)` starts exactly one `multiprocessing` **spawn** process.
The gateway imports no Newton or Warp code, including when importing the spawn target.
The child owns the only writable Model/State/Solver. The initial Manifest and full snapshot
arrive together before `start()` returns. Physics/contact and IK kernels warm during
LOADING, followed by reset + FK; the advertised initial state has simTime=0 and epoch=0.
Startup has a 60 s deadline; warmup is not a steady-state measurement.

`packages/transport` owns both Pipe endpoints and the compact frame codec. The
little-endian header is `<4sHHIIQddI` (44 bytes): `NSYN`, version, kind, numeric revision,
epoch, uint64 seq, simTime, sampleTimeMs, body count. Each pose is `<I7f` (32 bytes).
Session/revision string identity is negotiated through the reliable Manifest.

Periodic samples use a sending thread and one latest-only slot; a blocked Pipe never
blocks its producer. The gateway frame reader also coalesces samples into one pending
event-loop callback. Control uses a separate duplex Pipe, single sending thread in each
direction, and a reliable bounded queue. Command snapshots/results travel over control,
never the replaceable periodic slot. Sources return command state/frame solely through
`CommandResult`; `events()` carries lifecycle/error events, avoiding duplicate broadcasts.

The source keeps the newest authoritative seq, so late periodic frames cannot overwrite
a pause/step/reset/resume result. Local seq never resets with epoch. Gateway owns the
Session-wide restart offset and numeric revision. Pause flushes the final full reliable
frame; pause/resume/step/reset metadata align with ack and state. Gateway performs
frame → ack → state fanout. The shared `MAX_MANUAL_STEPS=1000` rejects larger requests
with `invalid_args`; the runner uses the same protocol validation and never clamps count.

The runner checks commands only at step boundaries. RTF pacing and publish timing use
separate monotonic wall clocks: default RTF=1, lag budget=250 ms, publish target=30 Hz.
Excess catchup is discarded and counted. Unlimited RTF uses bounded batches so control
and publication remain reachable. These targets are not hardware throughput guarantees.

## Readback ownership

CPU captures make one bulk copy at the step boundary. CUDA owns two independent slots,
each with a device staging array, pinned CPU array, and events. The simulation stream
copies body_q into the slot and records readiness before its next State write. A separate
copy stream waits for readiness, copies to pinned host memory, and records completion.
A readback thread waits for that event, freezes the complete host sample, validates poses,
and publishes it. A slot remains owned until its publication callback finishes. Periodic
capture skips when both slots are occupied; it never waits for a buffer or GPU completion.
Reliable command captures may wait for their requested result before acknowledgement.

Invalid/nonfinite poses or zero quaternions retain the previous valid body pose and count
consecutive invalid samples. An invalid initial sample fails loading instead of inventing
physics data. Thirty consecutive invalid samples emit a reliable server warning.

`stats()` returns cached data only: runner PID/aliveness, solve/readback/publish timings,
readback mode, stepHz, actualRtf, invalid counts, discarded periodic/catchup work, and reader
thread status. CUDA `readback_ms` is a CUDA-event transfer duration;
`readbackLatencyMs` includes host scheduling/queue latency. CPU readback timing is wall
latency. GPU `solver_ms` explicitly uses `solverTimingMode="host-submission"` and is not
GPU execution time. There is no CPU/GPU performance comparison in these receipts.

## Lifecycle and queries

Source requests use futures and bounded deadlines while Pipe I/O stays in threads.
Commands time out after 5 s; joint queries after 200 ms. Solver/contact and IK warmup
occurs before those deadlines apply. A hard child exit creates FAILED plus an error event
while keeping the latest read-only sample. Command timeout fails and closes the source.
Shutdown requests graceful exit, then uses bounded join/terminate/kill; it closes Pipes,
joins reader/writer threads, and releases the process handle. Concurrent close callers
share one shielded cleanup task. No shared memory, database, service port, or GPU context
is created by the gateway adapter.

For XPBD, HTTP joint queries derive Newton joint coordinates with `eval_ik` at a safe
boundary and read the resulting array once. Mesh IDs currently return KeyError because
P0-E's registered scene has primitives only; actual mesh export belongs to P1-B.

Known construction omission provenance now flows from registered builders'
`spatial_missing_labels` to `NewtonSimulation.missing_labels` and the actual exporter.
Unknown arbitrary external builders cannot reveal label omission after Newton generated
its defaults; their nonempty labels remain authoritative, as decided in P0-A.

The deployed D-10 default continues without observers. The current SpatialSource/Gateway
contract has no observer-count hook and HTTP creation exposes only RTF; the unused
`continueWithoutObservers=false` internal option needs coordinated P1-C support if exposed.

## Verification and evidence

```powershell
uv sync --extra simulation --extra usd
uv run --extra simulation --extra usd pytest tests/contract tests/unit tests/gateway tests/sim_runtime -m "not gpu" -q
uv run --extra simulation pytest tests/sim_runtime/test_newton_source.py tests/sim_runtime/test_transform_extractor.py -q
uv run --extra simulation python tools/smoke_newton_source.py --device cpu --crash
uv run --extra simulation python tools/smoke_newton_source.py --device cuda:0 --crash
```

- Final combined non-GPU Python run: **103 passed, 2 GPU cases deselected**, 13.98 s.
  Actual Newton CPU source/import tests ran; this is broader than the GPU-free CI gate.
- Prior focused source/extractor run: **5 passed**, including actual CPU and CUDA command
  sequences, FK reset, current-state joint query, unknown mesh, and clean shutdown.
- Fast tests prove uint64/32-byte frame layout, revision/length rejection, 10,000 latest
  overwrites, an unread 4 MiB Pipe send without producer blocking, bounded pacing, invalid
  pose fallback/30-sample warning, and engine-free gateway/spawn-target imports.
- The CPU/CUDA lifetime counterexample holds the first readback callback, overwrites State,
  captures a second sample, then overwrites State again. A third capture cannot borrow an
  occupied slot; both retained samples equal their independent known literals afterward.
- The real FastAPI/TestClient journey opens two WebSockets on one runner PID, checks the
  final paused HTTP/WS pose at the same seq, verifies reliable command ordering, kills the
  child, sees FAILED while health stays HTTP 200, then restarts the same Session with a
  new revision/PID and a strictly greater seq. No browser screenshot is claimed here.
- [CPU receipt](P0-E-cpu.json) and [CUDA receipt](P0-E-cuda.json) exercise the public adapter
  CLI, including explicit crash and cleanup. Both record `physicsImportedInCaller=false`,
  `runnerAlive=false`, and `readerThreadsAlive=[]` after close. Version/hardware remain
  the P0-A Windows/RTX 3070 Ti setup. GPU and child processes are released; no ports used.
- Scoped Ruff and whitespace checks pass. The existing Starlette/httpx TestClient
  deprecation warning is retained; it does not skip or fail the real API journey.

The initial exact HTTP-pose/WS comparison failed by 3.5e-7 m because TransformFrame JSON
rounds to six decimals while the Pose API retains more precision. The oracle now uses an
absolute 1e-6 m tolerance, consistent with the specified wire precision. No product code
or physical accuracy acceptance threshold was relaxed to resolve that observation.

## Change impact and bounded simplification

Producer/consumer trace: Newton State → step-boundary extractor → immutable TransformFrame
→ compact periodic Pipe/reliable control → engine-free NewtonSource → existing gateway
cache/fanout → HTTP/WS observers. Shared schemas remain authoritative. Real two-observer
execution proves the newly connected seam; isolated codec/filter/pacer tests prove failure
and timing rules independent of the renderer. Gateway source files remain owned by P0-C.

The simplification pass inspected the complete task-owned candidate, new files, and direct
gateway consumers. It removed a separate hidden pause-cache packet after adopting the
authoritative pause flush; one CommandResult path now updates all command caches. It
retained distinct replaceable/reliable channels, per-slot CUDA ownership, and one cleanup
task because their counterexamples exercise different obligations. GPU timer semantics
are explicit rather than represented as a synthetic execution benchmark. Independent
Standards/Spec review and actual PM integration remain pending.
