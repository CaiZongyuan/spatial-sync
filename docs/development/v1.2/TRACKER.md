# v1.2 delivery tracker

Authority: [Technical specification](../../spec/NEWTON_SPATIAL_SYNC_SPEC_V1.2.md). Baseline633735534aa05376138904582ebb7712a0ea19c9. Delivery target: local integrated master. Tested product candidate389687587435150665d10b3e0d726943c661f15d; later delivery changes are documentation/evidence only. User authorized development against the specification. Phase3 is interfaces only. No remote origin, external CI or deployment is configured.

**Local implementation and acceptance are complete.** Both independent review axes have no unresolved mandatory finding. [Final gate](FINAL-VALIDATION.md), [review](FINAL-REVIEW.md), [simplification survey](SIMPLIFICATION.md).

| Issue | Author / branch | Delivery |
| --- | --- | --- |
| P0-A Newton baseline | Newton / issue/p0-a-newton | Integrated: realCPU/CUDA/FK/import/Site and labels |
| P0-B Protocol | Protocol / issue/p0-b-protocol | Integrated: Pydantic authority, generatedTS/schema/contracts |
| P0-C Gateway/Synthetic | Protocol / issue/p0-c-gateway | Integrated: commands/order/dedupe/heartbeat/lifecycle/CPU-only source |
| P0-D Viewer | Viewer / issue/p0-d-viewer | Integrated: actual pose/Site/Inspector/control/recovery |
| P0-E Newton integration | Newton / issue/p0-e-newton-source | Integrated: single spawnedRunner,dualPipe,crash/restart/cleanup |
| P1-A Robot | Newton / issue/p1-a-robot | Integrated d60804f: FR3/MuJoCoCPU+CUDA/TCP; Featherstone contact limitation recorded |
| P1-B USD/assets | Protocol / issue/p1-b-assets | Integrated aadf7d6: actualY-upUSD,hashedGLB/assets,exclusive fallback groups |
| P1-C Reliability | Viewer / issue/p1-c-reliability | Integrated fc6bf49: observerpolicy/failure/restart/fallback/logs |
| P2-A Binary | Newton / issue/p2-a-binary | Integrated ec32d8a: actualmixedformat controls/recovery and comparable codec evidence |
| P2-B Subscriptions | Protocol / issue/p2-b-subscriptions | Integrated930aaca: scopes/cumulative deltas/keyframes; actualvisibility/raycast closed byC |
| P2-C Scaling | Viewer / issue/p2-c-scaling | Integrated0cf3e25: stress100/500/2k/10k/mixed,instances/LOD/culling,gather/lifetime and honest profiling |
| P2-D Remote | Newton / issue/p2-d-remote | Integrated15c2bcc: authenticatedboundedTLS,receiptclock,scene registry and querydeadline recovery |
| FINAL-G Auth/lifecycle | gateway_repair / issue/v1.2-gateway-review | Integrated40e1a8e via465a152: stagedstartup/error/HB/recovery/WS ownership |
| FINAL-S GPU interests/diagnostics/Pose | Newton / issue/v1.2-source-review | Integratedba48bc5: actualobserverunionGPUreadback,fullrecovery,realShape/HB,cache-onlyPose |
| FINAL-R TLS interests/heartbeat | gateway_repair / issue/v1.2-remote-review | Integrated5d7292d: strictcapture/barriers,readonlydeadlines,realageforwarding,shutdownownership |

## Final evidence

Root204Python/2optional skips in18.32s ran withoutNewton/Warp. Root64Web/6TS/generation/build/fullRuff passed. New actualNewtonCPU TLS narrow/full/heartbeat/cleanup test passed separately in8.08s. All15realChromium paths passed in1.9min with optionalflags enabled, including realUSD/robot/reliability/diagnostics. Source author10CPU/CUDA checks plus2post-Pose CPU/CUDA checks are retained with exact semantic-delta coverage; do not sum per-slice counts into the204rootgate.

Standards `/root/final_standards_refresh` and Spec `/root/final_spec` are independent non-authors. The new Pose regression and PM-found repeated-snapshot hotloop were repaired and re-reviewed. Optional shared scene-registry extraction is deferred; required distinctions remain. Authors/reviewers are parked, no implementation work remains in their worktrees.

## Retained resources and limits

Final production preview http://127.0.0.1:5189, API8009 PID23700/exec38910, webPID1904/exec99530. PM-owned paused/resetrobot session-289bc03f6231, Runner28972,epoch1seq1937simTime0 withTCPselected. Browser tab286453117 marked deliverable. No later tests should share this active manual session. Developer8008/5188 and testchildren were cleaned. Prior8000/5173/8009/5189 processes were absent after interruption; PM did not reset/close or fabricate the earlier unknown-owner authority. Foreign5175PID5676 is untouched.

Suggested45FPS remains unmet in the measured SwiftShader large scenes (two valid10k runs near4.03FPS). No new variable-target total speedup,solveroverlap,WAN,hardware-renderer or end-to-end freshness claim. ExternalCI was not run; the specification's externalCI checkbox remains open while equivalent local gates are complete. Offline timeline interactive visual QA remains unverified after the earlier fileURL rejection; static JSON/links are validated without circumventing that restriction.
