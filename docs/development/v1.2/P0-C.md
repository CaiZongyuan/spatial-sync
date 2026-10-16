# P0-C Gateway + SyntheticSource

Status: integrated into root `a7cd4b05f29c915523c6dbfb08eb017080d18f7c`;
PM non-author review and the real frozen-dependency viewer journey passed.
Original baseline `053055f`;
P0-A baseline advanced to `78f07c6`. Source checkpoint `255f77c`, shared manual-step
guard `384d712`.

Delivered source-independent FastAPI HTTP/WS endpoints, deterministic synthetic
laboratory, one active authoritative session, shared snapshots, typed events,
serial/idempotent commands, a single writer per connection, bounded reliable
queues and latest-only periodic frames. Synthetic mode never imports Newton or
Warp. The registered Newton adapter is loaded lazily at session creation.

The synthetic lab contains two articulated links, 20 moving tubes, a centrifuge,
a TCP Site at a nonzero Body-local offset, and an intentionally missing GLB with
a primitive collider fallback. Initial device state is cached and replayed after
the subscription snapshot. Registered scene IDs are `lab-demo`, `falling-boxes`,
`robot-and-tubes`, `usd-lab`.

## Validation and rework

- Frozen Python 3.13.14 / Pydantic 2.12.4 / FastAPI 0.143.2 / Starlette 1.8.0:
  `uv run --frozen python -m pytest tests/contract tests/unit tests/gateway -q`
  passes **82 tests**, including **21 gateway/source tests** (1.53 seconds reported
  by pytest). `ruff check apps/api tests/gateway` passes.
- TypeScript compilation, 5 protocol Node tests and deterministic regeneration
  (`pnpm protocol:check`) pass after the shared `MAX_MANUAL_STEPS = 1000` guard.
  Count 1000 is accepted; 1001 returns invalid_args without clamping.
- Public gateway tests cover two observers, exact command frame/ack/state order,
  full frame equality across observers, ack/state time and epoch equality,
  stable paused snapshots, heartbeats, fixed-step deltas, reset epochs, revision
  mismatch and re-subscription, entity scopes/rate clamps, reconnect snapshots,
  cached device events, duplicate IDs/concurrent requests, rejection reasons,
  Body/Site pose queries, joints, RTF, stats, reload, DELETE, failure/restart,
  zero-based source sequences, and resource cleanup before replacing a failed
  source. Socket-adapter tests prove timeout/overflow isolation and one writer.
- PM's independent Standards + Spec review found no blocking findings and reran
  the integrated core suite: 80 tests passed before the shared guard follow-up.
  See [review receipt](P0-C-review.md).
- Viewer reported the frozen API's real Synthetic E2E journey green in 13.8s:
  Body/Site poses and seq/time, acknowledged controls, two observers, reset epoch,
  actual WS relay cut/reconnect, entity state, missing GLB fallback and DELETE 204.
  This exercises the pause flush repair through the browser. Viewer owns the
  exact artifact paths and final browser receipt.
- Initial collection failed because no app existed; that was a prerequisite
  failure, not behavioral red. A subsequent public POST actually failed 503
  versus expected 201. The first two-client vertical slice then passed.
- Real viewer E2E exposed two integration facts: test Origin 5184 needed an
  explicit whitelist; Pause needed to flush its final authoritative sample so
  observers settled on the same paused snapshot. Both were repaired. Pause now
  uses reliable frame → ack → PAUSED state, consistent with the existing
  command delivery contract. This does not restart periodic publishing while
  paused. Viewer retained the failed traces separately from the green journey.

One dependency warning remains: Starlette deprecates its httpx-based TestClient
in favor of httpx2. Tests execute normally against the locked dependency set;
no unrequested dependency migration was introduced.

## Shared boundaries and impact

`sources/base.py` defines the spec's SpatialSource methods. `NewtonSource(session_id)`
must return its initial snapshot before start completes. Command-produced frames
and state are returned only in CommandResult; they are not duplicated through
events. Optional `source.stats()` is merged into diagnostics without importing
engine code. Newton owner received the constructor, step guard, final pause
flush, latest-frame retention and restart sequence requirements directly.

HTTP source data → typed gateway → JSON strings → single connection pump → real
viewer is the affected path. One encoding is reused for all observers sharing a
frame scope; each pump samples periodic frames at its granted rate. A failed or
slow connection does not block the source or other pumps. Remote mode checks
bearer roles, exact origins and request rates; local mode listens on loopback.
Only registered scene/source combinations and mesh/asset IDs are accessible.

The gateway maps new source sequence numbers with `priorSessionSeq + 1`, so both
zero-based and one-based initial snapshots exceed the old session sequence.
Reload closes the old source before starting a replacement. Creating a fresh
session after FAILED also closes the previous source before allocating another.
Incoming frame/event identity and revision checks prevent stale source data from
contaminating the current scene. Subscriber queues discard superseded periodic
slots before delivering a reliable command batch.

## Bounded simplification

Inspected the complete task-owned range from `053055f`: `apps/api`, gateway tests,
README, CI workflow and the small protocol guard delta, including new files and
registered consumers. Kept four responsibilities visible: source motion,
session authority/ordering, connection writer/backpressure, and HTTP/WS routing.
No speculative transport layer, database, event bus, or framework abstraction was
added. Shared command argument validation remains in the authoritative protocol;
synthetic and Newton reuse it rather than duplicating limits. A single JSON
encoder owns serialization, and the source/gateway boundary exposes read-only
published snapshots rather than writable physical state. No additional local
abstraction or deletion was justified by the current consumers.

## Resources and remaining evidence

Owned development API: `http://127.0.0.1:8001`, worktree `p0-c`, frozen `.venv`,
uvicorn reload watcher PID **21936** and its children, logs
`.scratch/api8001.locked.stdout.log` / `.stderr.log`. Positive health and a real
WebSocket handshake from Origin `http://127.0.0.1:5184` succeeded after alignment.
The previous isolated uv PID 18104/watcher 18736 tree was stopped. The current
API is retained for viewer integration and will be released under PM ownership.

This issue does not claim Newton solver/IPC correctness, Newton import or asset
compatibility, Newton browser visual parity, hardware performance, remote deployment,
or an actual GitHub CI run. Those require their owning integration evidence.
The GPU-free workflow and reproducible commands are delivered; CI must run after
the viewer scripts and Newton adapter are integrated. No initial implementation
duration is inferred from gaps in the journal.
