# Issue 85 checkpoint

## Current paused checkpoint

See CURRENT_CHECKPOINT.md for the authoritative current head, all three invalid trials, preserved resources, pause decision and required resume steps. The details below are historical; do not execute their obsolete four-shard/old-head commands.

## Historical checkpoint before diagnostic2/3

This task is not complete. Root owns independent review, the five CI attempts, PR readiness, and integration.

- Draft PR: https://github.com/CaiZongyuan/spatial-sync/pull/88
- Branch: `fix/issue-85-ci-margin`, pushed and clean.
- Exact head: `1ca52ea037700cd852f78a0a3516cad8ba51fad1`
- Exact tree: `857fa60c96b3eb2436f6031df74fed4b2fc20e36`
- Latest fetched main: `21417c855b5f7855525ebf28d76ae350f6f36541`, verified as an ancestor.
- Worktree: `D:/Projects/Robots/spatial-sync/.worktrees/issue-85`
- Evidence directory: `D:/Projects/Robots/spatial-sync/.scratch/rsi-platform/issue-85`
- Initial CI: https://github.com/CaiZongyuan/spatial-sync/actions/runs/39460558106
- Root now owns observer session **8581** and `ci-observer.json`. This agent started no background CI watcher and owns no active Source/browser process.

## Evidence and script entrypoints

`reduce-complexity-final.md` records the completed simplification pass. `IMPACT.md` and `PREP.md` retain scope, consumers and checkpoints. `pr-body-draft.md` is the exact published draft body; update its pending CI section before readiness.

`browser-assertion-map.md` maps every split journey. The unchanged Python collection is in `python-baseline-collection.txt` (963 nodes). `browser-candidate-collection.txt` contains 67 cases; `browser-baseline-skips.json` pins the eight unchanged skip identities. Expected final counts: Python **962 passes / 1 skip**, browser **59 passes / 8 skips** (the original 55/8 plus four mapped splits).

`platform-proof-summary.md` records the fixture repair. Pristine code plus a six-second delay at the actual cold-library property reproduces the original five-second Event timeout; the repair passes with that same delay and deadline. A sampler fault hiding LOADING still fails the original HTTP assertion. All seven uninstrumented platform cases pass with four workers. Original 51-pass/1-failure and invalid temporary-fixture attempts remain retained and classified. No product cache, source signal, deadline or serialization was changed.

`native-gate-summary.json` contains actual command/environment/result receipts for all four full CPU gates. Results: **33 CPU tests passed / 3 GPU cases deselected**, **15 actions / 6 physics checks**, Linux tick P95 **1.07685650109488 ms** across **3,788 samples** (40 bodies / 157 shapes / 100 declared welds). All 25 recorded identities exited. Raw receipts are `smoke-newton.json`, `smoke-newton-source.json`, `smoke-info-newton.json`, and `native-pytest.log`.

`capture-summary.json` and `preview-copy-verification.json` record all seven actual captures, strict input/file checks, seven strict readers and 28 document reads. All 29 capture identities exited. Raw outputs remain in `captures-710e98b2/`. `capture-jobs.json` records exact commands and wrapper hashes; `copy_verify_previews.py` performed validation and byte-only copying. Immutable library tree remains `002fd71457d6a0dc53163da3985182a1a8f2b0e7`. No capture metadata hash was edited manually. The two 99%-similar GLB renames are actual exporter output.

Capture window: Unix **1792514447.74–1792514569.74**. Root will compare the T0 D1 receipt to establish actual GPU overlap. Capture work is not a performance comparison; the latency gates passed beforehand in the separate window.

Earlier static checks passed: Ruff, platform skills (3/41), protocol generation, 7 protocol TypeScript tests, 183 web tests, production build and Chromium installation. Complete final GPU-free verification is the actual CI workflow; do not claim it passed until its results. Local full browser validation was not run; the capture grant did not include another Source/browser window. `browser-runtime-plan.md` is prepared if needed; ports 19885/5385 were never used by this agent.

## CI attempt procedure

Keep the final head frozen through review and all five valid attempts. Preserve every attempt, including failures and cancellations. A repair or rebase changes the candidate and resets the required five consecutive valid attempts.

Use **full reruns**, only after the previous attempt has completely finished and its artifacts/logs have been saved. Do not use `--failed`; every job and every case must be measured again. Do not overlap PR attempts because workflow concurrency cancels older running work.

```powershell
gh run view 39460558106 --repo CaiZongyuan/spatial-sync --json headSha,status,conclusion,jobs
# Only after the previous attempt is complete and retained:
gh run rerun 39460558106 --repo CaiZongyuan/spatial-sync
```

Artifact names are `browser-acceptance-evidence-<shard>-attempt-<attempt>`. Each contains `results.json` with exact timeout/duration/status, plus HTML/results and the API log. Download each attempt into a separate folder.

The receipt helper is `analyze_ci_attempt.py`. It was compiled and checked against the installed Playwright 1.64.1 JSON serializer, but has not yet consumed actual CI artifacts. Inspect raw inputs and output on first use. Parser format fixes are acceptable; do not relax the acceptance checks.

Example from the primary checkout, after the attempt has finished:

```powershell
$ciRunId = 39460558106
$ciAttempt = 1
$ciEvidence = '.scratch/rsi-platform/issue-85/ci-attempt-' + $ciAttempt
New-Item -ItemType Directory -Force $ciEvidence | Out-Null
gh api "repos/CaiZongyuan/spatial-sync/actions/runs/$ciRunId/attempts/$ciAttempt" | Set-Content -Encoding utf8 "$ciEvidence/run.json"
gh api "repos/CaiZongyuan/spatial-sync/actions/runs/$ciRunId/attempts/$ciAttempt/jobs?per_page=100" | Set-Content -Encoding utf8 "$ciEvidence/jobs.json"
gh run view $ciRunId --repo CaiZongyuan/spatial-sync --attempt $ciAttempt --log | Set-Content -Encoding utf8 "$ciEvidence/ci.log"
foreach ($ciShard in 1..4) {
  gh run download $ciRunId --repo CaiZongyuan/spatial-sync --name "browser-acceptance-evidence-$ciShard-attempt-$ciAttempt" --dir "$ciEvidence/shard-$ciShard"
}
$ciReports = rg --files --hidden --no-ignore $ciEvidence -g results.json
$ciReportArgs = @()
foreach ($ciReport in $ciReports) { $ciReportArgs += @('--report', $ciReport) }
.worktrees/issue-85/.venv/Scripts/python.exe .scratch/rsi-platform/issue-85/analyze_ci_attempt.py --run-json "$ciEvidence/run.json" --jobs-json "$ciEvidence/jobs.json" --log "$ciEvidence/ci.log" --expected-head 1ca52ea037700cd852f78a0a3516cad8ba51fad1 --collection .scratch/rsi-platform/issue-85/browser-candidate-collection.txt --baseline-skips .scratch/rsi-platform/issue-85/browser-baseline-skips.json @ciReportArgs --output "$ciEvidence/summary.json"
```

The helper checks the exact head, all eight complete successful jobs, each job ≤200s, all 67 case identities, unchanged skip identities, no retries, actual Python totals 962/1, and every duration×5≤timeout×3 without rounded log timing. Five consecutive valid same-head summaries plus independent review are still required.

Before merging: rewrite the PR's pending section with the actual attempt table and case/count results; ensure latest main push is successful, current main is an ancestor, and exact-head CI is green. Compare the integrated tree with `857fa60c96b3eb2436f6031df74fed4b2fc20e36`, or a refreshed reviewed/tested tree after any repair. Do not close #85 before actual integration.

## Retained owned resources

No Source/API/web/browser process remains. Root owns the separate CI observer. The worktree, branch and ignored receipts remain available.

- Windows base: worktree `.venv`.
- Linux base: `/tmp/spatial-sync-issue85-20261020-1527/venv`.
- Linux Native: `/tmp/spatial-sync-issue85-20261020-1527/native-venv`.
- Native Python cache: `/tmp/spatial-sync-issue85-20261020-1527/native-pycache`.
- Warp cache: `/tmp/spatial-sync-issue85-20261020-1527/warp-cache`.
- Earlier unused DrvFS environment: worktree `.venv-wsl` (ignored by uv).

Never resync another issue's or T0's canonical environment; never sync an environment with active consumers. User ports 8011/5191/8014 and staged HTML were untouched. No other issue was claimed.
