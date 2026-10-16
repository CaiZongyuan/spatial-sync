# I3 Newton implementation and verification

Date: 2026-10-17. Task: `LAB_INFORMATION_FOUNDATION_SPEC_V1.md` I3.
Implementation baseline: `309b44e`, branch `issue/info-i3-newton`.
This checkout predates the PM's later Store/CLI/documentation integration.
It consumes the frozen public `HostCore`, `Catalog`, `StateStore.apply/entities`,
and Source APIs; it does not replace or merge their newer implementations.
Final integration and independent review belong to the PM.

## Candidate and consumers

The complete candidate includes the source adapter, Runner/program loop,
PhysicsPort, equality adapter, scene, three Newton programs, trajectory helper,
scene binding and twelve taught locations, existing Source/solver consumers,
teaching/spike/smoke tools, and their focused tests. The preceding commits are
`da9ff20` (Site-frame facts, already integrated by PM as `00bb5cd`) and `a256f6e`
(batched Site transforms). The main I3 commit depends on the latter.

| Boundary | Actual consumers | Required fact and evidence |
| --- | --- | --- |
| `NewtonSource.start` returns Manifest/Catalog | `SessionManager`, Source tests, `smoke_newton_source`, `smoke_info_newton` | SessionManager already supports this Source contract. Updated direct consumers pass; actual HTTP session and installed `lw` complete the journey. Non-lab scenes receive an empty Catalog. |
| Reliable information IPC | Runner control Pipe, Source cache, gateway Store/pipeline | Observations/events/updates retain HostCore order. Intake and cancellation work while paused. Accepted state precedes execution, reset refreshes the local cache synchronously. Actual paused Source test and CLI interlocks pass. |
| Snapshot staging and targets | All Newton programs, TransformExtractor, published frame metadata | Programs bind indices once and read one staged body/joint/velocity boundary. CPU uses views; CUDA queues three pinned-host copies and synchronizes once. Persistent target buffers commit once per tick. Periodic frames reuse an owned immutable body snapshot and matching time; reliable controls refresh the current boundary. CPU/CUDA extractor ownership tests pass. |
| Program clock/reset | `NewtonSimulation.step/reset`, Runner pause/step/snapshot | Simulation installs one callback clock. Reset clears program state, welds, pending release and motion history; queued invocations fail with `session_reset`. Source tests verify actual joint movement, spindle motion, pause/cancel and immediate reset cache. |
| Equality lifecycle | Robot holding, centrifuge retention, `spike_grasp` | All private Newton equality imports are confined to `welds.py`; 100 pairs are declared disabled. Activation derives actual relative pose, never teleports bodies. Actual spike proves activation, drift and post-release motion. Final CLI ends with zero active welds. |
| Scene/body origins | Manifest/mesh export, spatial frame viewers, physical location facts, taught FK | Tube01–04 are 16×30 mm flat-base microtubes; every body origin is at its bottom. Tube05–20 retain legacy capsule world geometry and rotation. Default solver remains MuJoCo; sine autopilot is removed. Optional raw-scene reset/settling test and Source journey pass. |
| Tube location | Each TubeProgram, RobotArm pick acceptance, centrifuge slot facts | Velocity is the measured derivative of the tube bottom in each actual candidate Site frame, including rotational transport. Literal tests cover 14 mm offset at 60 rpm, actual slip, static Sites, independent Site frames and batched bodies. No weld identity or requested rpm substitutes for location. |
| Teaching | Actual `lw moveJoints`, HTTP jointPositions/Manifest/Snapshot, YAML users | Live capture writes the measured joint vector into one selected pose, merges existing output, and independently checks URDF FK against the actual Site. Real capture/merge/warning receipt below. SciPy is tool-only and physics is not imported. |
| Solver comparison | `check_robot_solvers.py`, optional scene test | Initial rack contacts invalidate the old “precontact” premise. Both fine-step reference and real 240 Hz support are reported separately. Unsupported Featherstone yields a diagnostic, zero timing samples and null median. MuJoCo must show finite supported contact work and real arm movement. |

## D-01 and scene-restoration qualification

PM accepted weld mode after reading `spike-final-ramp.json`: 20 CPU contact
pick/place attempts at speeds 0.5/0.6/0.7/0.8 yielded 0/20 successes; all 120
motion segments settled. The arm gains and actual 0.2 m/s-per-finger target ramp
match the final mechanism. Earlier 80 mm-tube or instantaneous-closing receipts
are historical failures, not current acceptance evidence.

The same receipt measures weld activation displacement 2.83122e-7 m, relative
drift 0.000966563 m, and actual displacement 0.02671868 m after release.
Opening defers unweld until the measured aperture clears the tube, including
its measured lateral offset. `release-immediate.json` showed residual finger
contact and ejection; `release-controlled.json` showed zero finger force at
release and an upright settled rack return. Stop/cancel holds measured targets
and clears queued release intent.

The original 20 trials preceded restoration of the other sixteen tubes and
table. The restoration preserves each legacy capsule's radius 0.016 m,
half-height 0.055 m and world centre/rotation, within 4.7e-8 m centre error.
The representation moves its local centre to z=0.071 m to retain bottom origins.
The table retains its original centre/extents and right legs.

`layout-impact-pinned.json`, `layout-before-audit.json`, `layout-after-audit.json`
and `layout-impact-result.json` qualify reuse of that decision. Four distinct
original contact-speed conditions were audited before/after, not a new set of
twenty. Both audits remained 0/4 with all segments settled. Changed legacy tubes
had zero contacts with the robot, active tubes, or centrifuge. Selected table
contacts all lay inside the shared unchanged top face. Across 2150 aligned
samples, arm/finger maximum coordinate differences were at most 21.02 μm,
rotor/lid/Tube02–04 were unchanged, and failed/falling Tube01 differed at most
1.451 mm. PM explicitly accepted this impact qualification; the complete
installed-CLI journey was then rerun on the restored layout.

## Verification receipts

Local raw evidence is in this checkout's `.scratch/info-i3-newton/`.
Python 3.13.14; Newton 1.7.0; Warp 1.18.0; MuJoCo 3.15.0; Windows CPU.
Execution windows were exclusive with the other developers' performance work.

- `cli-restored-final20.json`: actual installed `lw.exe` against the candidate
  gateway on 8012, 15 actions, expected rejection exit 2/success exit 0, six
  independent actual-pose/finite-difference-velocity checks. Two tubes travel
  rack A1/A2 → opposite slots 1/3 → rack, with home/zone interlock, single-loaded
  imbalance rejection, physical lid and 3000 rpm/20 second run. All 130 running
  samples include four idle/rejection samples and 126 spinning-phase samples.
  Among the latter, 124 retain the expected locations; two transient samples
  (accelerating at 216.67 rpm and decelerating at zero requested rpm) report both
  tubes loose/all slots null and recover at the next sample. These receipts lack
  matching per-fact-boundary poses, so the transient's physical cause is not
  proved. All 4078 control ticks
  are retained; p95 is 1.062995 ms, actual RTF 1.0014, invalid poses/dropped
  frames zero, declared welds 100/active zero. This measures readback + programs
  + diff + target commit, not solver throughput or gateway latency.
- `cli-restored-cleanup.json`: smoke exit 0, session DELETE 204/STOPPED,
  owned gateway stopped and 8012 closed. The gateway log identifies Runner 6876.
- `manual-teach-verification.json`: actual CLI moveJoints followed by live grasp
  and approach capture; maximum measured-vs-requested joint difference
  0.001604867 rad. FK distance 0.591016 m produces the required >1 cm stderr
  warning. All other locations and the default taught-file SHA remain unchanged;
  second capture preserves the first. `physicsImported=false`. DELETE 204,
  STOPPED, gateway stopped and port closed after cleanup. Initial immediate
  post-terminate port check preceded socket shutdown; subsequent explicit
  `portClosedAfterCleanup=true` records verified closure.
- `solver-support-final.json`: tool exit 0. MuJoCo fine-step reference supports
  real motion and contacts, and the actual default-dt 0.5 second support check
  passes. Featherstone is unsupported at both step sizes, with `samplesMs=[]`
  and `medianMs=null`; no supported Featherstone reference or comparative
  performance claim exists. `featherstone-first-failure.json` identifies loaded
  free tubes as the first divergent DOFs at 3.333 ms; no further solver tuning
  or manufactured precontact reference is part of this candidate.
- Focused pure and optional CPU checks: 26 passed in 30.33 seconds, covering
  trajectory/facts/programs, actual scene, Source lifecycle and extractor.
  Subsequent failure-path repairs pass eight focused Program tests, with Ruff
  and whitespace checks clean. The full successful CLI receipt predates only
  those failure-path repairs: gripper stall now holds measured targets, place
  reports `place_failed`, pick stall opens and retreats to approach before
  `grasp_failed`, and execution-time requires errors preserve `details.failed`.
  New independent fake-Port tests exercise those branches; normal journey
  behavior is unchanged, so the successful physical journey evidence is reused.
- `program-final20.json` is earlier public-Source full journey evidence before
  legacy restoration. It verifies engine-free caller and clean Runner/thread
  shutdown. It is supplemental; the restored actual CLI receipt is current.

Reproduction uses the existing environment without synchronization:

```powershell
python -m pytest tests/unit/test_info_trajectory.py tests/unit/test_info_tube_facts.py tests/unit/test_info_newton_programs.py tests/sim_runtime/test_robot_scene.py tests/sim_runtime/test_transform_extractor.py tests/sim_runtime/test_newton_source.py -q
python tools/check_robot_solvers.py --output .scratch/info-i3-newton/solver-support-final.json
python tools/smoke_info_newton.py --cli <installed-lw.exe> --url http://127.0.0.1:8012 --output .scratch/info-i3-newton/cli-restored-final20.json
lw --json do /World/Lab/RobotA moveJoints positions=[0,-0.4,0,-1.8,0,1.6,0.7] --wait
python tools/teach.py --capture rack.A1 --pose grasp --output .scratch/info-i3-newton/manual.locations.yaml
```

## Bounded simplification and remaining limits

The local pass inspected all I3-owned committed, modified and untracked files
since `309b44e`, plus their consumers above. It consolidated the shared
engine-free registry/catalog loader, moved callback-clock installation into
NewtonSimulation, simplified robot plan construction and holding lookup, and
removed temporary grasp-trace state. Site transforms and body-relative
velocities are shared per sample rather than recomputed for every tube. It
retains the equality adapter, staged immutable frames, real feedback settling,
per-entity property ownership, reliable control IPC, cancellation and reset
boundaries because consumers rely on them.

No GPU end-to-end laboratory journey or physical-hardware reachability claim
is made. Extractor CUDA staging is covered when the optional device is present;
the accepted laboratory journey and tick budget are CPU evidence. Taught paths
remain assumed safe, as declared by the type, rather than collision-planned.
Independent Standards/Spec review and integration with the PM's newer I1/CLI
baseline remain necessary; refresh the actual Newton consumer if those public
contracts change during integration.
