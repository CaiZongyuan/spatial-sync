# Lab Information Foundation

English | [简体中文](README.zh-CN.md)

A local laboratory workbench with a source-independent spatial gateway.
Synthetic mode runs without Newton, Warp, CUDA or a GPU. Newton mode owns one
separate Runner process; the API owns HTTP and WebSockets. The
[approved specification](docs/spec/LAB_INFORMATION_FOUNDATION_SPEC_V1.md)
defines information-plane development and acceptance.

Requires Python 3.13, uv, Node.js 22+ and pnpm.

```powershell
uv sync --frozen --group dev
pnpm install --frozen-lockfile
uv run --frozen python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

In another terminal run `pnpm dev`, then open http://127.0.0.1:5173.
Choose **Synthetic Laboratory** for GPU-free use. Health and API documentation
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
