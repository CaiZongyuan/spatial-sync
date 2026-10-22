# #95 phase 1: one bounded observability probe

Status: preparation only; no service, browser, build, environment sync or CI execution. Product source is frozen at `1ded8b0a5ef39f5978f808f54eb6bcae177ed9c6`, tree `e19ebb3d8f7927b5cabc82295aecbb94869215a0`. This disposable diagnostic branch must never merge. PM must give a separate bounded GO before commit/push triggers its one isolated job.

## Existing red and scope

The original command `SPATIAL_E2E_VIDEO=0 pnpm test:e2e --fully-parallel --workers=1 --shard=4/4` already failed in CI 39705463185. Actual shard4 has **18** cases. `fixed18.json` was extracted from that job's actual ordered results, including v1/v2 replay and the unchanged optional Newton skip; readiness compares exact file/title order before execution. Do not reuse the old 17-case packet or change the selection.

The current red marker is the second observer's connected-label success followed by its first sample evaluation not returning within the original 10s poll. It is not a returned `-1`, a never-connected socket, or proof that frames were absent. The supplied artifact has ready/ack/snapshot160 and161/162/163, successful HTTP, a live primary and server send counters advancing with zero drops/queues. Server sends are not proof of peer delivery. Original #95's connecting-label failure remains a separate marker in the same investigation class; identical cause is unproven.

## Three falsifiable layer expectations

1. **Source/transport:** if source progress stops or transport closes before observer readiness, independent Node HTTP stats and Node-side browser WS close/header events should show it in the original red window. Continued HTTP/server progress plus browser WS frame events refutes a source-wide halt, but browser events still travel through CDP and do not establish independent TCP arrival.
2. **Browser execution:** if the second page's JS/render execution is unavailable, its original sample and one parallel constant `evaluate(() => 1)` should both fail to return promptly while Node deadlines/HTTP/primary progress. Longtask and sparse rAF gaps may locate the blocked page interval. If the constant returns promptly but sample returns `-1`, inspect intake/buffer state; if the sample itself does not return, do not invent its value.
3. **Runner/dispatch:** if page-side sample getter/rAF/WS marks progress but Node receives the sample late, clock-aligned page execution versus runner request/receipt should isolate that gap. If no page export is available, report the missing fact rather than choosing a cause.

One green diagnostic is **unreproduced**, not fixed/latent/flaky. Any unrelated failure is a different symptom. A reproducing red permits the next causal question, not a guessed product repair or automatic rerun.

## Observable seams and invariants

`instrument.patch` changes only the workbench test import and original sample callback wrapper. Its value remains `getSample()?.seq ?? -1`, with unchanged 10s expectation and error propagation. No product stream/decoder/renderer code changes. `fixture.ts` targets only the original case205; it reaches both actual contexts before navigation and keeps two viewers, their original dimensions and the original browser/worker lifetime. No third WS connection/subscriber exists.

Per page there is at most **one** constant evaluation in the entire journey. It starts after submitting the original sample request, is never awaited by the business predicate, has its own 1500ms Node deadline, and consumes late success/rejection. It cannot retry or accumulate parallel evaluations. The new in-page WebSocket Proxy returns the same native socket/prototype and only registers sparse receive/close listeners; it adds timing overhead and is diagnostic. Node, CDP and JS WS receive paths preserve the first16 events, then each15th event, so initial161/162/163 progress is not sampled away. Longtask/rAF/JS receive records are capped512; Node/WS/CDP/HTTP records capped256. HTTP stats has at most one request in flight, once per second on the second observer, with800ms abort. No headers/auth payloads are recorded. No GL wrappers, CPU throttling, profiler, console traffic or per-frame disk writes.

Node records use performance.now + Date.now; CDP frames carry their native monotonic timestamp; each in-page record carries its document performance.timeOrigin. Begin CDP metrics and bounded end page/CDP captures provide clock brackets. Begin is about:blank; never subtract performance.now values across navigation without origins/calibration uncertainty. End export is bounded750ms per request and runs once before the original context close; timeout is a diagnostic missing export, not a new acceptance oracle. Extra teardown may follow the red and cannot explain earlier failure. WS event/header evidence remains browser/CDP evidence, not independent TCP capture.

## Executable command, resource ownership and GO

Future remote command, after source application/readiness and PM GO only:

```sh
uv run --no-sync python packet95/tools/observer95-diagnostic/run.py --shard=4/4
```

The new workflow checks out exact source separately from immutable packet commit, applies only test instrumentation, installs frozen base dependencies, builds production preview and runs the original full shard once. It preserves all original 35-image/10001-HTTP work, assertions, timeouts, retries0, worker1 and tracing. No source/build/env was executed while preparing this packet. `prepare.py` pins head/tree, validates patch applicability, stores packet hashes, and compares18 IDs/order; actual remote readiness has not yet run.

Proposal: one isolated Ubuntu job, original5min total job limit. The first workflow step records start/deadline before checkout or dependency preparation. Setup and execution share a creating `timeout` command ending35s before the recorded deadline, with a5s escalation guard, leaving30s for artifacts. `run.py` subtracts that35s guard plus15s cleanup, caps the test subprocess at210s and requires at least180s remaining for the original complete shard. API readiness can use at most30s and cannot consume that180s admission margin. If setup/readiness consumes the budget, record `budget-exhausted-no-business-verdict`; do not start a shortened shard or widen a case timeout. The budget receipt under runner.temp survives even setup failure. Hosted checkout/setup actions remain bounded by the unchanged overall5min job limit; an external action hang can still prevent upload. PM supplies an actual-start-relative outer window covering queue/setup/receipt cleanup and cancels if it expires. No automatic second condition, base comparison, pressure loop or full CI. Red/unreproduced/other failure/budget exhaustion each ends the phase and releases resources.

Fresh runner owns API8000/web5184; library/state/recordings/logs are under source `.scratch/observer95/`. Reports/traces retain original `.scratch/viewer-e2e` paths. Source and creating PID/command/create-time receipts are stored. `run.py` owns Popen handles/process groups. Initial natural-exec registration requires the current creating handle, unchanged ctime and two stable command observations within1s; later uncertainty never grants new ownership. Descendant discovery checks the root identity both before and after enumeration. Each root/descendant cleanup catches its own failure and continues. Final receipt emission follows fresh owned-PGID membership, listener8000/5184 and handle readback. Changed identity, uncertainty, remaining processes/listeners or an unexited handle forces nonzero exit even if tests returned0. No unknown PID is signalled, and a gone parent never establishes ownership of a new process. GitHub runner teardown is the last boundary. Do not reuse Windows primary, #91/#94 environments, user staged HTML, Site5192/5193, or another task's handles. No local Linux/Windows execution is authorized by this packet alone.

Final acceptance remains a justified fix, appropriate independent review and original final-head CI; diagnostic method readiness does not satisfy delivery.
