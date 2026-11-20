# Local training node

Jobs reference immutable Task, scene and asset packages. An approved plan declares
the permitted seeds and configuration. `lw job start` queues one seed; unknown
timing remains `estimate_unavailable`, and an estimate beyond the remaining plan
budget waits for a separate human job approval. GPU ownership covers preparation,
training and cleanup. A lost node fails its job after 60 seconds and retains the
physical lease until owner reconciliation confirms cleanup.

The local node runs in a subprocess and creates a private Task environment over
an existing, frozen Isaac Lab runtime. It never synchronizes that base runtime.
Configure the runtime's Python command before starting the gateway:

```powershell
$env:SPATIAL_NODE_TRAINER = '["D:/path/to/isaac-env/Scripts/python.exe","-m","isaaclab"]'
$env:SPATIAL_NODE_GATEWAY_URL = 'http://127.0.0.1:8000'
uv run --no-sync python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

Without a configured trainer, job metadata remains usable and no trainer process
starts. The gateway generates a private node credential separately from the
user's provider Vault. Its identity survives gateway restart. Internal heartbeat,
input and artifact endpoints require that credential and the exact node/dispatch
identity; ordinary operator or MCP credentials do not authorize node callbacks.

```text
lw node ls --json
lw job start plan-0007 --seed 42 --request-id round7-seed42 --json
lw job ls --plan plan-0007 --json
lw job show job-id --json
lw job logs job-id --follow --json
lw job cancel job-id --json
```

Use actual returned IDs. Stable request IDs make retries return the same Job,
including after a newer unapproved plan revision exists. Each Job retains its
original approved revision; it never substitutes the latest plan content.

## Upgrade a deployment with legacy local Jobs

A gateway restart can leave the old local node and its trainer alive while a Job
owns a lease. Before upgrading from a release whose node uploads pickle
checkpoints, pause new Job submissions and cancel queued Jobs, including Jobs
waiting for approval. Cancel each active legacy Job through the old gateway
while its owning node can still report process cleanup and final logs.

```text
lw job ls --json
lw job cancel job-id --json
lw job logs job-id --follow --json
lw job show job-id --json
lw node ls --json
```

Use the actual Job IDs. Wait for terminal Job status, a log chunk with
`complete: true`, and an empty local lease and quarantine set. Then shut down
the old gateway normally so its supervisor can stop the idle local node. Start
the upgraded gateway and node with the same state and library directories.
Cancelling a legacy Job preserves its logs and cost history. Any unfinished
training must start again from the approved plan.

If the gateway has already been replaced and the legacy node still owns the
same lease, use the same cancellation and settlement commands while the node
remains connected. The upgraded gateway rejects legacy `.pt` uploads. A Job
whose upload was already rejected may finish as failed. If a lease remains
quarantined, wait for its existing owner reconciliation before starting more
work on that device. Previously committed Job artifacts and frozen Policies
remain readable using their original references.

## First measured profile

The node advertises its actual hardware/resources and begins with no invented
timings. A measurement must identify the exact Task content hash, algorithm,
model, hyperparameters and advertised resource. Store measurement data outside
the Task directory so measuring it does not change its own content hash.

After a bounded, authorized calibration on that exact configuration, save the
actual per-iteration samples and setup duration. A modern receipt records the
runtime from the measurement, including the entry, complete package versions,
and the managed mjlab compiler hash. For Isaac Lab the compiler hash is null.

```json
{
  "schema": "lw.training-profile/2",
  "taskContentHash": "the Task contentHash returned by the platform",
  "hardware": "the local node hardware identity returned by lw node ls",
  "resourceId": "the advertised GPU UUID, or cpu",
  "algorithm": {"library": "rsl_rl", "name": "PPO", "version": "5.4.1"},
  "model": {"actor": [256, 128], "critic": [256, 128]},
  "hyperparameters": {"num_envs": 4, "max_iterations": 1, "device": "cpu"},
  "runtimeIdentity": {
    "backend": "isaaclab",
    "entry": "isaaclab:cli",
    "compilerHash": null,
    "trainerVersions": {
      "isaaclab": "3.0.0", "newton": "1.6.0rc1", "warp": "1.17.0",
      "torch": "2.11.0+cu128", "rsl_rl": "5.4.1"
    }
  },
  "samplesSeconds": [1.2, 1.3],
  "setupSeconds": 8.0,
  "dispatchOverheadSeconds": 2.0,
  "measuredAt": 1792776000.0
}
```

The numbers above illustrate the format; supply real observations for the chosen
Job. GPU calibration/execution still requires its existing authorization. CPU
work is represented as CPU, with no physical GPU or GPU-hours claim.

```text
uv run --no-sync python tools/import_training_profile.py --job job-id --resource cpu --measurement measurement.json --capabilities path/to/state/local-node/capabilities.json
```

The tool reads the Job's pinned revision, rejects mismatched scope and runtime,
retains the original receipt by SHA256 and adds the measured profile. Version 1
receipts remain readable for explicit pre-handshake local training capability
files. The default capability
file is `<SPATIAL_STATE_ROOT>/local-node/capabilities.json`; a configured
`SPATIAL_NODE_CAPABILITIES` path takes precedence. The node reloads profiles on
its next heartbeat, so an approved queued Job may then dispatch. Estimates
multiply the largest retained iteration sample by the requested iteration count
and add setup and dispatch overhead once. GPU dispatch requires a matching
measured profile.

Modern mjlab training and validation use the shipped managed launcher. The node
reports an incompatible entry when configured with a custom mjlab command.
Explicit pre-handshake local capability files retain their local training path.

## Inputs and outputs

The node downloads and verifies source-package hashes over HTTP, installs only
the selected Task into a private environment, and supplies `LW_NODE_INPUTS`,
`LW_TASK_PACKAGE`, `LW_SCENE_PACKAGE` and `LW_ASSET_PACKAGES` to its configuration.
`isaaclab.sceneInput` defaults to `usd`; `scene-package` explicitly delegates a
Python-builder scene adapter to the downstream Task. `robot-and-tubes@2` remains
a Python builder with no composed scene USD. A real Task adapter consuming this
scene is a separate, currently unverified input requirement; a Cartpole or fake
trainer does not establish that FR3 journey.

The supported launcher maps the approved RSL-RL PPO settings, sets
`PXR_WORK_THREAD_LIMIT=1`, and installs the bridge through the stock zero-argument
registration callback. The bridge preserves CLI arguments, observes the actual
learned runner and exports ONNX after learning. `export.json` links the exact
ONNX and checkpoint-source hashes. Portable Jobs send safetensors and JSON.
Other Jobs send `checkpoint-origin.json` with the digest of the exact
node-local checkpoint, including a valid zero-based `model_0.pt`. The node
retains the pickle file locally. Outputs include stdout/stderr, runtime versions,
seed and exact input identities. Ordinary legacy-runtime training also includes
TensorBoard events. A model file produced by a contract fixture proves transport
only.

With plan hyperparameter `device: cpu`, the launcher also sets
`CUDA_VISIBLE_DEVICES=-1`, `--device cpu` and `agent.device=cpu`. Native MuJoCo CPU
selection is added only for a declared `newton_mjwarp` preset; another declared
backend is preserved. This is CPU execution. It provides no GPU timing evidence.
A stock CPU trainer diagnostic establishes runtime plumbing. It does not supply
the Task-side FR3 scene adapter or establish the pinned-scene training journey.

The inspected frozen IsaacLab3.0.0/Newton1.6.0rc1 installation cannot complete its
native CPU contact initialization: four environments also hit a separate-worlds
restriction, and one environment still reaches unimplemented contact-report
methods. The bridge rejects that known native-MJWarp CPU pair before environment creation.
It preserves the approved environment count and GPU configuration. This is a
runtime compatibility limit; CPU process fixtures and zero-argument CLI parsing
are separate verification evidence.

## Baked training layouts and Layer B

Store and publish a Scene Draft with the same HTTP API used by CLI and MCP.

```sh
lw scene draft submit layout.yaml --json
lw scene draft show "$DRAFT_ID" --json
lw scene draft publish "$DRAFT_ID" --json
```

A `lw.scene-draft/1` layout supplies exact `asset` and `contentHash` pins,
placements, box/sphere/plane primitives and Sites. Positions and full box
sizes are metres, masses are kilograms, and orientations are unit xyzw
quaternions. A row of independent boxes makes the collision surface of stairs.
A rotated box supplies a slope. A sphere or box with a mass is a dynamic rigid
body. Waypoints and Teach Locations use the ordinary Scene location schema.
Publication runs the Scene validator and queues a Newton preview for a
launchable version. Repeating a publication preserves the original version.

In a declarative mjlab Task, `rigidReset` rows specify a canonical rigid body
`key`, three ordered `[lower, upper]` translation ranges and an optional
`yaw` range in radians. `targetSites` samples a baked Site at episode reset.
A Task with command bounds uses the selected Site to derive body-local planar
and yaw commands, clamped to those bounds. A `curriculum` supplies an ordered
list of levels with `spawn` Waypoints and `target` Sites, and metre thresholds
`successDistance` and `demoteDistance`. Each previously stepped environment
promotes within the former distance and demotes beyond the latter at reset.

A Task's optional `layerB` field declares `state: lw.reward-state/1`.
Reward expressions return a scalar and termination expressions return a
boolean. Constants, `{"state":"episode_time"}` and
`{"op":"sub","args":[1,{"state":"episode_time"}]}` show the expression
forms. `prop_position_world` and `prop_orientation_world` also require a
canonical Scene body `key`. The state uses local scene world coordinates with
Z up, right-handed body coordinates, SI linear/angular velocities, xyzw
quaternions and seconds. The compiler checks arity, fixed shapes and expression
paths, including a limit of 128 nodes and depth 12 per expression. Runtime
arithmetic clamps to ±10,000 and replaces NaN with zero. The preflight reports
the original non-finite node and fails before the training approval.

```sh
lw task push task-directory --json
lw plan submit plan.yaml --json
lw job start "$PLAN_ID" --kind plan_validation --node "$NODE_ID" --json
lw job show "$JOB_ID" --json
```

The vector3 fields are `base_position_world`, `base_linear_velocity_world`,
`base_angular_velocity_world`, `base_linear_velocity_body`,
`base_angular_velocity_body`, `prop_position_world`, `target_position_world`
and `command`. A command is ordered vx, vy, yaw rate. The quaternion fields
are `base_orientation_world` and `prop_orientation_world`. The scalar field
is `episode_time`. Operators include `add`, `sub`, `mul`, `div`, `neg`,
`abs`, `norm`, `dot`, `component`, `clamp`, `gaussian`, comparisons,
boolean operations and `where`. `component` takes a zero-based literal index
into vector3 or xyzw quaternion state.
