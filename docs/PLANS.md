# Operation plans

An operation plan records intended work and tracks actual device actions,
waits and checks. The gateway does not execute the plan. Start a Synthetic
Laboratory session, inspect its Catalog, then submit a YAML document:

```yaml
title: Close the centrifuge lid and verify
target: session
steps:
  - id: 1
    do: {entity: /World/Lab/Centrifuge, action: closeLid}
    expect: {entity: /World/Lab/Centrifuge, property: lidOpen, equals: false}
  - id: 2
    wait: {entity: /World/Lab/Centrifuge, property: lidOpen, equals: false}
    timeoutSeconds: 10
  - id: 3
    check: {entity: /World/Lab/Centrifuge, property: lidOpen, equals: false}
```

```text
lw plan create operation.yaml --json
lw do /World/Lab/Centrifuge closeLid --plan <id> --step 1 --wait --json
lw wait /World/Lab/Centrifuge lidOpen equals false --plan <id> --step 2 --json
lw check /World/Lab/Centrifuge lidOpen equals false --plan <id> --step 3 --json
lw plan show <id> --json
lw plan ls --status completed --json
```

For an approved operation/session plan, `lw plan run <id> --revision <n> --json`
executes its pending steps locally with one HTTP client. It checks the selected
Session, scene revision and epoch, pins the Plan revision, skips completed steps
and stops at the first failed or uncertain result. It reads back every step,
including `expect`; the response contains the Plan and actual per-step results.
`--timeout` bounds each action wait (default120seconds); wait steps use their
declared timeout. It never retries failed/running steps or makes approval decisions.
A transport failure leaves effects uncertain; inspect the existing invocation
and Plan before further work. The gateway still executes only individual actions.
Use one controller per Plan while running: the existing per-step API does not
atomically reserve ordinary actions across concurrent clients. The runner uses
each refreshed Plan to skip newly completed steps and stop on active/failed steps.
The run's `failed` status describes this local attempt. It preserves the server's
Plan lifecycle, which can remain `running` with a failed step for later review.

The three-step example above demonstrates the available mechanisms. For a single
immediate outcome, use `do` with `expect`; an additional identical wait/check is
unnecessary. Use `wait` for independently changing conditions and `check` for
additional assertions. A connected MCP client can drive the individual steps
with a persistent connection and batch final properties with `get_state(keys)`.

Entity/action/parameter/condition validation happens when submitting. It does
not predict whether future device preconditions will hold. A successful action
only completes its step when its optional `expect` also holds; inspect
`stepResults` for the actual invocation and failed conditions. Checks return
exit2 when false; waits return exit4 when timed out.

Ordinary simulation plans are approved automatically. A plan with high-risk
actions enters `submitted`, exposes an `approvalId`, and requires the existing
human approval workflow. Approval is bound to the exact revision, session,
scene revision, epoch, step and normalized action parameters. A high-risk step
grant is consumed once; retrying it requires an ordinary individual approval.
An approved plan can be revoked until its first associated step enters intake.
After intake, plan revocation returns409; it does not claim to cancel queued
device work. Ordinary invocation cancellation remains available separately.
Changing parameters or performing an action outside a plan also uses individual
approval for high-risk actions. Unplanned ordinary actions and skipped ordering
remain executable and are marked by reliable `plan.invocation` entity events.
With no plan in the session, legacy action approval policies are unchanged.

`lw plan revise <id> operation.yaml` produces the next immutable revision.
Completed steps keep their state when their IDs and normalized content are
unchanged; changing or removing one is rejected. IDs are unique positive
integers. Finish active steps before revising. A new revision reevaluates its
approval requirement. Historical content and results remain available with
`lw plan show <id> --revision <n>`. Server-owned step/lifecycle states should be
omitted from submitted YAML. Reset, reload, stop, source failure or restart
invalidates remaining authorization; historical plans stay readable.
Repeating an associated completed check or wait returns409 and preserves its
original result. A condition's diagnostic `message` stays with the plan and its
failure result; clients provide the executable condition without repeating that text.

Deviation conditions use `deviatesFrom` with a numeric reference property and
finite nonnegative `by`. Check/wait adapters accept `--by` in CLI and optional
`by` in HTTP/MCP. Thresholds participate in exact step matching; other operators
reject them. A deviation wait observes changes to both properties.

```text
lw check /World/Lab/Incubator temperatureC deviatesFrom setpointC --by 2 --json
```

Internal plan history uses an owned bounded FIFO writer. Device intake consumes
the one-use grant in gateway memory, then waits for that record's write/flush/fsync
acknowledgement before reaching the Source. Plan resource changes and associated
check/wait responses also wait for persistence. Reliable terminal events keep the
actual observed result while the worker saves it. HTTP plan details report
`persistence.status` as saved/pending/failed; a storage failure refuses further
plan grants and plan-save success responses without reporting a device-host failure.
Shutdown stops event producers, drains accepted records and joins the writer.

HTTP uses `POST /api/v1/plans`, `POST /api/v1/plans/{id}/revisions`, and
`GET /api/v1/plans[?kind=operation&status=...]` / `/api/v1/plans/{id}`.
Creation/revision accept the document and optional `sessionId`; absence selects
the one active session. Only operation/session targets are available. A live
target returns `target_unavailable` until a live host is connected.

Read a supported arm step with `GET /api/v1/plans/{id}/steps/{n}/preview`.
It returns at most64 world Z-up TCP points, current and target joint positions,
the target TCP pose and target body world poses keyed by Manifest entityKey.
The latter let viewers reuse actual robot visuals, including Synthetic's body
keys outside the robot's lexical subtree. The response identifies the plan
revision, session, scene revision, epoch and sampled simulation time. Optional
`?revision=n` pins the current revision; stale revisions or reset/reloaded
targets return409. Unsupported programs and wait/check steps return
`available: false` with a reason. Invalid taught targets return422.

Preview is a read: it is available before approval and does not execute an
action, change step results or consume an approval. Both taught arm programs
sample the same per-axis trapezoidal/triangular joint planner as their actual
motion. Virtual uses velocity limits1rad/s per axis, scaled by its existing
`speedScale` and action `speed`, and retains its minimum0.2s segment hold.
This replaces Virtual's previous linear joint interpolation. Cache entries
include plan revision/step, source/scene/epoch and current physical context.
The geometry predicts taught posture, not collisions or grasp success.
Compound pick/place paths finish at their taught retreat/approach pose; use
`moveTo` with `pose: grasp` to inspect the grasp endpoint itself. Policy
previews remain unavailable because no policy runtime is connected.

`GET /api/v1/sessions/{id}/wait` and `POST /api/v1/sessions/{id}/check` share the
condition syntax of `lw wait/check`. The check body is `{key, property, op,
value}`. Action, wait and check HTTP requests accept optional `planId`,
`stepId` and `planRevision`; provide planId/stepId together. A supplied stale
revision or target is rejected. Reusing a requestId with a different plan
context returns `idempotency_conflict` and never reparents the original call.

The action/host/Invocation protocol remains unchanged: HTTP correlation stays
inside the gateway, and strict WebSocket actions continue to reject plan fields.
Plans use the declared protocol2.1 full/step `plan.update` messages. Associations
and deviation markers use existing `EntityEvent.data`; they are also exposed in
HTTP plan details. Reliable plan updates are recorded in LWLG1 segments of
recording format2, including plan snapshots at segment rollover. Existing
readers ignore the new message types and continue spatial/property playback.

MCP provides structured `plan_create`, `plan_revise`, `plan_show`, `plan_list`
and `check_condition`; `invoke_action` and `wait_until` accept optional plan
references. CLI reads local YAML files; MCP submits document objects. Neither
client can approve its own requests. Plan updates use the session's reliable
event stream and the unified inbox without a separate plan-only polling API.
