# Final Gateway review repairs

Baseline: `5d6533f67e1fec0cd457d0843eddcafffa74f291`.

## Findings and public regressions

The independent review found two defects. A raw HTTP `Authorization: Bearer \xE9` raised `TypeError` in the string form of `hmac.compare_digest`, returning 500. A replacement Source whose `start()` raised left the session FAILED without an error payload, failure broadcasts or heartbeats, because reload stopped the source-owned task list containing the heartbeat.

`tests/gateway/test_failure_recovery.py` tests HTTP and WebSocket behavior through the FastAPI application. The initial authentication run was red with `500 != 401`; the initial lifecycle run was red with `500 != 503`. The startup tests cover failure before a Source has started and after it has allocated a running task. They exercise failed reload, failed restart, an existing observer, a late connection to FAILED, and another successful restart. The retained revision, numeric revision, seq, epoch, simTime and complete HTTP snapshot are checked against the paused pre-failure sample. Actual LOADING and FAILED heartbeat messages are observed.

The authentication fix compares consistently encoded UTF-8 bytes, retaining constant-time comparison and the existing operator/viewer roles. Invalid credentials yield HTTP 401 and WebSocket 1008. Valid operator controls and viewer reads/control rejection remain covered.

The lifecycle fix keeps a single session-owned heartbeat task across replacement attempts, while source frame/event tasks still stop with their Source. Failed replacement starts close the candidate, clear its handle, retain the previous valid cache and report `source_unavailable` in HTTP 503, session summary, `server.error` and the FAILED `session.state` cause. FAILED/STOPPED getters use the retained cache rather than continuing to read a closed or failed Source. Source-backed joint/mesh queries return 503 when the Source is unavailable; cached scene/snapshot/pose queries remain usable. A public joint-query counterexample first returned 500 and is covered after the guard. Reload also publishes LOADING using the prior valid revision. Session close stops the heartbeat after finishing connected clients.

A further public counterexample rejected the initial no-observer pause after `Source.start()` returned a Manifest. Before the fix, the FAILED summary used the unannounced candidate revision and advanced sample instead of the prior valid revision. Startup now stages the candidate Source, Manifest, scope resolver and initial observer-policy result before committing session fields. Heartbeats while the candidate awaits use the prior valid cache. Rejected policy cleanup publishes FAILED under the previous revision; another restart succeeds in PAUSED with one revision increment. The test also observes LOADING and FAILED heartbeats, verifies no `scene.replaced` was emitted for the rejected candidate, and checks the successful replacement announcement before its state.

Initial creation can fail before a Manifest exists. Such a failed create returns HTTP 503 with details, closes the candidate and leaves no registered session; later creation remains available. `Ready` and `SessionState` require a nonempty real revision, so the repair does not fabricate a revision for this case. A defensive pre-accept guard closes a reachable session without a Manifest with 1013; a failed unregistered session ID is rejected with 1008.

## Bounded simplification review

The repair reuses the existing failure broadcaster and retained frame cache rather than introducing a second error protocol or replacement state store. Heartbeat ownership is represented by one task handle, independent of the two source tasks. Both reload and restart continue through the same reload operation. Observer-policy command execution/validation and result application are shared by staged startup and ordinary observer updates; ordinary updates retain reliable frame-before-state order. Startup suppresses candidate broadcasts until the revision is committed and announced by reload. No shared scene registry extraction or unrelated refactor is included. No public protocol/schema change is needed.

An affected-test run with unraisable warnings treated as errors caught an intermittent existing WebSocket teardown leak: cancellation could remove a connection from its session and then skip `connection.finish()`, leaving the pump pending. The WebSocket finalizer now shields receiver cancellation, client removal and pump finish together. Nested `finally` blocks guarantee finish even if another cleanup step raises; arbitrary errors are not swallowed. The existing bounded close timeout remains. This shield also covers a future asynchronous interest update during `remove_client`.

## Validation

An intermediate full base run passed 169 tests with one optional simulation skip before the policy-rejection slice. An affected rerun with unraisable warnings promoted to errors caught the pump cleanup issue above; after repair the affected 28-test run passed with that warning policy. Final complete validation follows below.

All Python test commands use the base-only Python environment at `D:/Projects/Robots/spatial-sync-worktrees/p2-b/.venv/Scripts/python.exe`, except Ruff uses the root development environment. No existing server, user session, GPU process or foreign port is touched.

Final checks on the complete repair:

- `python -m pytest tests/contract tests/unit tests/gateway -q -W error::pytest.PytestUnraisableExceptionWarning`: **170 passed, 1 skipped in 14.26 s**. The skipped test is the optional real Newton remote-source test because the base environment has no simulation extra. The remaining warning is the installed Starlette TestClient/httpx deprecation; no pump/unraisable warning remains in this run.
- Root development Python `-m ruff check .`: **passed**.
- `git diff --check`: **passed**.

No new GPU or browser run is claimed for this Gateway-only repair. Real Newton/browser final integration belongs to the combined candidate validation. There is no external CI or deployment evidence in this local repair.
