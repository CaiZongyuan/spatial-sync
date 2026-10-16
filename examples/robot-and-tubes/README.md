# Official Franka FR3 laboratory

`robot-and-tubes` imports the official Newton example FR3 URDF from the bundled,
versioned `assets/robots/franka_emika_panda` package. It has seven revolute arm joints,
two prismatic fingers, a TCP Site, 20 dynamic tubes, a workbench and a driven centrifuge.
Newton's SolverMuJoCo is the default, using native MuJoCo contacts. It uses the official
CPU backend on CPU and mujoco-warp on CUDA; it never substitutes another engine silently.

```powershell
uv sync --extra simulation --extra usd
uv run --extra simulation python tools/smoke_newton_source.py --scene robot-and-tubes --device cpu --crash
uv run --extra simulation python tools/smoke_newton_source.py --scene robot-and-tubes --device cuda:0 --crash
uv run --extra simulation python tools/viser_reference.py robot-and-tubes
uv run --extra simulation python tools/check_robot_solvers.py --device cpu
```

For the Web Viewer, follow the root README, choose `robot-and-tubes` and source `newton`.
P1-B's mesh exporter supplies the robot GLB geometry. Assets are loaded locally; no robot
download occurs while starting an API session. The first CUDA run may compile for around
two minutes. The source waits up to 180 s during loading and restores the initial FK state
after its 120-step warmup before advertising READY.

After reset, inspect these Z-up physics world coordinates (meters):

| Entity | Key | Initial value |
|---|---|---|
| Hand Body | `/World/Lab/RobotA/fr3/fr3_hand` | p ≈ `[-0.07282344, 0, 1.37043449]` |
| TCP Site | `/World/Lab/RobotA/fr3/fr3_hand/tcp` | p ≈ `[-0.07282344, 0, 1.26703449]` |
| Fourth arm joint | `/World/Lab/RobotA/fr3/fr3_joint4` | q = `-2.0 rad` |
| First finger | `/World/Lab/RobotA/fr3/fr3_finger_joint1` | q = `0.02 m` |

The TCP local offset from the hand is `[0,0,0.1034]`; the hand faces down at home.
Body and Site origins therefore differ. Joint coordinates come from authoritative
`state.joint_q`, preserving continuous rotations beyond π.

The unmodified Featherstone comparison does not pass the complete contact workload at
the default 1/240 s timestep: the light tubes escape the table. Its reported timings cover
only the separately verified 0.125 s precontact subset. The default remains MuJoCo.
The uncaptured single-world CUDA path is correct but slower than the CPU path on the
specified development machine; use diagnostics to inspect actual RTF.

Newton 1.7's URDF importer omits labels on its imported Shapes. Their known provenance
produces 54 `unlabeled_entity` warnings; body/joint/TCP IDs remain stable. Strict label mode
rejects those missing Shape labels. See the P1-A evidence report for the precise scope.
