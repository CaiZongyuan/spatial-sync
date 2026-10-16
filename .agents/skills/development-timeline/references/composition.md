# One source for current checkpoint and HTML

`compose-report.mjs` reads a human-owned report, optional actor journals, and an optional CI observation JSON. It generates `current.md` and the existing offline timeline HTML from one normalized model. It does not query GitHub, run tests, claim issues, or change the source files.

## Inputs and invocation

Use the report schema in [data-contract.md](data-contract.md). PM supplies `task.status`, `task.goal`, the approved `scope`, and `snapshotAt`; machine observations cannot decide these values. Add a small optional checkpoint:

```json
"checkpoint": {
  "authorization": "Build a local preview; publishing awaits user review.",
  "roles": [{ "role": "Developer", "owner": "Agent A", "ticket": "42" }],
  "revision": "full-40-character-commit-sha-or-a-working-tree-label",
  "next": "Inspect the preview, then decide whether to publish."
}
```

The checkpoint is preserved as supplied, including multiline strings and structured roles or next actions. Use a full 40-character commit SHA when comparing a candidate with CI. A short SHA, dirty-tree label, or missing revision is an unknown relationship. A CI run on a commit cannot establish validation of later uncommitted edits; update the checkpoint revision label accordingly.

```bash
node skills/development-timeline/scripts/compose-report.mjs \
  --input .scratch/run/report.json \
  --events .scratch/run/developer.jsonl \
  --events .scratch/run/reviewer.jsonl \
  --ci .scratch/run/ci.json \
  --current .scratch/run/current.md \
  --output .scratch/run/timeline.html
```

`--ci` and `--events` are optional. Event files retain their own evidence origins and follow the existing start/end journal contract. The composer reads CI JSON, so the timeline skill works without installing the PM skill or its observer script. Reports without CI retain their original views; no CI tab is shown.

Current Markdown is a compact checkpoint: explicit goal, scope, authorization, roles, revision, next action, tickets, CI facts, the five latest recorded events, resources, and evidence. HTML retains the detailed timeline, causes, test records, and evidence views, and adds manual checkpoint and CI views when supplied. Relative evidence links resolve correctly from each output directory. Markdown destinations encode parentheses and whitespace and escape HTML entities without changing the target resource. Update the source report to change manual decisions; do not independently edit both generated outputs.

## CI snapshot contract

The current version consumes a JSON object with `schemaVersion: 1`:

```json
{
  "schemaVersion": 1,
  "source": { "repo": "owner/repo", "pr": 42, "url": "https://github.com/owner/repo/pull/42" },
  "headSha": "0123456789012345678901234567890123456789",
  "observedAt": "2026-10-06T10:30:00Z",
  "observation": { "status": "ok" },
  "lastSuccessfulAt": "2026-10-06T10:30:00Z",
  "stale": false,
  "pullRequest": { "state": "OPEN", "mergedAt": null, "mergeSha": null },
  "runs": [],
  "checks": [],
  "history": []
}
```

Observation status is `ok|error|cancelled|timeout`. An observation error may contain `{ "code": "...", "message": "..." }`. A failed observation keeps the last successfully observed head, runs, checks, and PR state; they are stale facts. A first observation failure has no established head or CI rows. The report displays the current observation error separately from last known runner statuses, rather than turning an old green run into a current pass.

A run has `{id,name,headSha,attempt,status,conclusion,url?,startedAt?,completedAt?,jobs:[]}`. A job has `{id,name,status,conclusion,url?,startedAt?,completedAt?}`. A check has `{id?,name,headSha,status,conclusion,url?,startedAt?,completedAt?,source?}`; source may be `check-run` or `status-context`. Rows retain their head, attempt, and nullable timestamps. CI URLs are retained only when they are absolute HTTP(S) links with no user information, query, or fragment; other CI links become `null`. The original unsafe URL is not copied into generated outputs or their embedded model. This conservative policy applies to CI locators, while ordinary report evidence keeps the general evidence-link contract. No aggregate "CI passed" flag is inferred, and missing check rows leave required-check coverage unknown.

`history` is optional. Its summary entries have `{at,type,headSha,observationStatus}`, where type describes an observed snapshot, error, recovery, timeout, or cancellation. The composer displays observation changes as instants and keeps detailed run history in the source CI JSON. It does not invent a runner start or a human next-action time from these observations.

The displayed candidate relationship is `current` only when the full candidate revision equals the observed head; unequal full SHAs are `historical`, and uncomparable identities are `unknown`. Freshness is `stale` after a failed observation or explicit stale flag; otherwise a comparable current head is `observed` and an unknown/historical relationship remains `unknown`. These labels describe evidence, not merge readiness. PM still checks required final-head CI in the actual project workflow.

## Output failure and source protection

The source report, journals, and CI JSON are read-only inputs. Outputs must be distinct regular-file destinations and cannot alias any input or each other through identical paths, hard links, or symbolic directory aliases. Output-file symlinks are rejected. Source bytes are never rewritten.

Both outputs are prepared in temporary files before publication. Replacing two files is not a filesystem transaction: if one replacement succeeds and the next fails, the CLI reports `Partial publication`, identifies the refreshed file, and exits unsuccessfully. Fix the cause and rerun the same command to refresh both outputs; do not claim both are current from a failed invocation. Snapshot and revision identities remain visible in each artifact. Temporary staging files are removed after success or a handled failure; an interrupted process can leave dot-prefixed `.tmp` files belonging to that invocation.

The helper uses Node.js 18+ and the timeline skill's existing renderer, with no additional packages. When available, the tests also use the external Python `markdown_it` CommonMark parser to verify actual parsed evidence destinations; basic destination assertions always run and the helper has no Python runtime dependency. Its public checks run with:

```bash
node --test skills/development-timeline/tests/*.test.mjs
```
