# A3 original geometry: evidence and next bounded decision

Baseline: `f7a480f6a588de2a3186fe166214954112083194`, clean branch `feat/issue-18-fr3-lab-import`. Runtime prerequisite PR #78 is actually merged; final PR CI and main push CI each passed all nine checks. No Newton, Source, Blender or browser process is running for this work.

## Observations

- The actual exported tube has 1,556 triangles, local bottom Z=0, height 0.1289999783 m and maximum radial extent 9.52 mm. Original placement is `[-0.028, 0.336, 0.962]`. No scale or origin change was used.
- Offline URDF math uses the pinned builtin source and actual base `[0.2, 0.7, 0.95]`. Of 25 deterministic HOME-seed target/orientation attempts, 12 meet the diagnostic 10 mm/1 degree criterion. This is not a proof that the other attempts are unreachable.
- Two opposite original sockets, `.003` and `.009`, have side-yaw-0 candidates with position errors 0.0000401 mm and 0.369821 mm. Rack side-yaw-0 error is 0.0000565 mm. Real Newton FK, whole-arm collision, approach paths and support clearance remain unverified. The far unsuffixed socket's best searched candidate misses by 18.603 mm; retain that failed attempt and all four promised slot identities.
- For all four tested horizontal side orientations, original tube vertices and the convex palm's support bounds have a strict separating axis. Downward palm overlap remains unknown, not a proven collision.
- At the existing 17 mm weld trigger width, all ten tested orientation/offset cases have zero tube/finger surface intersections despite `_in_fingers=true`. At zero width they intersect. The original cone is narrower than the previous cylindrical test shape near its bottom; existing weld mode may capture before pad contact. This cannot establish contact grasping.
- Actual evaluated surface checks find intersections between the original rotor and the lid in the source's closed frame, before adding a tube. They also find intersections between an upright original tube at every proposed socket center and that closed lid. Three world-coordinate triangle witnesses are stored for each case; enumeration stops after those witnesses.
- The closed central plate underside is Z=1.31737996 m. A whole 129 mm upright tube entirely below it requires bottom Z≤1.18837998 m, versus original socket centers Z=1.35793996 m. Socket centers are also not automatically valid seating floors: the original sockets are illustrative solid dark cylinders, and all support contacts need explicit design and measurement.

## Preserved attempts

`factory/offline-grasp-001` completed geometry then failed at missing SciPy in `.venv-base`: exit 1/1.096 s, owned identity gone. This setup failure is not a geometry or IK failure. The complete original script/runner snapshots are retained.

`factory/offline-grasp-002` used the already existing `.venv` SciPy, without importing SDKs or synchronizing an environment: exit 0/5.770 s, owner identity gone, inputs/source hashes unchanged. All 25 numerical results and failed searches are retained; source/runner snapshots are retained.

`factory/static-support-001.json` and `static-support-001-process` contain the actual triangle witnesses, independent intersecting/disjoint coplanar examples, unchanged input hashes and a normally reaped current creating handle: exit 0/0.480 s. No SDK, exports, ports, package validation or public state was involved.

## Proposed next physical probe: isolate the promised risk

Before any full factory export, use one external diagnostic scene containing the unchanged builtin robot, one exact original-size tube visual/collider, the measured original bench/rack support, and the two opposite original socket targets. The tube collider is an explicitly recorded conservative solid convex hull derived from the original tube shell; mass/inertia are estimates with their real gap. Do not call this an imported or validated `fr3-lab`.

1. First evaluate all retained taught vectors through real Newton FK at the original base; record actual TCP/Site distances and collision contacts. Keep the far-slot failure visible, and do not remove promised locations to obtain a green P6 report.
2. With no invocation active, run the unmodified required 480 steps/2 s from the original initial state, without warmup or body exclusions. Capture actual P2 displacement and P3 maximum penetration for the tube/support. Keep the first failure.
3. Only if the static physical state is usable, invoke the existing weld-mode `pick` from the rack via the real InfoHost/PhysicsPort path. Record accepted/rejected state, actual body/TCP/finger poses, weld transition, minimum separation or collision contacts, lift and release. No claim of contact grasping; static eligibility only exposes a pre-contact capture risk, while dynamic capture remains unverified until this probe observes it. Independently require actual lift ≥20mm, inactive release weld and measured release displacement >1mm, the existing diagnostic gates.
4. Keep this first run no Source/HTTP/browser, CPU1, one fixture, 90 s execution +5 s current-handle cleanup, dedicated kernel cache and no automatic rerun. Preserve original builtin bytes and exact fixture/script hashes. If fixture construction or the original physical geometry fails, stop at that specific result before adding adaptations.

The concrete fixture is now authored at `factory/original_tube_probe_fixture/`; script `probe_original_tube.py` and owned runner `run_original_tube_probe.py` are Ruff/syntax checked but **have not executed SDKs or parsed USD**. Frozen inputs/head/tree are recorded in `factory/original-tube-probe-prep.json`. The conservative original-scale tube hull has 806 source points/286 faces, volume 2.9952249e-5 m³, estimated mass 0.015 kg and density 500.797 kg/m³. Original GLB bytes are copied unchanged. Seven bench components and the actual Hero rack cubes are represented by explicitly conservative bounds proxies; all four original slot candidates remain FK targets, with their operational approaches expressly unverified.

The initial approach search from HOME hit its 90-evaluation limit and missed the diagnostic 1-degree orientation condition; this failed preparation is preserved in `original-tube-prep-001-process`. Seeding the same search from the already found rack grasp solved the 150 mm lift approach without changing thresholds; `original-tube-prep-002-process` exited 0/2.131 s with no SDK imports. This is offline teaching only.

The next SDK command, **only after PM opens its execution window**, is:

```powershell
& 'D:/Projects/Robots/spatial-sync/.worktrees/issue-18/.venv-base/Scripts/python.exe' 'D:/Projects/Robots/spatial-sync/.scratch/rsi-platform/issue-18/factory/run_original_tube_probe.py'
```

The runner freezes `f7a480f`/tree `9ce029f`, scripts and fixture, snapshots them, tracks the creating handle plus descendant PID/creation-time/command, owns a fresh kernel cache, runs at CPU1 for 90 s +5 s cleanup, and preserves actual exit/nonzero results without a retry. No Source/HTTP/browser or port is started. It refuses an existing first-run output directory and reports AccessDenied as unknown.

Actual first SDK execution `original-tube-001` stopped at structural setup: S7 could not read the generated USD dependency, and the resulting missing resources produced S8 expected-zero-joint failures. It did not build a model, run 480 steps, enable a weld or test any physical gate. Exit1/6.527s, cleanup0.000611s; independent `original-tube-001-process/final-check.json` confirms all three recorded identities gone, live/unknown empty, frozen fixture/source/builtin hashes unchanged. Preserve this setup failure and its exact snapshot; diagnose the underlying parser exception before authoring a revised fixture or proposing another physical execution window.

The exact read-only Sdf error was `base.usda:220:20 Expected } at def Xform A1 in /World/Lab/Rack`. Adding only statement/brace newlines repaired both Sdf parses. `original-usda-whitespace-repair.json` proves equal nonwhitespace token streams and complete numerical token sequences; all other fixture files/GLB and all builtin assets/URDF stayed byte-identical to001. Original001 source/fixture snapshots remain intact. No physics semantics or gate changed.

## Actual second physical probe

PM authorized exactly one whitespace-repaired002 execution. `original-tube-002` reached the actual CPU model, original480step settling, P6 and the real existing `pick` pipeline. It exited1/30.667s normally, cleanup0.000247s; independent final-check confirms all3recorded identities gone, live/unknown empty and all frozen inputs/builtin bytes unchanged. No Source/HTTP/ports were started. All actual kernels/model used CPU; Warp still enumerated CUDA, as in earlier runs.

- P1 finite-state pass. Initial joint checks pass.
- Original-state P2 measured tube displacement0.367420mm after exactly2s, within5mm. P3 measured maximum0.363111mm, within2mm, with a **warning** that325world-static pairs from26static proxies were not queried. These are the small diagnostic's results, not full A3/package acceptance.
- RealNewtonP6 rack0.000109mm; slots2/3/4 are0.000434/0.000169/0.369964mm. The far slot1 fails18.602685mm. All promised slots remain present; no all-P6 or scene launchability claim.
- Pick accepted then failed `motion_timeout` during descent, progress3/7. Final TCP Z0.996948m remains about35mm above original bottom Site. Maximum actual grasp-joint error0.08019rad; gripper stays80mm, holdingnull. No weld transition occurred, lift was absent and release was not attempted. Thus pre-contact weld capture is still a risk, **not an observed dynamic fact**.
- Final actual collision query finds right fingertip against `Rack16`/original `Rack upright.013` at9.076909mm penetration. Link6/worktop contact is0.503mm. Tube/finger proximity rows have positive separation≥31.37mm and are not touch contacts. This supports a grasp-descent obstruction diagnosis.

## Bounded unchanged-geometry posture alternatives

`rack-corner-search-001` (exit0/3.188s) retained six horizontal corner-angle alternatives at a9mm TCP offset from the **unchanged** bottom Site. An offline actual-URDF control matches the failed model's measured TCP within0.0947µm and recovers the same obstructing finger/upright pair. Five alternatives found local IK solutions within original P6 bounds; their hand/wrist overlaps with the unchanged worktop are21.6–23.2mm. The sixth search failed its positional/orientation criteria. None justifies a new SDK run.

`rack-corner-search-002` (exit0/3.855s, internal2.686s, CPU1 with explicit HiGHS thread1) retained eight tilted alternatives: yaw45/90/0 with30/45/60degree tilts. Actual TCP-to-original-Site distances are about9mm, so the Site/frame/device model was not changed. All retain rack overlaps at the open80mm or17mm width endpoints. The closest candidate, yaw45/tilt30, has2.140mm palm/upright overlap while open and6.576mm fingertip/lower-lattice overlap at17mm. Other cases have10.16–17.30mm rack overlap. The60degree cases additionally overlap the original tube/palm convex solids; their positive common inscribed radius is1.84–2.76mm, explicitly **not** the Newton penetration-depth metric. The150mm approach endpoints clear except for the32µm base/worktop contact, but the grasp endpoints fail.

Both pure-math groups preserve all attempted vectors, failed searches, input hashes and numerical limits. No SDK/Source/export or fixture/program edit occurred. The finite searches do not prove global impossibility, continuous path clearance or physical contact performance. There is currently **no new candidate justified for an SDK retry**. Evaluate a documented mechanical conversion or a separately specified grasp-affordance model before further physical execution; do not change the real TCP into an object-bottom frame.

## Decision after that probe

The original source is expressly illustrative, not surveyed mechanical CAD. An operational conversion needs a documented appliance adaptation, rather than pretending the source has a functioning cavity and closed lid. Candidate choices to evaluate are a preserved-scale lid/hinge placement adaptation with an explicit chamber extension, or a newly designed seated-tube cavity with a physically reachable grasp. Neither is currently designed or validated. A complete seated-tube approach may expose the existing bottom-origin grasp limitation and require a separately approved device configuration change; no such field, DeviceModel or schema has been added.

Do not shrink the tube, move its body/Site origin to its center, suppress collisions, fabricate a license, reuse old-base teaching, export all 61 remaining groups, or declare A3/P2/P3/P6/I3 complete on this evidence. The missing original procedural-environment license remains a real library gap.
