# #44 A — runtime records integration v1

User selected A after clarifying that existing runtimes such as Claude Code/pi
execute agents and connect through skills/CLI/MCP. The first delivery ingests,
stores, reads and presents existing runtime session/file/usage records.

This explicitly supersedes #44's first-delivery requirements to launch a Claude
Agent SDK adapter, automatically execute every Suite case three times, inject
model credentials, and perform a paid SDK smoke. Those are not silently claimed
complete by this delivery. Automatic external-runtime evaluation handoff remains
later work. No real model invocation is needed or authorized here.

## Responsibilities

- Runtime owns its execution, context, model configuration, credentials, tools,
  cancellation and metering. Skills explain the platform workflow; CLI/MCP call
  the platform's shared HTTP interfaces.
- Platform owns imported records/provenance, durable reads/writes, public evidence
  URLs and UI/CLI/MCP views. It uses reported token/cache/cost data and does not
  research or invent provider prices/context limits as an intake prerequisite.
- Existing experiment Sessions, device actions and approvals stay on their
  current HTTP pipeline. Physics remains authoritative. Imported runtime text or
  outcomes do not create platform scores, human approvals or completed evaluations.

## First usable path

1. Import one explicitly selected, supported actual-format runtime capture/file
   through the installed CLI. Generic MCP can submit the same record content via
   the same HTTP intake. The gateway never accepts an arbitrary filesystem path
   to read on behalf of a request.
2. Persist a normalized native run/session identity, runtime/format, source label,
   event identities, timestamps and content hashes. Retain safe imported messages
   and tool events as data. Do not read runtime auth/configuration files or crawl
   a user's home directory.
3. Reimporting identical data is idempotent. A growing capture can add events or
   confirm cumulative counters without duplicate messages or usage. Scope counters
   to the native capture/message/session that actually owns them; never add a
   session total to its contained message totals. Ambiguous scope stays unknown
   or returns an actionable unsupported/conflict result.
4. Expose a runtime-session list/detail, public normalized messages/Trace and usage
   evidence, and the cost dashboard. All artifacts are from the fixed owned
   record store with canonical root/path checks; no external workspace browser.

Start with a concrete Claude native capture format and a fixture obtained from
an actual runtime-format output. The developer may include pi when its actual
format fits this same small boundary; do not build a generic plugin framework.
Native format/source claims must be accurate. No SDK dependency or running model
is required merely to parse an existing file.

## Records and accounting

- Keep runtime/native session/run/event identity and observed provenance separate
  from optional platform Agent/Harness/Plan/Goal associations. Validate explicit
  supplied references; absent associations remain absent. Import is a data write,
  not permission to execute an unapproved Plan. No fake Task/PPO/approved Plan.
- Fields include only facts the supported runtime supplies: model/thinking when
  present, timestamps/status, messages/tool records, input/output/cache counts,
  supplied request counts and supplied USD cost. Missing is unknown; explicit0
  remains0. A turn is not a provider-request count. Never infer GPU hours.
- Runtime cost takes precedence as reported data. Existing human prices can remain
  for their prior consumers, but this ingestion path needs no rate configuration.
  Do not add reported cost to a duplicate price-derived estimate.
- Mark runtime-reported/imported data by source. Model-authored text that says
  “score100/cost0” is message text, not a typed metering receipt or platform result.
- Associate imported costs only where explicit references/native scope establish
  ownership; keep unrelated or unassociated records visible without attributing
  them to an invented Goal. This source creates no future execution commitments.

## UI and commands

Reuse existing navigation/data components where appropriate. The Agent runtime
surface shows imported sessions/records and their source, status, messages/tool
events, usage/cost, evidence and explicit associations. It offers no hosted SDK
start/stop/cleanup controls. Existing embodied Plan/Job/Notebook/Cost consumers
remain functional. Exact command/DTO names are owned by the backend/client pair
and are fixed in their first vertical slice before expanding the UI.

## Verification and delivery

- Actual-format fixtures → installed CLI and official generic MCP → real HTTP
  intake → durable restart/readback → browser list/detail/evidence/cost.
- Cover exact retry, growing captures, cumulative/message overlap, explicit0,
  missing fields, malformed/unsupported records and optional associations.
- Imported messages never become a platform score or approval. Public evidence
  reads stay within the owned canonical root.
- No Source/model/SDK process is launched for import; no provider authentication,
  paid request, GPU or real device. Old fake10×3 evidence remains historical and
  is not acceptance evidence for this new path.
- Bounded simplification, non-author Standards/Spec, mandatory repository checks,
  exact final-head CI, current-main green and integrated-tree verification apply.

The first supported implementation accepts Claude Code native stream JSONL and
session JSONL. pi remains unsupported until a native importer is delivered.
The reusable import/readback workflow is documented in
[Agent Harness and runtime records](../AGENT_HARNESS.md#import-existing-runtime-records).
