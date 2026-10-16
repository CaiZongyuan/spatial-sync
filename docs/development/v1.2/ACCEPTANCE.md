# v1.2 acceptance and verification plan

Authority: `docs/spec/NEWTON_SPATIAL_SYNC_SPEC_V1.2.md`. This plan does not replace or narrow the specification. Status is pending until linked execution evidence proves the behavior. GPU/Newton/import/load/visual checks are non-gating, as section 13 requires; their observed limitations must still be reported.

| Boundary / requirement | Discriminating public evidence | Owner | Initial status |
|---|---|---|---|
| Python/pnpm clean startup, CPU-only SyntheticSource | README commands from integrated checkout, HTTP health and rendered browser scene | Gateway / Viewer | Pending |
| Pydantic sole schema authority; generated TS matches | Regenerate without diff; Python samples accepted by TS runtime validator | Protocol | Pending |
| Labels and entityKey stability, missing/duplicate/invalid labels | Actual importer smoke plus pure validation tests, including strict mode | Newton / Protocol | Pending |
| World body poses and local visual composition | Known rotated body/local-offset fixture; no logical parent transform inheritance | Protocol / Viewer | Pending |
| Newton primitive dimensions and Z axes | Bounds/orientation tests for box, sphere, capsule, cylinder, cone, plane | Viewer | Pending |
| Site origin differs from Body origin | Nonzero local anchor independent world-pose calculation and browser inspector | Newton / Viewer | Pending |
| Joint error excludes legal freedom | Prismatic axial translation yields zero; transverse translation fails; revolute/ball/free semantics | Protocol / Viewer | Pending |
| Manifest/ready revision agreement | Race/reload causes refetch and resubscribe, then new complete snapshot | Gateway / Viewer | Pending |
| seq stays monotonic, epoch advances on reset | Pause, step, reset, repeat/reset, late periodic frame; all consumers reject old data | Gateway / Viewer | Pending |
| Commands serialize and deduplicate (256 requestIds) | Concurrent two-client commands and duplicate requestId produce one application and original ack | Gateway | Pending |
| Command frame → ack → state | Resume/step/reset message traces, requester and second observer; stale periodic frame cannot win | Gateway | Pending |
| Pause keeps heartbeat; step/resume show immediately | Public WS sequence and browser journey after long pause, with changing displayed seq/pose | Gateway / Viewer | Pending |
| Clock interpolation and visibility reset | Sliding-min offset, rate-derived delay, shortest quaternion path, large-gap jump and foreground reset tests | Viewer | Pending |
| Slow clients cannot block solver/other clients | Bounded reliable overflow disconnect and >2s send timeout using actual pump entrypoint | Gateway | Pending |
| One WebSocket sender per connection | Pump ownership inspection plus concurrent command/frame test | Gateway / Review | Pending |
| New/reconnected clients get snapshot and entity state | Two public WS sessions, reconnect, cached unknown-schema JSON shown in Inspector | Gateway / Viewer | Pending |
| One spawned Runner per session and isolated crash | Two observers inspect same process ID; terminate runner; API health remains live and FAILED can restart | Newton / Gateway | Pending |
| Two IPC pipes, latest-only periodic frames, reliable command frames | Transport tests and actual spawned Newton session command trace | Newton | Pending |
| CPU/CUDA extraction, safe buffer lifetime, finite-pose retention | Real CPU/GPU FK/step/reset, no half-copy stress and abnormal-pose behavior | Newton | Pending |
| Robot MJCF/URDF and solver choices | Actual 7-DOF import, MuJoCo/Featherstone smoke and explicit support/compatibility result | Newton | Pending |
| USD identity and axis handling | Real add_usd path maps, writable labels, world/body/site output and scene render | Newton / Viewer | Pending |
| Mesh GLB and bindings/fallback | MIME/cache/hash API; asset axis/scale/localPose tests; 404 leaves visible selectable placeholder | Newton / Viewer / Gateway | Pending |
| Lifecycle/reload/restart/close cleanup | Same session new revision; old runner stopped; source resources stop on DELETE and shutdown | Newton / Gateway | Pending |
| Roles/origin/asset whitelist | Local default only; reject invalid remote credentials/roles/origins, unregistered assets and arbitrary paths | Gateway | Pending |
| Diagnostics reflect actual work | Source step/RTF/readback/publish + gateway fanout/drops + browser FPS/draw/apply values observed | All | Pending |
| CI under five-minute target without Newton/GPU | Full contract/unit/gateway/browser gate result and measured duration | PM | Pending |
| Performance evidence before optimization | Correct complete outputs at 100/500/2k/10k; hardware/version/scene hash and measurement scope | PM / Developers | Pending |
| P2 binary stream | JSON/binary equivalent known poses, malformed headers/count/revision, real browser decode and benchmark | Pending P0/P1 | Pending |
| P2 subscriptions/increments/keyframes | Device/entity/viewport scopes, baseline-aware deltas and reconnect recovery under dropped frames | Pending P0/P1 | Pending |
| P2 gather/instances/LOD/culling | Selection maps, extraction/render profiling and 10k stress fixture | Pending P0/P1 | Pending |
| P2 remote transport | Same public API with remote source; receive-clock metadata and lifecycle/failure tests | Pending P0/P1 | Pending |
| Phase 3 extension seam | Gateway depends only on SpatialSource; event envelope remains payload-agnostic | Review | Pending |

## Early risk review

The first actionable command ordering and interpolation cases receive a bounded non-author check before later patterns expand. Final Standards and Spec reviewers cover the complete integrated candidate from `633735534aa05376138904582ebb7712a0ea19c9`, including generated consumers and new files. Author self-review is not independent review. No external CI or publication can be claimed because the repository has no remote origin.
