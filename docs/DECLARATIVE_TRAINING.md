# Declarative mjlab Tasks and Policy Export

Publish a directory with `lw task push <directory>`. A managed Task contains
`task.yaml` and the observation document named by its `observation` field. The
Task pins an exact Newton USD Scene and robot Prim Path. Use the existing
`implements`, `episode`, `success`, `evaluation`, `loopBudget` and `control`
fields. Declare the physics timestep and decimation in `mjlab`; their product
is the reciprocal of `control.hz`. `mjlab.task_id` is metadata on this path
because the managed launcher builds the environment directly.

```yaml
mjlab: {task_id: Lab-DataTask-v1, sceneInput: usd, physics_dt: 0.002, decimation: 10}
declarative:
  schema: lw.mjlab-task/1
  baseBody: /World/Lab/Bot/Base
  episodeSeconds: 6.0
  rewards:
    - {kind: upright, weight: 1.0}
    - {kind: height, weight: 0.4, target: 0.2}
    - {kind: action_rate, weight: 0.01}
  terminations:
    - {kind: fall_height, threshold: 0.05}
  reset:
    jointPosition: [0.04, 0.02]
    jointVelocity: 0.05
completion: {mode: posture_recovery, dwellSeconds: 0.25, uprightTiltDeg: 15.0}
```

The other Layer A rewards are `joint_tracking`, using a pinned reference, and
`velocity_tracking`, using an episode command. Joint tracking and action rate
return negative squared errors, so use positive weights to penalize error.
A `tilt` termination uses degrees. Reset offsets follow actuator order in SI
units. Optional `declarative.command` contains three body-frame lower/upper
pairs in `vx, vy, wz` order, sampled at reset from the seeded environment.
Each pair fits `episode.commandEnvelope` and the observation bounds. The
managed compiler supports one USD robot and the verified flat-plane composition.

Existing built-in Python archives are admitted by exact version and bytes.
For a human-reviewed Python archive, the host operator configures
`LW_TASK_AUDIT_FILE` as an absolute path to an operator-owned JSON registry
outside the uploaded library. Each record has `contentHash`, `reviewer` and
`note`. Compute its package digest with the platform's
`packages.library.common.content_hash(Path(directory))` before adding the
review entry. Executable archives use the existing backend entrypoint only
while their exact digest has current authority. Existing snapshots remain
readable. Data-only Tasks skip building or installing the archive.

The node uses the same managed entry for plan validation and training.
The compiler identity includes its shipped source files. The gateway
selects native input verification using that digest, the leased runtime and
the immutable Task. `lw policy check-observations` and
`lw policy verify-observation-source` accept the existing source-Job artifact
references. Declarative Jobs produce v2 sample and receipt documents with the
managed compiler identity. Promotion still uses the observation proof and
the human Controller decision.

## Reference motion and completion

A Task can embed a short `referenceMotion` for a pinned robot Asset version.
The object contains `asset`, `assetContentHash`, `license`, ordered
`jointKeys` relative to `/Asset`, `loop` and timestamped `frames`. Frames
contain `time` in seconds, root `position` in metres, `orientation` as a
unit `xyzw` quaternion, and `joints` in the declared order using SI. Frame
0 starts at time zero and later times strictly increase.

`lw.obs-spec/2` adds two flat observations. Both use `frame: world` and
`unit: "1"` for the composite tensor.

- `motion_phase` uses `[sin, cos]` for a loop and seconds remaining for a
  one-shot. Its width is respectively 2 or 1.
- `reference_motion_frame` contains root XYZ, root XYZW and interpolated
  joint positions. Its width is 7 plus the number of reference joints.
  Quaternions use shortest-arc spherical interpolation. Time comes from
  simulation progress and resets with the invocation or episode.
  One-shot sampling holds the last frame.

`embodied.policy` reads each Policy Task's `completion.mode`. `phase_clip`
runs to the end of the one-shot or the requested loop count.
`posture_recovery` requires upright, calm physical feedback.
`command_track` checks observed body velocity against the Action's bounded
command. `waypoint_nav` uses the current Scene's named Waypoints and shares
the body-frame guidance calculation with the built-in biped. Use ordinary
Catalog actions and the generic invocation tools for all four modes.

## Standalone export

```text
lw policy export policy/<name>@<revision> <new-file.zip>
python infer.py samples.json
```

The archive includes one fixed-shape ONNX graph, its external tensor files
when present, the manifest and a reference script that imports NumPy and
CPU ONNX Runtime. Run the command from the extracted directory. Input is a
JSON array of canonical samples with `state`, optional `command` and
`references`, and an optional `reset` flag. State includes `joints`
(`q`, `qd`), body or Site poses and world angular velocities as required
by the observation. Motion-enabled samples also include `motionElapsed`.
The script returns the assembled observation, raw output, clipped canonical
targets and source targets where a calibrated mapping exists.

The source calibration reader consumes `joint-calibration.json` from the
exact robot Asset. The schema is `lw.joint-calibration/1`. Store a rigid
row-major homogeneous `baseTransform` and one-to-one `joints` records with
`jointKey`, `sourceName`, `sign`, `zeroOffset` and `unit`. The equation
is `canonical = sign * source + zeroOffset` in SI units. For example, with
sign −1 and zero offset 0.25 rad, a canonical 0.40 rad command maps to
−0.15 rad at the source. The archive exposes unavailable calibration and
physical metadata for older material where the frozen inputs lack those facts.
