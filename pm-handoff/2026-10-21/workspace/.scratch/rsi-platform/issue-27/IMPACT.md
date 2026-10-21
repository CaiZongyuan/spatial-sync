# Issue 27 impact and design

Base/main: `5c29ca3bbc0495f868bc2bbe89ebb4cf53410f61`, tree `06770b9609d7aeb0c49f6111d2b41f9fd5d2b6a3`.
Owner worktree: primary `.worktrees/issue-27`, branch `feat/issue-27-device-host`.
Issue: https://github.com/CaiZongyuan/spatial-sync/issues/27. Native blocker 20 closed; root claimed 27.

## Existing boundaries and consumers

- `HostCore` owns Program callbacks, authoritative admission, observations/events/invocation ordering and stale metadata. Both existing Sources use this host. Programs must remain synchronous and nonblocking; actual MQTT I/O therefore lives in a separately owned driver. A per-entity mailbox exposed by `Ctx.driver` supplies already-received messages and stages commands, without physics or direct sockets in programs.
- `Catalog` compilation owns action/property validation, conditions, alert rules, configs and publication. `load_catalog` currently couples this compilation to scene bindings/Manifest resources. Extract only the common compilation for the second real consumer, live bindings; keep existing scene resolver and published Catalog shape unchanged.
- `ActionPipeline` owns role denial, idempotency, concurrency reservation, conditions, approvals, Host admission, cancellation and audited failures. It expects a target with session identity, revision, epoch, state, source, options, approvals, plans and `action_pipeline()`. A gateway-owned Live target implements that existing interface and reuses the same pipeline. It remains outside `SessionManager.sessions`, so single-active-simulation admission and spatial lifecycles never own Live.
- `install_info_routes` currently hardcodes session lookup/prefix. Reuse the installer for `/api/v1/live` through a target resolver; keep actual action serialization and controls identical. Approval routes must resolve Live identities as well as sessions. `/Real/` routes in the shared `GatewayClient` automatically benefit generic CLI and MCP tools; no MQTT SDK reaches those clients.
- Existing approval/plan integration is coupled to target identity. Live always sets `approval.high=require`, preserving the approved real-action contract from the first usable intake. Issue 28 owns daily recording, Live history/inbox attachment, Live UI and full live-plan enablement; unsupported live plans remain explicit until their integration. Do not create an alternate intake or approve MQTT work implicitly.
- `HostCore` stale metadata uses its monotonically advancing host time. Live uses elapsed monotonic wall time for ticks/deadlines (immune to system-clock jumps), not simulation time/pause. Wall-clock UTC is only external audit time. `StateStore` remains the immutable read authority.

## Driver and lifecycle

`SPATIAL_LIVE_BINDINGS` selects an opt-in YAML file. An example binds three `/Real/` devices to `*.mqtt` programs; optional layout/placeAt is read-only metadata, with no fabricated geometry/spatial stream. Default ordinary gateway tests open no unsolicited broker sockets.

Production MQTT uses a real maintained MQTT client adapter; tests inject an in-process broker driver with the same ownership/queue boundary. Proposed local JSON envelope: command `{requestId: invocationId, action, params}`; acknowledgement `{requestId, status: succeeded|failed, result|error}`. Ack is accepted only on the configured ack topic, for the current pending request. Commands are non-retained and never replayed after reconnect. Device online requires fresh valid telemetry and a connected broker; optional status topic can mark immediate offline. No telemetry means unavailable. Disconnect stops command admission and fails pending commands; reconnect requires fresh telemetry before another command.

MQTT callbacks enqueue owned input on the gateway event loop; Program tick drains only bounded mailboxes. Telemetry mapping is declarative dotted `$.field` paths. Invalid JSON, unknown fields and invalid property values cannot disable a device program or refresh readings. A command only succeeds after its matching device ack, with invalid/late/unrelated/retained acks ignored. Deadline expiry records `timeout`; disconnected/never-seen device records `device_unavailable`. Cancellation clears pending work; it cannot claim to undo a command already delivered to hardware. Shutdown stops intake, fails owned active/pending approvals, drains/stops the Live task, disconnects the exact owned MQTT client, and closes pipeline timers/waiters.

## Compatibility and verification obligations

No public WebSocket protocol/recording schema change, no Newton imports in Live, no immutable package edits. Changes to shared Catalog/HostCore (and a dependency lock) invalidate strict source-input captures; actual affected recaptures must follow the final producer freeze and coordinated CPU window. Existing session routes, approval behavior and static capture input readers remain mandatory consumers.

First discriminating check: actual HostCore with one MQTT Program and a fake mailbox, proving invalid/absent telemetry cannot dispatch; mapped telemetry admits a command; wrong ack cannot succeed; matching ack can; monotonic deadline fails; disconnect makes properties stale and rejects further admission. Expand only after the first check runs and root has reviewed the high-risk interface.

Physical devices are outside available access. Fake-broker tests prove gateway semantics only; a later owned local mosquitto plus three mock devices proves the actual MQTT adapter. Full final-head mandatory CI, bounded simplification, independent Standards/Spec reviews and authorized merge remain outstanding.
