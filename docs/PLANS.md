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

HTTP uses `POST /api/v1/plans`, `POST /api/v1/plans/{id}/revisions`, and
`GET /api/v1/plans[?kind=operation&status=...]` / `/api/v1/plans/{id}`.
Creation/revision accept the document and optional `sessionId`; absence selects
the one active session. Only operation/session targets are available. A live
target returns `target_unavailable` until a live host is connected.
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
client can approve its own requests. Plan updates already use the session's
reliable event stream; the future unified inbox can consume that stream without
a separate plan-only polling API.
