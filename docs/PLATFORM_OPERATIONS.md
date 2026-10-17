# Gateway platform operations

`GET /api/v1/platform` returns the last background sample. It never starts a
probe. One worker samples every two seconds per application lifespan; CPU and
memory use psutil, and NVIDIA metrics use a one-second bounded `nvidia-smi` query.
`sampledAtMs` is UTC Unix epoch milliseconds. Values describe the gateway host.

The summary distinguishes all retained sessions, RUNNING sessions, PAUSED
sessions, sessions with WebSocket clients, total WebSocket clients, and live
Newton processes owned by those sessions. A paused Newton process still counts
as a backend. Native Viewer clients are reported separately and do not count as
WebSocket clients. Other programs on the host never count as managed backends;
system CPU, memory and GPU measurements include other programs.

Each session includes its scene name, source, state, WebSocket client count,
simulation time, actual RTF and an optional Newton backend. Backend fields are
`pid`, `alive`, `device`, `cpuPercent`, `memoryBytes`, `metricsReason`, and `viewer`.
Viewer fields are `status` (`not_started`, `ready`, `unavailable`), `url`,
`clientCount` and `reason`. Resource values that cannot be measured are null;
`metricsReason`, CPU/memory `reason` and `gpuMessage` explain unavailable data.
CPU sampling needs two samples. Process CPU uses one logical core as 100%, so a
process can exceed 100%. Process memory is RSS; host used memory is total minus
available memory. GPU memory values convert NVIDIA MiB to bytes.

Initial LOADING sessions appear in platform samples while creation is pending.
They become accessible through existing session routes only after startup
succeeds. Failed initial creation is cleaned up and is not retained. Failed
reload and stopped sessions retain their existing session and health semantics.
The existing `/api/v1/health` shape is unchanged.

`POST /api/v1/sessions/{id}/viewer` returns status 200 with
`{status: "ready", url, backendPid}`. It opens or reuses the current Newton
Runner's native viewer and serializes with commands, reload and close. RUNNING
and PAUSED sessions are supported. Unknown sessions return 404, Synthetic
sessions 400, invalid session states 409, and adapter failures 503. Remote mode
returns 403: the native viewer URL is loopback on the gateway host. Existing
remote authentication, origin, role and rate controls also apply to these routes.
No WebSocket protocol or CLI contract changes.
