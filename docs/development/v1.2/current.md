# Newton Spatial Sync — current checkpoint

Snapshot: 2026-10-16T09:00:58.593Z
Task status (PM supplied): in-progress

## Goal and scope

> Implement and locally integrate specification v1.2 in phase order; reserve Phase 3 interfaces and condition Phase 2 optimizations on evidence

Platform: desktop-web; mobile included: false

The generator does not infer completion, authorization, or merge readiness from CI.

## Manual checkpoint

**revision**

> 4bb62179999667fcd596d216082bff8a5d23ab02 plus PM documentation

**authorization**

> User requested implementation according to the v1.2 specification. Local development, isolated issue branches, validation and integration are authorized. No remote or publication is configured.

**next**

> Complete B and C, then D; integrate, simplify, final gates and independent Standards/Spec milestone reviews.

**roles**

> [
>   {
>     "role": "PM/integration",
>     "owner": "/root",
>     "ticket": "v1.2"
>   },
>   {
>     "role": "Subscriptions developer",
>     "owner": "/root/protocol",
>     "ticket": "P2-B"
>   },
>   {
>     "role": "Scaling developer",
>     "owner": "/root/viewer",
>     "ticket": "P2-C"
>   },
>   {
>     "role": "Remote developer idle pending capacity",
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
  Pending: Windows generated artifact LF checkout repair assigned to B; Final full-head validation/review
- P2-B: Scopes, cumulative deltas and keyframes — in-progress
  Pending: Device/entities/viewport UI consumer, keyframe baseline and membership recovery; Frozen candidate and real browser evidence
- P2-C: GPU extraction and render scaling — in-progress
  Pending: Measured2000static+500moving and2k/10k workloads; Instancing/selection/LOD/culling; actual Warp gather/readback benchmark; Shared memory only if real Pipe bottleneck proven
- P2-D: Remote Runner transport — not-started
  Pending: Resume original Newton developer after B frees an implementation slot; Typed bounded JSON plus binary frames, auth/TLS and no network pickle; Gateway receive clock, failure/cleanup, loopback and real Newton evidence

## Recent recorded facts

- 2026-10-16T08:46:21.187Z: Binary reviewed/integrated; scaling dispatched — passed; actor Unknown; revision 4bb62179999667fcd596d216082bff8a5d23ab02
- 2026-10-16T08:38:43.540Z: Profile equal stream work and implement justified opt-in binary — passed; actor Unknown; revision b7cc387051405366087ab8667736f9be2f2ce682
- 2026-10-16T08:35:46.673Z: Robot, USD assets and reliability integrated; Phase2 profiling authorized — unknown; actor Unknown; revision b76a78213d29a77e89ca21f5fcf9f1c1fe3c9a4a
- 2026-10-16T07:09:51.599Z: Final CPU suite after warning oracle correction — unknown; actor Unknown; revision Unknown
- 2026-10-16T07:04:11.825Z: Final CPU asset and integrated source checks — failed; actor Unknown; revision Unknown

## Resources

- Root API8000 and web5173; owner PM; retained; Integrated preview API PID27012 exec13628; web PID13676 exec1257; actual Synthetic E2E closed its sessions
- Old root API8000 and FR3; owner PM; cleaned; Owned session STOPPED and API stopped before integration
- P1-C API8002/web5184; owner Viewer developer; retained; Sessions STOPPED; owner to reconcile reuse or cleanup for P2-C
- OldAPI8001/web5174; owner PM/developers; cleaned; Exact owned processes stopped and listeners verified gone
- P2-A8005/5185 and benchmark children; owner Newton developer; cleaned; Owner verified stopped and heavy reservation released
- P2-C CPU/network/GPU execution; owner Viewer developer; unknown; Must announce bounded heavy reservation and release
- Foreign5175; owner External process; unknown; Outside task ownership; preserved

## Evidence

- [Non-author issue review](./P2-A-review.md)
- [Methods, final checks and measured limits](./P2-A.md)
- [P1-B-first-validation.txt](./P1-B-first-validation.txt)
- [Real engine findings](./P0-A.md)
- [Review and repair record](./P0-C-review.md)
- [Browser evidence](./P0-D.md)
- [Review and evidence boundaries](./P2-A-review.md)
