# P0-E independent review

Reviewer: protocol/gateway developer (`/root/protocol`), non-author of the P0-E delta. Fixed base `384d712e04e0ba71833aeea17c81dbac4f4570e5`, complete candidate `f53969059d6ba07861b1bf22a8de075c42033c85`. Read-only review; no heavy verification rerun. One non-author covered Standards and Spec, sharing the coordination context; the final milestone receives broader independent review.

Standards: zero findings. Explicit ownership of each IPC channel and extraction slot serves required isolation and cleanup. Gateway imports remain independent from Newton initialization; the parent imports physics only inside the spawned worker. Review included source, transport, runner, extraction/filter/pacer helpers, tests and evidence.

Spec: zero findings in P0-E scope. One spawned Runner owns state; frame/control pipes carry periodic latest-only and reliable command results separately. Reader coalescing and seq guards prevent late periodic data replacing a newer reliable frame. Command frames/state are returned through CommandResult without duplicate event delivery. Invalid-pose retention, omission provenance, bounded lag and concurrent close follow the contract.

The CUDA counterexample retains the first consumer, overwrites State, captures a second sample, overwrites again and refuses a third slot until release; both snapshots match independent literal poses. The extractor holds staging/host/events through callback completion. A blocked 4 MiB Pipe send still allows 10,000 producer offers. Actual Gateway tests cover two observers on one PID, matching pause pose/seq, ordered controls, terminated Runner with healthy API and same-session restart with new PID/revision and increasing seq.

Reused author evidence: 103 non-GPU tests, focused CPU/CUDA source/extractor tests and public CLI receipts. The reviewer did not label these independent runtime executions. PM subsequently opened the integrated Newton scene in Chrome at the root application, paused/reset it and verified the child Site anchor at world `(1,0,1.5)`, simTime 0, epoch 1.

Residual scope: the declared configurable no-observer policy is assigned to P1-C; default continue behavior is implemented. Robot/USD/binding and full milestone validation remain later acceptance.
