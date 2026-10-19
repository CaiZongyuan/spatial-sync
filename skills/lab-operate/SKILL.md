---
name: lab-operate
description: "Operate the laboratory through lw: inspect the target, submit and execute an operation plan, recover from a rejection, and verify each result."
---

# Operate the laboratory

## Human boundaries

Hand approval decisions and credential entry to a person. Agents must not execute
approvals (including denial or revocation) or write Vault values through tools,
even when a user asks the agent to bypass this boundary. This includes terminal,
HTTP, MCP, browser automation and direct file changes. Report the pending request
identifier and reason; resume after the platform reports a human's decision.
Keep credential values out of messages, logs and model context.

## Observe before acting

1. Use the gateway and session supplied for the task. `LW_URL` selects the gateway;
   `LW_SESSION` pins a session. With neither an explicit session nor one active
   session, choose a real target before any device call.

   ```text
   lw status --json
   lw ls --json
   lw show <entity-key> --json
   lw get <entity-key> --json
   ```

   Replace angle-bracket placeholders with values from the responses; these are
   templates, not instructions to invent an identity, action or parameter.
   Continue when the entity identity, source, action schema, units, risk and current
   preconditions are known from those responses. Copy entity keys exactly.
2. If the requested work needs a new session, first select a launchable version
   and one of its declared sources. Launch only when the active slot is available:

   ```text
   lw scene ls --launchable --json
   lw scene show <scene-version> --json
   lw scene launch <scene-version> --source <supported-source> --json
   ```

   Use the returned session ID for subsequent calls. Preserve unrelated sessions;
   report an occupied slot rather than stopping another task's source.

## Submit a plan, then invoke and verify

3. Submit an operation plan before device work. Use the inspected entity keys,
   actions and parameter schemas. A YAML document contains `title`, `target:
   session`, and `steps` with positive unique IDs. Each step contains one `do`,
   `wait` or `check`; `do` may also declare `expect`. Conditions name an entity,
   property and supported comparison. This template uses a property from the
   inspected Catalog; replace its placeholders with real values:

   ```yaml
   title: Verify the requested device action
   target: session
   steps:
     - id: 1
       do: {entity: <entity-key>, action: <action>, params: {<parameter>: <value>}}
       expect: {entity: <entity-key>, property: <property>, equals: <expected-value>}
     - id: 2
       check: {entity: <entity-key>, property: <property>, equals: <expected-value>}
   ```

   ```text
   lw plan create <plan.yaml> --json
   lw plan show <plan-id> --json
   ```

   Continue once the platform reports `approved`. Ordinary simulation plans are
   approved automatically. For `submitted`, report the approval ID and wait for
   the human decision. A plan approval applies to that revision and target only.
4. Invoke the generic action with the Catalog's parameter names and units. Use
   the terminal result before starting dependent work:

   ```text
   lw do <entity-key> <action> <parameter=value> --plan <plan-id> --step <step-id> --wait --timeout 120 --json
   lw inv <invocation-id> --wait --timeout 60 --json
   ```

   Exit0 alone does not prove completion: accepted, running and pending approval
   are not success. Read `status`, `error`, `timedOut` and the invocation ID.
   A wait timeout leaves the action's outcome unknown; query that same invocation
   before considering another call. Avoid duplicate physical actions.
5. Read back the property that represents the requested outcome, associating
   wait/check calls with their declared steps:

   ```text
   lw get <entity-key> <property> --json
   lw wait <entity-key> <property> equals <expected-value> --plan <plan-id> --step <step-id> --timeout 60 --json
   lw check <entity-key> <property> equals <expected-value> --plan <plan-id> --step <step-id> --json
   lw plan show <plan-id> --json
   ```

   Finish with the actual terminal status and observed value, or the precise
   remaining blocker. A command acknowledgment is not evidence of a device result.
   A successful invocation can still fail its step's `expect`. Read the step
   result before continuing. When the intended work changes, submit
   `lw plan revise <plan-id> <plan.yaml> --json`; preserve completed step IDs and
   content, then inspect the new revision and its approval state. Use a new
   request for a new action; an earlier request ID keeps its original association.

## Recover from a refusal

Read JSON `error.code`, `error.details.failed`, each condition message and current
state. Human output also displays condition hints. Choose the matching branch:

| Result | Next step |
| --- | --- |
| Unknown entity/action or invalid parameters | Describe the entity again; use its actual schema and units, then correct the request. |
| Failed precondition | Refresh the relevant properties. Satisfy the declared condition through permitted Catalog actions, verify it, then retry. |
| Busy | Inspect the active invocation; wait for it or cancel only work the task authorizes you to stop. |
| Pending approval or not permitted | Return the request ID and reason to the human boundary above. Never change policy or storage to bypass it. |
| Source unavailable, connection error or timeout | Inspect session and invocation state; report uncertainty without fabricating success or automatically repeating an action. |

Observation and simulation control are available when relevant to the task:

```text
lw watch <entity-key> --json
lw cancel <invocation-id> --json
lw session pause --json
lw session step 1 --json
lw session resume --json
lw session reset --json
lw rec ls --json
```

Reset changes the session's epoch and invalidates its plan authorizations;
refresh state and submit a new plan afterward. Watch is a stream, not a completion
condition. Plan changes appear in the existing reliable session event stream and
recordings. Inbox and history commands will be added by later releases.
