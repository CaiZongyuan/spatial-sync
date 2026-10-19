# Declarative alerts

Alerts run beside the gateway's information state. Device Programs publish
properties and carry out actions; YAML declares which conditions require
attention. The built-in incubator, biosafety cabinet and environment sensor
types provide the defaults from Live Operations §3.1.

For a Synthetic demonstration, start the gateway using the README, then run:

```powershell
lw scene launch lab-demo@2 --source synthetic --seed 43
lw do /World/Lab/BiosafetyCabinet setUv on=true
lw alerts --active
lw alerts ack <alert-id> --note "Checking the front window"
lw do /World/Lab/BiosafetyCabinet setUv on=false
lw alerts
```

The cabinet starts with its front window open, so UV raises the default
`unsafeUv` alert. Switching UV off clears it. Acknowledgement is available to
operators and agents; it grants no authority to perform other actions.

## Rules and conditions

EntityType definitions and scene bindings accept an `alerts` mapping:

```yaml
alerts:
  tempDeviation:
    when: {all: [{property: temperatureC, deviatesFrom: setpointC, by: 1.0}]}
    forSeconds: 60
    severity: critical
    message: 温度偏离设定值超过 1 °C
    hint: 检查门是否关闭
```

Bindings replace the complete type rule with the same ID and supplement it
with new IDs. A replacement supplies its own condition, severity and message.
`forSeconds` defaults to zero and must be finite and nonnegative. Severity is
`info`, `warning` or `critical`; `hint` is optional. Rule references and property
types are checked when the Catalog loads.

`when` accepts a condition atom or structured `all`. The existing comparisons
remain available. `deviatesFrom` compares two numeric properties of the target
entity: `abs(property - reference) > by`. Equality at the threshold is false;
`by` must be a finite nonnegative number. `stale: true` explicitly tests expiry.
An absent property is neither stale nor known fresh.

Duration uses ordered source information and simulation time. A pause accrues
no duration; Step advances it. A continuously true condition raises once when
its duration is reached. False or unknown interrupts an unraised candidate.
An active episode clears only on a known false condition, so missing or stale
ordinary operands cannot invent recovery. Repeated true samples and repeated
acknowledgements emit no duplicate transition.

## HTTP, CLI and MCP

`GET /api/v1/alerts` returns `{alerts: [...]}` across retained sessions. Optional
query parameters are `sessionId`, `state`, `severity`, `active` and `limit`
(1–1000, default100). `active=true` includes raised and acknowledged episodes
in a current context; `active=false` selects inactive episodes. Omitting it
includes both. Rows include `sessionId`, the latest EntityAlert fields,
`active`, `acknowledgement` and `closedContext`.

`POST /api/v1/alerts/{id}/ack` accepts `{note?: string}` (up to4000 characters).
The operator role is required; `X-Actor: user:name`, `cli:name` or `agent:name`
provides attribution. The first confirmation records actor, note, simulation
time and sample time in `acknowledgement`. Retrying returns that same result.
Unknown IDs return404 `unknown_alert`; cleared or ended contexts return409
`alert_unavailable`. Invalid parameters or actor headers return422.

`lw alerts [--active] [--state STATE] [--severity SEVERITY]` lists episodes;
`lw alerts ack <id> [--note TEXT]` confirms one. Explicit `LW_SESSION` limits
the list to that session. Otherwise the list spans retained sessions and works
without a running session. MCP exposes the same HTTP operations as
`alerts_list` and `alert_ack`; its actual peer name is the confirmation actor.

## Episodes and recordings

The lifecycle is `raised → acknowledged → cleared`, with direct
`raised → cleared` also valid. A recurrence gets a new episode ID. Reset,
reload, stop and source failure close the old context with an explicit reason;
the last alert state remains available and is no longer active. These context
changes never claim that the condition recovered. The live episode list is
retained with the gateway's sessions; recording files retain the audit after
the gateway process ends.

Each lifecycle transition uses the existing reliable `entity.alert` message.
Confirmation also writes an existing `entity.event` named
`alert.acknowledged` with `{alertId, actor, note}` in `data`. This gateway audit
does not require a device event declaration. The public EntityAlert shape and
protocol2.1 version are unchanged. All messages enter the session WebSocket,
ordered `/events` stream and recording; old ordinary readers continue their
spatial/property replay while skipping unfamiliar messages.
