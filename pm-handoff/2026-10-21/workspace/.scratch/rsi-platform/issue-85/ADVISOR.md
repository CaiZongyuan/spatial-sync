# Advisor: separate CI-margin investigation from UI delivery

**Recommendation — B.** Continue #86 against current main; retain #85/PR88 as
unfinished, at **0/5** valid margin attempts. Do not run another full CI trial
merely to seek a favorable runner. The 200-second/60%-timeout targets belong to
#85's acceptance, not to #86's dependency graph. This changes scheduling, not
either ticket's acceptance.

#86 must still pass its actual full mandatory checks and existing GPU-free CI
within five minutes, plus applicable Newton/preview checks, independent reviews,
latest-green-main, exact-head CI and integrated-tree verification. Run that real
candidate when ready and decide from its results; do not assume it will exceed
the old limit or waive a gate. #85 later rebases and refreshes affected evidence.

**Evidence.** One read of completed run39465698472 confirms nine successful jobs
on9b41f23. Gateway was238s (396 tests/224.27s pytest); browser5 was approximately
208s. Thus success of checks still fails #85 margin. The previous same-worker
gateway observation was193s; physical2/affinity4/selected4 is already measured.
Video-off changes the measurement conditions and establishes no product speedup.
Third-run detailed case/JUnit accounting was still being collected at consultation.

`ThumbnailQueue.tsx` renders one queued entry at a time, has a shared32-image
cache and queue-local retained results. The35-image contract deliberately exceeds
that cache. `SceneViewport.tsx:623–630` waits for actual physical-node readiness
and three frames, then calls `gl.render` and synchronous `toDataURL`. Existing
29.857s/27.323s phases encompass that entire path; they do not identify frame
scheduling, model readiness, draw or PNG readback as the bottleneck. Neither
reducing35/7, warming the cache nor deleting settling frames is justified.

**One best next probe — offline paired receipt analysis, after the owner finishes
collecting trial3.** Join trial2/trial3 JUnit by exact test identity; verify all963
identities/skips, compare per-case changes and gateway summed case time divided
by four with actual pytest wall time. Include the existing two thumbnail phase
receipts; add no browser/Source run or instrumentation now. Success means this
separates broad case-time inflation from increased unaccounted scheduling/tail
time while preserving exact work. Missing identity, phase or timing boundaries
means “not identifiable”, then stop rather than prescribe an optimization.

This preparation can proceed while #86 implements/freezes; it must not reserve
the UI's Native window or delay its final CI. No fourth remote probe is proposed
in this phase. Remaining uncertainty is real runner variation and finer renderer
timing; eventual #85 rendering edits may overlap #86 and require semantic rebase
and renewed affected verification.

Read-only consultation. No code, test, limits, runner tier, tracker or PR changed.
