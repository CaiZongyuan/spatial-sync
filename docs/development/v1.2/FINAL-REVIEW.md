# Final independent review and repairs

Frozen candidate: `5d6533f67e1fec0cd457d0843eddcafffa74f291`, milestone baseline `633735534aa05376138904582ebb7712a0ea19c9`. Reviewers `/root/final_standards` and `/root/final_spec` did not author the implementation. Both inspected the complete milestone range and real consumers. This candidate is not accepted as complete.

Standards found a mandatory authentication defect: `_role` passes untrusted non-ASCII strings to `hmac.compare_digest`, producing `TypeError`. A raw HTTP bearer header containing byte `E9` returned 500. Valid credentials still worked. Repair acceptance is HTTP 401 and WS 1008 for malformed credentials while preserving operator/viewer behavior.

Spec found three mandatory gaps:

- Replacement startup failure leaves `FAILED` with `error=null`, after reload cancels heartbeat/source tasks. The public reproduction receives only STOPPING. Spec requires FAILED broadcast/details, heartbeat in every state, and restart recovery. Both failed reload and restart need public regressions, honest retained sample/revision, live API/WS and a subsequent successful restart.
- GPU extraction still gathers all Manifest Bodies, followed by Gateway subscription filtering. Spec requires target-body gather before readback. The earlier [pipeline note](P2-C-pipeline-decision.md) does not supersede that requirement. Implement the active-observer interest union while retaining complete HTTP snapshots, scope expansion/reconnect recovery, reliable command ordering and truthful sample identity.
- Diagnostics lack actual Shape count and a producer for Runner heartbeat. Expose `model.shape_count` and a real local/remote heartbeat age that continues while PAUSED and reflects source loss. SyntheticSource must not fabricate a Runner.

Repair ownership: `/root/gateway_repair` owns authentication and failed-start lifecycle in `review-gateway`; `/root/newton` owns source interests, full recovery and diagnostics in `review-source`. Both branch from the frozen candidate. PM will inspect the first source-interest safety example before expansion, integrate complete repaired candidates and refresh affected non-author review plus final gates.

Optional Standards suggestion: move the shared scene registry out of session orchestration while retaining one source/scene compatibility authority. This is a maintenance proposal, not a delivery blocker.

The suggested 45 FPS target is unmet under SwiftShader. No hardware-renderer, WAN, end-to-end freshness or solver-overlap improvement was proved. Those limits remain explicit; they do not excuse the mandatory gaps above.

## Gateway repair integration

`40e1a8efe272f0e845bb73e2604c053a3bfc4811` was merged at `465a152518437051e637a3213212c7f083eb8414`. PM independently inspected the code/tests and ran all five public repair regressions: **5 passed in 0.88 s**, with unraisable warnings treated as errors. The author's complete base gate passed **170 tests, 1 optional skip in 14.26 s**; full Ruff passed. The repair additionally stages startup through initial observer policy, guards source-backed queries after failure, and guarantees WebSocket pump cleanup under ASGI cancellation. See [the repair evidence](FINAL-gateway-repair.md). Complete-candidate independent review and final validation remain pending with the source-interest repair.

## Source and TLS repair review

Source delivery `b21b00c` and TLS delivery `5d7292d` implement the original four findings. The independent Spec reviewer traced the combined boundaries and found no other missing original requirement or scope creep. The Source author executed ten real CPU/CUDA checks without skips, 149 non-TLS base checks, frontend/protocol/build checks and two actual diagnostics browser paths. The TLS author executed 55 network/transport checks with two optional Newton skips. These are per-slice results, not a claim that the combined final head passed.

PM found a remote paused hotloop that revalidated an unchanged complete snapshot every two milliseconds. `5d7292d` guards all observations by seq, validates a newer control snapshot once, and retains late-query barriers. The Spec reviewer inspected this incremental fix and found no new issue.

One new mandatory Spec regression remains open at this checkpoint: section6:674 requires ordinary Pose HTTP to use the published read-only snapshot without GPU synchronization. The source repair routed Pose through explicit full capture; the prior stronger `pose.seq > full.seq` test was an unrequested oracle and is superseded. The narrow repair keeps explicit snapshot/subscription capture while serving Pose from one complete cache with its original seq/simTime/staleMs. Public capture-call stability and metadata regressions are assigned to the original Source author. The specification is unchanged.
