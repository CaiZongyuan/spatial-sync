# v1.2 acceptance and verification plan

Authority: `docs/spec/NEWTON_SPATIAL_SYNC_SPEC_V1.2.md`. This plan does not replace or narrow the specification. Status is pending until linked execution evidence proves the behavior. GPU/Newton/import/load/visual checks are non-gating, as section 13 requires; their observed limitations must still be reported.

| Boundary / requirement | Discriminating public evidence | Owner | Current evidence / status |
|---|---|---|---|
| Python/pnpm clean startup, CPU-only SyntheticSource | README commands from integrated checkout, HTTP health and rendered browser scene | Gateway / Viewer | [Launch guide](../VERIFICATION.md); local root startup and health verified; final-head refresh pending |
| Pydantic sole schema authority; generated TS matches | Regenerate without diff; Python samples accepted by TS runtime validator | Protocol | [P0-B](P0-B.md), [P2-A review](P2-A-review.md); schema and consumer gates pass |
| Labels and entityKey stability, missing/duplicate/invalid labels | Actual importer smoke plus pure validation tests, including strict mode | Newton / Protocol | [P0-A](P0-A.md), [P1-A](P1-A.md); actual provenance/strict-mode coverage |
| World body poses and local visual composition | Known rotated body/local-offset fixture; no logical parent transform inheritance | Protocol / Viewer | [P0-D review](P0-D-review.md), [P1-B](P1-B.md); known transformed visual/browser checks |
| Newton primitive dimensions and Z axes | Bounds/orientation tests for box, sphere, capsule, cylinder, cone, plane | Viewer | [P0-D](P0-D.md); all six cases covered |
| Site origin differs from Body origin | Nonzero local anchor independent world-pose calculation and browser inspector | Newton / Viewer | [P1-A review](P1-A-review.md); independent TCP golden and root HTTP/UI same-sample check |
| Joint error excludes legal freedom | Prismatic axial translation yields zero; transverse translation fails; revolute/ball/free semantics | Protocol / Viewer | [P0-B](P0-B.md), [P0-D](P0-D.md); pure convention/constraint coverage |
| Manifest/ready revision agreement | Race/reload causes refetch and resubscribe, then new complete snapshot | Gateway / Viewer | [P0-D](P0-D.md), [P1-C review](P1-C-review.md), [P2-A](P2-A.md); real replacement recovery |
| seq stays monotonic, epoch advances on reset | Pause, step, reset, repeat/reset, late periodic frame; all consumers reject old data | Gateway / Viewer | [P0-C](P0-C.md); P2-B adds real delayed-control high-water regression, not yet integrated |
| Commands serialize and deduplicate (256 requestIds) | Concurrent two-client commands and duplicate requestId produce one application and original ack | Gateway | [P0-C](P0-C.md), [P0-E](P0-E.md); source/pump and multiple-observer coverage |
| Command frame → ack → state | Resume/step/reset message traces, requester and second observer; stale periodic frame cannot win | Gateway | [P0-C-review](P0-C-review.md), [P2-A](P2-A.md); actual JSON/binary reliable ordering |
| Pause keeps heartbeat; step/resume show immediately | Public WS sequence and browser journey after long pause, with changing displayed seq/pose | Gateway / Viewer | [P0-D](P0-D.md), [P1-C](P1-C.md); real browser pause/foreground/recovery |
| Clock interpolation and visibility reset | Sliding-min offset, rate-derived delay, shortest quaternion path, large-gap jump and foreground reset tests | Viewer | [P0-D](P0-D.md), [P2-A](P2-A.md); shared JSON/binary buffer tests |
| Slow clients cannot block solver/other clients | Bounded reliable overflow disconnect and >2s send timeout using actual pump entrypoint | Gateway | [P0-C](P0-C.md), [P2-A review](P2-A-review.md); sole pump overflow/timeout isolation |
| One WebSocket sender per connection | Pump ownership inspection plus concurrent command/frame test | Gateway / Review | [P0-C-review](P0-C-review.md), [P2-A review](P2-A-review.md); traced writers and actual entrypoint tests |
| New/reconnected clients get snapshot and entity state | Two public WS sessions, reconnect, cached unknown-schema JSON shown in Inspector | Gateway / Viewer | [P0-D](P0-D.md), [P1-C](P1-C.md); Synthetic entity state and real reconnect |
| One spawned Runner per session and isolated crash | Two observers inspect same process ID; terminate runner; API health remains live and FAILED can restart | Newton / Gateway | [P0-E review](P0-E-review.md), [P1-C review](P1-C-review.md); shared PID/crash/API health/restart |
| Two IPC pipes, latest-only periodic frames, reliable command frames | Transport tests and actual spawned Newton session command trace | Newton | [P0-E](P0-E.md); actual spawned source command trace |
| CPU/CUDA extraction, safe buffer lifetime, finite-pose retention | Real CPU/GPU FK/step/reset, no half-copy stress and abnormal-pose behavior | Newton | [P0-E](P0-E.md); staged double-buffer baseline; P2-C gather change pending |
| Robot MJCF/URDF and solver choices | Actual 7-DOF import, MuJoCo/Featherstone smoke and explicit support/compatibility result | Newton | [P1-A](P1-A.md); MuJoCo CPU/CUDA accepted, Featherstone full contact unsupported |
| USD identity and axis handling | Real add_usd path maps, writable labels, world/body/site output and scene render | Newton / Viewer | [P1-B](P1-B.md); actual Y-up golden body/mesh bounds and browser |
| Mesh GLB and bindings/fallback | MIME/cache/hash API; asset axis/scale/localPose tests; 404 leaves visible selectable placeholder | Newton / Viewer / Gateway | [P1-B](P1-B.md), [P1-C](P1-C.md); API hashes/cache/whitelist and complete fallback groups |
| Lifecycle/reload/restart/close cleanup | Same session new revision; old runner stopped; source resources stop on DELETE and shutdown | Newton / Gateway | [P0-E](P0-E.md), [P1-C](P1-C.md); actual process lifecycle and browser acceptance |
| Roles/origin/asset whitelist | Local default only; reject invalid remote credentials/roles/origins, unregistered assets and arbitrary paths | Gateway | [P0-C](P0-C.md), [P1-B](P1-B.md); credential/role/origin and registered asset tests |
| Diagnostics reflect actual work | Source step/RTF/readback/publish + gateway fanout/drops + browser FPS/draw/apply values observed | All | [P1-C](P1-C.md); actual frame recorder/logs; large-scene P2-C measurements pending |
| CI under five-minute target without Newton/GPU | Full contract/unit/gateway/browser gate result and measured duration | PM | Root local 115 Python / 55 Web / 6 TS / build / 2 E2E passed at integrated P2-A; no remote CI configured; final-head gate pending |
| Performance evidence before optimization | Correct complete outputs at 100/500/2k/10k; hardware/version/scene hash and measurement scope | PM / Developers | [P2-A](P2-A.md); correct codec/network/browser comparisons; P2-C100/500/2k/10k render/readback pending |
| P2 binary stream | JSON/binary equivalent known poses, malformed headers/count/revision, real browser decode and benchmark | Pending P0/P1 | [P2-A review](P2-A-review.md); integrated and independently checked |
| P2 subscriptions/increments/keyframes | Device/entity/viewport scopes, baseline-aware deltas and reconnect recovery under dropped frames | Pending P0/P1 | P2-B in progress; real-source HWM counterexample independently checked |
| P2 gather/instances/LOD/culling | Selection maps, extraction/render profiling and 10k stress fixture | Pending P0/P1 | P2-C dispatched on isolated4bb6217 baseline; actual fixtures/benchmarks pending |
| P2 remote transport | Same public API with remote source; receive-clock metadata and lifecycle/failure tests | Pending P0/P1 | P2-D pending developer capacity; typed safe transport and receive-clock acceptance required |
| Phase 3 extension seam | Gateway depends only on SpatialSource; event envelope remains payload-agnostic | Review | [SpatialSource](../../../apps/api/app/sources/base.py) and payload-agnostic EntityState; final Spec review pending |

## Early risk review

The first actionable command ordering and interpolation cases receive a bounded non-author check before later patterns expand. Final Standards and Spec reviewers cover the complete integrated candidate from `633735534aa05376138904582ebb7712a0ea19c9`, including generated consumers and new files. Author self-review is not independent review. No external CI or publication can be claimed because the repository has no remote origin.
