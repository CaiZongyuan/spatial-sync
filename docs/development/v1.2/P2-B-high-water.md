# Observed sequence watermark audit

Baseline: `b76a782` plus P2-A source checkpoint `4b22d4b`.
Public regression: `tests/gateway/test_sequence_watermark.py`.

A real SyntheticSource publishes at the supported 1 Hz rate. Its
`set_realtime_factor` command applies at seq 1, simTime 0; the test delays only
that control reply while the unchanged source loop continues publishing through
seq 3. The gateway monitor skips its ticks while the command lock is held.

One case reads seq 3 from the real HTTP snapshot endpoint. The other queues an
accepted subscription before a reload on the same command lock, so its reliable
snapshot exposes seq 3. Releasing the old control reply preserves its original
seq 1/time 0. The queued reload then runs before the next monitor tick.

Both target assertions failed before repair: the replacement revision reused
seq 3 (`3 > 3` was false). This was behavioral red in two cases, not a collection
failure or an inferred scheduling risk. No source poses, private frame buffers,
or browser state were fabricated. The source loop generated all advanced frames.

The repair maintains `_seq_high_water` independently from a state's sample seq.
Reading a frame, caching a frame, or applying state observes that sequence;
reload allocates its offset above the watermark. Delayed command frame/state
metadata remains unchanged. This permits honest old samples while preventing
reused Session counters across scene revisions.

After repair both scenarios and the existing zero-based source reload regression
passed: **3 tests in 5.67 s**. The original two scenarios reported **2 failures in
9.16 s**. These durations are pytest receipts, not application performance claims.
