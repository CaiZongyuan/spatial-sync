# P0-D / P1-C — Viewer workbench

Status: P0-D implemented and verified against the real SyntheticSource gateway; P1-C workbench source implemented. Local integration and final independent review remain PM-owned. Newton failure/reload acceptance belongs to the integrated milestone check.

Authorization: implement specification v1.2 P0-D and P1-C, with local integration authorized by the user. Implementation owner: Viewer Developer, branch `issue/p0-d-viewer`, baseline `633735534aa05376138904582ebb7712a0ea19c9`; shared protocol baseline `80b5b1d4185f943fd769ae9bb61ee2da60d5f233`.

## Delivered source

- Actual TanStack Router file routes and Vite plugin; scene/source launch and existing-session connection.
- React Three Fiber + Three.js viewport, OrbitControls, flat world-body containers under one Z-up conversion root. Primitive, mesh GLB, and external GLB loaders; asset-axis correction, normals, fallback visuals, compatibility warnings, and GPU cleanup.
- Logical scene explorer with group/body/static/site/joint selection and visibility; selected-body highlight and Site coordinate markers. Inspector reads the latest authoritative sample, labels its exact sequence/simulation/sample time, and derives Site world pose from Body × Site local pose.
- Mutable non-React typed frame buffer, recent 64-frame retention, sliding two-second minimum clock offset, two-period playout delay, same-hemisphere quaternion interpolation, no extrapolation, long-gap jump, epoch/revision/sequence guards, and foreground resubscription.
- WebSocket lifecycle with heartbeat liveness and reconnect to a complete snapshot. Control success waits for applied acknowledgement; lifecycle and target RTF come from server state. Paused step frames show immediately. Unknown device schemas display raw payload JSON.
- Collision/Site/origin/grid debug layers, source/gateway/browser diagnostics, and joint positional constraint error that excludes legal prismatic motion.
- Explicit session close returns to the scene chooser and handles the API's empty 204 response, allowing the single active session to be replaced through the browser.

## Public verification

From the repository root:

```powershell
pnpm install --frozen-lockfile
pnpm test:web
pnpm build
pnpm --filter @spatial-sync/web exec playwright install chromium
# Start the real gateway first. Defaults to API port 8000 and isolated browser port 5184.
pnpm test:e2e
```

`SPATIAL_API_URL` changes the Vite proxy and browser-test API target. `SPATIAL_WEB_URL` connects Playwright to an already running viewer. The developer preview owns port 5174; public integrated preview port 5173 is reserved for PM. The browser gate uses software WebGL and a 1440×960 desktop viewport. Its screenshots, traces, and HTML report are generated under `.scratch/viewer-e2e`.

## Current evidence

- 43 unit tests passed in 353 ms on the final source: all Newton primitives; shared Python-generated fixture cases; axis, Body × Site composition; legal joint degrees of freedom; sample clock, interpolation, discontinuities, epoch/reset, equal-sequence paused recovery; heartbeat and command authority; revision replacement; obsolete asynchronous response rejection; unknown device schemas; actionable HTTP errors and empty 204 responses.
- TypeScript check and Vite production build passed on pnpm 10.32.1 / Node 24.18.0, Vite 8.3.4, React 19.3.1, Three.js 0.186.1.
- One comprehensive real SyntheticSource Playwright journey passed in 13.8 s (16.6 s including harness startup), against frozen API port 8001 with Python 3.13.14 / FastAPI 0.143.2 / Starlette 1.8.0 / Pydantic 2.12.4. Browser: Chromium 156, 1440×960, software WebGL. Covered scene launch, actual moving rendered Body, exact same-sequence Inspector XYZ/quaternion/simTime against the HTTP snapshot, independently calculated Site offset, two clients, applied acknowledgement, paused heartbeat, step, reset epoch, resume, real upstream WebSocket close/reconnect, device state, asset fallback, and close-session cleanup.
- The test closed its Session and confirmed STOPPED. Isolated web port 5184 exited. Developer preview port 5174 remains owned by the Viewer Developer and proxies API port 8001 for PM's independent visual check.
- [Site Inspector screenshot](viewer-evidence/synthetic-site-inspector.png) and [diagnostics/device-state screenshot](viewer-evidence/synthetic-diagnostics.png) were inspected visually. The viewport and controls are exposed and legible. A separate performance benchmark is not claimed.
- PM independently opened the real homepage and verified layout/readiness; final independent workbench review is pending.

## Change impact

Changed boundaries: generated protocol consumers, public HTTP/WS endpoints, renderer resource ownership, and workspace frontend dependencies. Generated protocol sources/types/schema remain untouched. The viewer uses the shared Manifest validator only at scene loading, and generated message types throughout the frame/controller boundary. HTTP scene/session metadata types are local because that metadata is not part of the spatial stream contract.

The exact-pose Inspector and interpolated renderer retain separate data: an interpolated pose is never presented as an authoritative snapshot sequence. Recovery allows an equal-sequence full snapshot only during explicit resubscription; duplicate periodic frames remain rejected. Unit tests prove both distinctions, and the real gateway journey proves the exact authoritative sample reaches both Inspector and renderer after pause.

Actual gateway statistics were traced to `Session.stats()` and Newton Runner `stats()`. The UI reads the real client rate/drop/queue fields, derives fanout from subscribed clients, reports the source's `stepHz`, `solver_ms`, `readback_ms`, and computes publish rate from successive published-frame counters. Missing source metrics display an empty value.

## Rework and evidence limits

Six real-browser attempts were executed, with one final complete pass. The first two stopped before connection because test Origin 5184 was not active in the gateway allowlist/reloaded process. The third found an ambiguous test locator (`output` has implicit status role); the locator now identifies the command result explicitly. The fourth exposed a real final-pause-frame delivery gap; the source owner added reliable final-frame delivery before acknowledgement and PAUSED state. The fifth proved Chromium's HTTP offline emulation preserved existing WebSockets; the test now closes a real upstream relay and reconnects to the real server, forwarding every motion frame unchanged. The sixth passed all target assertions on frozen dependencies.

Failure traces for attempts 2–5 remain separate under `.scratch/viewer-e2e/`. The first trace was overwritten by Playwright's normal output reset before preservation; the tool output and its classified prerequisite failure remain recorded. Exact thinking/queue/wait durations were not measured and are not attributed.

## Bounded simplification

Inspected all task-owned frontend code and its immediate protocol consumers. Reused generated `PrimitiveGeometry` directly, removed an unused matrix helper, kept scalar network counters outside React snapshots, and formatted the workbench JSX for review. Added generation guards so an obsolete Manifest response cannot overwrite an active connection after unmount/restart. Retained explicit renderer/controller/buffer boundaries because they own different timing and cleanup rules. No protocol hand edits or broad framework abstractions were introduced. Final unit/build and real browser validation passed after this pass.

## Limitations and pending work

- Newton failure/replacement integration and independent final review are pending. Revision replacement is already covered through the controller's generated-message boundary by unit tests; the integrated milestone must verify the actual source lifecycle.
- The Three/R3F workbench route produces a large-bundle warning (~1.15 MB minified before gzip); it is loaded separately from the homepage. There is no artificial bundle-size acceptance claim.
- The developer machine's software-WebGL browser is functional evidence only; hardware/performance claims require the specification's separate benchmark methodology.
- Newton/robot/USD source correctness and reference-image comparison belong to their source integration owners; the viewer consumes the same protocol for every source.
