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

A loadable candidate is **unqualified**. Its original immutable artifact keeps
`evaluations: []` and `qualified: false`; current API readback projects linked
platform evaluations and observation reports under `evidence` without changing
that artifact/content hash. Numerical fixtures, short exports and the failed T0
checkpoint do not establish contact-policy quality. #35's real learned Newton
pickPlace and actual256 independent trainer-state gate remain open. Promotion
is a separate capability.

## Evaluate an immutable policy

```powershell
lw eval run policy/fr3-pick-tube@1 --suite suite/fr3-pick-tube@1 --json
lw eval run checkpoint/job-EXAMPLE/checkpoints/run/model_0.pt --suite suite/fr3-pick-tube@1 --json
```

Use an existing compatible exact Suite and actual checkpoint reference. Checkpoint
intent reuses policy freezing and resolves to the same immutable candidate on
retry. Versioned script subjects need the separate program loader.

The existing evaluator retains its one Session slot and freezes the Suite's
evaluation Scene/Task/source. It verifies every original binding, replaces only
the target implementation, and independently compares Gateway/Runner Catalogs.
An absent or mismatched override rejects before running a taught fallback.
Published Scene packages and recording format remain unchanged; the recording
contains the actual policy Catalog. A future deployment Scene does not replace
the fixed experiment environment.

Candidate weights, checkpoint and training Task remain provenance. Comparison
uses the same fixed base Program slot for teacher/policy candidates, preserving
all non-target behavior. A private native probe records actual ORT version and
provider availability for both. The policy receipt must match that probe and
reports actual inference calls/finite outputs and a PhysicsPort-observation
digest. These counters do not assert committed joint targets or motion. The
platform grades the frozen Suite facts even if the candidate's own Task goal
reports success. Unknown comparison proof cannot establish a new Reference.

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

## Freeze and verify source-Job evidence

A Task may implement synchronous
`env.unwrapped.lw_policy_observation_samples(256) -> {method, samples}`.
The owned training Bridge calls it after exporting the exact learned model.
Samples supply unique `sourceStateId`, raw canonical `state`, independently
captured `trainingObservation`, raw `previousAction` and optional `references`.
The Bridge freezes a versioned envelope and capture receipt with the actual
Task hook/code, fenced Job dispatch, model/observation hashes, versions and IDs.
An old Task without the hook retains missing evidence.

```powershell
lw policy check-observations policy/fr3-pick-tube@1 --artifact observations/samples.json --json
lw policy observation-checks policy/fr3-pick-tube@1 --json
lw policy verify-observation-source policy/fr3-pick-tube@1 check-EXAMPLE --artifact observations/capture.json --json
```

These commands and generic MCP methods consume existing authenticated source-Job
artifacts; they accept no host paths, score upload or qualified flag. Reports
freeze the input bytes, implementation/runtime identity and recomputed per-term
errors. Failed numerical checks, identical retries and restart readback remain
available. `verify-observation-source` separately checks the actual hook/Task,
dispatch, succeeded process, weights, versions, IDs and hashes against the
capture receipt. Its `capture_contract_verified` status validates those source
facts; it explicitly does not establish actual training quality or independent
observation semantics. The frozen Policy package retains `qualified:false`.

## Native observation qualification

Reviewed source profiles live in the repository's `library/observation-profiles/`.
The authenticated Job input selects a profile by exact Task hash and RSL version;
a Task or HTTP caller cannot register one. Without a matching profile, native
qualification remains unavailable. The default registry currently has no admitted
real training profile.

The owned environment observer tracks successful step/reset calls independently
of view IPC. For the pinned RSL MLP boundary, the collector copies actual inputs
to the actor normalizer only during `alg.act`, records matching physical state
and raw previous actions, and excludes SGD calls. A reviewed Task provider maps
physical channels; it cannot return the actor vector or previous action as proof.
The verifier requires256 distinct environment/epoch/physics-step witnesses,
the exact succeeded fenced Job/export/Task/Scene, reviewed implementation hashes
and maximum recomputed observation error ≤1e-4. Normalization stays inside the
exported graph. Unknown exporters or external normalization remain unverified.

Native artifacts use `observations/native-samples.json` and
`observations/native-capture.json` with the same check/verify commands above.
Derived qualification is separate from the Policy package's immutable creation
flag. Controlled constructor adapters used by transaction tests retain their
controlled provenance and do not establish actual learned/contact quality.

## Request human Controller promotion

```powershell
lw policy promote policy/fr3-pick-tube@1 --scene robot-and-tubes@2 --evaluation eval-EXAMPLE --request-id promotion-EXAMPLE --json
lw promotion show promotion-EXAMPLE --json
```

Use the promotion ID returned by the first command. `policy_promote` and
`controller_promotion_show` are the equivalent generic MCP tools. Requests use
`POST /api/v1/controller-promotions`; no command sets a qualified flag or human
decision. The bundled `lab-train-evaluate` skill follows approved work and public
train evidence through this same pipeline.

Admission requires this exact Candidate to remain the current accepted train
row, its complete prior comparable Reference and trusted observation qualification.
The platform recomputes acceptance against that prior Reference, then charges
both private test scopes to the existing approved Plan budget and shared queue.
Test results never advance the train Reference. Ordinary Evaluation, Session,
recording, Notebook, runtime, cost-detail and Inbox reads hide private evidence.

The existing approval page fetches a human-only review with train/test scores
and a red warning when Candidate test is below Reference. Approval publishes an
immutable derived Scene, immutable Controller version and current pointer in
that order. Retries verify and reuse reserved destinations. A concurrent current
or Reference change blocks the old intent. Normal Scene validation remains in
force; a controlled structural fixture does not certify physical stability.

The robot resource page shows the committed default Scene, version, composition
and exact train Evaluation. Policy action frequency comes from the actual frozen
Task's `control.hz`, with `action.hzSource`; an undeclared script tick frequency
stays unknown with its reason. Model parameters and licence remain unknown when
their source does not establish them. Active Sessions keep their loaded identity.

New interactive Sessions require a committed human proof matching actual Scene,
Controller, complete bindings and immutable material. An imported policy binding
alone cannot authorize execution. A later Training Plan revision/revocation
blocks new evaluation work but preserves an already committed deployment proof;
rollback requires a new explicit transaction.
