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

## First measured profile

The node advertises its actual hardware/resources and begins with no invented
timings. A measurement must identify the exact Task content hash, algorithm,
model, hyperparameters and advertised resource. Store measurement data outside
the Task directory so measuring it does not change its own content hash.

After a bounded, authorized calibration on that exact configuration, save the
actual per-iteration samples and setup duration:

```json
{
  "schema": "lw.training-profile/1",
  "taskContentHash": "the Task contentHash returned by the platform",
  "hardware": "the local node hardware identity returned by lw node ls",
  "resourceId": "the advertised GPU UUID, or cpu",
  "algorithm": {"library": "rsl_rl", "name": "PPO", "version": "5.4.1"},
  "model": {"actor": [256, 128], "critic": [256, 128]},
  "hyperparameters": {"num_envs": 4, "max_iterations": 1, "device": "cpu"},
  "samplesSeconds": [1.2, 1.3],
  "setupSeconds": 8.0,
  "measuredAt": 1792776000.0
}
```

The numbers above illustrate the format; supply real observations for the chosen
Job. GPU calibration/execution still requires its existing authorization. CPU
work is represented as CPU, with no physical GPU or GPU-hours claim.

```text
uv run --no-sync python tools/import_training_profile.py --job job-id --resource cpu --measurement measurement.json --capabilities path/to/state/local-node/capabilities.json
```

The tool reads the Job's pinned revision, rejects mismatched scope, retains the
original receipt by SHA256 and adds the measured profile. The default capability
file is `<SPATIAL_STATE_ROOT>/local-node/capabilities.json`; a configured
`SPATIAL_NODE_CAPABILITIES` path takes precedence. The node reloads profiles on
its next heartbeat, so an approved queued Job may then dispatch. Estimates use
the largest retained iteration sample plus measured setup time. No measurement
means no autonomous GPU launch.

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
learned runner and exports ONNX after learning. `export.json` links exact final
checkpoint/policy hashes, including valid zero-based `model_0.pt`. Required
outputs are checkpoints, ONNX, TensorBoard events, stdout/stderr, runtime versions,
seed and exact input identities. A model file produced by a contract fixture
proves transport only.

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
