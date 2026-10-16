# Official Franka FR3 laboratory

`robot-and-tubes` imports the official Newton example FR3 URDF from the bundled,
versioned `assets/robots/franka_emika_panda` package. It has seven revolute arm joints,
two prismatic fingers, a TCP Site, 20 dynamic tubes, a rack and a driven centrifuge.
Newton's SolverMuJoCo is the default, using native MuJoCo contacts. It uses the official
CPU backend on CPU and mujoco-warp on CUDA; it never substitutes another engine silently.

```powershell
uv sync --frozen --group dev --extra simulation --extra usd
uv run --no-sync python tools/smoke_newton_source.py --scene robot-and-tubes --device cpu --crash
uv run --no-sync python tools/smoke_newton_source.py --scene robot-and-tubes --device cuda:0 --crash
uv run --no-sync python tools/viser_reference.py robot-and-tubes
uv run --no-sync python tools/check_robot_solvers.py --device cpu
```

For the Web Viewer, follow the root README, choose `robot-and-tubes` and source `newton`.
The mesh exporter supplies the robot GLB geometry. Assets are loaded locally; no robot
download occurs while starting an API session. The first CUDA run may compile for around
two minutes. The source waits up to 180 s during loading and restores the initial FK state
after its 120-step warmup before advertising READY.

After reset, inspect these Z-up physics world coordinates (meters):

| Entity | Key | Initial value |
|---|---|---|
| Hand Body | `/World/Lab/RobotA/fr3/fr3_hand` | p ≈ `[-0.07282344, 0, 1.37043449]` |
| TCP Site | `/World/Lab/RobotA/fr3/fr3_hand/tcp` | p ≈ `[-0.07282344, 0, 1.26703449]` |
| Fourth arm joint | `/World/Lab/RobotA/fr3/fr3_joint4` | q = `-2.0 rad` |
| First finger | `/World/Lab/RobotA/fr3/fr3_finger_joint1` | q = `0.04 m` |

The TCP local offset from the hand is `[0,0,0.1034]`; the hand faces down at home.
Body and Site origins therefore differ. Joint coordinates come from authoritative
`state.joint_q`, preserving continuous rotations beyond π.

The arm holds its initial pose until an action runs. Device programs own joint targets,
physical tube facts and the lid/rotor lifecycle. The first four tubes are16mm ×30mm
microtubes at rack Sites; the remaining16 retain their original capsule geometry.
All tube bodies use a bottom origin. The checked-in twelve approach/grasp pairs
are joint-space teaching data. Runtime motion does not use IK or collision planning.

Run the complete CPU acceptance journey, including interlock/imbalance rejections,
with the installed CLI and a gateway:

```powershell
uv run --no-sync python tools/smoke_info_newton.py --url http://127.0.0.1:8000 --cli .venv/Scripts/lw.exe --duration 20
```

On Unix, use `.venv/bin/lw` for the installed executable. The smoke command creates
and closes its own session; run it with no existing workspace session.
The arm remains in the destination zone after a transfer; move it home before closing
the lid. Load the second tube into opposite slot3 before starting the centrifuge.
The declared spindle speed is capped visually at60rpm; requested rpm and the full
simulation-time duration remain observable. Final rotor alignment permits taught returns.
The measured contact spike selected runtime welds for grasping; constraints preserve
the current relative pose when enabled and release back to contact physics.

After moving the arm with `lw do ... moveJoints`, capture its measured joint state
into a local teaching copy:

```powershell
uv run --no-sync python tools/teach.py --capture rack.A1 --pose grasp --output .scratch/manual.locations.yaml
```

`LW_URL` and `LW_SESSION` select the gateway/session, as with lw. Capture merges only
the named approach/grasp vector and warns when grasp TCP forward kinematics is more
than1cm from its Site. `--write` separately regenerates all default offline waypoints.

The Featherstone comparison does not support this final contact scene: light seated
tubes become nonfinite even at the fine reference step. The comparison tool reports
unsupported status and no valid timing samples. The default remains MuJoCo.
The uncaptured single-world CUDA path is correct but slower than the CPU path on the
specified development machine; use diagnostics to inspect actual RTF.

Newton 1.7's URDF importer omits labels on its imported Shapes. Their known provenance
produces 54 `unlabeled_entity` warnings; body/joint/TCP IDs remain stable. Strict label mode
rejects those missing Shape labels. Source asset attribution is bundled with the robot.
