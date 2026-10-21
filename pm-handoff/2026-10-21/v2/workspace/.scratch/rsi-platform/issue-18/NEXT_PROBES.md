# #18 — next bounded probes (not executed)

Inventory001 passed in 6.7527s; two recorded process identities are gone, no unknowns, source/working bytes unchanged. Partition assigns all 1,653 daylight objects exactly once. Everything below needs a separate execution window.

## Visual probe: five actual partitions, no physics

Inputs: `factory/working/laboratory_final.blend` SHA `c19aa24…96947f`; `factory/partition-plan.json`; reviewed `factory/export_visuals.py`. Groups: `bench-02`, `rack-03-tube-01`, `centrifuge-housing`, `centrifuge-lid`, `centrifuge-rotor`. Together 39,640 raw triangles; actual evaluated counts remain unknown. This covers a real ~129mm tube, bevel/font conversion, source scale, a transparent material, and three independent appliance components. Do not begin with all 66 partitions or the room.

Exact child command:

```powershell
$fr3FactoryRoot = 'D:/Projects/Robots/spatial-sync/.scratch/rsi-platform/issue-18/factory'
& "$fr3FactoryRoot/tools/portable/blender-4.3.2-windows-x64/blender.exe" --background --factory-startup --disable-autoexec --threads 1 --python-exit-code 1 --python "$fr3FactoryRoot/export_visuals.py" -- --group bench-02 --group rack-03-tube-01 --group centrifuge-housing --group centrifuge-lid --group centrifuge-rotor --output "$fr3FactoryRoot/visual-probe-001"
```

Budget: 90s execution +5s owned cleanup, CPU thread1, CUDA hidden, private Blender config/scripts, no render, .blend save, USD/physics or Source. Parent holds the current Popen handle and records descendant PID/creation-time/command until actual exit. On timeout kill only that current tree, then only identities still matching the current receipt; retain the first red output and do not retry automatically. Rehash working/extracted `.blend` and original archives, confirm every recorded owner/child gone or report unknowns before releasing the CPU.

Output: five GLBs and `visual-staging.json`; these are explicitly **not yet importable Assets**. Required checks:

1. Evaluated triangles ≤500,000 per group; independently decoded GLB triangle totals equal measured evaluated meshes; finite positions, no animations/cameras/external image URIs. No texture input exists, so expected embedded image count is zero. Z-up export explicitly disables glTF's automatic Y-up conversion.
2. Independent GLB POSITION + node-transform decoding, followed by the recorded rigid placement transform, must reconstruct the separately measured **original evaluated world bounds** (`boundsWorldSourceEvaluated`); source scale is baked once, with no axis swap. Use float32 representation error at actual coordinate magnitude, not a geometric tolerance large enough to hide centimetre-scale mistakes.
3. Tube retains actual source geometry/height; no 129→30mm conversion. Bench world footprint/top must reconstruct the original geometry. Lid and rotor each contain only their assigned objects, not the whole appliance; 16 baked-in occupied sample pieces remain excluded. All source objects remain assigned once.
4. Preserve Principled scalar/color PBR inputs. The two transparent Mix materials get an explicit alpha adaptation; unsupported procedural noise/bump is omitted with a named adaptation record, not silently claimed baked. Review the exported material values and transparency before expanding. A missing original license remains a real gap; no placeholder license is created.

## Minimum SDK evidence: separate, prior to any runtime fix

Prepare one test-only composed USD fixture referencing the actual immutable builtin `robot/fr3@1`, plus one honestly identified diagnostic 129mm tube and a rotor Site/body. Fixture metadata is not a fabricated validation report or imported `fr3-lab`. Set the robot scene placement explicitly to `[.2,.7,.95]`. Use the **existing** effective bindings/Config fields for RobotArm hand/tubes and centrifuge slots; do not add schema fields. Construct through `prepare_scene_config()` so the validator and live producer share the real path.

- **Home/drive red:** inspect actual imported joint q, limits, target modes and ke/kd after real model creation/FK. Assert seven q equal the intended HOME, initial-joint P5 has no fail, and configured arm/finger drives exist. The unmodified builtin stage has no state/Drive opinions, so retain the actual result even if it disproves the predicted zero/default failure. Also read the actual base/TCP poses: this catches a world-fixed anchor or frame error rather than assuming composed translation worked.
- **Weld red:** resolve the actual imported hand, tube and Site-owning rotor body indices. Construct the existing Welds/PhysicsPort boundary and request the exact existing program pairs; assert the hand×tube and slot-owner×tube pairs were predeclared disabled. Capture the actual missing-pair exception and/or failed assertion on the unmodified assembly. Do not infer failure just from source text and do not treat unrelated model-loading errors as the intended red.
- Only after those results, repair the assembly in its existing simulation/program boundary with `welds.py` and existing Config semantics; use stronger **scene USD** state/Drive opinions with exact degree/radian and gain conversion, leaving builtin bytes untouched. Same fixture then green, including nonzero joint-motion feedback and actual weld enable/disable/no-teleport checks; no new resource framework or HTTP/MCP device branch. P2/P3/P6 and the full lab journey remain separate later evidence.

Concrete fixture is now `factory/sdk_probe_fixture/{scene.yaml,devices.yaml,locations.yaml,stage.usda}`; executable tests are `factory/test_sdk_resources_probe.py`. Three assertions share one real model/Manifest/PhysicsPort/InfoHost observation: HOME/P5/drives/base pose, declared disabled weld pairs, actual program initialization. The fixture uses real production structural validation and program schemas; its report is an observed diagnostic, never a fabricated package validation file. All builtin FR3 bytes are hashed before/after. The 129mm diagnostic tube keeps its bottom origin; this cylinder is explicitly a test-only shape, not a substitute for the source Blender tube. No simulation stepping, Source or ports.

Proposed first SDK red window: 90s +5s cleanup, CPU1, CUDA hidden, fresh own kernel cache; retain the first actual red or unrelated setup failure and do not retry automatically. Exact child command below still requires PM execution authorization and a creating-handle cleanup receipt:

```powershell
& 'D:/Projects/Robots/spatial-sync/.worktrees/issue-18/.venv/Scripts/python.exe' -m pytest 'D:/Projects/Robots/spatial-sync/.scratch/rsi-platform/issue-18/factory/test_sdk_resources_probe.py' -q --basetemp 'D:/Projects/Robots/spatial-sync/.scratch/rsi-platform/issue-18/factory/sdk-red-001/pytest' --junitxml 'D:/Projects/Robots/spatial-sync/.scratch/rsi-platform/issue-18/factory/sdk-red-001/junit.xml'
```

Script/fixture hashes and syntax/Ruff results are in `factory/sdk-probe-prep.json`. It has not executed; actual SDK behavior remains unverified.
