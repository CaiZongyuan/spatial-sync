---
name: development-timeline
description: Record development stages and verification evidence, then generate offline HTML reports of delivery, waits, rework, and improvements. Use for PM milestone delivery, pauses, handoffs, or development retrospectives.
---

# Development Timeline

Show what was delivered, what remains unfinished, and where work waited or repeated. Record stage changes during development and reuse evidence at delivery. When Matt's `retro` is used, give it these same records for environment-improvement recommendations and link the decisions back. Report generation adds no product gate or further development obligation. Write report content in English unless the user requests another language.

## Start and Record Stages

1. Create a local report directory for the authorized task. Before the first record, read the [data and evidence contract](references/data-contract.md) and create a short report JSON. Reuse the structure across projects, rather than copying project-specific issue numbers or conclusions.
2. Record dispatch, stage boundaries, actual reasons for waiting, role changes, key verification or review findings, repairs, and integration. Use [record-event.mjs](scripts/record-event.mjs) or existing structured runner records. Record meaningful changes rather than every tool call.
3. Issues, PRs, the tracker, and actual delivery results are authoritative. Distinguish local work, integrated results, and unverified areas. Passing tests alone do not establish issue completion.

```bash
node /path/to/development-timeline/scripts/record-event.mjs \
  --journal .scratch/task/events.jsonl start --id sdk-check \
  --lane "API ticket" --kind command --category validation --label "SDK check"
node /path/to/development-timeline/scripts/record-event.mjs \
  --journal .scratch/task/events.jsonl end --id sdk-check --outcome passed
```

Timestamps default to the actual recording time. Use `--at` for historical times only when supported by evidence; preserve unknown thinking, queueing, and command start times. Multiple actors can record separate journals and merge them during rendering.

## Report and Attribute

At task or milestone completion, pause, or handoff, update the task status, issue and delivery results, findings, and retained resources, then generate the report. Report failed and partial deliveries as they are, without waiting for every problem to be resolved.

To generate both `current.md` and HTML from the same decisions, journals, and optional CI observations, read [shared-input composition](references/composition.md) and use `scripts/compose-report.mjs`. Keep authorization, scope, and completion as explicit PM input. The composer does not infer them from CI. For a standalone timeline, use the existing renderer below.

```bash
node /path/to/development-timeline/scripts/render.mjs \
  --input .scratch/task/report.json --events .scratch/task/events.jsonl \
  --output .scratch/task/timeline.html
```

- Explain causes as "rule or choice -> actual execution -> result -> proposed change". Distinguish necessary verification, misapplied rules, execution errors, and unproven inferences. There is no required number of findings or rules.
- A `phase` interval may contain coding, reading, and waiting. A `command` interval includes startup and cleanup and is not CPU time. Points, save times, and stages without an end have no measured duration. Show cumulative command intervals and their union separately; leave unknown gaps unknown.
- For browser checks, distinguish actual execution from zero collected tests, and expected failures from product defects or failed prerequisites. Check whether an existing predicate already proves the user's requirement; failure counts alone do not measure wasted work.
- Identify useful delivery and verification. Explain evidence limits when hours cannot be allocated precisely, and propose concrete changes rather than assigning broad efficiency labels.
- Load raw evidence only for unresolved questions. Produce a lightweight report when history is missing; report gaps rather than rerunning product tests or recruiting an audit team to fill the chart.

## Check and Deliver

Check JSON, event times, statuses, relative evidence paths, and redaction. Open the offline HTML with available browser capabilities and check filters, details, and main views. Select representative desktop viewports for the report's purpose; add mobile product testing only when included in the approved scope.

The HTML needs no server or CDN. Deliver a directly openable file link, a short account of completion, and the most important improvements. Keep methods, test results, and rule analysis in the report. Public publication, uploads, and tracker changes still require existing authorization; generating a local report does not provide it.
