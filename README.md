# Newton Spatial Sync

A source-independent spatial gateway and browser workbench. Synthetic mode runs
without Newton, Warp, CUDA, or a GPU. Newton mode uses one separate runner process
for the active session; the API process owns HTTP and WebSocket connections.

Requires Python 3.13, [uv](https://docs.astral.sh/uv/), Node.js 22+ and pnpm.

```powershell
uv sync --frozen --group dev
pnpm install --frozen-lockfile
```

Start the API, then the web app in another terminal:

```powershell
uv run --frozen python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
pnpm dev
```

Open the web address printed by Vite (normally `http://127.0.0.1:5173`), select
**Synthetic Laboratory** / **synthetic**, and start the scene. The lab contains a
two-link robot, 20 moving tubes, a rotating centrifuge, and a deliberate missing
asset with a primitive fallback. Select `/World/Lab/Robot/tcp` to inspect a Site
offset from its parent Body. Pause, Step, Reset and Resume affect the shared
authoritative source; two browsers observe the same session.

For API development, add `--reload --reload-dir apps/api`. The default listener
stays on loopback. `GET http://127.0.0.1:8000/api/v1/health` and `/docs` are available
before a scene starts. The web app proxies `/api` to port 8000 by default.
`SPATIAL_API_URL` can select another API port for isolated development.

For Newton CPU/GPU setup and baseline smoke commands, see
[the Newton baseline report](docs/development/v1.2/P0-A.md). Install its locked
extras with `uv sync --frozen --group dev --extra simulation --extra usd`. Then
select a registered Newton scene; do not run multiple API workers. The Newton
adapter is installed by P0-E; an absent adapter returns `503 source_unavailable`.

## Tests

The mandatory Python gate uses the locked base environment and excludes optional
Newton integration tests:

```powershell
uv run --frozen python -m pytest tests/contract tests/unit tests/gateway -q
uv run --frozen ruff check apps/api tests/gateway
pnpm protocol:check
pnpm test:protocol:ts
pnpm test:web
pnpm build
pnpm --filter @spatial-sync/web exec playwright install chromium
pnpm test:e2e
```

Keep the API running on port 8000 for E2E. Playwright starts its own web server on
port 5184 and uses Chromium software WebGL. CI installs Chromium system libraries
with `playwright install --with-deps chromium`. Optional Newton/CPU/CUDA tests live
under `tests/sim_runtime`; they are outside the GPU-free gate. The versioned
protocol and generation workflow are documented in
[packages/protocol](packages/protocol/README.md).

## HTTP and WebSocket API

All endpoints start with `/api/v1`. Only registered scene/source combinations and
registered mesh/asset IDs are accepted. MVP uses one active session; creating the
same scene/source reuses it, while another active scene returns `409 session_exists`.

| Method | Path | Result |
| --- | --- | --- |
| GET | `/health` | API and active-source health |
| GET | `/scenes` | `{scenes:[{sceneId,name,sources,description?}]}` |
| POST | `/sessions` | `{sceneId,source,realtimeFactor?}` → SessionSummary |
| GET | `/sessions/{id}` | State, revision, epoch, seq, time, RTF and counts |
| GET | `/sessions/{id}/scene` | Manifest; ETag is quoted sceneRevision |
| GET | `/sessions/{id}/snapshot` | Latest complete `stream.snapshot` |
| WS | `/sessions/{id}/ws` | Ready → subscribe → ack → snapshot → frames/events |
| GET | `/sessions/{id}/pose?key=...` | Body/Site world `p`,`q`, seq/time and staleMs |
| GET | `/sessions/{id}/joints/state?key=...` | On-demand joint coordinates; 200 ms timeout |
| GET | `/sessions/{id}/stats` | Source metrics plus fanout/backpressure diagnostics |
| GET | `/sessions/{id}/meshes/{meshId}` | Registered Newton GLB mesh |
| GET | `/assets/{assetId}` | Registered static asset; unknown IDs return 404 |
| POST | `/sessions/{id}/reload` | Close old source, new revision, re-subscribe |
| POST | `/sessions/{id}/restart` | Restart a FAILED session only |
| DELETE | `/sessions/{id}` | Stop source and close its observers |

```json
{"type":"client.subscribe","protocolVersion":1,"sceneRevision":"<manifest revision>","scope":"all","rateHz":30}
```

`entities` scope accepts `entityKeys`; a selected Site or Joint includes its Body
pose and a selected Group includes descendant Bodies. Rates clamp to
`[1,basePublishHz]`. A revision mismatch returns `server.error` and leaves the
connection open so the client can reload the Manifest and re-subscribe.

```json
{"type":"command.request","requestId":"unique-session-request","command":"step","args":{"count":1}}
```

Commands execute serially and retain the latest 256 request acknowledgements for
idempotency. Resume/Step frames and Reset snapshots use reliable delivery in
frame → requester ack → all-observer state order. Pause also flushes its final
authoritative frame before ack/state so observers settle on the paused HTTP
snapshot. Periodic frames stop while paused; heartbeat continues in every state.
Each connection has one writer, a bounded reliable queue and a latest-only frame
slot. Overflow or a send exceeding two seconds disconnects only that observer.

## Optional remote mode

Keep local defaults for development. A remote deployment requires a TLS reverse
proxy and explicit credentials/origins before enabling `SPATIAL_REMOTE=1`:

- `SPATIAL_OPERATOR_TOKEN`: operator bearer token (required).
- `SPATIAL_VIEWER_TOKEN`: read-only bearer token (optional).
- `SPATIAL_ALLOWED_ORIGINS`: comma-separated exact browser origins (required by deployment).

HTTP and WS handshakes use `Authorization: Bearer ...`; browser deployments can
have the authenticated reverse proxy supply that header on upgraded connections.
Viewer credentials can observe but cannot create/close sessions or execute
commands. Remote HTTP requests and WS commands have bounded request rates.
Origins are checked before remote writes. Tokens must not be placed in URLs.
