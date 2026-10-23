# D2 fixed-action A/B — source candidate, no execution authorization

Revision2 fixes both Advisor findings on76adba5. That old commit and its frozen
v1 source request are preserved. No counter, syntax, Windows Job or GPU result
is claimed for either source revision.

This private experiment reuses the already reviewed D1 observer, original Task,
first-done oracle and handle-owned Windows JobObject supervisor. It adds one
recorded-action adapter and one predeclared gripper transform. It does not change
`experiments/fr3_contact/`, assets, reset data, original rewards, physical limits,
raw validity, terminal rules, or success definition. D1 remains0/1024, not the
second-round trained-candidate final result. No pilot/training follows this probe
automatically. H2 closing/contact attainability and reward causation remain open.

## Inputs and action contract

The frozen D1 trace is25,121,659bytes (not250MB), SHA256
`d112c87a8c1fc3dc7ade76eabfbf834c6ea0091bdc3034c54d18de518d6b0587`.
Its exporter saves `actual_mean_action_history`, `episode_history`, `first_done`
and `home_rows`. Future runtime reads it with `torch.load(map_location="cpu",
weights_only=True)` and checks finite CPU float32 shape600x1024x8, first-done
shape1024x10, epoch shape600x1024x2, exact128home-cycle mapping repeated8times,
first epoch1, unseeded home and archived0/400/624 facts. No tensor was loaded or
code compiled/tested during this source preparation. Original48 D1 input pins
remain; the new manifest additionally pins these sources, trace and play backend.

A returns the saved commands in their original order. B copies the same schedule
and changes **column7 only**: first command per world is unchanged; thereafter
`g[t,w]=0.25*g_D1[t,w]+0.75*g[t-1,w]`, computed in CPU float32. Seven arm columns
and first commands must match byte-for-byte. No clipping, alpha scan, cross-world
state, policy forward or episode-driven filter restart. All1024 worlds receive
commands until their own first terminal; B is never masked by A's termination.

After a reference A first terminal, D1's later commands originated in a subsequent
reset episode. B continues this same global open-loop schedule if still in its
first episode. Epoch and reference-epoch arrays disclose this explicitly. The
comparison identifies action participation under this schedule; it cannot claim
that continued B actions are a coherent policy rollout or establish trainability.

The pinned play CLI's callback replaces its local `OnPolicyRunner` symbol with a
recorded-action adapter before runner construction. `load` checks only the old
checkpoint path token; no checkpoint/model is read or built, no inference runs,
and its export methods write nothing. Backend bytes and default flags are guarded.
Original environment creation, wrapper reset, original counter and Task remain.
A must requalify the actual baseline because avoiding runner RNG consumption may
matter to later resets; no equivalence is assumed from source alone.

The standard-library bootstrap `d2_entry.py` checks already-loaded modules before
the CLI, registers the same Task and records gym entry/configuration strings.
The callback imports required Task definitions, wrapper and observer modules and
checks their actual `__file__`, ModuleSpec, loader paths and actual-file SHA
against `loaded_module_pins`; key constructor, wrapper-step, counter and observer
code filenames are checked too. Old same-byte Task files at the old worktree path
are rejected. Registered class/configuration objects are resolved and checked
before the callback returns, hence before the pinned backend enters
`launch_simulation` or constructs an Env. Actual module/registration/symbol
records persist in `module-origins.jsonl`.

All bootstrap/import work is inside the globally billed Job. Source inspection
of this pinned kitless backend places callback/Task config imports before App
launch; runtime validation is pending. Import-time library/GPU side effects are
not claimed absent: they remain charged and under the same hard limit. If a
dependency cannot be imported before App initialization, this candidate fails
before Env construction; do not skip/defer the check or launch an unguarded App.
Any such change needs a new reviewed proposal with charged initialization stated.

## Output and qualification

The unmodified D1 observer still captures raw native outputs/first RANGE events,
terminal-before-reset facts and original1024-first-done result; its final-control
diagnostic latching fix is reused. `round2-first-episode.pt` retains proximity,
contact/force and gripper duty. New `d2-replay.pt` records requested/effective
actions, every control/world epoch and live mask, all raw reason/sample/output/
excess/speed snapshots, q/qd and both actual native target arrays before autoreset.
These are actual targets of each arm; no unmeasured counterfactual target is
invented. Collection is CPU telemetry, with no new persistent GPU history buffer.
It adds host copies/serialization; there is no throughput comparison claim.

A must retain1024 first-done facts exactly matching D1, including0success,
400RANGE/624timeout, zero other invalid reasons/stages,600controls and the same
epoch history; first event must be world51/control111/native887. Any discrepancy,
unexpected diagnostic flag, changed effective bytes, missing episode, or uncertain
cleanup makes A inconclusive and prohibits B. Do not tune tolerances after a run.

B must independently retain all1024 original first-done facts with the canonical
home mapping, first epoch1, no observer/oracle disagreement or unexpected flags.
The published H1 positive prediction is RANGE<=80/1024 (>=80% decrease) and zero
NONFINITE. Record every invalid reason, timeout and stage even when that prediction
fails. A qualifying negative B result rejects this single filtering branch; no
parameter sweep follows. Progress/success remains the original oracle's output;
diagnostic exit0 or reduced RANGE is not T0 acceptance.

Final T0 acceptance stays128canonicalhome x8,1024 first episodes, at least922
ordered physical contact-grasp/lift/insert/release/stable10 successes and raw-valid
original physical ranges (finger upper40mm), without seeded history. This replay
intervention is never used as the mean-policy final audit. D-01 stays weld.

## Future preparation, literal execution shape, and budget

`prepare_d2.py --root D:/Projects/Robots/spatial-sync --directory
D:/Projects/Robots/spatial-sync/.scratch/rsi-platform/issue-29-round2/d2-prep-v2`
is a **future** standard-library preparation entrypoint, not run in this phase.
It rejects dirty source/stale inputs, copies unchanged D1 observer modules and
supervision helpers, and changes only five exact supervisor text seams: hard430s,
inner420s, soft390s, callback `d2_replay.install`, and new-module hash checking.
It produces an identity manifest/request including loaded-module pins and the
private bootstrap; no budget write/launch.

Future outer argv (after independent source/counter checks and specific PM GO):

```text
powershell.exe -NoProfile -File <source>/Run-D2-Replay.ps1
  -Preparation <frozen-preparation> -RootGo <exact-PM-GO.json> -Execute
```

For each arm, the literal ordered inner payload remains:

```text
<canonical-python> <frozen-d2_entry.py> play --rl_library rsl_rl
  --task Lab-FR3-Pick-Tube-v0 --num_envs 1024 --checkpoint <old-model630-path>
  --external_callback d2_replay.install presets=newton_mjwarp
```

The launcher requires a fresh PM file with scope`D2_AB_ONCE`, exact request SHA,
source commit, phase`d2-fixed-actions-YYYYMMDD-HHmmssfff`, reservation_seconds900,
not_before_utc and expires_utc. The live ledger must contain the same active
phase/request/900s reservation, no unresolved usage, settled92s diagnostics,
0full-training and intact1200s final-audit reserve. Neither the preparation nor
this source freeze changes the ledger or creates such a GO/reservation.

The thin PS entrypoint records launch origin before any GO/request/ledger/hash
read and starts a guardian by its current process handle. The guardian uses D1's
native API definitions in `d2_jobapi.py`, creates its unnamed kill-on-close Job
and arms a timer before receipt/file setup and child resume. Every GO/ledger/git/
hash/preflight operation, both arm supervisor/physics trees and qualification/
terminal readback run in its suspended-bound coordinator child. Blocking hash
work cannot escape this guardian. One900s absolute launch boundary applies:
guardian stops work at origin+870s with<=15s cleanup; PS escalates only its fresh
guardian handle at origin+885s and bounds the tail by origin+900s. Subordinate
arm limits cannot reset that origin. Interpreter/startup time is counted; late
startup refuses preflight. Missing Job0/receipts or PS escalation is fatal and
unresolved, never accepted. No historical PID/process-name cleanup is used.

Two serial arms retain <=450s inclusive subordinate windows each:
soft390/supervisor420/hard430/cleanup15/launcherheadroom5. The coordinator refuses
another arm without450s before the global work-stop. Setup/inventory/hash/failed
start/serialization/cleanup count too. Read-only inventory has a10s bound and terminates only
its freshly created process handle if necessary. Each physics supervisor reuses
suspended-create -> bind current kill-on-close JobObject -> resume, then requires
normal exit, terminal receipt and verified Job0. Root reconciles the guardian's
entire-Job-zero receipt and outer handle receipt and conservatively charges the
full launch window; coordinator-only elapsed is not a settlement. The launcher does not change live
accounting, release a reservation, or treat missing cleanup as zero. No retry.

Current budget unchanged:92/7200diagnostics,7108remaining including1200audit,
5908unreserved; at most one full training <=14400s, currently0. If this entire
900s proposal is used, totals would be992/7200 with5008unreserved diagnostics.
It cannot borrow final-audit or training time. Exhausted/inconclusive cleanup
requires PM reconciliation before another launch. Second-round final failure
stops T0 and routes T2 specification options to `ready-for-human` and the user.

## Required counterexample checks before any execution GO — NOT RUN

Use standard-library fake modules/lists or later separately authorized CPU tensors;
no physics is needed. The reviewer should exercise the actual new adapter/seams,
not merely mirror the formula in a separate implementation:

1. Pin Task/auditor/reset/limits bytes against1abcd490 and D1 manifest; original
   counter still counts each world once and keeps all failures in the denominator.
   Preload an old Task path with identical bytes, wrong wrapper/observer origin,
   wrong ModuleSpec/loader or wrong registered class/constructor. Each must fail
   with zero Env constructions and record actual rejected origins. The healthy
   current-path registration must reach the original counter.
2. Asymmetric impulse in one world: only its gripper filter changes; first commands
   independent per world, arm signed-zero bytes preserved, alpha fixed. Resetting
   one world must not reset/reindex either schedule or another world's filter.
3. Wrong shape/dtype/NaN/home permutation/epoch/tracehash rejected before simulation.
   Exhausting600 actions fails; no policy constructor/forward/checkpoint load or
   export is reachable via the replaced runner. Wrong backend hash/flags rejected.
4. Let A terminate world7 atcontrol111 and B at600. B still receives all controls;
   first terminal snapshot survives later autoreset-like mutations. Current epoch
   vs reference epoch is retained; sticky RANGE may clear only across an epoch.
5. Actual effective arm byte change/clipping, missing native outputs, bad raw-bit
   clearing, nonfinite or positive invalid reward on the final control must fail
   before the original counter's SystemExit and persist a negative qualification.
   Clean final exit still retains all1024 original facts. Reuse D1 final-boundary
   counterexamples; do not infer a new pass from their old results.
6. A with0/400/624 but altered per-world first facts/event/epoch cannot start B;
   a process exit0 with no qualification/terminal/Job0 cannot start B. Exercise
   failed child assignment, supervisor exit with descendants, missing receipts,
   failure before and during B, inventory timeout and unknown cleanup: only current
   handles are stopped; the cleanup receipt stays unresolved on any missing proof.
7. Stale/wrong GO, request/identity/head mutation, ledger unknown usage, insufficient
   remaining450s, audit reserve damage and duplicate phase reject without launching.
   Proposed receipt counts failed startup/cleanup and never settles the live ledger.
8. Exercise actual guardian/coordinator seams with a fake clock/current-handle Job
   API. Block GO read, input hash or terminal readback and advance beyond the
   single work-stop: fatal termination and cleanup must be charged from the same
   original launch origin. Failure cleanup cannot gain a new900s interval. Also
   cover guardian startup/receipt-write stalls, failed suspended assignment,
   descendants surviving coordinator exit and outer fallback stopping only its
   current guardian handle. Start/partial/argv/helper identities must remain
   available when terminal persistence fails. Healthy fake A->B must qualify;
   fake results do not establish actual Windows/nested-Job cleanup behavior.

Independent review and any separately scheduled source-free counter validation
remain required. No tests, compilation, simulation, training, build or installation
were run for this candidate. No new PR/issue/comment or GPU reservation is created.
