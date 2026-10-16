# Pose read-model correction receipt

Baseline: `b21b00ca6de30f973680e85e03c57b30e8dfae87`. Corrected code: `646af0fab9d7ccfc3ba510c4b8741fe9bca2610b`. Scope: Session.pose plus Gateway tests only; spec §6, Runner/extractor/GPU/filter/TLS/protocol/UI remain unchanged.

Independent Spec review rejected the prior forced-freshness Pose oracle (`pose.seq > full.seq`). It required an HTTP-triggered full GPU capture and contradicted the published read-only snapshot requirement. That old assertion is superseded, not counted as a new requirement or successful acceptance evidence.

The new public regression starts a real running Synthetic source, narrows periodic membership to Body 1, explicitly obtains one complete `/snapshot`, waits for newer partial samples, and queries excluded Body 31 through `/pose`. It records source snapshot/interest/execute calls before the query. Before the fix:

```text
uv run --no-sync pytest tests/gateway/test_source_interest.py -k running_narrow_pose -q
FAILED: snapshot_calls 2 != expected 1
1 failed, 3 deselected in 0.72s
```

After the fix, repeated Pose reads retain the prior complete sample's coordinate/quaternion, seq, simTime, epoch and revision; `staleMs` increases while newer partial seq values continue. Source control counters remain unchanged. Unknown key returns 404, a removed complete cache returns 503, and the Session remains RUNNING:

```text
uv run --no-sync pytest tests/gateway/test_source_interest.py -q
4 passed in 0.82s
```

Real spawned Newton CPU and CUDA Gateway verification wraps the actual source snapshot/interest/execute methods with await-count spies. After an explicit full snapshot, it waits until the real periodic readback is narrow again. `/pose` matches the prior full sample, invokes none of those controls, ages naturally, and leaves readback=1 Body / 28 bytes. Other scope expansion, viewport, reset, paused heartbeat and owned-failure/reconnect assertions remain active:

```text
uv run --no-sync pytest tests/sim_runtime/test_gateway_interest.py -q
2 passed in 20.12s
```

Final broad non-TLS verification on the corrected code:

```text
uv run --no-sync pytest tests/contract tests/unit tests/gateway -q --ignore=tests/gateway/test_remote_runner.py
150 passed, 1 warning in 11.03s
```

Ruff and `git diff --check` passed. The original CPU/CUDA capture/filter/lifetime/heartbeat/shape checks remain reusable because their production paths did not change; the real Gateway tests were refreshed for corrected Pose behavior. Earlier protocol/web/build/browser evidence is unchanged. The companion TLS source remains PM-owned integration work.

No preview servers were launched for this repair. Both owned real Runner instances were cleaned up by the test's lifespan/source close; the final process inventory found no Python process using `review-source` and no spawned Newton child remained.
