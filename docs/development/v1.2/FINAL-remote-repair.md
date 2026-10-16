# TLS interest and Runner diagnostics repair

Baseline: `3eb0c71` on `issue/v1.2-remote-review`. Shared dependencies were fast-forwarded first: `91942f7` supplies `SpatialSource.snapshot/set_interest`, `SourceFrames` and `RunnerHeartbeat`; `5c44309` supplies actual SyntheticSource capture/interest operations. The task-owned candidate changes only `apps/api/app/sources/remote_source.py`, `packages/transport/remote.py`, `packages/transport/remote_server.py` and the new remote-interest tests/report.

## Contract and impact

The real readers/writers are RemoteSource on the Gateway, RunnerServer's owned peer on the host, and the host's fixed SpatialSource implementation. Commands, HTTP/WebSocket callers and the existing remote query tests consume these adapters. There is no browser or public WebSocket schema change and the NSYN layout is unchanged. Both ends must support the new internal operations; an older host rejects unknown operations explicitly. Existing command/joint/mesh packets serialize as before. No network pickle is introduced.

The TLS wire adds bounded typed `snapshot` and `interest` requests. Interest IDs are strict unique uint32 values with a maximum count, validated against the host Manifest before Source authority is called. A mutation requires explicit `streamIds`, with JSON null meaning all Bodies and an empty list meaning no periodic Bodies. Typed `SnapshotResult`/`InterestResult` metadata and a complete NSYN snapshot are placed in one reliable Outbox batch. The interest acknowledgement echoes the requested IDs, including explicit null. A full capture must have `stream.snapshot` kind and the exact complete Manifest Body set.

RemoteSource shares the local adapter's `SourceFrames` cache, exact membership checks and sequence floor. It marks transition before issuing an interest mutation and validates the echoed request before installing the complete reliable barrier. During the transition, only exact old/new legal periodic sets may be discarded; unexpected sets still fail the source. Once acknowledged, frames at or below the barrier are discarded and future periodic samples require the exact installed scope. No stale poses are merged into a supposedly current full sample. Source seq, epoch and simTime remain the original sample metadata; `sampleTimeMs` remains the actual Gateway receipt clock as before.

The host likewise marks forwarding transition before awaiting a command, full capture or interest change. It validates the full authoritative response and installs its barrier before forwarding resumes. `latest_frame()` can expose a late read-only full `stream.snapshot` after a local host deadline; the pulse validates it as complete control authority and never forwards it as narrowed periodic data. Ordinary `transform.frame` membership stays exact. Reliable command frame/ack/state semantics remain unchanged.

Snapshot is read-only: either client or host deadline returns `RemoteQueryTimeout`, the control reader drains late payloads and future periodic/control operations continue. An interest mutation whose result is unknown fails and explicitly closes the owned connection/source. Host shutdown retains the existing order: stop accepting, cancel/join handlers, join snapshotted peer resource owners, then wait for the listener to close. Joining the peer owners avoids abandoning shielded cleanup after a second handler cancellation.

`RunnerHeartbeat` is applied at both proxy hops. Relative receipt age advances while no new producer heartbeat arrives; forwarding the same producer seq cannot reset it. Actual source metrics and Manifest Shape count pass through. SyntheticSource produces no Runner marker, so its diagnostics remain null rather than inventing a heartbeat. The controlled TLS heartbeat test proves proxy age accounting; it is not evidence that SyntheticSource produces a real Runner heartbeat.

## Evidence and bounded simplification

The first actual Synthetic TLS acceptance run failed because RemoteSource had no `set_interest`. Its repaired journey uses the actual SyntheticSource host and authenticated sockets with dynamic ports: sparse stress-100 IDs, one-Body periodic frames, complete full capture, empty interest, scope expansion, complete pause/reset frames and full-scope recovery. Paused snapshots retain the authoritative seq/time.

A controlled wire gate holds the reliable interest metadata while the real frame channel sends a newer narrowed sample. That early sample is discarded. After acknowledgement, an injected old-scope frame at or below the barrier is discarded and a future narrowed sample is accepted. The same gate with unknown, missing or wrong-scope IDs first failed all three rejection tests, proving an overly broad transition drop; the adapter's old/new membership guard makes these failures explicit. The tests use an independent 0.15 s rejection budget below the 0.8 s channel timeout.

Separate controlled-source TLS tests cover client-local snapshot deadline, host snapshot deadline with a late full sample held observable, and already-applied interest mutations timing out on either side. The read-only cases observe a newer narrowed seq and a successful complete pause after the late capture. Mutation cases observe `source_unavailable`, disconnected diagnostics and released host/source tasks. Another controlled producer tests elapsed age without host traffic, repeated same-seq host heartbeats, a higher controlled producer sequence, PAUSED and connection loss.

Malformed response tests reject a wrong ID echo, partial full capture, wrong snapshot kind and wrong result-operation kind. The last case first failed with `DID NOT RAISE RuntimeError`: a SnapshotResult could previously satisfy an interest future without installing its scope. Pending-operation matching now rejects that response and explicitly fails the mutation.

An optional real Newton CPU TLS test is provided with `pytest.importorskip` inside the test. It requires one gathered/read-back Body and 28 bytes, a complete capture, new scope, PAUSED snapshot/Runner heartbeat, reset and cleanup, including absence of the Runner PID from `multiprocessing.active_children()`. It records actual source identity, remote Runner PID and readback metrics when executed. No GPU or second browser is needed.

The implementation reuses `SourceFrames` and `RunnerHeartbeat` instead of maintaining separate duplicate cache/age logic. One host capture method handles the two new reliable full-result operations; existing Outbox batching supplies atomic metadata/frame delivery. No shared scene-registry extraction or unrelated transport redesign is included. No ports or sessions belonging to another user/task are modified.

PM inspection of the frozen diff found that the pulse would revalidate an unchanged full snapshot on every tick, especially after a PAUSED reset. The bounded follow-up makes the observed-seq guard cover snapshots and advances it after successful control classification. An unchanged full snapshot is therefore validated once, remains off the periodic channel, and a newer late capture still advances its control floor. No generic redesign or performance benchmark was added.

## Final validation

Final focused gate on the complete remote repair:

- Base-only Python `-m pytest tests/gateway/test_remote_runner.py tests/gateway/test_remote_interest.py tests/gateway/test_remote_interest_validation.py tests/unit/test_remote_transport.py -q -W error::pytest.PytestUnraisableExceptionWarning --junitxml=.scratch/final-remote-repair.xml -o junit_family=xunit1`: **55 passed, 2 skipped in 8.35 s** after the observed-snapshot follow-up (previous frozen run: 8.73 s). Both skips require the optional Newton installation: the existing full-scope test and the new narrowed CPU test. No unraisable/pending-task warning appears in the final run.
- Root development Python `-m ruff check .`: **passed**.
- `git diff --check`: **passed**.

The XML receipt remains in the ignored scratch directory. Its captured properties provide concrete sample evidence:

| Source | Operation | Observed seq |
| --- | --- | --- |
| Actual SyntheticSource stress-100 over authenticated TLS | Full interest barrier → first one-Body periodic → complete capture | 2 → 3 → 4 |
| Controlled DelayedSnapshot(SyntheticSource) over authenticated TLS | Client snapshot deadline: before → later narrowed periodic → complete pause | 3 → 12 → 14 |
| Controlled DelayedSnapshot(SyntheticSource) over authenticated TLS | Host snapshot deadline with late full sample: before → later narrowed periodic → complete pause | 3 → 12 → 14 |

Synthetic tests measure transport semantics and CPU kinematic fixtures, not GPU gather throughput. The optional actual Newton CPU test is supplied for the root's combined candidate run and is **not claimed executed** here. No browser/GPU run, performance speedup, external CI or deployment claim is made by this remote repair.
