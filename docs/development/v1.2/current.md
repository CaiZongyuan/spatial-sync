# Newton Spatial Sync — current checkpoint

Snapshot: 2026-10-16T04:51:52.299Z
Task status (PM supplied): in-progress

## Goal and scope

> Implement and locally integrate specification v1.2 in phase order; reserve Phase 3 interfaces and condition Phase 2 optimizations on evidence

Platform: desktop-web; mobile included: false

The generator does not infer completion, authorization, or merge readiness from CI.

## Manual checkpoint

**revision**

> a7cd4b05f29c915523c6dbfb08eb017080d18f7c plus PM notes

**authorization**

> User requested implementation according to the v1.2 specification. Local development, isolated issue branches, validation and integration are authorized. No remote or publication is configured.

**next**

> Complete actual Synthetic browser journeys; integrate gateway/viewer; validate spawned Newton source; then robot/USD and evidence-driven Phase 2.

**roles**

> [
>   {
>     "role": "PM/integration/review",
>     "owner": "/root",
>     "ticket": "v1.2"
>   },
>   {
>     "role": "Newton Developer",
>     "owner": "/root/newton",
>     "ticket": "P0-E"
>   },
>   {
>     "role": "Gateway Developer",
>     "owner": "/root/protocol",
>     "ticket": "P0-C"
>   },
>   {
>     "role": "Viewer Developer",
>     "owner": "/root/viewer",
>     "ticket": "P0-D"
>   }
> ]

## Tickets

- P0-A: Newton baseline — integrated
  Pending: Full browser parity belongs to P0-E
- P0-B: Protocol schema — integrated
  Pending: Integrated consumer verification
- P0-C: Gateway and SyntheticSource — integrated
  Pending: Final author evidence/simplification; Complete frozen-dependency real browser recovery journey; Real Newton adapter merge
- P0-D: Web viewer — in-progress
  Pending: Controlled actualWSdisconnect/reconnect, reload, finalsourcefreeze and review
- P0-E: Newton source integration — in-progress
  Pending: Finalreceipt/sourcefreeze and integration/review
- P1-A: Articulated robot — not-started
  Pending: 7DOF import and solver evidence
- P1-B: USD scene and visual binding — not-started
  Pending: USD/mesh/binding actual user journey
- P1-C: Workbench reliability — in-progress
  Pending: Actual integrated reliability evidence
- P2-A..D: Scaling and remote transport — not-started
  Pending: Binary/subscriptions/keyframes/gather/render scaling/remote transport; shared memory only if justified

## Recent recorded facts

- 2026-10-16T04:44:01.635Z: Gateway candidate integrated; independent default-environment checks pass — unknown; actor Unknown; revision 080bb12cd007eefec842eef6e81001bcd053ba06
- 2026-10-16T04:28:50.453Z: Actual browser exposes final-sample gap at pause boundary — unknown; actor Unknown; revision Unknown
- 2026-10-16T04:19:26.905Z: Reviewed Newton baseline and targeted repairs integrated — unknown; actor Unknown; revision 78f07c6e709c5a294cb431be4ffe2c60594cea48
- 2026-10-16T04:18:34.592Z: First public two-client command slice green — passed; actor Unknown; revision Unknown
- 2026-10-16T04:13:28.833Z: Repair prescribed warning code and reuse existing joint anchor Sites — unknown; actor Unknown; revision Unknown

## Resources

- Root integration worktree and .venv; owner PM; retained; Integrated default environment, no Newton installed
- API127.0.0.1:8001; owner Gateway developer; retained; Viewer integration; uv parent18104/reload watcher18736 and owned child
- Web127.0.0.1:5174; owner Viewer developer; retained; Preview proxy toAPI8001
- GPU/Viser P0-A processes; owner Newton developer; cleaned; Completed real-engine baseline smoke; exit/ports verified
- GPU P0-E execution reservation; owner Newton developer; retained; Bounded asynchronous extractor verification

## Evidence

- [Realengine findings](./P0-A.md)
- [Review/repair record](./P0-C-review.md)
