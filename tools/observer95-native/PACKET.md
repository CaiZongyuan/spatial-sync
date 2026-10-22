# #95 Phase2 native trace packet — prepare only

Disposable branch/worktree: `diag/issue95-native`, `.worktrees/issue-95-native`. Product bytes are separately pinned to `192fc4b465012a34ad95fc4c6ef460da322436a3`, tree `bfe2374c28551edebee0655a0c2045b82e8d75fd`. Phase1 branch/artifacts remain unchanged. No runtime/build/sync/collection/CI has run in this preparation. Commit locally for review; **do not push until separate PM GO**.

Original red remains the18-case shard4 in CI39714449179. B connected succeeds; first sample evaluate starts141147.615ms without after/result;10s poll expires151146.454ms. B already saw snapshot149/deltas150–153, with a6630.902ms browser-event gap. Server B droppedFrames=1 is preserved, queue0; no cause is established. Offline facts and the passing A getter-before-execution delay bounds are in primary `.scratch/rsi-platform/issue-95/phase2/`.

## Source delta and timing

Only the original workbench test import changes. The sample callback, test body,45s/10s timeouts, original18 selection/order,35-image/10001-HTTP workload,2contexts/camera/viewer,1worker and0retries stay unchanged. The auto fixture depends on existing browser/context/page: A is created but still blank when tracing begins, before the test body/API session creation/navigation. Original predecessor cases run unchanged with the same worker/browser lifetime. Tracing ends in auto-fixture teardown after the original body/its cleanup. Thus post-red context/session cleanup is captured but cannot become the earlier failure's cause.

Use one browser CDP session; no page JS, Proxy, rAF, constant evaluate, Network.enable or Performance.enable. Browser session acquire≤1s; abandoned late sessions are detached with consumed rejection. Setup has a shared6s deadline, including directory creation, A identity, discovery, acquire/start and begin clock. Trace start≤2s; an uncertain start still gets best-effort end/detach. Any readiness failure is recorded separately and **the unchanged business test still executes**.

Categories: `toplevel,toplevel.flow,devtools.timeline,v8,gpu,cc,viz,renderer.scheduler`. Native32MiB continuous buffer, gzip ReturnAsStream; no CPU sampling or systrace. The categories may reveal renderer task/native GPU/IPC intervals but are **not kernel sched_switch/off-CPU evidence**. Missing runnable/blocked-state evidence remains unknown.

Retention correction: parent packet `bf978b4` was executed once in run39717939641: business17passed/1skip, but8MiB lost data and the relevant B execution interval. The sole new variable is `traceBufferSizeInKb:8192→32768`; original evidence remains under its parent commit/artifact. After separate PM GO, execute this correction once and stop. All other code, callbacks, categories, identity/clock hooks, gzip≤16MiB,6ssetup/8steardown,5min job,194s admission and cleanup stay unchanged. Validity requires no loss, unique B mapping, reliable synchronization and complete request→return/10s coverage. Green can locate only this execution's actual delay and cannot explain the old10s failure or count as fixed. If this correction is still invalid or lacks coverage, stop expansion/reruns and return to maintenance redesign; no OS sampling or further condition is authorized here.

End clock, Tracing.end, completion and bounded file append/read share7.25s. The final750ms is reserved for IO.close≤200ms, session.detach≤200ms, metadata write≤350ms; all are within one8s teardown deadline. Directory/append/final writes are bounded. A timed-out async file operation may finish later; record missing metadata/export, and the outer owned runner stops/verifies the process on its budget. Native control failure never widens, retries or substitutes the original10s sample assertion.

## Identity and clock evidence

A/B each get one short-lived page CDP session: Target.getTargetInfo and Page.getFrameTree, then detach. No page JS or per-page Network/Performance hooks. A mapping is awaited before tracing; B is triggered by Node context.page lifecycle without awaiting its business goto/newPage. Each mapping is bounded1s (400/400/200ms) and late acquisitions are detached. Record distinct targetId/browserContextId/root frameId and Node labels; browser Target discovery events are capped64. These RPCs/trace introduce observer effects, not assumed neutral.

Each sync bracket records `process.hrtime.bigint()` as string, performance.now/timeOrigin, Date and Node PID. Inspect installed Playwright1.64.1 `lib/coreBundle.js:4717`: `monotonicTime=floor((performance.now()+_timeShift)*1000)/1000`; setTimeOrigin can change `_timeShift=performance.timeOrigin-origin`. Therefore **do not assume fixture performance.now equals PW trace startTime**. Native clock_sync events bracket Node↔Chrome; original PW context-options/time-origin metadata and hrtime/Date brackets must establish any PW offset/uncertainty offline. If that correspondence or precise callback attribution is insufficient, mark unknown; do not insert a page callback marker or repeat the run.

`native-state.json` keeps original business status separately from readiness, export and cleanup. `captureValid` only means the bounded native capture/export/close succeeded. `identityCaptureComplete` only means two distinct target/frame maps were obtained; `packetComplete` combines those mechanical checks. `causalUsability` stays **unassessed** in-process. No data loss alone does not prove usability.

Offline causal usability requires correct B target/frame→native renderer PID/TID mapping, required categories/events, sync uncertainty, full original sample-request→10s deadline coverage, no missing/truncated/evicted relevant interval, and a corresponding native execution event before assigning pre-execution versus return-path delay. Missing mapping/events/clock precision is missing evidence. A native schedule gap alone cannot distinguish off-CPU scheduling from a native wait.

## Single execution proposal and budget

New workflow only, Ubuntu/Node22/Python3.13/frozen base dependencies/production preview. Source.json pins head/tree and actual patch/fixture/run hashes; compare fixed18 file/title order before source startup. Artifact `observer95-native-evidence`, owned roots `.scratch/observer95-native`; original PW report/results remain `.scratch/viewer-e2e`. Command after GO/readiness:

```sh
uv run --no-sync python packet95native/tools/observer95-native/run.py --shard=4/4
```

First workflow step records start+300s. Reuse the verified owned runner: work stops50s before deadline,15s cleanup plus35s upload/escalation guard,5s kill escalation leaving30s upload. Test cap210s; admit the full shard only with≥194s (original180 +6setup +8export), API readiness cannot consume this margin. Insufficient time means budget-exhausted/no-business-verdict, not a shortened run. Preserve original business exit separately; missing/invalid native packet forces nonzero overall status even if business passed. Original business failure is not hidden by trace failure. Fresh identity/PGID/listener readback and no uncertainty are still required for successful cleanup.

Red/unreproduced/other/readiness-invalid/budget-exhausted each stops the phase. No automatic rerun/base/stress/full CI/product repair. A passing diagnostic is unreproduced, not a fix or permission to merge PR106. External hosted action hangs remain bounded by the unchanged5min total job cap and can still prevent artifact upload.
