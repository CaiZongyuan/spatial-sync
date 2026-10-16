# Newton Spatial Sync — current checkpoint

Snapshot: 2026-10-16T10:45:15.813Z
Task status (PM supplied): in-progress

## Goal and scope

> Implement and locally integrate specification v1.2 in phase order; reserve Phase 3 interfaces and condition Phase 2 optimizations on evidence

Platform: desktop-web; mobile included: false

The generator does not infer completion, authorization, or merge readiness from CI.

## Manual checkpoint

**revision**

> 9a93c83b5cdb47607afacb752d242ad322aa1267 plus PM documentation

**authorization**

> User requested implementation according to the v1.2 specification. Local development, isolated issue branches, validation and integration are authorized. No remote or publication is configured.

**next**

> Complete C/D and independent review; validate latest integration on separate8009/5189 to preserve the unknown-owner paused B session on8000/5173.

**roles**

> [
>   {
>     "role": "PM/integration",
>     "owner": "/root",
>     "ticket": "v1.2"
>   },
>   {
>     "role": "Subscriptions developer parked after frozen delivery",
>     "owner": "/root/protocol",
>     "ticket": "P2-B"
>   },
>   {
>     "role": "Scaling/scoped renderer developer",
>     "owner": "/root/viewer",
>     "ticket": "P2-C"
>   },
>   {
>     "role": "Remote Runner developer",
>     "owner": "/root/newton",
>     "ticket": "P2-D"
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
- P2-C: GPU extraction and render scaling — in-progress
  Pending: Instancing/selection/LOD/culling and complete scoped visibility; Actual Warp gather/readback/Pipe benchmark; shared memory only if justified; Comparable critical repeats with exclusive heavy resources
- P2-D: Remote Runner transport — in-progress
  Pending: Implement typed bounded control/frame channels, sampleTimeClock and receipt stamping; Real loopback/publicGateway failure/cleanup/auth/cert tests and optional NewtonCPU journey

## Recent recorded facts

- 2026-10-16T10:45:15.813Z: Preserve unknown-owner paused scene; freeze existing UI build — passed; actor Unknown; revision Unknown
- 2026-10-16T10:18:24.224Z: Scoped stream integrated; remote Runner developer resumed — passed; actor Unknown; revision 9a93c83b5cdb47607afacb752d242ad322aa1267
- 2026-10-16T10:01:54.541Z: Scoped JSON and binary consumers verified on final local candidate — passed; actor Unknown; revision 4bca2acffef4a17412c8f1242082d8b703bb4058
- 2026-10-16T08:46:21.187Z: Binary reviewed/integrated; scaling dispatched — passed; actor Unknown; revision 4bb62179999667fcd596d216082bff8a5d23ab02
- 2026-10-16T08:38:43.540Z: Profile equal stream work and implement justified opt-in binary — passed; actor Unknown; revision b7cc387051405366087ab8667736f9be2f2ce682

## Resources

- Retained B preview8000/5173; owner PM; retained; B API PID36204 exec8139 retained for unknown-owner paused Synthetic session-b469c0e4cf99. B production bundle copied to.scratch/preview-b-9a93c83, preview5173 PID41600 exec69500. Dev Vite13676 stopped; temporary5191 verification server stopped.
- Old root API8000 and FR3; owner PM; cleaned; Owned session STOPPED and API stopped before integration
- P1-C API8002/web5184; owner Viewer developer; retained; Sessions STOPPED; owner to reconcile reuse or cleanup for P2-C
- OldAPI8001/web5174; owner PM/developers; cleaned; Exact owned processes stopped and listeners verified gone
- P2-A8005/5185 and benchmark children; owner Newton developer; cleaned; Owner verified stopped and heavy reservation released
- P2-C CPU/network/GPU execution; owner Viewer developer; unknown; Must announce bounded heavy reservation and release
- Foreign5175; owner External process; unknown; Outside task ownership; preserved
- B API8007/web5187; owner Protocol developer; cleaned; All E2E sessions deleted; author verified both listeners absent
- Unknown-owner paused Synthetic session-b469c0e4cf99; owner Unconfirmed; active C/D developers deny creating it and PM-owned E2E sessions are stopped; retained; Preserve PAUSED epoch0 seq268/simTime8.895833333334; no close/reset. Final complete preview will use separate8009/5189 after integration.

## Evidence

- [Bounded independent review](./P2-B-review.md)
- [P2-B.md](./P2-B.md)
- [Non-author issue review](./P2-A-review.md)
- [Methods, final checks and measured limits](./P2-A.md)
- [Real engine findings](./P0-A.md)
- [Review and repair record](./P0-C-review.md)
- [Browser evidence](./P0-D.md)
- [Review and evidence boundaries](./P2-A-review.md)
- [Independent review and cross-issue condition](./P2-B-review.md)
