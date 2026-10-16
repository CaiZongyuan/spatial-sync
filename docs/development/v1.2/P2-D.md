# P2-D — Remote Runner

Baseline: `9a93c83b5cdb47607afacb752d242ad322aa1267`. Frozen code/test candidate: `30e330b0f9153da4ac0662e53c48e1a19e33b89c`. Implementation checkpoints: `1dd1d745` and `beced36`; later commits complete test/CLI evidence. This receipt records local implementation and actual loopback execution. Root integration and final independent review are PM-owned.

Integration repair candidate: `ab9fdc520d3ad9053f6fd9995ca52567acde949c`, after merging root `2127988e1d2bbc214d865cabd601456e2cdcd6db`. The original receipts below remain intact; the final section records the bounded combined C+D refresh.

## Behavior and ownership

An explicitly configured Gateway uses `RemoteSource`, preserving the existing SpatialSource and HTTP/WebSocket interfaces. A separate TLS Runner host uses the existing NewtonSource to spawn, supervise and reap the sole process holding Newton State. It retains the local trusted Pipe implementation inside that host. No `Connection.recv`, pickle decoder, executable builder name, server file path or unvalidated Python object crosses the network boundary. C's Runner/gather implementation is untouched.

The host's source kind and CPU/CUDA device are configured locally. Requests can select only registered scenes for that fixed source. A control connection authenticates with a protected token and receives a random, expiring ticket. A second TLS connection must authenticate and attach that ticket exactly once. The expensive source is created only after both channels are paired and the start request is validated. Pair timeout, either channel's EOF, malformed traffic or a stopped writer closes both channels and the owned source. A rejected duplicate frame attachment does not terminate the valid peer.

Each channel has one writer. Periodic NSYN data uses a replaceable latest-only slot; reliable metadata, command results and their NSYN frames share atomic control batches. Solver work never waits for network writes. Commands are serialized, carry request IDs and use a bounded host replay cache; the public Gateway retains its existing 256 acknowledgements. Failed connections are not transparently replayed against uncertain State: the Session becomes FAILED and the existing restart endpoint creates a new source/revision. Graceful close waits for host source cleanup and control EOF, releasing the one-session slot before reload.

Joint/mesh queries remain read-only. A host query deadline produces typed `query_timeout`; HTTP joint queries retain 504 while subsequent frames and commands remain usable. A command or transport deadline fails the remote source and cleans it up. Unknown entities retain the existing KeyError/404 behavior. Registered mesh responses use an explicitly bounded binary packet, not a JSON Python-bytes representation.

## Wire and limits

All traffic requires TLS 1.2 or newer; the client verifies the configured CA and certificate server name. There is no verification-disable option. The default host bind is 127.0.0.1; this task did not expose a public listener. Tokens come from the environment, are excluded from configuration/model representations, and are compared as UTF-8 bytes with `hmac.compare_digest`. Disposable ASCII and Unicode credentials both pass actual TLS tests. Keys and credential values are excluded from committed evidence.

The outer envelope is five bytes: a one-byte kind and big-endian uint32 payload length. Kind 1 is typed UTF-8 JSON, kind 2 is an unchanged Protocol 1 NSYN frame, and kind 3 is a mesh blob. NSYN retains its little-endian 44-byte header and 32 bytes per pose. Packet type and announced length are checked before reading/allocating the payload. JSON rejects duplicate keys, nonfinite numbers, unsupported operations and invalid command arguments. NSYN validates header/revision/count, finite poses/quaternions and the complete Manifest body set. Remote subscription pushdown is not introduced; the Gateway retains complete snapshots for HTTP and new scopes.

Defaults:

| Limit | Value |
| --- | --- |
| JSON / NSYN / mesh packet | 4 MiB / 4 MiB / 32 MiB |
| Reliable pending count / bytes | 128 / 64 MiB, including in-flight outbound bytes |
| Inbound client event queue | 128 messages and 64 MiB |
| Periodic queue | One replaceable pending frame plus the in-flight send |
| Host replay cache | 128 results and 64 MiB |
| Authenticated source ownership | One active peer/source |
| Application handshake handlers / socket backlog | 8 / 16 |
| TLS/connect, pairing and auth read | 5 s each |
| Normal read / send or request / start | 10 s / 5 s / 180 s |
| Heartbeat | 1 s, including paused/loading data peers |
| TLS close / source cleanup budget | 3 s / 12 s |

CLI options expose packet and operation deadlines. The read deadline must exceed the heartbeat interval. The application handler cap applies after TLS negotiation; TLS negotiation itself has a finite handshake deadline. Loopback proof does not establish an Internet connection-flood or deployment capacity claim.

## Clock contract

Remote Runner sample clocks are not compared with Gateway or browser clocks. `RemoteSource` records `time.monotonic() * 1000` immediately after each complete payload arrives at the actual Gateway, before decoding. One shared decoder applies this to periodic NSYN frames, the ready snapshot and reliable command frames. Epoch, source sequence and simTime are preserved. Existing Session allocation still owns global sequence/revision on reload. A late control response retains its old source sequence/simTime while sampleTimeMs identifies that response's Gateway receipt.

`SourceInfo.sampleTimeClock` and `server.ready.sampleTimeClock` are `gateway-receive` for this adapter and default to `runner-monotonic` for local sources. The clock regression deliberately gives the remote source `sampleTimeMs=1.0`; ready, genuinely advancing periodic frames and command frames must lie between the Gateway's before/after monotonic readings. This rejects copied remote clocks, wall-clock timestamps and future constants.

## Actual execution

The mandatory gate uses production TLS transport and an explicitly synthetic host. It needs no Newton, Warp or GPU. Test credentials/certificates are generated for the run, redacted from failure representations and deleted at fixture teardown.

`test_remote_runner.py` covers complete Manifest/snapshot, advancing periodic frames, two actual HTTP/WebSocket observers, reliable frame → ack → state ordering, pause/step/reset/resume, positive GLB and joint queries, reload/global revision/sequence, either channel loss, remote host loss → FAILED, API health and restart, no-observer auto-pause/resume, read-only query 504 followed by live frames and a successful command, idempotent command replay, and owned cleanup. All HTTP/WS tests bind dynamic loopback ports and contact the actual Uvicorn listener, not an ASGI transport substitute.

Negative entrypoint tests use actual connections for unauthorized/unpaired input, duplicate attachments, untrusted CA, wrong certificate hostname, oversized JSON/NSYN lengths before payload allocation, malformed/truncated frames, wrong magic/revision/count, nonfinite poses, unsupported operations, unknown commands/scenes, invalid step arguments, pickle-like input, pairing timeout, stalled TLS handshake, start timeout and command timeout. They assert that invalid operations never reach the source authority.

Independent review found that the initial slow-peer test's 0.5 s input timeout could explain closure without proving a send deadline. After moving input expiry to 10 s, neither an unread asyncio peer nor a real synchronous SSLSocket peer with a 4 KiB OS receive buffer established writer closure within two seconds for one 16 MiB TLS write. The asyncio diagnostic observed a paused 49,498-byte reader buffer, but zero pending Outbox/SSL bytes; the source remained live. These attempts are retained as [unsupported writer-timeout evidence](newton-p2d-evidence/actual-slow-peer-limit.json), not green gates. No larger payload or longer observation was added.

The final actual TLS test instead verifies all 16,777,216 received mesh bytes, then loses the peer and proves source cleanup and a healthy replacement owner. Separately, a controlled blocked `writer.drain` exercises the production Outbox/send deadline. It uses `asyncio.wait` with an independent 0.25 s observation, then checks task completion and the task's internal exception. Delaying the internal deadline to 10 s produces an explicit unfinished-task failure; restoring 0.02 s produces the internal TimeoutError, releases the budget and leaves a healthy channel usable. [Red/green receipt](newton-p2d-evidence/drain-oracle.json). This proves deadline logic under controlled backpressure; it does not prove that one actual 16 MiB TLS write triggers that deadline on this Windows transport.

The optional actual Newton CPU test passed in 29.47 s using Newton 1.7.0 / Warp 1.18.0. It ran a real spawned child through TLS and actual Gateway HTTP/WS, with pause, 24-step advancement (+0.1 s), reset at known first-body z=1.5, known revolute coordinate −π/4, resume, child reaping and live API health. The recorded child PID was 32972, distinct from the client/host process. No CUDA workload ran. Later changes preserve that successful path; the optional engine journey is not repeated solely for documentation or unchanged code.

`tools/smoke_remote_runner.py` additionally started independent CLI host/Gateway/client processes (42696 / 33360 / 40552), negotiated verified TLS 1.3, exercised all four controls through the public interface on API 8008 / Runner 8766, closed the session, stopped its owned processes and removed its credential directory. Its browser option uses web 5188 for the existing JSON/binary Chromium journeys. This demonstrates the documented environment/CLI wiring separately from in-process test orchestration.

Final checks passed: 158 CPU protocol/Gateway/unit tests (including 29 mandatory remote network tests and one controlled drain unit; one optional Newton test deselected), 63 frontend tests, six TypeScript protocol tests, production build, official protocol regeneration/diff check and owned Python Ruff/format checks. The new required Ready field was added to the typed client test fixture after the first build caught its omission; the final build and ten affected client tests passed. [Validation receipt](P2-D-validation.txt).

The final CLI browser run passed both existing JSON/binary Chromium journeys in 19.9 s against the independent TLS host/Gateway processes, with all 24 authoritative body poses, paused reconnect/visibility recovery, controls and scene replacement. Its host/Gateway/client PIDs were 44056 / 11980 / 4748. [Receipt](newton-p2d-evidence/cli-browser.json), [wire counters](newton-p2d-evidence/browser-wire.json), and [visually inspected Site inspector](newton-p2d-evidence/json-remote-site-inspector.png). The utility explicitly decodes Node output as UTF-8 after a Windows GBK capture failure; that failed capture was not counted as a successful utility run. Neither loopback TLS nor these fault tests measure WAN latency, deployment performance or renderer capacity.

## Impact and simplification

Boundary trace: protected configuration → typed TLS handshake/pair → host SpatialSource → NSYN and typed control → Gateway RemoteSource → existing Session/global metadata → HTTP/WS → existing browser buffer/renderer. The clock fields originate in the Python schema; generated schema/types/fixtures are regenerated. Repository clients consume the updated low-frequency contract. External strict-schema clients were unavailable to test; no general external compatibility claim is made. Local Pipe ownership and local clock values remain intact, and Settings' new option is appended to preserve positional argument order.

The bounded simplification pass consolidated all frame validation/clock replacement into one decoder and all channel writes into one Outbox. It retained independent periodic/reliable ownership, strict wire validation, complete snapshots, query/command failure distinctions, byte/count budgets and idempotent close. No generic transport framework, new dependency, GPU state copy or subscriber-union API was added. The one new runtime dependency boundary uses existing standard-library SSL/asyncio and already installed Pydantic.

The original full-gate tree predates C's source integration; the integration refresh below covers the affected paths and refreshes the changed gather's actual Newton CPU journey. The PM owns the final full integrated gate. The implementation worktree has no retained listeners on 8008, 5188 or 8766, no owned benchmark child, and no retained credential directory/private key. Generated receipts include portable hashes; text hashes normalize CRLF to LF. Routine logs still contain non-secret session IDs, PIDs and temporary certificate filenames.

One implementation defect found during real execution was Python 3.13 Server.wait_closed waiting for active connections before handlers had been cancelled. Shutdown now closes the listener, cancels/cleans owned handlers and sources, then awaits server closure; the actual public peer-loss test bounds this operation. Earlier test oracles were also corrected to drain legal buffered entity/heartbeat packets before asserting EOF and to find the failure event after earlier legal entity state. Those failed attempts are not counted as delivery evidence.

## Launch and reproduce

See the [README remote Runner commands](../../../README.md#remote-runner-over-tls). The fast complete demonstration is `uv run --no-sync python tools/smoke_remote_runner.py`; add `--browser` after installing frontend dependencies. It defaults to a synthetic host and cleans up all owned processes/credentials. Manual host launch is `python tools/run_remote_runner.py --cert <server.pem> --key <private.pem> --device cpu`; supply the protected token through `SPATIAL_RUNNER_TOKEN` to both sides.

The Gateway's `SPATIAL_RUNNER_CA_FILE`, `SPATIAL_RUNNER_HOST`, `SPATIAL_RUNNER_PORT`, `SPATIAL_RUNNER_SERVER_NAME` and `SPATIAL_RUNNER_KIND` explicitly select the remote adapter. All Runner settings absent preserves local mode; partial configuration fails explicitly. Use a certificate whose identity matches server name. The normal Gateway module and all public endpoints remain the same.

Mandatory tests: `uv run --no-sync pytest tests/contract tests/gateway tests/unit -m "not newton" -q`. Optional CPU proof: install `uv sync --frozen --extra simulation`, then `uv run --no-sync pytest tests/gateway/test_remote_runner.py -k optional_actual_newton -q`. Keep extras explicit during synchronization or use `--no-sync` afterward. No public deployment is authorized by these commands.

## Combined C+D integration repair

After root `2127988e` was merged at `6f8cf7e`, an actual TLS/public HTTP test proved a shared-consumer failure: GET `/scenes` advertised the registered Synthetic stress scenes, while POST `stress-100` returned 503 because the host had a separate whitelist containing only `lab-demo`. `51b124d` replaces that duplicate list with a lazy read of the same `SCENES` scene/source combinations. It still rejects unknown scenes before source construction and does not interpret a request as a builder or file path.

The red receipt has one genuine 503 assertion failure. The green checks prove 100 complete bodies with IDs `100 + 3*i` (100, 103, 106 … 397), the independently known permuted second row ID 121, advancing full WebSocket periodic frames, and exact paused HTTP/WS sample equality. The existing raw TLS unknown-scene rejection remains green. The focused stress/rejection run passed two tests in 1.27 s.

The combined source exposed a fixture lifecycle assumption in six malformed-frame tests: they had called SyntheticSource's private pose helper without starting it. Their legal baseline now comes from public `start` → `latest_frame` → `close`, after which the same independent byte corruptions are applied. The affected remote/import guard run passed 33 tests in 4.50 s, explicitly excluding the unrelated 16 MiB test and optional engine test. Official protocol regeneration/diff and owned Ruff/format checks passed. No CLI, large-payload or renderer profiling was repeated.

Because C changed the actual extraction/gather path, the optional Newton CPU journey was refreshed once against the combined tree: one test passed in 5.16 s, child PID 40436 was reaped, the known reset pose/joint and 24-step advancement remained correct, and public API health survived cleanup. This duration is a verification receipt, not a performance comparison with the earlier cold run.

A second narrow counterexample covered the client's own read-only deadline. With client budget 0.04 s < actual joint-query delay 0.12 s < host budget 0.5 s, HTTP returned 504 but the old local timeout branch also changed the Session to FAILED. `ab9fdc5` makes client-side joint/mesh deadlines raise the read-only TimeoutError subtype while preserving the readers; expired tokens are removed and late replies drain normally. Command/close timeout policy remains unchanged. The actual TLS/public HTTP green proves 504 with RUNNING state, advancing periodic sequences, a naturally returned late joint reply, and a subsequent applied pause. That case plus the existing host-query-timeout and fatal-command-timeout cases passed three tests in 1.73 s. The unaffected successful Newton CPU path was reused.

[Incremental raw red/green, affected gate and fresh CPU receipts](newton-p2d-evidence/integration-refresh/summary.json) have their own portable hash inventory. Earlier full-gate/CLI/large-payload evidence was preserved. Root will run the complete combined gate in its default environment and obtain fresh independent review before final integration.
