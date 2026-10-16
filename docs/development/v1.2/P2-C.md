# P2-C — GPU extraction and render scaling

Status: implementation in progress; this is the instance-selection and subscription-visibility checkpoint, not final acceptance.

LOD/culling checkpoint: actual instance bounds include VisualLocal and full primitive extents. Spheres intersecting the frustum remain eligible even when their origins lie outside. Curved primitives below a 12 CSS-pixel projected bounding-sphere diameter use lower tessellation; selected primitives retain full detail. `quality=full` disables tessellation changes for comparable controls. Pixel ratio remains capped at 1.75 and shadows remain disabled. Both detail levels release geometry on unload.

The real LOD browser test passed in 4.1 s: all 500 Bodies remained drawn at the same full/adaptive camera, submitted triangles decreased, a box whose center was outside the left plane remained submitted, an away camera culled all instances without altering the 500 protocol members, and returning restored all 500. The original four selection/scope tests also passed on this renderer. `p2c-evidence/lod-culling.json` records actual Three counts. Large scene trees index parent relationships once and initially collapse their groups; search still finds any declared entity. The old arbitrary 30 m OrbitControls maximum was removed because it clamped the 10k scene's complete-bounds fit distance and excluded Bodies.

`adaptive-checkpoint-10000.json` verifies all 10,000 actual instance matrices against a complete independent HTTP Snapshot, no missing Bodies, and actual submitted instance/triangle counts. A six-second SwiftShader window produced 26 RAF samples, FPS p50 4.1 and apply p95 9.2 ms. The 45 FPS suggested target was not met. The explicit/default browser renderer probe already reports UNMASKED_RENDERER as SwiftShader; hardware-browser performance is unproved. The old 10k baseline still has no valid ratio.

## Gather and IPC checkpoint

The actual Runner exports its Manifest before constructing the extractor, resolves every declared Body's `debug.newtonBodyIndex` and `streamId`, and gathers in stable entity-key order. Compact array rows are zipped to those stream IDs; row number is never used as identity. Invalid-pose warnings map back to original model indices. Full HTTP/new-scope recovery remains complete.

On CUDA, a real Warp kernel gathers those indices into the slot-owned device staging array on the simulation stream. An event orders the independent copy stream's single pinned-host transfer; the existing worker waits for completion, copies/filter-normalizes the private sample, publishes, and only then releases the double-buffer slot. CPU uses bulk `np.take` into the private slot. Separate CUDA-event gather/DtoH and host enqueue/copy/filter/latency metrics are additive stats fields; the prior readback-mode values stay compatible.

Ten CPU/CUDA tests passed in 2.71 s, including non-contiguous reordered `[5,1,7]` outputs, state mutation while callbacks hold both slots, a third capture dropping safely, invalid targets, and Manifest identity mapping. Three actual spawned Runner CPU/CUDA/crash tests passed in 13.99 s. `gather-runner-cuda.json` records the real CUDA Runner's pause/step/reset, stable reset output and child/thread cleanup, including `gatherMode=manifest-targets-full-recovery` stats.

`extraction-pipe.json` contains 3 repeats × 40 checked samples after 10 warmups for 100 / 500 / 2,000 / 10,000, full / quarter and CPU / CUDA. Baseline is a real synchronous bulk `body_q.numpy()` followed by CPU gather/filter; candidate is the actual asynchronous extractor and every Future is awaited. All outputs match independent source-array/index oracles exactly. Quarter experiments reduce transferred pose bytes from 280,000 to 70,000 for 10k, solely in that explicit subset experiment. Full production targets have no claimed byte reduction.

The 10k full CUDA baseline end-to-end p50 was 0.948 / 1.245 / 1.257 ms; asynchronous gather was 1.066 / 1.967 / 1.931 ms. Single-observation latency did **not** improve. The test does not measure solver/readback overlap speedup. CUDA kernel p50 was 0.035 / 0.023 / 0.023 ms and DtoH p50 0.034 / 0.174 / 0.174 ms; the repeat variation is retained. Device event durations are not summed with host wall times.

The Pipe experiment uses actual spawned processes and the production FrameCodec, checks every decoded ID/pose, and reaps every child. At 10k full, blocking `send_bytes` p95 was 0.295 / 0.340 / 0.360 ms; rows through encode, receive, decode, independent oracle and acknowledgement p95 was 23.655 / 25.052 / 25.246 ms. The latter is a conservative roundtrip with validation, not pure Pipe transfer. Receive wait includes producer wait. This evidence does not establish Pipe as a bottleneck, so the existing latest-only threaded Pipe remains. No shared-memory channel was added.

Change-impact: the extractor's only production caller is Runner; its optional index argument retains the old all-Body default. The source adapter and Gateway keep the existing full-frame contract, while the existing stats dictionary carries additive metrics to local and remote consumers. Scene tree and diagnostic interfaces are web-local. Neither protocol/schema generation nor the public HTTP/WS format changed. A final full-consumer verification refresh remains before freeze.

## Checkpoint

The real registered SyntheticSource fixtures cover 100, 500, 500 moving + 2,000 static, 2,000 and 10,000 moving Bodies. IDs are non-contiguous, frame rows deliberately differ from Manifest order, and static poses never enter periodic frames. These are deterministic kinematic rendering loads, not Newton physics workloads.

Repeated primitives share geometry and material in actual `InstancedMesh` batches. Each packed slot maps to its entity / stream ID, including after subscription membership changes. Body pose diagnostics read the Float32 instance matrix actually submitted to Three, then remove VisualLocal. Assets, planes and Bodies with attached Sites retain the individual path. Debug colliders/origins also preserve that path. Geometry/material resources are released on unmount.

Actual membership controls Body and static visibility in both paths. Attached Sites inherit the visible Body; world Sites check their own scope. R3F raycast results discard invisible ancestors, and pointer handlers check membership before selecting. Instance refs remain stable across selection updates so pointerdown does not reset the live instance count before click.

Validation: production TypeScript/Vite build passed. Four actual-browser E2Es passed in 10.5 s: individual / instanced × JSON / binary, using API 8006 and production preview 5186. They use real mouse clicks and Three ray intersections, verify stream ID 151 against packed instance slot 0 after scope narrowing, exclude hidden objects from picking, verify camera scope departure/reentry, and check device / Site / Joint dependencies and hidden-parent Sites. Receipts are `p2c-evidence/visibility-*.json`.

## Baseline evidence and limits

The unoptimized production viewer recorded six-second RAF windows with independent HTTP Snapshot oracles for every moving Body. SwiftShader was the actual browser renderer; the workstation's RTX inventory does not establish hardware browser rendering. Draw calls were 102 / 502 / 2,502 / 2,002 for 100 / 500 / mixed / 2,000 loads. Corresponding observed FPS p50 was approximately 42.9 / 15.2 / 1.6 / 1.0. These are short descriptive observations on a shared workstation, not scaling guarantees. The 10,000 baseline timed out before a valid correctness/measurement window and has no FPS or acceleration ratio.

Early instance checkpoints verified all 100 / 500 Body matrices with no missing poses and < 1e-4 position/quaternion error; the 500 scene submitted all 191,300 triangles in 5 draw calls. Their source hashes identify the exact intermediate tree; they precede the stable-ref interaction repair and are not final performance results. All comparison runs explicitly disable P2-B sparse deltas and wait for the accepted scope snapshot. A final B-consumer individual control is still required before attributing renderer gains.

## Remaining authorized work

- Measure the completed renderer with comparable quality and explicit useful-work counts.
- Run final stress repeats, bounded simplification, applicable checks and independent review.

The full HTTP Snapshot and arbitrary future scope remain full consumers. The actual Runner gathers all declared Manifest Bodies; subset gather is a separate benchmark. Narrow subscriptions still pay the readback cost for all declared Bodies: per-observer GPU pushdown is not implemented and no byte reduction is claimed. Shared memory will be introduced only if actual Pipe profiling establishes that it is a bottleneck.
