# Policy candidates

`lw policy create <jobId>/<checkpoint-relative-path>` freezes a succeeded Job's
validated export as `policy/<Task.name>@N`. `lw policy ls` and `lw policy show`
read the same `/api/v1/policies` resources as the generic MCP tools
`policy_create`, `policy_list`, and `policy_show`. No Session is created.

Creation rechecks the Job artifact manifest, exact checkpoint/export/ONNX hashes,
external weights, `versions.json`, and pinned Task/Scene hashes. It copies the
ONNX and its external weights, Task, observation specification and provenance
into the existing user library's immutable `policies/<name>/<revision>/` tree.
Identical retries return the same version; changed sources or snapshots reject.
The CPU loader requires one fixed float32 `[1,N]` observation input and `[1,A]`
action output, and registers only `CPUExecutionProvider`. `onnx` and the CPU
distribution of `onnxruntime` are frozen in the lockfile; neither imports Isaac Lab.

A loadable candidate is **unqualified**. The current candidate record keeps
`evaluations: []`, `qualified: false`, and names the missing platform Newton
evaluation and actual 256 trainer-state observation comparison. The existing
embodied evaluator still accepts scene-bound programs; policy/checkpoint Suite
binding and promotion are subsequent integration work. Numerical fixtures,
short training exports and the existing failed T0 checkpoint do not establish
contact-policy quality. #35's real Newton pickPlace and observation gate remain
open. There is no policy promotion endpoint in this engineering slice.

## Supported observation format

Historical Task observation files remain opaque and readable. Creation requires
the following explicitly versioned initial supported format:

```yaml
schema: lw.obs-spec/1
observations:
  - name: arm_q
    source: joint_position
    keys: [/World/Lab/RobotA/fr3/fr3_joint1]
    frame: world
    unit: rad
    shape: [1]
    offset: [0.0]
    scale: [1.0]
action:
  space: joint_position_delta
  jointKeys: [/World/Lab/RobotA/fr3/fr3_joint1]
  units: [rad]
  scale: [0.05]
  lower: [-2.7]
  upper: [2.7]
  maxDelta: [0.05]
```

The example demonstrates one channel's syntax; actual keys and all bounds must
come from the pinned robot/Task. Terms concatenate in document order. Supported
sources are `joint_position`, `joint_velocity`, `body_position`,
`body_orientation`, `site_position`, `site_orientation`, and `previous_action`.
Joint terms use distinct ordered `keys`; pose terms use one `key`. `shape` is a
single flat dimension and `offset`/`scale` must have that exact length. The
formula is `(raw - offset) * scale`. Values must remain finite float32.

Newton coordinates remain Z-up; Cartesian positions use metres and orientations
use normalized xyzw quaternions. Angular joint terms use rad/rad/s; separate
prismatic terms use m/m/s. Pose orientation terms use unit `xyzw`, shape `[4]`;
positions use `m`, shape `[3]`. Only Cartesian positions support a relative body
`frame`, using the inverse actual body rotation. Every other term uses `world`.

`previous_action` is the model's **raw output before action scaling or clipping**;
it starts at zero for each invocation, uses unit `1`, and has the action shape.
Action channels map directly to distinct configured arm/finger joints. Each raw
output is scaled, bounded by `maxDelta`, added to the measured joint position,
then clipped to the declared lower/upper limits. There is no implicit gripper
channel coupling or normalization.

Explicit canonical keys are preferred. `$from`/`$to` may select the canonical
Site in the fixed configured location; `$object` selects the only configured
tube whose observed location is that exact source Site. Missing or ambiguous
matches reject. No nearest-body or geometry guess fills missing references.

`robot_arm.policy` implements the existing robot-arm interface through Program
and PhysicsPort, with contact-derived holding facts and joint targets. It never
calls weld/unweld. Only the Task's declared action with its exact
`episode.params` is admitted, plus concurrent `stop`. Other actions/parameters
return `precondition_failed`. The evaluation parameter grid does not establish
training coverage. Success uses the frozen Task condition and observed facts;
timeout, cancellation and inference failure hold the current actuator positions.
Task control frequency must fit the Runner's control frequency.

The Program reserves the configuration's unique interlocked location zone at authoritative
admission, before any queued motion or policy target. A black box has no trusted
pick/place stage, so the reservation persists throughout the action and through
stop, cancellation, success, timeout or inference failure. Multiple interlocked
zones cannot be represented by the existing single `zone` property and reject.
A Task naming only rack sites retains the same reservation because the black
box has no path constraint. Without an interlocked zone, named Task locations
(or all configured locations for an action without names) must select one zone.

Idle facts update `zone`/`atLocation` only from actual seven-joint feedback that
matches home or a known taught approach/grasp, using the existing strict
position error <0.01 rad and velocity <0.05 rad/s bounds. Unknown feedback keeps
the previous reservation; an unknown initial pose leaves the property UNKNOWN,
so existing interlocks fail closed. No stop/terminal flag alone clears a zone.

## Observation comparison

Export raw training states and their independently computed observations to a
JSON array. Each element contains `state` (`joints: {key: {q,qd}}`,
`bodies/sites: {key: {p,q}}`), `trainingObservation`, `previousAction`, and optional
`references`. The trainer must retain the actual Task/export/version identities
and sampling provenance with the manual evidence.

```powershell
uv run --no-sync python tools/check_policy_observations.py obs_spec.yaml trainer-states.json
```

The tool reports per-term and global maximum error and file hashes. A numerical
pass requires at least 256 supplied samples and maximum error ≤1e-4; its exit
code is 0 for pass, 1 for insufficient/divergent samples, 2 for invalid inputs.
It always labels provenance as caller supplied and never qualifies/promotes a
policy itself. The engineering fixture is not the required real trainer sample.
`sim_gap` compares success fractions and triggers strictly above fifteen
percentage points; unknown rates remain unknown.
