# P2-C extraction and full-snapshot boundary

The specification's section 7.2 calls for GPU gather by target body indices before bulk readback, and P2-C explicitly requires Warp gather plus asynchronous-readback benchmarks. The existing SpatialSource.latest_frame contract supplies a complete current sample synchronously to HTTP snapshots, pose queries and any newly subscribed scope.

PM accepted the following implementation boundary after the scaling author proposed it and the non-author Newton developer gave a bounded read-only architecture opinion. Runner derives target Newton body indices and stream IDs from the Manifest, gathers those actual indices on GPU, then performs the staged/pinned readback. Compact slot positions must never become stream IDs. CPU/CUDA tests must cover non-contiguous and reordered targets. Subset gather has separate correctness and timing evidence.

The full set of declared Bodies remains an active source consumer because HTTP and recovery require it. Per-observer entities/device/viewport filtering is implemented in the Gateway. When the Manifest declares every Newton Body, gather reads the complete Body set and does not reduce bytes. Multiple narrow subscriptions still pay the cost of reading every declared Body. This is a stated performance limit; no subscriber-dependent GPU pushdown or readback-byte reduction is claimed.

Changing the source target to only the current observer union would require additional full-snapshot and recovery semantics: an HTTP query or a new viewport may need a Body that has stopped being read. An asynchronous full-snapshot operation and target generations would change the source contract. No unused API or duplicate full-plus-subset readback is introduced just to simulate this optimization. A future pushdown change needs a measured bottleneck and a complete consumer/recovery impact assessment.

The read-only consultation did not establish implementation correctness. C still owes actual gather/readback/Pipe measurements, the live Runner path, scoped renderer/raycast behavior, bounded simplification and non-author final review.
