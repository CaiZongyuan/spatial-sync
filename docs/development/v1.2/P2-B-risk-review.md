# P2-B early non-author checks

PM `/root` reviewed the real-source sequence oracle at `8f33d1af6c1a3ca138cd62dc99423b0702c0cc80` and the in-progress scope/delta helpers. This is an early risk check, not the complete issue review or an integrated passing result.

The high-water regression is accepted: a supported 1 Hz SyntheticSource genuinely advances while an older control result is delayed. HTTP or subscription observes seq at least 3; the delayed control keeps seq 1 and simTime 0; replacement must allocate strictly above the observed old sequence. This distinguishes global allocation from honest sample metadata without fabricating source frames. The separate high-water allocator is appropriate.

The cumulative-delta helper's smallest examples are appropriate: drop an intermediate update, return a Body to its keyframe pose, add then remove a new member, and require a new baseline across epoch/revision. These must also be exercised through actual WS/PoseBuffer consumers. Scope tests conservatively retain a shape overlapping a clipping plane despite its outside origin, and retain unknown mesh bounds.

Two consumer risks were sent to the author for concrete tests:

- The draft pump discards a reliable frame when its subscription ID differs from the server's current ID. A queued command frame can then disappear while its session.state is still sent, before the new scope snapshot. FIFO delivery of old-scope frame/state followed by new acknowledgement/snapshot may preserve the contract more simply. Test a blocked pump, another observer's command and scope replacement; do not infer safety from eventual recovery alone.
- Leaving a subscription must affect exact-pose readers as well as rendering. A cached old Body pose must not be returned by Inspector with the current global latestSample seq. Test removal with distinct poses/sequences, including Site/Joint dependencies, and show an unavailable/outside-scope state rather than relabeling an old sample.

The author also owns a persistent LF checkout rule for generated protocol artifacts, preserving the byte-determinism oracle, and graceful optional simulation-test collection without Warp. The Three-to-physics plane mapping `(nx, -nz, ny, d)` needs an independent axis test.
