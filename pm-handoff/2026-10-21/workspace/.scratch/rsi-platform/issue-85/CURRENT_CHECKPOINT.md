# Issue 85 paused checkpoint

Root paused #85 to prioritize the user-facing #86 UI delivery on current main. PR88 remains Draft/open; #85 is incomplete with **0/5 valid final attempts**. No new diagnostic, rerun or product change is authorized during this checkpoint.

Current pushed, clean head: `9b41f23989d5689e8abd6e03e066f682739a37b8`; tree `8a74b458ef5d1db7e815ff498d6d1df354f07a86`. Last observed remote main was `21417c855b5f7855525ebf28d76ae350f6f36541`, verified as an ancestor. Standards and independent Spec source reviews PASS at this exact head. Those reviews do not waive the margin gates.

| Actual run / attempt | Head | Longest job | Outcome |
|---|---|---:|---|
|39460558106 / 1|1ca52ea|280s|All 8 jobs green; 3 case-margin violations|
|39464063126 / 1|bdfdd4b|211s|Overview precondition failure; 2 case-margin violations|
|39465698472 / 1|9b41f23|238s|All 9 jobs green; 2 case-margin violations|

Complete raw data lives respectively in `ci-attempt-1-diagnosis`, `ci-diagnostic-2` and `ci-diagnostic-3`. Preserve all source, environments, refs and receipts. Both later trials selected 4 workers on physical 2 / affinity 4. The latest trial preserves all 963 Python identities (962 passed / 1 skipped), all 69 browser identities (61 passed / 8 unchanged skips), and no retries. Gateway pytest took 224.27s / job 238s; browser shard 5 took 208s. The 35-image/all-seven cases took 27.853s/28.391s, with render phases of 26.862s/27.620s. Gateway slowdown affected 372 of 396 cases relative to diagnostic 2. No causal video or product speedup is claimed.

The overview split accepted a prior replay before the owned live session; the preserved failure trace proves it. The committed repair establishes the owned sessionId, read-only=false, connected state and actual scene/socket requests before taking the baseline. Post-theme equality is unchanged, and diagnostic 3 passes that case. CI video OFF was the single approved performance condition. Failure traces/screenshots, existing explicit screenshots, phase JSON, full logs and JUnit remain. Complete Chinese Newton feature recording belongs to the separate media delivery. No production renderer/cache/frame/timeout change exists.

No task-owned Source/API/web/browser/watcher/download process is active. Root owns observer session8581 and will end it. No protected user port, staged HTML or T0 environment was modified. Retain all owned environments listed in HANDOFF.md. All owned gh download/log sessions have exited.

Resume with the same owner after UI merges. Rebase onto then-current main, refresh impact and complete collections, reassess strict preview inputs, and regenerate affected actual Source captures with required Newton CPU gates. Refresh exact-candidate review and every mandatory check. Old-head green runs cannot validate the new head. Use retained JUnit to choose one bounded probe supported by actual evidence; do not blindly rerun. Two Python lanes / `-n auto`, all 7/35-image obligations, actual three settling frames, 45s timeouts, and the 200s / 60% / five-consecutive-same-head gate remain.

The analyzer supports `--shards 5`, collection `browser-diagnostic-2-collection.txt` (69 cases), baseline skips, `--python-collection python-baseline-collection.txt`, both `--junit` XMLs and all five `--report` files. Downloaded artifacts include `home/runner/work/spatial-sync/spatial-sync/.scratch/viewer-e2e/results.json`; discover paths with `Get-ChildItem -Recurse -Force`. Each diagnostic has `summary.json` and `diagnosis.md`. Native producer4137b9e and actual captures1ca52ea remain historical evidence for their frozen inputs, not future rebase evidence.

Pause comment: https://github.com/CaiZongyuan/spatial-sync/issues/85#issuecomment-6297081362
