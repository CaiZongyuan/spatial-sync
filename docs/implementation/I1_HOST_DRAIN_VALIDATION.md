# Host publication guard

Baseline: `e150c135c03ec54e35e6fd8d837776e062ecf98d`.
Branch/worktree: `issue/info-i1-host-drain` / `info-i1-host-drain`.
The bounded candidate changes `packages/info/program.py`, adds three targeted
Host tests, and records this evidence. It does not change the benchmark workload,
Source interfaces, clocks, physics, polling, GC, program callbacks or reliability.

## Change and actual consumers

Every Host drain previously visited every entity and called its PropertyDiff,
including polls with no changes or pending values. The Host now records whether
any entity needs publication. A true change in `_set` marks work; a drain keeps
the mark while any rate-limited property remains pending; `_prime_diff` clears
it when initial/reset snapshots establish the new baseline. A completely idle
drain skips the entity scan. With work present, the original canonical order is
retained and only dirty/pending entities call PropertyDiff. Sparse work still
checks the canonical entity list; no sparse O(1) traversal claim is made.

All repository writes were traced with `rg`: `_EntitySlot.reset` and Host `_set`
own `dirty`; `PropertyDiff.update/reset` own `pending`. Other programs, Source
adapters, gateway and tools do not mutate them. The existing Ctx ownership check,
input copying, per-property metadata/revision logic, PropertyDiff implementation,
and canonical program/event order remain the owning boundaries.

| Boundary / consumer | Compatibility fact | Evidence |
| --- | --- | --- |
| Ctx / all virtual and Newton programs | Real changed writes mark publication; no-op writes do not. Every scheduled program still ticks in the original order. | Full old/new callback sequence equality; ownership/error/disable tests. |
| Synthetic running / paused service | Pending properties publish from wall-clock drain without another simulation tick. Returning to the sent value clears pending work. | New targeted Host tests and existing publication-clock/cadence/virtual tests. |
| Host events / action pipeline | Observations precede events and terminal updates; terminals wait for their entity's final pending properties. | Explicit literal ordering assertions, old/new complete message equality and gateway tests. |
| Initial snapshot / reset / epoch | Primed values are snapshot baselines; old pending values do not reappear after reset. Stale epoch requests remain rejected. | Targeted reset test, real Host reset/old-epoch parity and real Newton Source reset/cache test. |
| NewtonInfoHost | Same public Host API, callbacks, staged facts and control targets. | Final candidate's actual CPU Newton Source invocation/pause/cancel/step/spindle/reset test. |

## Verification

The new idle-work test failed against the baseline: an empty drain called both
entity diffs. The candidate avoids those calls while the same test exercises
partial publication, final pending flush before terminal, and full program ticks.
The other new tests independently assert canonical message ordering and
return-to-baseline/reset behavior; they do not depend on the guard's name or
container representation.

Final functional gate:

```powershell
python -m pytest tests/contract tests/unit tests/gateway tests/sim_runtime/test_robot_scene.py -k 'not official_seven and not unsupported_featherstone' -q -W error
```

Result: **297 passed, 2 deselected in 71.55 seconds**. The two deselections are
the raw-scene and unsupported-solver tests, outside this Host publication change;
the actual Newton Source consumer is included. The isolated focused group also
passed 32 tests. Source teardown completed and no service was started by this
gate. Shared execution windows were released after actual completion.

Raw parity/component evidence is in this worktree's
`.scratch/info-i1-host-drain/`. `check_host_drain.py` loads the frozen baseline
Host from Git and the candidate using the same real Catalog/program classes,
1,000 telemetry entities plus the controller and 22 laboratory entities.
Deterministic publication clocks make the complete outputs comparable; actual
synchronous durations use `perf_counter_ns`. No timer sleep, HTTP, WS, solver
throughput or end-to-end budget is inferred from this component harness.

`host-drain-results.json` and `parity-trace.json` establish exact old/new equality
of **132 complete messages** (96 observations, 31 invocation updates, one event,
four snapshots) and **249,627 ordered program callbacks**. The sequence includes
public controller actions, arm motion, lid/run completion, paused drains,
cancel-before-start, reset with pending telemetry, stale epoch rejection and
new-epoch work. Final pending count is zero. Metadata, values, revisions,
invocation fields and message/callback order are compared, without dropping
progress or observation content.

## Component measurements and limits

The final harness keeps normal GC and full immutable JSON audit retention outside
the timed Host calls. Each pair has 30 warm-up ticks, then 180 complete ticks and
540 between-tick drains. Two repetitions reverse old/new execution order. The
existing representative 15 Hz generation/publication and stress 60/20 Hz
settings produce exactly 45,000 / 60,000 measured property changes in each pair;
complete wire digests and retained message counts agree. No generated work,
final value, control tick or callback is skipped.

| Scenario | Baseline poll p95, ms (two repetitions) | Candidate poll p95, ms | Baseline tick p95, ms | Candidate tick p95, ms |
| --- | --- | --- | --- | --- |
| Idle | 0.1646 / 0.1107 | 0.0011 / 0.0010 | 1.2441 / 0.8901 | 0.7400 / 0.7019 |
| Representative 15/15 Hz | 0.1285 / 0.1523 | 0.0025 / 0.0027 | 5.6148 / 5.7215 | 5.7802 / 6.1251 |
| Stress 60/20 Hz | 0.7099 / 0.7586 | 0.7985 / 0.7017 | 6.0150 / 6.0135 | 5.9587 / 6.0375 |

For the fixed representative workload, disjoint tick-plus-poll wall totals fall
from 408.2601 to 367.9578 ms and from 423.6558 to 350.9605 ms (9.9% / 17.2%).
These are sums of actual operation durations, not sums of percentiles. Its tick
tail does not improve. Stress totals vary from +2.2% to -4.7%; no consistent
dense-work or active-tail improvement is established. The supported claim is
elimination of unnecessary idle scan/diff work, with lower total component cost
for the representative cadence. HTTP/WS budgets remain for PM integration
verification; this evidence does not mark them passed.

The first candidate used a dirty/pending entity set and canonical rank sorting.
Although parity passed, its dense active costs regressed. Moving sorting to
outgoing batches still did not establish a dense improvement. Those negative
results remain in `first-sorted-candidates-results.json` and
`sort-output-only-results.json`; their complete traces and logs are retained.

## Bounded simplification

The final implementation replaces both proposed index collections and all
sorting with one boolean guard and an existing-order scan when necessary.
This removes cache/rank invalidation obligations and avoids speculative
scheduling or clock changes. The property diff, terminal pending gate, reset
and program ownership boundaries remain separate because actual consumers
depend on them. The bounded pass inspected the complete candidate and immediate
callers; no other worthwhile cleanup belongs in this change. Independent
non-author review is still required before PM integration.
