# MQTT laboratory devices

Install the frozen base dependencies (`uv sync --frozen --group dev`). Live is opt-in:

```powershell
$env:SPATIAL_LIVE_BINDINGS = 'devices/live/lab.yaml'
uv run --no-sync uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

The gateway owns one monotonic DeviceHost and MQTT client independently of spatial
sessions. `/api/v1/live/entities`, `/entity?key=`, `/state?keys=` and `/actions`
share the session information handlers and ActionPipeline. `/api/v1/live` returns
the configured name, recording status, rules and optional layout/placements.
`/live/catalog`, `/live/snapshot` and `/live/events` provide a coherent information
bootstrap and bounded event continuation. An absent configuration returns
`live_unavailable`; ordinary gateway startup creates no MQTT connection.

Open the Web app's **实况** page at `/live` to see the three devices, actions,
one-hour history, alert acknowledgement and operation plans without starting a
simulation. Simulation telemetry is available from a session's **设备** view.
With a configured `layout: scene-name@version`, the host resolves the exact scene
hash and holds a deletion reference. The browser uses that version's captured
static preview and `placeAt` bindings; it never starts a physics Source. Missing,
changed or uncaptured layouts leave the device cards usable and show the layout
as unavailable. Recordings keep their independent Live identity.

The example expects a broker at `mqtt://127.0.0.1:1883`. For an isolated development
broker, create a local Mosquitto configuration with `listener 1883 0.0.0.0`,
`allow_anonymous true` and `persistence false`, then mount that file read-only in
the official `eclipse-mosquitto:2` container. Publish its port only on loopback,
for example `-p 127.0.0.1:18827:1883`; point the binding's broker URL and mock tool
at port18827. Use a task-specific container name and stop that container afterward.

In a second terminal, start the three mock devices:

```powershell
uv run --no-sync python tools/mock_mqtt_device.py --seconds 90
```

Use `--broker mqtt://127.0.0.1:18827` for another broker. `--fault heat`,
`--fault disconnect` or `--fault low-airflow` takes effect after `--after 5`
seconds. Disconnect silences only the incubator while the other devices continue.
`--drop-acks` and `--ack-delay 6` exercise command timeouts. The tool stops after
the requested duration (maximum3600s) or Ctrl+C; it does not start a broker.

Read and act through the same generic commands and MCP tools:

```powershell
lw ls --query /Real/
lw get /Real/Incubator2
lw do /Real/Incubator2 setSetpoint temperatureC=38 --no-wait
lw history /Real/Incubator2 temperatureC --since 1h --every 1m
lw alerts --active
lw inbox
```

Keys beginning `/Real/` automatically select Live, even without an active session.
State reads can combine Live and simulation keys; invocation reads/cancellation
resolve the target through a read before sending control. High-risk actions always
require human approval, including actions requested by a human or CLI. Pending
approval produces zero MQTT command publications; MCP cannot approve. The normal
global approval endpoints and existing approval UI resolve the Live target.

MQTT mappings use literal, case-sensitive topics. Different entity keys must own
different command topics because the command envelope has no recipient field.
Multiple actions belonging to the same device may share one command topic.
Telemetry and status/ack topics may be shared when the mappings require it.

The JSON command is `{"requestId":"inv-…","action":"setSetpoint","params":{"temperatureC":38}}`.
Publish an ack on the configured ack topic as `{"requestId":"inv-…","status":"succeeded","result":null}`
or `{"requestId":"inv-…","status":"failed","error":"device explanation"}`.
Commands use QoS0 and `retain=false`; a successful publish only stages transport,
not completion. Only a matching non-retained ack received after dispatch and before
the command deadline completes an active invocation. Retained, old, duplicate or
unrelated acks cannot complete another action. Cancellation removes unsent Host
commands; it cannot undo a command already handed to the MQTT client or hardware.

Telemetry must match the declared dotted JSON mapping and property schemas before
any property or online state refreshes. Receipt time determines freshness, even
when processing is delayed. Broker disconnect or queue overflow fails pending work;
reconnection creates a new clean client, discards old transport packets and requires
fresh non-retained telemetry. A status payload `{"online":false}` makes that device
immediately unavailable. Silent readings expire by `staleAfterSeconds`; actions
return `device_unavailable` before evaluating stale preconditions.

Live continuously records information in UTC daily segments, retains seven UTC
days and supports cross-segment history and read-only replay. See
[RECORDINGS.md](RECORDINGS.md) for clocks, retention and recovery. Alerts and their
first acknowledgement use the existing shared alert API; the durable inbox cursor
continues across gateway restarts.

Submit an operation plan with `target: live` through `lw plan create` or the generic
MCP tool. All Live plans require a human decision in **待审批**, including check-only
plans. Approval covers only the declared action, parameters, revision and step;
the agent still executes each step. Matching retries are idempotent. Plan changes,
revocation and Host shutdown invalidate the old grant. Live plans display their
results on `/live` and `/plans/{id}`; they have no simulated motion preview.

Physical hardware and actual broker behavior require separate validation;
injected-broker tests establish gateway semantics only.

Live and a Synthetic session have independent lifecycles: selecting a session in
`LW_SESSION` still routes `/Real/` keys to Live, and closing either host leaves the
other host's work intact. The GPU-free regression is in
`tests/gateway/test_live_devices.py`.
