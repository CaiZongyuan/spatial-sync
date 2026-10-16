# Newton Spatial Sync — current checkpoint

Snapshot: 2026-10-16T13:00:48.929Z
Task status (PM supplied): in-progress

## Goal and scope

> Implement and locally integrate specification v1.2 in phase order; reserve Phase 3 interfaces and condition Phase 2 optimizations on evidence

Platform: desktop-web; mobile included: false

The generator does not infer completion, authorization, or merge readiness from CI.

## Manual checkpoint

**revision**

> 465a152518437051e637a3213212c7f083eb8414 plus PM documentation

**authorization**

> User requested implementation according to the v1.2 specification. Local development, isolated issue branches, validation and integration are authorized. No remote or publication is configured.

**next**

> Finish FINAL-S source interests/full snapshots and diagnostics; inspect integrated transition behavior; refresh final gates and independent review.

**roles**

> [
>   {
>     "role": "PM/integration",
>     "owner": "/root",
>     "ticket": "v1.2"
>   },
>   {
>     "role": "Gateway repair author (parked after integration)",
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
  Pending: Final complete-head checks/review; actual renderer/raycast condition closed by C; GPU interest repair tracked as FINAL-S
- P2-C: GPU extraction and render scaling — integrated
  Pending: Mandatory active-observer GPU interest filtering and diagnostics tracked as FINAL-S; Final complete-head validation/review; suggested45FPS unmet under software WebGL
- P2-D: Remote Runner transport — integrated
  Pending: Refresh source-interest/diagnostics delta through TLS in FINAL-S; Final complete-head validation and independent review
- FINAL-G: Authentication and failed-replacement lifecycle repairs — integrated
  Pending: Affected complete-candidate independent review and final gates
- FINAL-S: Subscription-driven GPU targets and complete diagnostics — in-progress
  Pending: GPU interests with complete recovery snapshots; Actual Shape count and Runner heartbeat

## Recent recorded facts

- 2026-10-16T13:06:28.540Z: Independent first variable-gather CPU oracle passed — passed; actor Unknown; revision 3eb0c71 source branch including6459339 and465a152
- 2026-10-16T13:00:48.929Z: Authentication and failed-source recovery repair integrated — passed; actor Unknown; revision 465a152518437051e637a3213212c7f083eb8414
- 2026-10-16T12:57:09.410Z: Accepted first concrete variable-target safety oracle — passed; actor Unknown; revision review-source working tree based5d6533f
- 2026-10-16T12:45:42.112Z: Final independent review found four mandatory gaps; repairs dispatched — failed; actor Unknown; revision 5d6533f67e1fec0cd457d0843eddcafffa74f291
- 2026-10-16T12:18:42.870Z: Shared registry and client read-only deadline repairs verified — passed; actor Unknown; revision ab9fdc520d3ad9053f6fd9995ca52567acde949c

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

- [Repair design, red/green and complete checks](./FINAL-gateway-repair.md)
- [Frozen-candidate findings and repair acceptance](./FINAL-REVIEW.md)
- [Repair evidence](./newton-p2d-evidence/integration-refresh/summary.json)
- [Real engine findings](./P0-A.md)
- [Review and repair record](./P0-C-review.md)
- [Browser evidence](./P0-D.md)
- [Review and evidence boundaries](./P2-A-review.md)
- [Independent review and cross-issue condition](./P2-B-review.md)
