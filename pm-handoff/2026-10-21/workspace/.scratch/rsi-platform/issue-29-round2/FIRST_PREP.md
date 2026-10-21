# T0 round two — read-only preparation checkpoint

Authorization is #83 section5 and root's 2026-10-20 handoff. The first round's hold is obsolete, but no GPU process is authorized by this preparation artifact alone: root must review and schedule the concrete first probe. No new GPU initialization, IsaacSim/Warp execution, training, pip, uv/conda sync, provider TTS, or user service action occurred.

## Identity and preservation

- Primary checkout resolved with `git worktree list`: `D:/Projects/Robots/spatial-sync`.
- Old canonical branch/worktree remain `spike/issue-29-fr3-contact`, `.worktrees/issue-29`, baseline `1abcd49084a2491d7654a2a699fc98926e9467dc`. No old code/environment/evidence/model/data was modified.
- New isolated branch/worktree: `spike/issue-29-round2`, `.worktrees/issue-29-round2`; it starts from the actual old experiment, deliberately not a platform merge candidate. The first source checkpoint and manifest are listed in EXECUTION_REQUEST.json.
- Canonical interpreter remains `D:/Projects/Robots/spatial-sync/.worktrees/issue-29/experiments/fr3_contact/.venv/Scripts/python.exe`. It is the protected Windows T0 environment, not the platform or Tutorial serving environment. Run scripts by that exact interpreter, without any synchronization.
- IsaacLab f754f2965 / Tutorial c64024e / Newton1.6.0rc1 / Warp1.17 / MJWarp-MuJoCo3.12 / Torch2.11cu128 / RSL5.4.1. Source registration stays via the old bootstrap; PYTHONPATH selects the frozen new worktree experiment, which byte-matches the old Task.
- model630 and the canonical semantic-open375 reset data/asset hashes were re-read on CPU and match the archived identities. See cpu-evidence-v2.json. Their exact SHA values remain in the manifest.
- User staged HTML and generated routeTree edits are preserved. A3 height WIP, #53, #70, 8011/5191/8014 and all existing services are untouched.

## Evidence already resolved

`prepare_cpu.py --check-acceptance` replayed the archived actual oracle as an expected RED (exit1): 0 successes /1024, required922, RANGE400, timeout624; CUDA initialized=false. This is an archived feedback signal, not a newly run physical reproduction. Old actual audit and native probes are reused for physical red capability. New hypotheses remain hypotheses until discriminating physical probes.

The first native counterexample is home51/world51 passive finger2 at tick887/control111: 39.803952→40.043704mm, legal active target around37.5–38.2mm, passive P/D0. Prior replay matched q/v/target bitwise; native synchronization/mapping was consistent. Runtime margin0 predicts no pre-step limit row at that input. Offline M/H/J/f analysis shows even the local exact equality solution cannot retain40mm (at least17.098µm excess for all D≥0); global solve convergence remains qualified. Existing substeps10 also failed. Do not repeat these negative branches, assert a Newton bug from warning counts, or change limits to avoid failures.

Existing 111-control mean-action prefix: 49.1% gripper |a|<0.05,21.5% close below−0.05,29.4% open above+0.05; the final32 average is−0.000475. It contains only8 terminal episodes and cannot explain all624 timeouts. The full first-episode probe adds that missing coverage.

Eight equal reset phases contain128 rows each; only1/8 home, later phases restore demonstrated grasp/lift history. Home fingers≈37.5mm; tube centerZ≈34.9407mm, XY within source±6mm. No A3 h80 value is imported.

## Benchmark facts and limits

The tutorial's real preserved 800-update event file is at `D:/Projects/Robots/IsaacLabTutorial/logs/rsl_rl/so101_vial_state/2026-09-25_15-42-47_state/events.out.tfevents.1790322188.Caii.34828.0`. Frozen params show2048env×64steps, dt1/120, decimation4, substeps2 and the same library versions. It is PPO training; the separate4096env zero-action runtime benchmark is a different operation and is not used as its baseline.

| Training evidence | samples | median collection | median learning | median paired iteration |
|---|---:|---:|---:|---:|
| Tutorial preserved state run |800|9.438814s|0.357232s|9.791077s|
| FR3 actual v5 continuation |630|21.0385s|0.786s|21.835s|

Both updates contain131,072 agent transitions, hence approximately13,387 versus6,003 transitions/s at these median iteration boundaries. The spec's≈12.6s is an approximate documentation claim, not this raw run's statistic. These facts do not establish engine regression: robot DOFs/collisions/contacts/observation sizes differ, FR3 adds raw native validity plus extensive semantic recovery checks, frequent failed episode resets and 254,701,157 repeated warning lines/19.1GB stdout. Collection dominates the difference. A later bounded FR3 on/off instrumentation comparison must preserve raw validity and valid outputs, use equal64-step actions/resets, report warm-up, actual completions and contention, and claim collection scope only. No new full Tutorial benchmark is justified.

## Process/GPU inventory

Inventory receipt is inventory-20261020T1605Z.json; observed UTC is inside the file. RTX3070Ti Laptop8GB UUIDea968db7-d722-8e6a-d1d7-70adca2d5248/driver610.62. Desktop, browser, VSCode, WeChat and Windows GPU consumers remain; inaccessible names resolved read-only to dwm.exe,TabTip.exe,LZTray.exe. Board utilization is not an exclusive reservation and per-process GPU memory is unavailable under WDDM. No canonical T0 training process was present.

Archived first-prefix/native-replay/metadata current supervisor PID+creation identities are absent, and their authoritative Job receipts report0 descendants. The final first-round training/audit cleanup and old process trees are retained in the inventory. No historical PID or unrelated process was terminated. Recheck availability immediately before root's scheduled launch.

## Scope and impact

Only `experiments/fr3_contact_round2/` on the isolated experiment branch and the new primary scratch directory were added. Public protocols, recording formats, entityKey, device model, platform code, shared dependencies, assets, Task reset geometry, physical joint limits and oracle are unchanged. This is an experiment preparation checkpoint, not a delivered platform milestone or accepted T0 result. Full platform mandatory checks and Newton CPU merge gates are not claimed for this platform-free preparation; the source is not being merged.

Probe helper reuses the prior audited native observer and current-handle JobObject supervisor. Generated observer differences only extend601 controls/64MiB and continue after valid RANGE while preserving its original termination and first-done oracle; a new subclass adds manager-only measurements and pre-reset terminal snapshots. No observation code writes actions, targets, model or state. Its GPU runtime remains unverified until the scheduled probe; incompatible layout, nonfinite data, missing terminal facts, raw oracle disagreement, overflow or missed cleanup is an inconclusive/error outcome, never acceptance.

CPU validation: archived oracle RED, frozen hash check,6 generated scripts AST parsed without simulator imports; a real CPU mock counterexample proves first terminal snapshots survive later reset-like state changes and observation does not write physics state; experiment ruff and diff whitespace passed. The original observer kernels have prior actual GPU ABI evidence, reused only for unchanged kernels. No GPU work occurred during validation.

## Published plan and pending execution

The plan was published at https://github.com/CaiZongyuan/spatial-sync/issues/29#issuecomment-6295252825 on2026-10-20T16:01:31Z and read back byte-exact:6,626 UTF8bytes, SHA256`1d22b3204b64ea799cfc0844da71725175f48192c972d169afb6e01b90c4f4f2`. See published-plan-comment-proof-v1.json. The original start/resume comment was sufficient; no duplicate start status was posted.

Source checkpoint is`2dfe52a27f7d9442d1065c17ae00548566e05b51`. Current frozen preparation is first-home-prep-v3; manifest SHA256`1ebb34613e49937b146fd6e3f4c92f0a9784100c9a2082f6e86b7efd9e560090`, all48 input hashes match. The literal command contract passed CPU validation and rejected an extra`--deterministic` argument. v1/v2 preparations are preserved but superseded, never executed. Cache seed copied599files/110,711,341bytes read-only into the new warp-cache-v1; only that owned cache may be written by the future probe.

Only D1 is requested now. Expected wall3–6min is an estimate from old128.321s complete plain audit and77.107s instrumented111-control prefix. It is capped at900s+15scleanup, with930s reserved including launcher headroom,601controls, at most615,424policy transitions and4,923,392world-native outputs. New persistent observer allocation49,372,568bytes includes manager snapshots, under64MiB cap; transient Torch/driver allocations are additional. FreeVRAM>=4GiB is checked before launch, and desktop/browser consumers remain protected. Startup/compilation and current background load are separately reported; no exclusive throughput claim.

Root reviews EXECUTION_REQUEST.json and the frozen script/manifest, schedules one short GPU window and grants the owner a continuation. Full training initialization/code/argv must be frozen after diagnosis, not selected by favorable audit outcomes. Hypotheses and budgets are in the public comment; budget-ledger.initial.json records zero actual use and no unresolved new usage. First-stage checkpoint is complete; no GPU was launched.

## v4 review correction

The independent preflight requested one final-control flag-latching correction. It is implemented at source29938219e7554419c73f905493dcde8d6cf76f4a; the current request is EXECUTION_REQUEST.v4.json and current manifest first-home-prep-v4/prepared_identity.json. REVIEW_DELTA_D1_V4.md records exact new pins and the minimal generated observer diff. Five final-control CPU faults fail against frozen v3 and pass against v4 while the unchanged original counter retains1024facts and exit0; a clean final control still completes. v3 artifacts and review are preserved. GPU executions remain0, all budgets and physical/oracle settings unchanged. Await root's same-reviewer delta check and subsequent scheduling.
