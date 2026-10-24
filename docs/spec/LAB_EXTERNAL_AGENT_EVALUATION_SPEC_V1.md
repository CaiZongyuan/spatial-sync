# #44 B — external runtime evaluation handoff v1

The user explicitly selected continuing #44 B after A was delivered in PR154.
Existing runtimes execute the Agent; the platform hands out public work, owns
experiment Sessions/evidence/grading, and persists results. A records/usage stays
the data source. No hosted SDK, model proxy, credential injector, provider-price
registry, runtime process manager or pi-format expansion is part of B.
Real model execution still needs its later concrete bounded authorization.

## Admission and fixed scope

- One concrete Agent branch in the existing evaluator shares its admission queue,
  single active Session slot, recordings, persistence and cleanup. Do not build a
  second scheduler or represent Agent cases as Tasks/PPO/physical episodes.
- The operator/controller requests an Agent/Harness version, immutable Agent Suite
  and declared runtime/model/thinking context. Freeze the exact Harness archive,
  locked skill contents, Suite/Scene identities, all public train cases/repeats,
  runtime declaration and finite platform wall deadline before any Source starts.
  No model defaults, later editable Harness or dispatch overrides replace these.
- Normal scope is the complete public train Suite, each case at its fixed repeats
  (at least3). Held-out test remains unavailable outside promotion. An explicit
  one-case verification purpose may run one repetition but cannot publish a
  complete evaluation/baseline or change Reference.
- Basic CPU evaluation follows existing evaluator admission. It does not invent
  a training hypothesis, Task or approved Plan. R3 optimization/training approval
  remains its existing separate workflow. Optional links, if accepted, must be
  validated and cannot authorize an unapproved training Plan.
- Exact request retries return the same run/pins; changed content conflicts.
  Durable intent precedes Session effects. No model/provider call is made here.

## External work protocol

Use a small shared HTTP/CLI/MCP controller interface for create/read, next-work,
complete and cancel; exact names are fixed by the first vertical slice. The
`/agents/{id}/runs` command/route may delegate to the same evaluator admission.

1. The external controller requests work. Pending work has one durable case-run
   identity/repetition and an owned Synthetic Session after the shared slot is
   admitted. Repeat requests return that same active work rather than advancing.
2. Work contains only the public statement, frozen public Harness/skills and
   platform connection information. Never hand out private rubric/test/Vault or
   external filesystem paths. Historical skill pins resolve exact bytes or fail
   explicitly; do not rewrite locks or substitute current contents.
3. The runtime creates its own fresh Target conversation and executes. It owns
   context, model configuration, tools, authentication and native metering. The
   platform records the declared native session identity and rejects reuse across
   independent case repetitions. This is not OS-isolation attestation.
4. Target receives only its case-scoped platform MCP/HTTP grant. Server binds the
   actor/run/case/session independently of supplied headers and enforces it before
   local operator fallback. Allowed capabilities are own-Session public reads,
   actions/check/wait/invocations, operation Plans, alerts and required controls
   including pause/reset. No approval, parent-run control, other Session, private
   data, model/Vault management or arbitrary file capability is granted.
5. The controller reports completion and optional imported `runtimeRecordId`,
   with native session/runtime metadata. Completion is a signal, not a score or
   authoritative final state. Target's scoped tool list excludes controller tools.
   Duplicate completion returns the same finalized outcome; stale/wrong-case or
   revoked grants cannot advance work or regrade it.

Secrets for a scoped connection must not enter public Trace/artifacts. Native
records containing platform grant literals are redacted by the record boundary.
The platform constrains its APIs; external Shell/files/network configuration
belongs to the runtime. Do not claim a machine sandbox or launch hook programs.

## Trusted case outcome and cancellation

- Capture admitted platform HTTP calls and authoritative Source observations,
  before/after Scope, approval-policy rejections and final HTTP observation time.
  Grading uses this platform evidence and the existing pinned private rubric.
  Native messages or declared scores/final states cannot replace it.
- Evaluation high-risk/human gates are denied inside the existing action pipeline,
  including an attempted operation-Plan grant bypass. Record the rejection and
  feed the unchanged grading metrics. Add no protocol approval enum.
- Seal scoring at the final observation; cleanup events cannot change the result.
  Retain real running invocation witnesses when stop later cancels an action.
- Explicit completed Target failure can remain a valid scored negative. Missing
  handoff/completion, Source failure, unavailable evidence or scope mismatch is
  infrastructure-invalid/incomplete. No silent retry replaces a valid negative.
  Incomplete required repetitions publish no complete scoreboard/baseline.
- Cancel/expiry revokes platform grants and closes only the platform's owned
  Source/Session. It signals external cancellation but never kills the runtime.
  Unconfirmed Source cleanup keeps the slot blocked until the existing cleanup
  path confirms it. Restart fences outstanding work; old completion cannot revive
  effects. Attempts, native record links and usage are retained.

## Results and cost

- Only a complete valid train scope appends its reported 0–100 Agent result and
  recording/Trace references. `ci95` is null. Keep reported separate from accepted,
  Reference, promotion and R3 Notebook decisions; #132D policy is unchanged.
- Compare the fixed Suite/model/thinking conditions and show their provenance.
  Runtime declarations remain labelled declarations; observed native mismatches
  cannot be silently accepted as the requested execution context.
- Link A runtime records by explicit identity/content hash; do not copy a whole
  session total into every case or add native totals to contained message usage.
  Missing metrics remain unknown. Snapshot costs in results have an explicit
  observation basis; later A record updates cannot change scores or reopen work.
- Web shows queued/waiting/running/final/invalid/cleanup facts, per-case trusted
  evidence and runtime-reported usage. It provides no hosted model/context UI.

## Verification and delivery

### Concrete first interface

- `POST /api/v1/agents/{name}/runs`: `{requestId,suite,harnessVersion,runtime:{name,version?,model,thinking?},purpose?,caseId?,wallSeconds?}`. `purpose` is `train` (default) or `verification`; only verification selects one public `caseId`. Runtime version/thinking can stay unknown. Wall seconds are finite, 1–7200 (default900).
- `GET /api/v1/agent-runs[?agent=agent/name]` and `GET /api/v1/agent-runs/{id}` expose public Run/cases/pins, declared and observed provenance, cleanup facts, native links and saved result. The ID is also the shared evaluator's `evaluationId`.
- `POST /api/v1/agent-runs/{id}/next`: controller receives `pending`, `work` or `finished`. Work has `caseRunId`, `caseId`, one-based `repeat`, `statement`, `sessionId`, expiry, frozen public Harness/skill files and `connection:{sessionId,token,tokenType:"Bearer"}`.
- `POST /api/v1/agent-runs/{id}/complete`: `{caseRunId,nativeSessionId,runtimeRecordId?,runtime?}` signals completion and returns `{case,run}`. No score/final/usage field is accepted. Runtime context, if supplied, must match the fixed declaration.
- `POST /api/v1/agent-runs/{id}/cancel` uses existing platform ownership cleanup. Operator retry after a blocked close is the existing `POST /api/v1/evaluations/{id}/cleanup`.
- Public case artifacts use fixed `trace/final/score` identifiers under `/api/v1/agent-runs/{id}/cases/{caseRunId}/artifacts/{name}`; incomplete cases expose only retained evidence. Physical evidence availability is classified at B completion; the pure grader's known-false/unknown scoring function is unchanged.
- Controller CLI is `lw agent-run create/ls/show/next/complete/cancel`; the generic MCP names are `agent_run_create/list/show/next/complete/cancel`. Target's tool list excludes all controller, approval, Vault, policy and arbitrary file interfaces.

The first implementation uses in-process Synthetic Sources. Scope proof in
final HTTP reads uses the information store's `X-Spatial-Scene-Revision` and
`X-Spatial-Epoch` response headers; its JSON and recording format remain readable
unchanged. Required physical values and metadata must be available/fresh/typed;
missing actions and absent derived process/alert events remain ability negatives.

For authentication separation from the global operator, the supported external
Target deployment uses existing remote/operator-token mode. The controller
keeps its credential and gives Target only the case grant/scoped MCP. Additional
Shell/raw HTTP access is the runtime's configuration responsibility. Anonymous
local development keeps its existing operator behavior; this is not a claim of
OS isolation or unrestricted-network coverage.

### Acceptance checks

First one-case scripted external controller through real HTTP/MCP, then all
10 public cases×3 actual Synthetic Sessions using the same public handoff.
Cover exact retries, wrong/expired grant, no self-approval, high-risk denial→grade,
pause/reset, cancel/restart/cleanup and no duplicate native costs. Scripted data
must be labelled, not presented as paid/runtime policy-quality evidence.
Reuse A/embodied/recording compatibility checks. Run affected installed CLI/MCP
and built browser journeys, bounded simplification, full non-author Standards
and Spec, mandatory/exact-head CI and integrated-tree verification.
Prepare real external-runtime one-case verification only after the complete
implementation is reviewable; request concrete model/budget authorization then.
