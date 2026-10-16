# Data and Evidence Contract

Use a JSON report and optional JSONL events. Start with essential facts and add actual results at completion. Omit rules, tests, or resources when there is no corresponding work, and use actual counts rather than example counts. The helpers do not read GitHub, execute business commands, or infer integration state.

## Minimal Report

```json
{
  "project": "Project name",
  "title": "Development delivery",
  "snapshotAt": "2026-10-05T01:00:00Z",
  "task": {
    "status": "partial",
    "goal": "Approved result",
    "startedAt": "2026-10-04T10:00:00Z"
  },
  "scope": { "platform": "desktop-web", "mobile": false },
  "tickets": [],
  "events": [],
  "findings": []
}
```

Example times do not represent actual work. `task.status` is `in-progress|completed|partial|blocked|paused`. `completed` means the authorized result was delivered; it is not inferred from an exit code.

## Events

| Field | Meaning |
| --- | --- |
| id, lane, label | Unique identity, issue/role/stage lane, and short event name |
| kind | `phase`: work window; `command`: recorded execution; `wait`: confirmed waiting; `point`: instant |
| category | `implementation|review|validation|diagnosis|integration|coordination` |
| start, end | ISO timestamps with a timezone; end is optional. Points have no end. A missing end is not extended to the snapshot |
| outcome | `passed|failed|expected-red|skipped|unknown`; `expected-red` requires the target assertion to fail, rather than inference from a filename |
| confidence | `verified|inferred|unknown`; describes the claim supported by evidence. A known time does not establish a known cause |
| actor, revision | Actual executor and commit/tree; may be omitted. Preserve the real target rather than substituting a stale one |
| reason, detail | Reason for waiting, check coverage, failure classification, or remaining unknowns |
| evidence | `[{"href":"relative/log-or-http-url","label":"Short source"}]` |

Only commands with an end contribute to measured execution intervals. Intervals across lanes can overlap, so their cumulative sum may exceed task wall time. Their union measures time when at least one recorded command was running. Neither measure represents CPU time or all development work.

A file's mtime can support an "artifact saved" point, not a reviewer's start or end. Leave unknown gaps empty. When coding or rechecking is known to have occurred but cannot be allocated precisely, state that limit rather than assigning the entire gap to waiting.

## Optional Results

- tickets: `{id,title,status,url?,blockers:[],delivered:[],pending:[]}`. Status is `integrated|in-progress|ready|blocked|not-started`. Use actual tracker state and merge evidence when available; a ready label alone does not establish readiness.
- findings: `{id?,title,mechanism,impact?,proposal,confidence,evidence:[]}`. Explain how a cause produced a result, rather than repeating observations such as a late start or many tests.
- rules: `{id,title,source?,quote?,actual,mechanism,proposal,retain?,classification?,confidence?,evidence:[]}`. Classification can be `rule-design|misapplication|execution-error|necessary|unproven`.
- tests: `{id?,label,method,start?,end?,uiExecuted?,outcome,cause,fix?,evidence:[]}`. State whether the real stack or an HTTP substitute ran, test discovery counts, target identity, expected failures, and setup errors. A tests view is optional when no browser checks ran.
- resources: `{name,owner,purpose,disposition}`. Disposition is `cleaned|retained|unknown`, supported by the actual ownership ledger and final inventory. Missing resource records do not establish cleanup.

Evidence paths are relative to the JSON or JSONL file that contains them and are rebased when rendered to another directory. Record command names, exits, public target identities, source revisions, and redacted paths. Exclude credentials, complete environments, authorization headers or cookies, secret-bearing raw requests, and signed URLs. Reports stay local by default.

## Lightweight Journal

The helper does not execute business commands. An end closes the same id as its start; use a new id for the next attempt. Record repeated commands as useful, without requiring an event for every tool call.

When merging multiple actors' journals, use globally unique ids with an actor, issue, or stage prefix. Each actor owns a separate file. Register an event once, in either the report JSON or a journal; choose one source for events already summarized.

```json
{"type":"start","at":"2026-10-04T10:00:00Z","event":{"id":"api-test","lane":"API","kind":"command","category":"validation","label":"HTTP behavior","start":"2026-10-04T10:00:00Z","outcome":"unknown","confidence":"verified","evidence":[]}}
{"type":"end","at":"2026-10-04T10:00:12Z","eventId":"api-test","outcome":"passed","confidence":"verified","evidence":[]}
{"type":"point","at":"2026-10-04T10:01:00Z","event":{"id":"review-found","lane":"API","kind":"point","category":"review","label":"Boundary finding","start":"2026-10-04T10:01:00Z","outcome":"unknown","confidence":"verified","evidence":[]}}
```

End records retain the start's detail and evidence. Explicit actor, reason, or revision values update those fields; omitted values retain the start's values. An end without explicit confidence inherits the start's confidence; update it explicitly only when supported by new evidence. Orphan ends can become points with a missing-start warning, without inventing a start. A stage registration time may differ from the actual command start; prefer precise runner records when available.

## Optional Manual Checkpoint and CI Composition

A report may include `checkpoint: {authorization,roles,revision,next}`. These human-owned values are preserved as supplied; strings and structured JSON are supported. CI comparison uses only a full 40-character commit SHA in `checkpoint.revision`. Task state, goal, scope, authorization, roles, and next actions are never inferred from machine facts.

Use [composition.md](composition.md) for the `compose-report.mjs` command, optional version-1 CI snapshot contract, and output failure behavior. It merges the same journals as the renderer and produces both a compact `current.md` and the detailed offline HTML from one model. The original `render.mjs` command remains available when only HTML is needed.
