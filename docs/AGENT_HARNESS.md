# Agents and Harness versions

An Agent keeps its model and thinking level outside its editable Harness.
`lab-operator` is bundled with both fields unconfigured (`null`). Agent SDK
execution and model configuration are separate capabilities; creating a snapshot
does not start a model request, a job or a simulation.

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

The Web evaluation center lists the Agent suite, its ten public train statements,
fixed environment and three-runs-per-case requirement. Its held-out row stays
locked. Until the Agent runner produces real results, the page shows no formal
baseline, score curve, Trace or cost.

The internal evaluator uses weighted final-state conditions and trusted process
evidence: tool steps, monotonic elapsed time, rejected high-risk actions and
whether a relevant operation Plan preceded the first action or command. Plan
credit requires matching session, scene revision, epoch, Plan, step, device,
action and parameters. Each task can choose its applicable dimensions; session
control and inspection tasks do not require a device operation Plan. The Target
cannot submit counters, elapsed time or grades through a public endpoint.
Submitting an Agent suite to the embodied evaluation endpoint returns
`agent_runner_unavailable` before creating or queuing a Session. Real Agent runs,
their Trace storage and scoreboard writes require the Agent runner.
