# Observe GitHub CI Changes

Use the helper when repeatedly checking the same PR would add no decision value. It uses Node.js 18+ and the existing authenticated `gh` executable; it reads GitHub.com REST APIs without changing GitHub state or downloading job logs. Pagination uses `gh`'s built-in `--jq '@json'` to produce one compact JSON object per page, so it needs no external `jq` or newer `--slurp` flag. The CLI compatibility surface is tested against gh 2.45.0.

```bash
node <pm-development>/scripts/ci-observer.mjs \
  --repo OWNER/REPOSITORY --pr 23 --output .scratch/my-task/ci.json

node <pm-development>/scripts/ci-observer.mjs \
  --repo OWNER/REPOSITORY --pr 23 --output .scratch/my-task/ci.json \
  --watch --interval-ms 15000 --timeout-ms 1800000
```

Create the output's parent directory first. Run `--help` for arguments and exit codes. `--repo` and `--pr` are required even inside a repository: the current directory never selects the target. The default is one observation, a 15-second watch interval, and a five-minute total deadline. Choose a deadline appropriate to the host's CI rather than starting an unbounded watcher.

Watch stops when every *currently visible* run, job, check run, and legacy status context is terminal, or when interrupted or timed out. A failed or cancelled CI result can be a complete observation with exit 0. An empty CI list keeps watch active until its deadline; a successful one-shot with empty lists only proves that the API returned no records.

PM acts on the first observation and subsequent changes. Routine poll timestamps do not produce notifications. Head, attempt, job/check state, result, and PR merge changes do; repeated identical API errors are reported once, with another notification on recovery. The JSON is refreshed on each poll. Long notifications point to the JSON rather than printing full job lists.

## Facts and Decisions

The JSON is a machine-fact source for a checkpoint or delivery report. It contains no `passed`, `requiredCI`, or task-completion aggregate. Apply the host's required-check list, final candidate, review evidence, integration evidence, and authorized delivery goal separately.

- `headSha` scopes current `runs` and `checks`. Current arrays exclude records from other heads; changed heads and previous attempts remain attributed in `history`.
- The PR head is read before and after collecting CI. If it changes during collection, the mixed observation is rejected with `error.code: "head_changed"`; the previous successful facts remain stale, and watch retries the next candidate. A one-shot returns the error for the caller to retry. Reconfirm the host's final candidate at its delivery gate: this check cannot freeze a PR against future updates.
- Actions jobs come from each run's explicit `run_attempt`, including paginated jobs. Checks include latest check runs and the latest state per legacy status context. Failure URLs are retained for focused investigation.
- Run, job, check, and legacy-status URLs are saved only when they are valid HTTP(S) locators without userinfo, query, fragment, or control characters. Safe URLs are normalized (for example, a path space becomes `%20`). Other URLs become null before snapshots/history are published; their original values are omitted. Use the constructed PR link or a clean retained run link to find omitted external evidence through the authenticated UI.
- `pullRequest.state`, `mergedAt`, and `mergeSha` record what GitHub says. An unmerged PR's test-merge SHA is not recorded as an actual merge.
- `observation.status` describes fetching and waiting, separately from each CI conclusion. On `error`, `timeout`, or `cancelled`, `stale: true` and the last successful facts remain visible with `lastSuccessfulAt`; the retained green state cannot establish current success. A first failed observation has null head/PR facts and empty arrays.
- Job and check timestamps come from API fields. Actions run REST has no precise completion timestamp, so `run.completedAt` is null; `updated_at` is not used as a substitute. Missing times stay unknown.
- `observedAt` is when the observation was saved, not when CI finished or when the PM first noticed it. Record a consequential next action separately in the existing journal.

An API error is generic and intentionally excludes raw stderr and environment values. Check local authentication, repository access, and network before retrying. If a required check is missing, resolve that through the host workflow; a visible green subset does not prove coverage.

## Output Contract (Version 1)

```text
schemaVersion: 1
producer: { name: "ci-observer", ownerId }
source: { repo, pr, url }
headSha: string | null
observedAt: ISO timestamp
observation: { status: "ok" | "error" | "cancelled" | "timeout", error?: { code, message } }
lastSuccessfulAt: ISO timestamp | null
stale: boolean
pullRequest: { state, mergedAt, mergeSha } | null
runs: [{ id, name, headSha, attempt, status, conclusion, url, startedAt, completedAt,
         jobs: [{ id, name, status, conclusion, url, startedAt, completedAt }] }]
checks: [{ id, name, headSha, status, conclusion, url, startedAt, completedAt,
           source: "check-run" | "status-context" }]
history: [{ at, type, observationStatus, headSha, pullRequest, runs, checks }]
```

`history.type` is `snapshot`, `error`, `recovery`, `timeout`, or `cancelled`. It records changed facts during this run, not every polling tick; a restarted observer begins a new owner/history. Historical snapshots retain their original head and attempt. CI providers' conclusions remain their original strings (for example, `success`, `failure`, `neutral`, `skipped`, or `cancelled`). Host policy determines which conclusions satisfy required checks.

## Ownership and Interruption

Each invocation owns a unique ID, one canonical output lock (`ci.json.lock`), and an atomic temporary file (`ci.json.<ownerId>.tmp`). It can replace an earlier observer report for the same repo/PR. It refuses to replace an unrelated file or a symlink, and the canonical-directory lock prevents two aliases from writing the same target concurrently.

SIGINT/SIGTERM and the total deadline stop in-flight `gh` process groups, save an honest cancelled/timed-out report when writable, and release owned temporary files and the lock. No separate background daemon is started. On an uncatchable termination such as SIGKILL or machine loss, verify the lock's PID/owner and any remaining process before manually removing that exact lock/temp file. Restart after reconciling those files; the tool does not steal another observer's lock.

Use a unique output per independent owner/task. Keep reports in the task's scratch/evidence directory and retain them as the single source for presentation. The script needs no Docker, npm packages, CI trigger, or tracker mutation.

## Offline Verification

```bash
node --test <pm-development>/tests/ci-observer.test.mjs
```

Tests use controlled API responses and an executable `gh` substitute that rejects unsupported `--slurp`. They cover multi-page JSONL parsing, Chinese/emoji split across stdout chunks, URL omission before persistence, target selection, state deduplication, head changes during collection, head/attempt/job changes between observations, historical success, API errors/recovery, empty lists, terminal CI failures, PR merge facts, output ownership, deadline and signal cleanup. They do not contact GitHub or run the host project's CI.
