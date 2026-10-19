# Lab Information Foundation

English | [简体中文](README.zh-CN.md)

A local laboratory workbench with a source-independent spatial gateway.
Synthetic mode runs without Newton, Warp, CUDA or a GPU. Newton mode owns one
separate Runner process; the API owns HTTP and WebSockets. The
[approved specification](docs/spec/LAB_INFORMATION_FOUNDATION_SPEC_V1.md)
defines information-plane development and acceptance.

Requires Python 3.13, uv, Node.js 22+ and pnpm.

The [product platform skills](skills/README.md) teach agents how to inspect and
operate the lab through the actual Catalog and CLI. List them with `lw skills ls`;
install them into an explicit agent directory with `lw skills install --target DIR`.

```powershell
uv sync --frozen --group dev
pnpm install --frozen-lockfile
uv run --frozen python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

In another terminal run `pnpm dev`, then open http://127.0.0.1:5173.
Open **Sessions** and choose **Synthetic Laboratory** for GPU-free use. Health and API documentation
are `/api/v1/health` and `/docs`. The web app proxies `/api` to port 8000;
`SPATIAL_API_URL` selects another API.

For Newton scenes, install and launch with the optional dependencies:

```powershell
uv sync --frozen --group dev --extra simulation --extra usd
uv run --frozen --extra simulation --extra usd python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

Use one API worker and one authoritative Runner. While the environment is in
use, checks should use `uv run --no-sync` to preserve installed extras.
Shipped scenes and Franka assets work offline.

| Scene | Source | Purpose |
| --- | --- | --- |
| `lab-demo` | synthetic | Deterministic laboratory without a physics dependency |
| `falling-boxes` | newton | Rigid bodies, Body/Site selection and controls |
| `robot-and-tubes` | newton | Official Franka FR3, tubes and centrifuge |
| `usd-lab` | newton | Y-up USD import and visual fallbacks |

Select a Body or Site, then Pause, Step, Reset and Resume. Observers share one
authoritative session and keep independent cameras. Close the session before
changing scenes. The Inspector reports authoritative samples; the viewport may
interpolate between published poses.

## Platform overview

The home page shows the active session in an isometric spatial view. Select a
device in the view, its label or the legend to inspect its type, actions and
teaching locations. The **读数** switch controls the live label readouts; the
activity timeline shows actual device events and invocation transitions.
With no active session, the most recent available recording provides a read-only
scene preview, or the page guides you to the scene library.
**Scenes (场景库)** (`/scenes`) lists immutable versions with searchable card/list
views, source and launchability filters. Open a version to inspect its static
initial scene, entity bindings, teaching and recorded validation, then launch
that exact version into its connected workspace.
**Sessions (会话)**
(`/sessions`) lists managed sessions and lets another observer join by ID.
**Resources · Compute** (`/resources/gpu`) shows host resources and
managed Newton backends. These pages refresh every two seconds. **Recordings**
(`/recordings`) links to read-only replay at `/replay/{id}`.
The sidebar collapses and remembers your preference; below 900 px it becomes
top navigation. The theme follows your system until you choose Light or Dark.
Latin IBM Plex fonts and navigation icons ship locally.
Session workspaces can switch between perspective and isometric cameras;
`?camera=isometric` shares that view. Light/dark themes update viewport materials
and lighting without restarting the source or reloading its scene.
Stopped sessions remain in history; a paused Newton process still counts as a
live backend. CPU, RAM, GPU utilization and GPU memory describe the gateway
machine, including its other applications. Missing measurements display as
unavailable. Per-process CPU uses one logical core as 100% and can exceed 100%.

Use **Open Newton viewer** on a live Newton session or its workspace to open
the native debug view in a new tab. It uses the same Runner and simulation;
repeated opens reuse its viewer server. Camera and visualization options live
there; simulation controls stay in the platform. The viewer is local to the
gateway host and closes with its session. See [operations details](docs/PLATFORM_OPERATIONS.md)
and [native viewer ownership](docs/implementation/NATIVE_VIEWER.md).

## Spatial API

Paths start with `/api/v1`. Entity keys are query parameters. One active session
is allowed; creating another scene returns `409 session_exists`.

| Method | Path | Result |
| --- | --- | --- |
| GET | `/health`, `/scenes` | Health and registered scenes |
| POST | `/sessions` | Create or reuse a session |
| GET | `/sessions/{id}` | Lifecycle, revision, epoch, seq and simTime |
| GET | `/sessions/{id}/scene`, `/sessions/{id}/snapshot` | Manifest and full JSON snapshot |
| WS | `/sessions/{id}/ws` | Ready, subscribe, ack, binary snapshot, frames/events |
| GET | `/sessions/{id}/pose?key=...` | Cached world pose and sample identity |
| GET | `/sessions/{id}/joints/state?key=...` | Joint coordinates with bounded query |
| GET | `/sessions/{id}/stats` | Source and observer diagnostics |
| GET | `/sessions/{id}/meshes/{id}`, `/assets/{id}` | Registered visual assets |
| POST | `/sessions/{id}/reload`, `/sessions/{id}/restart` | Reload or restart failed source |
| DELETE | `/sessions/{id}` | Close source and observers |

```json
{"type":"client.subscribe","protocolVersion":2,"sceneRevision":"<revision>","rateHz":30,"info":true}
```

WS spatial frames use the existing NSYN binary layout. HTTP snapshots remain
JSON. Rates clamp to `[1,basePublishHz]`. See the
[protocol contract](packages/protocol/README.md).

```json
{"type":"command.request","requestId":"unique-request","command":"step","args":{"count":1}}
```

Commands are serialized and idempotent. Each observer has one writer, a bounded
reliable queue and a latest-only periodic frame slot. Slow observers cannot
block others. Heartbeats continue while paused.

## Device state and actions

Select a device or one of its child bodies, then open **Device** in the Inspector.
Properties, units, parameter forms and conditions come from `devices/types/` and
the scene's YAML bindings. The invocation panel shows progress, results, errors
and actors. Robot motion advances only when an action runs.

Information paths use `/api/v1/sessions/{id}`:

| Method | Path | Result |
| --- | --- | --- |
| GET | `/catalog`, `/entities?type=&q=` | Definitions, bindings and entity summaries |
| GET | `/entity?key=...`, `/state?keys=a,b` | Definition/details or current properties |
| POST | `/actions` | `{requestId, entityKey, action, params, wait?, timeoutSeconds?}` |
| GET | `/invocations`, `/invocations/{id}?wait=true` | Recent calls or terminal wait |
| POST | `/invocations/{id}/cancel`, `/invocations/{id}/approve` | Cancel or operator approval |
| GET | `/wait?key=&property=&op=&value=` | Event-driven property wait; `value` is JSON |
| GET | `/events?after=&timeoutSeconds=` | Ordered information messages with a cursor |

For example, `closeLid` followed by `start` on `/World/Lab/Centrifuge` exercises
the Synthetic device lifecycle. The arm must leave the centrifuge zone before
closing the lid. Rejections include structured reasons and condition messages.
`X-Actor: cli:lw` or `agent:<client name>` records attribution; it grants no
permissions. Agents cannot approve calls. Reuse a `requestId` to retrieve the
same invocation without executing it twice.

The **Approvals** inbox at `/approvals` shows pending requests and their decision
history. An operator can approve or deny with a note, and revoke an approval
while its invocation is still queued. Once execution starts, revocation is
refused. Withdrawing a denial keeps the original invocation rejected; submit a
new action to try again.

`lw approve <invocation-or-approval-id> [--deny] [--note "reason"]` displays the
request and requires typing `approve` or `deny` in an interactive terminal.
Non-TTY input and `--json` return `approval_requires_human` before any HTTP
request. MCP exposes no approval tool. The inbox API provides
`GET /api/v1/approvals`, `GET /api/v1/approvals/{id}` and
`POST /api/v1/approvals/{id}/decision` with `{decision, note?}`; decisions are
`approve`, `deny` or `revoke`. The subject envelope supports `invocation`,
`plan`, `policy`, `job` and `deletion`; current device calls use `invocation`.

Approval audit records are separate from spatial recordings, in
`~/.spatial-sync/state/approvals.jsonl`. `SPATIAL_STATE_ROOT` selects another
state directory. Records survive gateway restart; requests whose gateway-owned
session disappeared are closed and cannot execute from a retained approval.

## Recording and replay

Sessions record automatically to `recordings/<sessionId>/`. Close the session to
finish its recording, then choose it from **Recordings** on the home page.
`/replay/<recordingId>` reuses the scene, Inspector and invocation views with
read-only controls, playback speed from 0.25× to 4×, and seeking. An external
recording can be opened with `?src=<recording.json URL>`.

`GET /api/v1/recordings` lists recordings. The `/recordings/<id>/` files include
frozen manifests, catalogs, assets and LWLG format1 segments; replay needs no
live source session. `SPATIAL_RECORDINGS_ROOT` selects the directory,
`SPATIAL_RECORDINGS_KEEP` defaults to20, and `SPATIAL_RECORDINGS_ENABLED=0`
disables recording. Restarting the gateway recovers interrupted recordings as
`incomplete` and preserves complete records before a truncated tail.

The recent query window keeps1000 terminal invocations; older exact results are
read from the session log. An unavailable archived result returns503
`history_unavailable` with its original invocation ID, preserving deduplication.

## CLI and MCP

Discover and launch from the installed `lw` entrypoint, or open a workspace in
the browser:

```powershell
uv run --no-sync lw scene ls --json
uv run --no-sync lw asset ls --kind robot --json
uv run --no-sync lw asset show robot/fr3@1 --json
uv run --no-sync lw asset import <asset-directory> --json
uv run --no-sync lw asset validate robot/fr3@1 --json
uv run --no-sync lw scene import <scene-directory> --json
uv run --no-sync lw scene validate <scene-version> --json
uv run --no-sync lw scene show robot-and-tubes --json
uv run --no-sync lw scene launch lab-demo
uv run --no-sync lw status
uv run --no-sync lw ls --type centrifuge/v1
uv run --no-sync lw show /World/Lab/Centrifuge
uv run --no-sync lw do /World/Lab/Centrifuge closeLid
uv run --no-sync lw do /World/Lab/Centrifuge start rpm=3000 durationSeconds=30 --wait
uv run --no-sync lw rec ls --json
```

`LW_URL` defaults to `http://127.0.0.1:8000`; `LW_SESSION` selects a session
instead of discovering the unique active one. `LW_TOKEN` supplies a bearer token
when required. `do` waits by default; `--no-wait` returns after acceptance.
Parameter values use JSON, falling back to strings. Use `lw --help` for all
commands and `--json` for machine output. Exit codes are0 success,1 usage or
connection error,2 rejection,3 failure/cancellation and4 wait timeout.

Asset and scene imports upload an uncompressed tar from the CLI client's directory. The
gateway checks metadata, allocates immutable revisions and deduplicates identical
content. CPU-only structural checks record S1–S8 results; physics checks remain
explicitly not run. Revalidation appends a report without changing published
version bytes or their hash. See [asset packages](docs/ASSET_LIBRARY.md).

`lw mcp` serves thirteen generic tools over stdio through the pinned official MCP
SDK. Library discovery and launch work before any session exists; the original
eight device/session tool schemas are unchanged. See [scene versions](docs/SCENE_LIBRARY.md)
for package roots, latest versus latest-launchable selection and compatibility.
For a Claude Code MCP configuration, replace the repository path below:

```json
{
  "mcpServers": {
    "lab": {
      "command": "uv",
      "args": ["--directory", "<absolute-repository-path>", "run", "--no-sync", "lw", "mcp"],
      "env": {"LW_URL": "http://127.0.0.1:8000"}
    }
  }
}
```

MCP can discover and launch a workspace. Tools cover discovery, definitions, state,
actions, invocation reads/cancellation, property waits and session control.
Approval remains a human operation in Web or CLI. The agent receives Catalog
instructions and condition hints, and its client name is recorded as its actor.

## Verification and assets

The [verification guide](docs/VERIFICATION.md) records commands, optional Newton
checks and cleanup. Local results do not establish an external CI result.
Asset IDs are registered in `assets/registry.yaml`; IDs are never server paths.
Bindings replace a visual group, with native meshes, colliders and placeholders
as fallbacks. The renderer chooses one group. See the
[robot notes](examples/robot-and-tubes/README.md) and
[USD compatibility notes](examples/usd-lab/README.md).

Local access needs no token. Optional gateway access uses operator/viewer bearer
tokens and explicit allowed origins. Viewer credentials are read-only.
Deployment is outside this specification's scope.
