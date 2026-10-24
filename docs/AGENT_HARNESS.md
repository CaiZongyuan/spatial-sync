# Agents and Harness versions

An Agent keeps its model and thinking level outside its editable Harness.
`lab-operator` is bundled with both fields unconfigured (`null`). Runtime
execution and model configuration belong to the existing external runtime; creating a snapshot
does not start a model request, a job or a simulation.
Explicit Agent model references and thinking levels use the model catalog's
syntax. Selecting a platform default model leaves existing Agent definitions
unchanged.

The user library stores `agents/<name>/agent.yaml`, an editable `harness/`,
immutable `snapshots/vN.tar`, and `versions/vN.json`. The bundled Agent becomes a
user-library fork on its first snapshot. A Harness contains `AGENTS.md`,
`skills.lock`, `mcp.yaml` and optional UTF-8 files under `hooks/`. Hooks are
versioned authoring inputs; this interface does not execute them.

```powershell
lw agent ls --json
lw agent show lab-operator --json
lw agent snapshot lab-operator --json
lw agent versions lab-operator --json
lw agent export lab-operator 1 --output original.tar --json
lw agent create my-operator original.tar --json
lw agent import my-operator original.tar --json
lw harness diff my-operator 1 1 --json
```

Export requires a new output file. Import restores the editable Harness and
records a new Candidate if the contents differ. Before replacing a locally
edited Harness, import also snapshots those edits. Importing identical current
contents is idempotent. Version allocation is serialized and durable: failed
publication can leave a gap, and rejected versions remain allocated. Snapshot,
import and fork do not promote the Reference. Promotion belongs to the RSI
acceptance workflow.

Snapshots contain only Harness authoring files, never Agent metadata, `.env` or
Vault files. Filesystem links, hardlinks, archive links, path escapes, portable
filename aliases, binary files and recognized credential literals are rejected.
MCP configuration supports stdio servers with `command`, `args` and `env`;
environment values must be references such as `${LW_URL}`, not literal values.
Keep all credentials in their dedicated store and use environment references in
instructions and hooks as well. Snapshot validation never opens that store.

```yaml
# harness/skills.lock: version identifies a specific installed platform skill
skills:
  - name: lab-operate
    version: "1"
---
# harness/mcp.yaml
servers:
  lab:
    command: lw
    args: [mcp]
    env:
      LW_URL: ${LW_URL}
```

The HTTP API exposes `/api/v1/agents`, `/agents/{name}`,
`/agents/{name}/versions`, and `/agents/{name}/versions/{N}`. Add `?against=M` to
the last route for a text diff. `GET .../versions/{N}/snapshot` downloads tar
bytes. `POST /agents?id=<new-name>&title=<title>` creates an Agent from an
`application/x-tar` body and leaves its model and thinking level unconfigured.
`POST /agents/{name}/snapshots` accepts either an empty body with no Content-Type
to snapshot the editable Harness, or `application/x-tar` to import and restore.
Mutations require operator authorization; role headers cannot grant access.
The CLI and generic `agent_*` / `harness_diff` MCP tools share these routes and
also work with no active Session. No Vault or approval tools are added.

`lw suite show suite/lab-ops-basic@1 --json` returns ten public laboratory tasks
for Synthetic `lab-demo@3`, pinned to its actual content hash. The tasks cover
sample centrifugation and transfers, session control, alert acknowledgement,
recovery after a rejected operation, stopping a cycle, cabinet controls and
preserving a human approval gate. The package also contains private grading and
held-out inputs; public API, CLI, MCP and library-file routes do not return them.
The shared suite list retains the existing embodied suite projection.

## Import existing runtime records

Existing Claude Code executes the Agent and connects to the platform through
skills, CLI or MCP. The platform imports an explicitly selected native JSONL
file; it does not launch a model, scan runtime directories or resolve credentials.
Open **Runtime records** at `/runtime-sessions`, or use the installed CLI:

```powershell
lw runtime import capture.jsonl --format claude-code-stream-json --json
lw runtime ls --query claude --json
lw runtime show <runtime-session-id> --json
lw cost --since 7d --json
```

The supported formats are `claude-code-stream-json` (native `--output-format
stream-json` records) and `claude-code-session-jsonl` (native session JSONL).
Select the format that produced the file; JSON syntax alone does not identify
the format. pi is not yet supported. A growing file can be imported again:
native event identities and cumulative receipts prevent duplicate messages or
cost. A changed event or a different capture scope for the same native session
returns a conflict; keep the original capture rather than combining unrelated
outputs. Native stream captures support one final result; multi-result scopes
return an unsupported-format error.
After a result is retained, new conversation events or changed message usage
require a full ordered snapshot containing every retained event and the original
result identity, with its reported counters preserved or increased. Backfilled
conversation stays before that result. Partial conversation increments return
a conflict; known-event retries and metadata-only additions remain supported.

`lw runtime ingest records.json --format claude-code-stream-json --json` accepts
a JSON array of the same native records. Generic MCP `runtime_import` accepts
the array directly; `runtime_list` and `runtime_show` read the same HTTP records.
The HTTP intake is `POST /api/v1/runtime-sessions/import`, with exactly one of
`content` (JSONL text) or `records` (object array), a `format`, and an optional
`sourceLabel`. The gateway accepts content up to 16 MiB, never a server path.
Mutations require operator permission.

Agent, Harness version, Plan revision and Goal are optional explicit associations:
CLI flags are `--agent`, `--harness-version`, `--plan`, `--plan-revision` and
`--goal`. A Plan needs its revision; a Harness version needs its Agent. The
referenced resources must exist, but importing does not approve or execute them.
Absent links stay absent, including a Goal not explicitly supplied with a Plan.
Reimport without association flags preserves existing links;
`--clear-associations` removes them.

Runtime detail shows safe imported messages/tool records, native timestamps and
models, plus transcript and usage evidence URLs. These artifacts are generated
from the owned normalized store at `SPATIAL_STATE_ROOT/runtime-sessions.json`;
they do not expose the runtime workspace or original source file. Recognized
credential fields and literals are redacted before persistence.

Usage keeps runtime-supplied input/output/cache counters and USD cost. Explicit
zero stays zero; missing requests, total tokens, thinking level or execution time
stay unknown. Turns are separate from requests. A capture total replaces its
contained message receipt projection; it is never added to it. Runtime list-price
estimates are labelled `runtime-estimate`, other supplied costs
`runtime-reported`. No price table or model registration is needed. Cost charts
bucket these records by first server import time (`timeBasis: importedAt`), not
an invented execution timestamp, and attribute only explicit associations.

Install the [product skills](../skills/README.md) to use `lab-runtime-records` in
an existing runtime. See the [first-delivery specification](spec/LAB_AGENT_RUNTIME_RECORDS_SPEC_V1.md)
for the record and accounting boundary.

## External Agent evaluations

An external runtime executes each Target. The controller interface freezes an
Agent/Harness version, Suite/Scene identities, exact locked skill bytes, declared
runtime/model/thinking context and a finite platform deadline. It shares the
existing evaluator queue and single active laboratory Session. The first source
is Synthetic; admission needs no model process, credential or provider price.

```powershell
lw agent-run create lab-operator --suite suite/lab-ops-basic@1 --harness-version 1 --runtime claude-code --runtime-version 2.1.306 --model claude-opus-5-5 --thinking high --purpose verification --case pause-reset --wall-seconds 120 --request-id my-verification --json
lw agent-run next <run-id> --json
lw agent-run complete <run-id> --case-run <case-run-id> --native-session <native-session-id> --runtime-record <runtime-record-id> --json
lw agent-run show <run-id> --json
lw agent-run cancel <run-id> --json
```

Creation starts only platform CPU work. The example model/runtime strings are
declarations, not a model invocation or proof of execution. Runtime model work
remains separately authorized. The controller polls `next` for public work and
supplies its exact public Harness/skill contents to a fresh Target conversation.
It sets that Target's `LW_TOKEN` and `LW_SESSION` from the case connection. The
runtime owns context, authentication, tools and native metering; the supported
Target configuration exposes the scoped platform MCP. Additional Shell/raw HTTP
capabilities remain the runtime's configuration responsibility. Private grading
and held-out material never enter the work package.

Only controller `next` returns case connection credentials. Public reads and
artifacts omit them, and known case-token literals in imported messages are
redacted. Use the existing remote/operator-token gateway mode for authentication
separation from the global operator, keeping controller credentials outside
Target. Missing or forged case credentials there grant no operator control.
Anonymous local development retains its ordinary operator behavior. These
interfaces do not attest or sandbox a runtime's other files or networks.

Completion is a controller signal with a fresh native conversation identity and
optional explicit imported A record. Native identity/context links are validated
and labelled with their provenance. Duplicate completion returns its saved case
outcome; independent repetitions cannot reuse a native conversation identity.

The internal evaluator uses weighted final-state conditions and trusted process
evidence: tool steps, monotonic elapsed time, rejected high-risk actions and
whether a relevant operation Plan preceded the first action or command. Plan
credit requires matching session, scene revision, epoch, Plan, step, device,
action and parameters. Each task can choose its applicable dimensions; session
control and inspection tasks do not require a device operation Plan. Target
cannot submit counters, elapsed time, authoritative state or grades. Evaluation
policy denies high-risk actions and human-gated operation Plans, including Plan
grant bypass attempts. Pause/reset remain scoped to the owned Session. Grading
is sealed before cleanup and retains genuine running invocation observations
when a later stop cancels the action.

Required physical grading observations must be present, fresh, typed and in the
same Session/scene/epoch scope. Unavailable evidence is an invalid attempt with
retained Trace/native links; a fresh known-false result stays a valid negative.
Derived absence of an action or alert is not a Source failure.

`verification` runs one public case once and publishes no complete evaluation.
`train` requires every public case at its fixed repeats (ten × three in the
bundled Suite). Only a complete valid scope appends a `reported` 0–100 mean with
`ci95: null`; it changes no Reference, acceptance, promotion or R3 Notebook
decision. The Web Agent suite shows frozen conditions, case evidence, recordings
and native cost snapshots. Held-out remains locked.

Cancellation/expiry revokes grants and closes only the platform's owned Source;
it never kills an external runtime. Unconfirmed cleanup blocks the shared slot.
An operator retries `POST /api/v1/evaluations/{id}/cleanup`; public cleanup facts
update after confirmation. Gateway restart invalidates unfinished in-process
Synthetic work and old grants never revive effects.

Native metering stays in A. A case snapshots an explicit record ID/content hash
at completion. Missing counters remain unknown; later A updates cannot regrade
or change that snapshot. Capture totals are not added to contained message
totals or duplicated across repetitions. Imported messages alone still create
no scores, approvals or Notebook outcomes.

Install [lab-agent-evaluate](../skills/lab-agent-evaluate/SKILL.md) for the shared
CLI/MCP workflow. See the [external evaluation specification](spec/LAB_EXTERNAL_AGENT_EVALUATION_SPEC_V1.md)
for the fixed contract and delivery checks.
