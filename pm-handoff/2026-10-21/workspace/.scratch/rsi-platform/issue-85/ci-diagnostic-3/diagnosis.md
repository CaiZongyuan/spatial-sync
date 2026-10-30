# Actual third diagnostic attempt

Run39465698472/attempt1 on exact9b41f23989d5689e8abd6e03e066f682739a37b8. All9jobs succeeded, but acceptance is false and no rerun was launched.

All seven artifacts, full workflow logs, run metadata, job/step times, both JUnit files and all five browser case reports are retained. Browser collection69matches exactly:61passed,8skipped, no retries, same skip identities. Python566core+396gatewaypasses/1skip and all963actualJUnitidentities match the frozen complete baseline.

| Observation | Diagnostic2, video on | Diagnostic3, video off |
|---|---:|---:|
| Gateway job |193s|238s|
| Gateway pytest |174.42s|224.27s|
| Core job |103s|82s|
| Browser5job |211s|208s|
|35-image case |29.320s|27.853s|
|35-image render phase |27.323s|26.862s|
| All7case |32.047s|28.391s|
| All7render phase |29.857s|27.620s|

Both Python runners again report physical2, affinity[0,1,2,3], selected4. Gateway slowdown is spread across cases:372of396are slower,21faster,3equal; sum668.953to852.829case seconds; median paired ratio1.27177amongcases>0.1s. Its slowest case20.193s performs the same actual10,001HTTP/event-retention workload. No single fixture outlier explains the job margin.

The trace-proven owned-live overview precondition repair passes with original request equality. Workbench cases9.038s/20.696s/15.347sremain below27s. Only the35-image/all7thumbnail cases fail the browser case margin (configured45s, gate27s). No rendering/frame/cache/threshold/product code changed.

The observed thumbnail decreases do not establish video-recording causality from one pair of separate hosted runners, and the trial still fails both case and job budgets. No product speedup is claimed. Always-onvideo remains off in this candidate, with failure traces/screenshots and explicit existing screenshots retained; complete Chinese Newton feature recording remains the independent media deliverable.

Valid final attempts remain0/5. Parent's conditional four-rerun authorization was not activated. No further diagnostic or product change is authorized by that condition. PR remains draft; issue85 remains open; current checkout is clean and all task-owned local heavy processes remain gone.
