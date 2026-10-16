# P1-A — Articulated robot laboratory

Status: implementation ready for independent review and combined A+B integration.
Baseline `f53969059d6ba07861b1bf22a8de075c42033c85`; coherent source/assets checkpoint
`c3ff7c999429da92d1d4e866adccfcee6afe046f`. PM owns the final integrated Web Viewer
robot/GLB journey after P1-B; this report claims real physics, source and API evidence.

## Asset decision D-02

The official Newton 1.7.0 examples call
[`download_asset("franka_emika_panda") / "urdf/fr3_franka_hand.urdf"`](https://github.com/newton-physics/newton/blob/v1.7.0/newton/examples/ik/example_ik_franka.py).
That package contains the **Franka Research 3**, with seven revolute arm DOFs and two
prismatic fingers. It is used directly through `add_urdf()`, then composed with
`add_builder(label_prefix="World/Lab/RobotA")`. No synthetic robot or asset fallback was
needed. The exact asset revision pinned by Newton is
`f8fb7abcbeba2318814a74f3eeb02780ad7925d6` in
[newton-assets](https://github.com/newton-physics/newton-assets/tree/f8fb7abcbeba2318814a74f3eeb02780ad7925d6/franka_emika_panda).

Only the referenced URDF, collision/visual meshes, original LICENSE and README are
vendored: 22 files / 27,420,151 bytes. The [provenance ledger](../../../assets/robots/franka_emika_panda/provenance.json)
records SHA-256 and sizes for every upstream file. The asset integrity test checks all
22 files. Scoped `.gitattributes -text` preserves upstream bytes across Windows checkout.
`tools/vendor_franka_asset.py` is a fixed-revision developer tool; no API scene load invokes
it or fetches an arbitrary URL/path. The original per-asset Apache 2.0 terms are retained.

Actual DAE import required `pycollada==0.9.3`; `scipy==1.18.1` supplies trimesh's sparse
normal calculation and avoids its slow/noisy fallback. They are simulation-only extras.
Newton 1.7.0, Warp 1.18.0, MuJoCo/mujoco-warp 3.15.0 and Pydantic 2.12.4 stay unchanged.
Default dependency export confirms no Newton/Warp/MuJoCo/pycollada/SciPy dependency.

## Runtime design and supported behavior

`SceneConfig` holds the trusted builder, explicit solver, optional driver factory and warmup
steps. `build_scene()` remains available; the default falling-boxes scene retains XPBD.
`robot-and-tubes` selects SolverMuJoCo. `usd-lab` has a lazy registry entry for P1-B's
`usd_scene.usd_lab_builder`. Registration accepts scene IDs, not arbitrary files.

The functional scene has 35 streamed Bodies: 14 imported robot/frame Bodies, 20 free
tubes and one centrifuge rotor; static workbench/legs, housing and floor are shapes with
body=-1. Seven arm joints plus two fingers use joint motor targets. A bounded Warp driver
varies three arm targets and rotates the centrifuge. It never rewrites body poses or drives
the renderer. Tubes fall under real gravity and make contact with the workbench.

CPU MuJoCo selects `use_mujoco_cpu=True`; CUDA uses mujoco-warp. Both select native
MuJoCo contacts and pass `contacts=None` to the official Solver. Featherstone uses a
separate Newton CollisionPipeline. Solver selection never falls back silently. CPU scene
construction is scoped to the requested CPU device, including mesh mass properties.

Reset invokes the solver's reset method, clears Control from Model defaults, recreates
both States, and executes FK. The reset regression also repeats the same 120-step
trajectory and recovers the same CPU poses within 1e-6. Reduced-coordinate solvers answer
joint queries from their actual `state.joint_q`; XPBD retains its IK-derived query path.
The rotor query test crosses π, catching a wrapped-angle IK substitute for true joint_q.

All non-free joints get child-side Newton anchor Sites. The hand TCP Site is at local
`[0,0,0.1034]`. Its independently worked URDF origin-matrix golden is
`[-0.07282344,0,1.26703449]`, while the hand Body is
`[-0.07282344,0,1.37043449]`. The initial test computes the Site world transform through
Warp and checks that literal. Prismatic finger metadata and 20 free-joint entries are
verified through an actual spawned Source.

Newton 1.7's URDF `parse_shapes()` never passes label= to add_shape, even for named URDF
visuals. Construction therefore records those 54 exact omissions as provenance, including
the main builder shape offset. The exporter emits `unlabeled_entity`; strict mode rejects
them. This does not guess from label spelling. Body/joint/TCP/anchor IDs remain stable.

## Solver support decision D-03

Both devices execute the same imported model, motor targets, 1/240 s step and original
contact parameters. Complete-work correctness is checked separately from timing:

| Solver | Device | 30-step precontact subset | 120-step / 0.5 s full contacts | Final tube center Z | Max joint position error |
|---|---|---|---|---|---|
| SolverMuJoCo | CPU | valid | valid | 0.790927–0.820633 m | 1.26e-7 m |
| SolverMuJoCo | CUDA | valid | valid | 0.790927–0.820633 m | 5.37e-8 m |
| SolverFeatherstone | CPU | valid | unsuitable for this configuration | 2.341199–2.514359 m | 1.49e-7 m |
| SolverFeatherstone | CUDA | valid | unsuitable for this configuration | 2.341201–2.514360 m | 1.33e-7 m |

The Featherstone arm and anchors remain valid, but its light-tube contacts add enough
energy to eject them from the table. Finite numbers alone do not count as a correct demo.
Exploratory smaller-step/material changes did not establish a valid full workload and were
discarded; MuJoCo's original valid material parameters remain. This finding is limited to
this model, timestep and parameters, not a general claim about Featherstone contacts.
Recommend retaining **SolverMuJoCo** as the articulated demo default.

`check-benchmark` was applied before interpreting timings. The measured operation is
30 precontact steps (0.125 simulated seconds), including motor/control/contact traversal,
with device synchronization at both timing boundaries. Setup/import/JIT is separate;
30 steps warm the timed path. Three reset runs per solver provide the retained sample
variation. Full-contact correctness is a separate result; failed full work is never counted
as throughput. Raw inputs/results are [CPU](P1-A-solvers-cpu.json) and
[CUDA](P1-A-solvers-cuda.json). No full-demo FPS/throughput claim follows from those timings.

The uncaptured single-world CUDA MuJoCo path is slower on this machine: the observed
30-step medians were approximately 450.8 ms CUDA and 13.3 ms native CPU. This is a narrow
local observation across different backends, not a GPU speed claim or hardware guarantee.
CPU remains the default; GPU actual RTF/catchup is reported. CUDA graph optimization is
deferred. Hardware is the P0-A Windows/RTX 3070 Ti 8 GiB / driver 610.62 setup.

The first direct CUDA run spent about 89.16 s in 120 steps, dominated by cold compilation,
plus roughly 30 s solver setup. The robot's 120-step warmup now occurs in LOADING, then
reset/FK restores simTime=0/epoch=0 before READY. The optional CUDA startup deadline is
180 s. Cached Source startup was verified; the shared Warp cache was not deleted to
repeat a clean-cache startup. Startup/warmup costs are excluded from steady timing.

## Verification, scope and handoff

```powershell
uv sync --extra simulation --extra usd
uv run --extra simulation --extra usd pytest tests/contract tests/unit tests/gateway tests/sim_runtime -m "not gpu" -q
uv run --extra simulation python tools/check_robot_solvers.py --device cpu
uv run --extra simulation python tools/check_robot_solvers.py --device cuda:0
uv run --extra simulation python tools/smoke_newton_source.py --scene robot-and-tubes --device cpu --crash
uv run --extra simulation python tools/smoke_newton_source.py --scene robot-and-tubes --device cuda:0 --crash
```

- Full non-GPU suite: **107 passed, 2 GPU cases deselected**, 28.16 s. Following the
  provenance/reset/query additions, the four affected robot/integrity tests passed again.
- Actual CPU/CUDA Source receipts: [CPU](P1-A-source-cpu.json), [CUDA](P1-A-source-cuda.json).
  Both carry 35 poses, command frame/epoch/seq semantics, hard child failure and cleanup.
  Neither imports physics in the caller; neither retains child/read threads after close.
- Physics receipts retain their exact measured source/asset-manifest hashes. Later warning
  provenance bookkeeping and generated-ledger newline normalization do not change the
  upstream asset bytes, motors, model dynamics or retained mechanical correctness results.
- Scoped Ruff, lock and whitespace checks pass. The 22-file offline integrity check passes.
  GPU is released; no ports or development server were retained by this issue.

Change-impact trace: trusted scene registry → Simulation solver/controller/contact policy
→ Runner warmup and raw joint queries → existing Source protocol → gateway/viewer. Existing
falling-boxes and protocol/gateway checks remain green. P1-B owns mesh export and its
asset-only response hunks; this issue does not change wire schemas or its exporter.

The bounded simplification retained the explicit solver/contact branch and tiny lazy
registry seams because solvers and optional asset modules have different requirements.
Controller targets allocate once and update in one Warp kernel. Full-contact failure is
reported rather than hidden behind solver-specific tuning. No further extraction was
justified. Pending: independent review and PM's integrated A+B browser appearance check.

PM golden inspector paths are documented in
[the example guide](../../../examples/robot-and-tubes/README.md). D-02 should record the
official FR3 asset pin; D-03 should retain MuJoCo with the measured Featherstone scope above.
