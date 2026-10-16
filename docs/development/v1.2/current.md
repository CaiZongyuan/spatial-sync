# Newton Spatial Sync — current checkpoint

Snapshot: 2026-10-16T12:45:42.112Z
Task status (PM supplied): in-progress

## Goal and scope

> Implement and locally integrate specification v1.2 in phase order; reserve Phase 3 interfaces and condition Phase 2 optimizations on evidence

Platform: desktop-web; mobile included: false

The generator does not infer completion, authorization, or merge readiness from CI.

## Manual checkpoint

**revision**

> 5d6533f67e1fec0cd457d0843eddcafffa74f291 plus PM documentation

**authorization**

> User requested implementation according to the v1.2 specification. Local development, isolated issue branches, validation and integration are authorized. No remote or publication is configured.

**next**

> Repair four mandatory final-review gaps, inspect first GPU-interest safety example, integrate repairs, then refresh complete final gates and affected independent review.

**roles**

> [
>   {
>     "role": "PM/integration",
>     "owner": "/root",
>     "ticket": "v1.2"
>   },
>   {
>     "role": "Gateway review repairs",
>     "owner": "/root/gateway_repair",
>     "ticket": "FINAL-G"
>   },
>   {
>     "role": "Source/subscription GPU and diagnostics repairs",
>     "owner": "/root/newton",
>     "ticket": "FINAL-S"
>   },
>   {
>     "role": "Independent Spec reviewer (parked)",
>     "owner": "/root/final_spec",
>     "ticket": "v1.2"
>   },
>   {
>     "role": "Independent Standards reviewer (parked)",
>     "owner": "/root/final_standards",
>     "ticket": "v1.2"
>   }
> ]

## Tickets

- P0-A: Newton baseline — integrated
  Pending: Final complete-milestone review and affected validation
- P0-B: Protocol schema — integrated
  Pending: Final complete-milestone review and affected validation
- P0-C: Gateway and SyntheticSource — integrated
  Pending: Final complete-milestone review and affected validation
- P0-D: Web viewer — integrated
  Pending: Final complete-milestone review and affected validation
- P0-E: Newton source integration — integrated
  Pending: Final complete-milestone review and affected validation
- P1-A: Articulated robot — integrated
  Pending: Final milestone independent review and affected validation
- P1-B: USD scene and visual binding — integrated
  Pending: Final milestone independent review and affected validation
- P1-C: Workbench reliability — integrated
  Pending: Final milestone independent review and affected validation
- P2-A: Binary transform stream — integrated
  Pending: Final full-head validation/review; persistent generated LF checkout repair integrated with B
- P2-B: Scopes, cumulative deltas and keyframes — integrated
  Pending: C-owned actual mesh/instance/Site/raycast visibility must close overall subscription acceptance; Final complete-head checks and independent review
- P2-C: GPU extraction and render scaling — integrated
  Pending: Instancing/selection/LOD/culling and complete scoped visibility; Actual Warp gather/readback/Pipe benchmark; shared memory only if justified; Comparable critical repeats with exclusive heavy resources
- P2-D: Remote Runner transport — integrated
  Pending: Implement typed bounded control/frame channels, sampleTimeClock and receipt stamping; Real loopback/publicGateway failure/cleanup/auth/cert tests and optional NewtonCPU journey
- FINAL-G: Authentication and failed-replacement lifecycle repairs — in-progress
  Pending: Public malformed bearer rejection; Failed reload/restart details and live heartbeats
- FINAL-S: Subscription-driven GPU targets and complete diagnostics — in-progress
  Pending: GPU interests with complete recovery snapshots; Actual Shape count and Runner heartbeat

## Recent recorded facts

- 2026-10-16T12:45:42.112Z: Final independent review found four mandatory gaps; repairs dispatched — failed; actor Unknown; revision 5d6533f67e1fec0cd457d0843eddcafffa74f291
- 2026-10-16T12:18:42.870Z: Shared registry and client read-only deadline repairs verified — passed; actor Unknown; revision ab9fdc520d3ad9053f6fd9995ca52567acde949c
- 2026-10-16T11:55:58.880Z: Complete bounded failure coverage and final delivery — passed; actor Unknown; revision 30e330b0f9153da4ac0662e53c48e1a19e33b89c
- 2026-10-16T11:55:58.880Z: Narrow unsupported TLS timeout claim; verified controlled deadline red/green — passed; actor Unknown; revision 30e330b0f9153da4ac0662e53c48e1a19e33b89c
- 2026-10-16T10:45:15.813Z: Preserve unknown-owner paused scene; freeze existing UI build — passed; actor Unknown; revision Unknown

## Resources

- Retained B preview8000/5173; owner PM; retained; B API PID36204 exec8139 retained for unknown-owner paused Synthetic session-b469c0e4cf99. B production bundle copied to.scratch/preview-b-9a93c83, preview5173 PID41600 exec69500. Dev Vite13676 stopped; temporary5191 verification server stopped.
- Old root API8000 and FR3; owner PM; cleaned; Owned session STOPPED and API stopped before integration
- P1-C API8002/web5184; owner Viewer developer; cleaned; Latest listener inventory confirms both absent.
- OldAPI8001/web5174; owner PM/developers; cleaned; Exact owned processes stopped and listeners verified gone
- P2-A8005/5185 and benchmark children; owner Newton developer; cleaned; Owner verified stopped and heavy reservation released
- P2-C CPU/network/GPU execution; owner Viewer developer; cleaned; Author execution finished; developer ports8006/5186 absent. Repair runtime window will be announced separately.
- Foreign5175; owner External process; unknown; Outside task ownership; preserved
- B API8007/web5187; owner Protocol developer; cleaned; All E2E sessions deleted; author verified both listeners absent
- Unknown-owner paused Synthetic session-b469c0e4cf99; owner Unconfirmed; active C/D developers deny creating it and PM-owned E2E sessions are stopped; retained; Preserve PAUSED epoch0 seq268/simTime8.895833333334; no close/reset. Final complete preview will use separate8009/5189 after integration.
- Final candidate API8009/web5189; owner PM; retained; API PID40616 exec10943; preview PID38344 exec3114 at5d6533f. Reserved for latest integrated tests; restart/rebuild after repairs. No manual session yet.

## Evidence

- [Frozen-candidate findings and repair acceptance](./FINAL-REVIEW.md)
- [Repair evidence](./newton-p2d-evidence/integration-refresh/summary.json)
- [Final check receipt](./P2-D-validation.txt)
- [Scope and evidence limits](./P2-D.md)
- [Real engine findings](./P0-A.md)
- [Review and repair record](./P0-C-review.md)
- [Browser evidence](./P0-D.md)
- [Review and evidence boundaries](./P2-A-review.md)
- [Independent review and cross-issue condition](./P2-B-review.md)
