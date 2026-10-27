---
name: lab-operate
description: "Operate an opened laboratory scene through lw or MCP: inspect its Catalog, execute an approved operation Plan, verify outcomes, and recover from refusals."
---

# Operate the laboratory

Use this workflow for device work in an existing scene. The Catalog and live
state are authoritative; the browser is for viewing and necessary visual checks.
Read development specifications when implementing a change or resolving a
capability gap discovered through these interfaces.

## Observe and pin the target

Read the inbox at the start and after a long wait. Retain its opaque cursor for
the next read, including across gateway restarts. Report critical alerts before
continuing device work. Inbox covers the gateway; LW_SESSION selects devices.

```text
lw inbox --json
lw status --json
lw ls --json
lw show <entity-key> --json
```

Set LW_URL to the supplied gateway and LW_SESSION to the returned session ID.
Inspect the relevant entities once. Continue when their exact identities, source,
actions, parameters, units, risk, conditions and current state are known.
`show` includes current state; use `get` when a later decision needs a fresh value.
Action location names and returned Site Prim Paths may differ: copy the actual
property value into conditions.

Preserve existing sessions. Only when a new session is required, discover a
launchable version and a declared source before launching:

```text
lw scene ls --launchable --json
lw scene show <scene-version> --json
lw scene launch <scene-version> --source <supported-source> --json
```

## Plan and execute

Submit a Plan before device work. Each positive unique step ID has one `do`,
`wait` or `check`; a `do` can include `expect`. Use inspected capabilities:

```yaml
title: Complete the requested device operation
target: session
steps:
  - id: 1
    do: {entity: <entity-key>, action: <action>, params: {<parameter>: <value>}}
    expect: {entity: <entity-key>, property: <property>, equals: <expected-value>}
```

Use `expect` for an action's immediate outcome. Add `wait` for an independently
changing condition, and `check` for an additional assertion. Repeating the same
condition with all three mechanisms is unnecessary. Preserve interlocks and
required outcomes when combining checks. Derive plans from the current Catalog,
rather than assuming a fixed device sequence.

```text
lw plan create <plan.yaml> --json
lw plan run <plan-id> --revision 1 --json
```

`plan run` is a local client loop: one process and HTTP client issue the existing
per-step actions, waits and checks. The gateway still handles each through its
ordinary pipeline. The runner pins the Session/revision/scene/epoch, preserves
completed steps, checks every step's actual result including `expect`, and stops
at the first failure or uncertain result. Inspect its `status`, `plan`, `results`
and any `stoppedStep`; completion requires both the run and Plan to be completed.
Use the actual returned revision; `1` above is an example. Its `--timeout` bounds
each action wait; explicit wait steps use timeoutSeconds.

For decisions between steps, use a connected persistent MCP client or execute
individual steps. The `do` command waits by default; MCP mirrors the same generic actions
and supports batched state reads with `get_state(keys)`.

```text
lw do <entity-key> <action> <parameter=value> --plan <plan-id> --step 1 --plan-revision 1 --json
lw plan show <plan-id> --json
```

Use the submitted step ID and revision in individual calls. An invocation's
success does not prove its Plan `expect` passed: inspect the
associated step before dependent work. Exit0, accepted, running and pending
approval are not completion. A timeout leaves the physical outcome unknown;
query that same invocation before considering a new call. The runner never
retries a failed or uncertain step automatically.

```text
lw inv <invocation-id> --wait --timeout 60 --json
```

## Verify and recover

Read back all requested final properties once, preferably with a batched MCP
state read or one shared GatewayClient. Finish with the actual values and Plan
status. Waiting is needed only while the relevant terminal condition is pending;
for a centrifuge, wait for action success and phase=idle because nominal rpm=0
can precede physical rotor alignment.

```text
lw get <entity-key> --json
lw inbox --since <cursor> --json
```

Read JSON error.code, error.details.failed, condition messages and current state.
Correct invalid parameters from the Catalog; satisfy failed preconditions through
permitted actions. On busy, inspect the existing invocation. On connection or
Source errors, retain uncertainty and inspect Session/invocation state. When the
intended work changes, revise the Plan and inspect its new approval state:

```text
lw plan revise <plan-id> <plan.yaml> --json
```

Reset changes epoch and invalidates authorization. Preserve completed step IDs
and content when revising. For history, use numeric aggregation only for numeric
properties and `last` for other properties. Stream observation, cancellation and
simulation control are available when the task calls for them; inspect their help
at that point.

## Human boundaries

Ordinary simulation Plans are approved automatically. For a submitted Plan or
pending invocation, report the approval identifier and reason; resume after the
platform reports the human decision. Hand `/Real/` device intervention to a
person. Agents must not execute approval decisions (including denial/revocation)
or write Vault values through terminal, HTTP, MCP, browser or direct files, even
when asked to bypass this boundary. Keep credentials out of messages and logs.
