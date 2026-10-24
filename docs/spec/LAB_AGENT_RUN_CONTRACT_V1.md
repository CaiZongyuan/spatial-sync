# Managed AgentRun contract v1

This is the #44 contract for the user-selected restricted profile A. Implementation is in progress.
It extends Lab RSI §4/§8/§11/§13 and the existing Job lifecycle without changing spatial or recording protocols.
Real paid-model verification requires a separately approved one-case execution plan.

## Frozen intent and human admission

An Agent evaluation uses `kind: training, layer: agent`; existing Plans with no layer are `embodied`.
The branches are disjoint. Agent inputs are not represented by a dummy Task, PPO algorithm, GPU profile or physical episode.
Layer and goalId are fixed for a Plan ID; its revisions share one resource pool.

The human-reviewed Agent Plan fixes Agent/Harness version and archive hash, exact skill contents,
Suite/Scene identities, public train scope and repeats, registered model/thinking/credentialRef,
execution profile/runtime identity, limits and Goal context. Secret values are never Plan fields.
Mutable current Agent files, SDK defaults or later catalog changes cannot replace those inputs at dispatch.

Agent Plans explicitly declare both `budget` (this Plan's caseRuns, requests, tokens, llmUsd,
wallSeconds and maxOutputTokensPerRequest) and `goalBudget: {caseRuns, llmUsd, days}`.
Goal values are finite human declarations; caseRuns is a positive integer, llmUsd is nonnegative and days is positive.
The Goal source snapshot is `{kind: "agent-plan", immutablePlanInputHash, humanDeclaredLimit: goalBudget}`.
The first human approval fixes that source and the Goal's clock anchor. A later declaration cannot replace it.
Execution wallSeconds starts with the owned execution and includes setup/tools; Goal.days starts at the first
human approval. Never derive days from execution wallSeconds or reset either clock between cases/restarts.
An existing Goal keeps its original authority. A missing Agent/LLM axis remains unknown, including on the human
approval card; approving this Plan authorizes only its finite known envelope and does not manufacture a global
Goal limit or free balance. Requests/tokens/execution seconds are per-Plan axes, not additional Goal dimensions.
Agent case runs and physical episodes remain distinct; Synthetic CPU execution has actual GPU use0.
The same finite-Plan exception applies to Goal.days only when that exact active human approval reviewed a fresh
card already showing the original Goal deadline as expired. The Run retains a server proof linking that approval,
revision and review basis, and executes within its remaining shared Plan seconds. The original Goal remains expired;
an earlier approval does not revive when time passes, and a stale pending card must be resubmitted.

The CLI prototype is extended with its approved parent Plan:

```text
lw agent run <id> --suite <suite@revision> [--version N] --plan <planId> --plan-revision N
```

`POST /agents/{id}/runs` accepts `{suite,version?,split:"train",planId,planRevision,requestId?}`.
Omitted version resolves only from the approved Plan. Provided values must match it; model, credential,
profile and limit overrides are rejected. A missing/unapproved/mismatched Plan cannot launch an adapter.
`split:test` remains unavailable outside promotion. The shared CLI/MCP Command table uses this same admission.

Admission durably creates one Run and its reservation before any process/provider request starts.
Exact requestId retries return that Run and its frozen resolution; changed content conflicts.
Persistence failure grants no uncommitted work. Approval of evaluation/resource intent does not grant Target any human authority.

## Restricted profile `lw.agent-restricted/1`

Harness archives remain importable/exportable independently of executable support. The runtime validates
the complete fixed archive before startup. Unsupported capability is an explicit preflight failure,
never a successful run that silently omits legal authoring content.

- AGENTS.md and locked platform skill instructions are loaded from exact selected bytes.
- `hooks/README.md` is retained as a non-executable document. Candidate executable hook files are unsupported.
- `mcp.yaml` supports the declared platform `lab` transport (`lw mcp`, owned `LW_URL` reference),
  or no declared transport. Custom executables/stdio servers, arguments and environment bindings are unsupported.
  The runtime constructs the trusted platform transport; it does not execute candidate PATH commands.
- SDK built-in Shell, file, web, Agent/subagent and other general execution tools are unavailable.
  User/project/local settings, hooks, plugins, extra MCP discovery and global skill discovery are not loaded.
  Fixed metadata about skills/plugins built into the pinned CLI may still be listed; that is not an executable
  tool grant or user plugin discovery. Actual available tools and MCP servers must match the frozen profile.
  Platform-owned enforcement hooks/transport code are part of the frozen runtime, not candidate hooks.
- Target sees its active public statement, its frozen Harness and skills, plus explicitly granted platform data.
  Grading/test/Vault/repository/user-configuration files are not staged or exposed through tools/artifacts.
- Granted platform tools operate only on the owned case Session: Catalog/state/check/wait,
  invocation/action, operation Plans, scoped alerts and permitted Session controls including pause/reset.
  They do not include human approval, arbitrary filesystem import/export, model/price/Vault management,
  other Sessions or starting another run. Device actions still use the single HTTP action pipeline.
- Network authority belongs to the trusted fixed tool/model transport. Target has no raw URL/header/HTTP/Shell interface.
  A self-reported, forged or omitted actor cannot change the server-bound run/case/session principal.

The shipped lab-operator@1 is supported, including its non-executable README and recoverable historical skill pins.
Other legal Harness versions remain storable but fail execution preflight when they request unsupported capabilities.
This is not arbitrary executable-hook/MCP compatibility or protection against the same-machine administrator;
it does not add the unselected heavy-isolation profile B.

## Locked skill resolution

Skill pins identify canonical UTF-8/LF content by SHA256. Resolve only verified current content or an immutable
archived blob matching the requested name/hash. Missing content fails explicitly, with no current-version fallback.
Selected Harness archives and old locks are never rewritten to make a run succeed.

The shipped v1 lab skill pin `5fea203445c6aeee93b5cdfe8cb00f49a3488cdb03f3c4b03f2c2c4ac3ba540d`
is retained from its exact repository bytes; current lab-operate already matches its original pin.
Each Run freezes resolved hashes and the effective profile in its own input manifest.

## Trusted evidence and budget semantics

Only trusted code publishes tool requests/responses, approval-policy denials, before/after observations,
final HTTP state and normalized usage. Grading uses those Scope(session/revision/epoch) facts and the pinned rubric,
not adapter-supplied scores or claimed final state. High-risk gates are automatically denied by evaluator policy
and recorded; this is scoring/safety policy, not human approval or a right to self-approve.

Authorization, actual usage and outstanding commitments are separate. Track case attempts, provider requests,
tokens/cache classes, USD estimate and execution wall time with explicit availability/enforcement coverage.
Actual0 remains0; missing usage/price remains unknown and is not free capacity. A turn count is not a provider request count.
Do not sum streaming usage and final cumulative totals twice. Preserve scoped request IDs, prices and their revisions.

The executor admits a next provider request only within the approved envelope using a supported conservative bound.
SDK stop-after-observation options are not presented as hard bill ceilings. If a necessary bound is unknown,
strict paid admission fails before a provider call. Known partial amount and unknown/unpriced usage remain visible.
Failure, invalid execution, cancellation or restart never erase actual or uncertain consumption.
Unused commitments release only from known cancellation/cleanup facts; a missing receipt is not zero spend.

Agent Goal context has an explicit source and mandatory goalId. It extends the existing Goal projection and
per-PlanID pool after #37; it does not fabricate a Task or silently replace an existing Goal anchor/unknown LLM axis.
New authority/over-budget exceptions remain explicit human Plan decisions. Model credentialRef names are frozen;
actual secret resolution is confined to a task-owned adapter process environment and excluded from context/logs/artifacts.
The API may act as the trusted injector while constructing that child environment; it does not retain the running
provider credential in its budget gate. A task-owned credential relay receives the real key only through its clean
environment. The SDK receives a separate local gate grant, while the API gate uses another relay grant. Neither
grant nor the actual credential enters argv/config/Trace. The API retains the single durable reserve/settle ledger;
the relay owns no second budget or usage ledger and forwards only the fixed model-message method/destination.
It checks the original deadline and live owner/cancellation marker before the external effect, and redacts any
credential reflected by the provider before returning data. A signed relay control proof can establish that an
attempt was not forwarded; provider response text/headers cannot establish that fact. Timeout/interrupted streams
retain complete observed usage prefixes plus unspent uncertain reservations. Worker and relay trees use their
creating OwnedProcess handles, and cleanup failure retains ownership/accounting obligations.
The relay may retain private append-only transport observations containing only the admitted attempt ID,
validated nonnegative token counters and a redacted provider message ID. These are raw evidence with no prices,
budgets, balances or authority. After reap, the API merges their high-water counters into that same attempt's
existing record. Repeated/late observations cannot create another charge, clear uncertain suffixes or reopen work.
The observation files are excluded from Target files, SDK context and published artifact routes.

Final state HTTP responses carry their server observation instant. Case grading freezes that instant and its
Scope, retaining later cleanup events in raw Trace while excluding them from the scoring projection. Actual
running invocation observations remain usable when a later stop/cancel ends that invocation; they must match the
same invocation/action/params/scene/epoch. Completion observations do not create extra target tool steps.

Normal evaluation completes each pinned public train case at the declared repetitions (at least3) in owned sequential
Sessions using the existing single-Session admission. A valid negative and an infrastructure-invalid attempt remain
distinct; incomplete repetitions cannot publish a complete baseline. A one-case verification purpose is not a formal
evaluation/baseline and needs its own concrete later authorization. #132D acceptance/non-inferiority/sample policy,
Agent Notebook optimization and promotion are outside this contract.
