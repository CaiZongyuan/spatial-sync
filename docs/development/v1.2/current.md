# Newton Spatial Sync — current checkpoint

Snapshot: 2026-10-16T15:12:12.920Z
Task status (PM supplied): completed

## Goal and scope

> Implement and locally integrate specification v1.2 in phase order; reserve Phase 3 interfaces and condition Phase 2 optimizations on evidence

Platform: desktop-web; mobile included: false

The generator does not infer completion, authorization, or merge readiness from CI.

## Manual checkpoint

**revision**

> bf85f35315302d7760a1603936c539aa591fd169 plus Chinese README and shutdown documentation

**authorization**

> User requested implementation according to the v1.2 specification. Local development, isolated issue branches, validation and integration are authorized. No remote or publication is configured.

**next**

> Services closed at the user request. Use README.zh-CN.md to restart the workbench or the independent Newton ViewerViser reference.

**roles**

> [
>   {
>     "role": "PM/integration and retained demo",
>     "owner": "/root",
>     "ticket": "v1.2"
>   },
>   {
>     "role": "Source author (parked)",
>     "owner": "/root/newton",
>     "ticket": "FINAL-S"
>   },
>   {
>     "role": "Gateway/TLS author (parked)",
>     "owner": "/root/gateway_repair",
>     "ticket": "FINAL-G/FINAL-R"
>   },
>   {
>     "role": "Independent Spec reviewer (complete)",
>     "owner": "/root/final_spec",
>     "ticket": "v1.2"
>   },
>   {
>     "role": "Independent Standards reviewer (complete)",
>     "owner": "/root/final_standards_refresh",
>     "ticket": "v1.2"
>   }
> ]

## Tickets

- P0-A: Newton baseline — integrated
- P0-B: Protocol schema — integrated
- P0-C: Gateway and SyntheticSource — integrated
- P0-D: Web viewer — integrated
- P0-E: Newton source integration — integrated
- P1-A: Articulated robot — integrated
- P1-B: USD scene and visual binding — integrated
- P1-C: Workbench reliability — integrated
- P2-A: Binary transform stream — integrated
- P2-B: Scopes, cumulative deltas and keyframes — integrated
- P2-C: GPU extraction and render scaling — integrated
- P2-D: Remote Runner transport — integrated
- FINAL-G: Authentication and failed-replacement lifecycle repairs — integrated
- FINAL-S: Subscription-driven GPU targets and complete diagnostics — integrated
- FINAL-R: TLS interest/full-snapshot and heartbeat forwarding — integrated

## Recent recorded facts

- 2026-10-16T15:12:12.920Z: User requested shutdown and Chinese README — passed; actor Unknown; revision Unknown
- 2026-10-16T14:54:43.667Z: Local product integrated, independently reviewed and accepted — passed; actor Unknown; revision 389687587435150665d10b3e0d726943c661f15d
- 2026-10-16T14:16:22.263Z: Independent Spec review rejects ordinary Pose GPU capture — failed; actor Unknown; revision sourceb21b00c +remote5d7292d
- 2026-10-16T13:46:24.420Z: Actual CPU/CUDA interest and recovery journeys passed — passed; actor Unknown; revision review-source working candidate after5c44309
- 2026-10-16T13:28:36.246Z: Actual CPU source slice green; TLS split into available author capacity — passed; actor Unknown; revision 91942f79dbec3c822975e13fdd313c6ea9e82e24 source branch

## Resources

- Retained B preview8000/5173; owner PM; unknown; B API PID36204 exec8139 retained for unknown-owner paused Synthetic session-b469c0e4cf99. B production bundle copied to.scratch/preview-b-9a93c83, preview5173 PID41600 exec69500. Dev Vite13676 stopped; temporary5191 verification server stopped. Latest post-interruption inventory:8000/5173 listeners absent; prior authority no longer observable. PM issued no stop/reset/close for this unknown session; cause of process exit not established.
- Old root API8000 and FR3; owner PM; cleaned; Owned session STOPPED and API stopped before integration
- P1-C API8002/web5184; owner Viewer developer; cleaned; Latest listener inventory confirms both absent.
- OldAPI8001/web5174; owner PM/developers; cleaned; Exact owned processes stopped and listeners verified gone
- P2-A8005/5185 and benchmark children; owner Newton developer; cleaned; Owner verified stopped and heavy reservation released
- P2-C CPU/network/GPU execution; owner Viewer developer; cleaned; Author execution finished; developer ports8006/5186 absent. Repair runtime window will be announced separately.
- Foreign5175; owner External process; unknown; Outside task ownership; preserved
- B API8007/web5187; owner Protocol developer; cleaned; All E2E sessions deleted; author verified both listeners absent
- Unknown-owner paused Synthetic session-b469c0e4cf99; owner Unconfirmed; active C/D developers deny creating it and PM-owned E2E sessions are stopped; unknown; Preserve PAUSED epoch0 seq268/simTime8.895833333334; no close/reset. Final complete preview will use separate8009/5189 after integration. Latest post-interruption inventory:8000/5173 listeners absent; prior authority no longer observable. PM issued no stop/reset/close for this unknown session; cause of process exit not established.
- Final candidate API8009/web5189; owner PM; cleaned; Prior API/web listeners absent at post-interruption inventory. Root will relaunch fresh after final integration; foreign5175 remains untouched.
- Final integrated production preview and paused robot; owner PM /root; cleaned; API8009PID23700exec38910;web5189PID1904exec99530;robotSession289bc03f6231Runner28972PAUSEDepoch1seq1937simTime0;ChromeTab286453117marked deliverable. Onlytheseownedresourcesretained; no test sessions remain active. User requested shutdown. DELETE completed for the owned demo, Runner28972 exited, API23700 and preview1904 stopped. Ports8009/5189/8080/8766 were absent; foreign5175 was preserved. The previously owned browser tab was already absent.

## Evidence

- [Chinese usage guide](../../../README.zh-CN.md)
- [Finalcheckmatrixandpreview](./FINAL-VALIDATION.md)
- [Independentreview](./FINAL-REVIEW.md)
- [Retaineddemo](./final-preview.png)
- [Repair review](./FINAL-REVIEW.md)
- [Real engine findings](./P0-A.md)
- [Review and repair record](./P0-C-review.md)
- [Browser evidence](./P0-D.md)
- [Independentreviewandrepairrecord](./FINAL-REVIEW.md)
- [Ownershipandfinaldelivery](./TRACKER.md)
- [Independent review and repair record](./FINAL-REVIEW.md)
- [Review and evidence boundaries](./P2-A-review.md)
- [Independent review and cross-issue condition](./P2-B-review.md)
- [Final integrated check matrix](./FINAL-VALIDATION.md)
- [Actualtransport/runtimeevidence](./FINAL-VALIDATION.md)
- [Finalbrowsercoverage](./FINAL-VALIDATION.md)
- [FinalsceneandInspector](./final-preview.png)
