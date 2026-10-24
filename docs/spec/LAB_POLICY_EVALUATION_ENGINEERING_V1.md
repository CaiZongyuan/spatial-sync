# Policy evaluation and observation evidence

Engineering slice for #35, following Training §7–§8.3 and RSI §3.2/§6–§8.
This extends the shipped policy core; it does not satisfy the real contact-policy
quality, T0 or independently sampled trainer-state manual gates by itself.

## Fixed inputs and scope

An embodied Suite fixes the evaluation Scene Version/content hash, source, target,
action, cases, success/milestones and private seeds. A policy fixes the Job,
checkpoint/export/ONNX/external weights, Task and observation specification.
Evaluating a candidate substitutes only that target's Program/config in an owned
evaluation Session. The Suite Scene and all non-target bindings remain unchanged.

The evaluation Scene is the fixed experiment environment. A future deployment
Scene is a newly published immutable version created by #36 after its gates and
human approval; it never replaces the Suite environment or rewrites recordings.
This slice creates no deployment Scene, second scheduler or recording format.

Supported policy execution is Newton simulation with the CPU ONNX provider.
Synthetic/real sources, foreign Scene hashes, missing exact policy versions,
changed artifacts, incompatible target/action or unverifiable target bindings
reject before dispatch. A Task's evaluation grid never widens its training
coverage: unsupported episode parameters still produce precondition_failed.
Legacy scene-bound subjects and opaque historical Task/observation files remain
readable with their original behavior. An opaque observation file cannot become
a runnable policy or a successful consistency check.

## Subject resolution

Public intent remains the existing `POST /evaluations` and `lw eval run`/generic
MCP evaluation tool. Accept exact `policy/name@revision` and a Job/checkpoint
reference. Checkpoint intent uses the existing policy freeze operation to resolve
an immutable candidate; identical retries recover the same policy version.
Versioned script programs remain outside this slice until #38 supplies a loader.

The new policy-subject helper has three narrow responsibilities:

| Operation | Inputs | Result |
| --- | --- | --- |
| resolve | manager, requested subject, fixed Suite, verified Scene | immutable candidate identity and target binding proposal |
| verify | the frozen proposal and current resources | reject changed policy/Task/Scene/base binding before every episode |
| validate execution | frozen proposal, actual started Catalog/Source and native execution receipt | prove the expected policy/config/weights actually ran |

The frozen subject descriptor contains:

```text
requestedSubject
candidateIdentity:
  kind, policyId, policyContentHash, jobId, checkpoint, checkpointSha256
  onnxSha256, observationSha256, task, taskContentHash
evaluationScene: id, contentHash, source
target: canonical entityKey, interface, action
bindingPatch:
  expectedBaseBindingHash
  program: robot_arm.policy
  config: exact policy ref plus existing declared robot/location bindings
  bindingHash
```

The helper builds config from the existing target declaration, validates it with
the registered Program Config, and retains inherited interlocks. It does not
guess geometry, use a default robot, relax binding integrity or import an engine.
The policy's pinned Scene/hash, target and implemented action must match the fixed
Suite environment. The target's replacement binding is candidate material; the
fixed Scene and non-target programs remain environment material.

## Session and Runner boundary

Use the current EvaluationService admission queue and one Session slot. Freeze
the subject descriptor durably before issuing any Source start. Session/Source
receive an evaluator-only internal binding proposal, separate from public
SessionOptions and the published Scene package. Ordinary interactive requests
cannot provide this override. A policy intent cannot fall back to the ordinary
scene-bound program when the policy/override is unavailable.

Newton constructs the same fixed physics world, loads the ordinary Catalog and
applies the one verified target replacement before HostCore initializes programs
or returns readiness. The actual Catalog is recorded by the existing recorder;
the Evaluation separately retains candidate/base-binding identities. This does
not modify old recording bytes or add a spatial protocol field.

Read-only native execution evidence uses the existing private Runner request/
reply transport. It records the actually loaded policy/content/ONNX/observation
identities, CPU provider, invocation-start and inference-call counts, and a digest
of the PhysicsPort-derived observations. `validOutputs` counts finite model
outputs with the expected shape; it does not assert accepted/committed joint
targets or physical movement. No model output is accepted as a success flag.
Zero/unknown inference counts remain explicit; they do not become
claims that a controller performed a physical action. Evaluation success and
milestones remain the platform's frozen Suite conditions.

Cancellation, deadline handling, recording completion, session ownership and
cleanup reuse existing service behavior. #132B owns the shared distinction
between healthy valid failures and infrastructure-invalid episodes; this slice
does not extend deadlines or turn a Source failure into a fabricated zero.

## Comparison identity coordination

#132A owns shared environment/judge comparison identity and reference selection.
The agreed internal DTO is:

```text
comparisonIdentity: {environment: nonempty opaque identity, judge: nonempty opaque identity}
candidateIdentity: independently recoverable candidate provenance
```

Unknown comparison identity refuses statistical comparison. Policy/ONNX/
checkpoint identity stays outside the comparison key. Environment identity must
retain the fixed Suite/Scene/Task/dependencies and non-target programs, and must
include actual observation/inference runtime implementation where relevant.
Judge identity includes the actual grading implementation. Do not simply exclude
all programs or mix a candidate-specific binding into a supposedly fixed
environment. The helper exposes candidate/base binding facts to that owner;
shared producer signatures are coordinated before editing evaluations.py.

`candidateIdentity.task` is the training Task provenance. The fixed evaluation
Task is the Suite's frozen `task.yaml`, covered by its Suite content hash. A new
training Task/reward/observation version alone must not create a different
evaluation environment; the platform still grades the fixed evaluation Task.

## Immutable observation evidence

Persist observation checks beside policy evidence, not by mutating the frozen
policy package or its content hash. Public intent identifies an exact policy and
an existing artifact from its verified source Job; clients do not upload scores,
qualifications or gateway host paths. Reuse the existing authenticated node Job
artifact transport and its SHA manifest.

The initial artifact envelope is versioned and binds:

```text
schema: lw.policy-observation-samples/1
policy: checkpointSha256, onnxSha256, observationSha256
source: jobId, task, taskContentHash, scene, sceneContentHash, versions
capture: declared producer/method and independent-training-observation provenance
samples[]: unique sourceStateId, state, trainingObservation, previousAction, references
```

The owned training Bridge calls optional
`runner.env.unwrapped.lw_policy_observation_samples(256)` after exporting the
exact learned checkpoint/ONNX. This follows the existing Task-owned
`lw_training_view()` boundary; the platform does not guess Isaac task fields or
assemble the training-side observations. The synchronous hook returns
`{method, samples}`. Missing hooks leave evidence absent, preserving old Tasks.
An invalid hook return fails export rather than inventing samples.

The Bridge records the actual hook return, module/qualname and one matching
frozen Task source file/code hash, runner class, versions, model/observation
hashes, distinct state IDs, opaque fenced-dispatch attempt identity and exact
sample artifact bytes. A separate `lw.policy-observation-capture/1` receipt
binds these facts to the sample hash/count/ID digest. It does not assert learned
quality or independent observation semantics merely because256 entries exist.

Public endpoints use the existing exact policy reference:

| Endpoint | Intent |
| --- | --- |
| POST `/policies/{name}/{rev}/observation-checks` `{artifact}` | recompute/freeze an existing source-Job artifact |
| GET `/policies/{name}/{rev}/observation-checks` | list immutable report summaries |
| GET `/policies/{name}/{rev}/observation-checks/{id}` | read one frozen report and source-verification state |
| POST `/policies/{name}/{rev}/observation-checks/{id}/verify-source` `{artifact}` | verify an existing same-Job native capture receipt and persist the evidence-bound result |

Verification derives the trainer return-code fact from the succeeded fenced Job,
and checks the actual owned exporter/Task source, versions, weights, unique IDs
and input hashes. Hook/Job provenance validation and actual trained/independent
observation semantics remain separate fields. There is no arbitrary qualified
setter. The source-verification operation and readback are the executable seam
for a later authorized real capture; controlled samplers retain their provenance
and never establish real policy quality.

`state` and the numerical comparison keep the shipped obs_spec/1 conventions.
The gateway checks the exact source Job/Task/Scene/export/observation and artifact
hashes, verifies distinct sample identities and finite shapes, and recomputes
every platform observation. A numerical pass requires at least256 samples and
maximum absolute error≤1e-4 over all supplied samples. The independent training
observations must be supplied as captured training outputs, not recomputed by
the platform assembler or inferred from an ONNX success result.

The immutable check freezes input bytes, input artifact hash, policy/hash,
comparison implementation/runtime identity, report/per-term errors and source
declarations. Retry identity covers those exact inputs; changed source bytes or
changed comparison implementation cannot silently reuse an old check. Missing,
malformed, foreign or tampered artifacts reject. Failed checks remain available
for diagnosis and never erase earlier reports. A report also remains readable
after the gateway restarts.

Provenance and numerical agreement are separate facts. An authenticated artifact
hash proves which Job supplied bytes; it does not by itself prove genuine
independent trainer sampling. Fixture/caller-declared origins retain that label.
The intake therefore records numerical_pass/numerical_fail and sampling evidence
without setting qualified:true. The real256-state capture and its execution
provenance still need the separately authorized actual experiment. #36 must
reject missing/unverified observation-quality evidence rather than interpreting
a numerical fixture pass as the manual gate.

## Result readback

Evaluation records retain requested/resolved subject identity, fixed experiment
environment, actual target binding, native execution evidence, completed episode
recordings and platform-written results. Train/public projections expose safe
identity/count summaries; private test cases, seeds, observations and reports
retain their existing access restrictions. A public caller cannot submit a
platform score or a qualified marker.

Policy readback may project linked immutable checks and platform evaluations
from the separate evidence store while the original artifact/content hash stays
unchanged. Known training success rate is compared with the actual platform rate
using the shipped strict>15percentage-point sim_gap rule; unknown rates remain
unknown. Observation evidence, statistical acceptance and deployment approval
remain distinct. No promotion endpoint is supplied in this slice.

## Verification and remaining gates

- Engine-free tests distinguish policy/Scene/Task/base-binding tamper and show
  that candidate identity cannot change the fixed comparison environment.
- A real small ONNX and actual Newton PhysicsPort execute through the existing
  evaluator/HTTP action pipeline, occupy the existing single slot, and produce
  a completed recording plus measured inference evidence. An honest failed
  engineering episode is acceptable evidence of the path, never quality.
- Observation intake exercises independently specified golden samples, missing/
  malformed/foreign/duplicate/tampered evidence, retries and restart readback.
  Numerical fixtures remain explicitly unqualified.
- Public API/installed CLI/official MCP read the same resources; ordinary
  subjects and historical opaque inputs retain their prior behavior.
- Run required mandatory checks and four Newton CPU commands for native/runtime
  changes. Revalidate and actually recapture only affected existing bundled
  previews without weakening fingerprints or changing Scene/GLB bytes.

#29/contact quality, a real learned Newton pickPlace, actual independently
sampled256 trainer states and #36's held-out/promotion gates remain open. #18 and
new Scene Factory work are excluded; use existing published scenes/Tasks. No GPU
training, paid model execution, real device or asset release is authorized by
this engineering specification.
