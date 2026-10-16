# P2-C — GPU extraction and render scaling

Status: implementation in progress; this is the instance-selection and subscription-visibility checkpoint, not final acceptance.

## Checkpoint

The real registered SyntheticSource fixtures cover 100, 500, 500 moving + 2,000 static, 2,000 and 10,000 moving Bodies. IDs are non-contiguous, frame rows deliberately differ from Manifest order, and static poses never enter periodic frames. These are deterministic kinematic rendering loads, not Newton physics workloads.

Repeated primitives share geometry and material in actual `InstancedMesh` batches. Each packed slot maps to its entity / stream ID, including after subscription membership changes. Body pose diagnostics read the Float32 instance matrix actually submitted to Three, then remove VisualLocal. Assets, planes and Bodies with attached Sites retain the individual path. Debug colliders/origins also preserve that path. Geometry/material resources are released on unmount.

Actual membership controls Body and static visibility in both paths. Attached Sites inherit the visible Body; world Sites check their own scope. R3F raycast results discard invisible ancestors, and pointer handlers check membership before selecting. Instance refs remain stable across selection updates so pointerdown does not reset the live instance count before click.

Validation: production TypeScript/Vite build passed. Four actual-browser E2Es passed in 10.5 s: individual / instanced × JSON / binary, using API 8006 and production preview 5186. They use real mouse clicks and Three ray intersections, verify stream ID 151 against packed instance slot 0 after scope narrowing, exclude hidden objects from picking, verify camera scope departure/reentry, and check device / Site / Joint dependencies and hidden-parent Sites. Receipts are `p2c-evidence/visibility-*.json`.

## Baseline evidence and limits

The unoptimized production viewer recorded six-second RAF windows with independent HTTP Snapshot oracles for every moving Body. SwiftShader was the actual browser renderer; the workstation's RTX inventory does not establish hardware browser rendering. Draw calls were 102 / 502 / 2,502 / 2,002 for 100 / 500 / mixed / 2,000 loads. Corresponding observed FPS p50 was approximately 42.9 / 15.2 / 1.6 / 1.0. These are short descriptive observations on a shared workstation, not scaling guarantees. The 10,000 baseline timed out before a valid correctness/measurement window and has no FPS or acceleration ratio.

Early instance checkpoints verified all 100 / 500 Body matrices with no missing poses and < 1e-4 position/quaternion error; the 500 scene submitted all 191,300 triangles in 5 draw calls. Their source hashes identify the exact intermediate tree; they precede the stable-ref interaction repair and are not final performance results. All comparison runs explicitly disable P2-B sparse deltas and wait for the accepted scope snapshot. A final B-consumer individual control is still required before attributing renderer gains.

## Remaining authorized work

- Add and verify LOD / conservative per-instance frustum culling, then measure the completed renderer with comparable quality and explicit useful-work counts.
- Add real Warp gather before the existing pinned double-buffer asynchronous readback; preserve Manifest body-index to stream-ID mapping in the actual Runner.
- Benchmark CPU / GPU / encode / actual Pipe boundaries, full versus subset and synchronous versus asynchronous paths, with output oracles and timing scopes.
- Run final stress repeats, bounded simplification, applicable checks and independent review.

The full HTTP Snapshot and arbitrary future scope remain full consumers. The actual Runner gathers all declared Manifest Bodies; subset gather is a separate benchmark. Narrow subscriptions still pay the readback cost for all declared Bodies: per-observer GPU pushdown is not implemented and no byte reduction is claimed. Shared memory will be introduced only if actual Pipe profiling establishes that it is a bottleneck.
