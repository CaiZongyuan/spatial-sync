# Script Program packages and simulation loading

Engineering scope for #38, Training §8.5 and RSI §3.2/§6–§8. This is an
immutable code-artifact path through the existing training Plan, Evaluation,
Notebook and human Promotion machinery. It does not change #132C/D, acceptance
rules, spatial messages, geometry conventions or recording formats.

## Package and immutable identity

`lw program push <directory>` snapshots a standalone
workspace package as `program/robot_arm.script.<name>@N`. The gateway accepts a
bounded portable tar of regular files using the existing archive/path rules.
It never imports, compiles for execution, instantiates or invokes uploaded Python.

```yaml
schema: lw.program-package/1
name: pick-tube
title: Explicit pick/place controller
entrypoint: controller.py:ScriptProgram
implements: robot-arm/v1.pickPlace
configSchema:
  type: object
  properties: {speed: {type: number, minimum: 0, maximum: 1}}
  required: [speed]
config: {speed: 0.2}
```

`program.yaml`, UTF-8 `.py` files and an optional `README.md` are material input;
client-supplied provenance, qualification, scores, bytecode, native extensions,
dependency installation and filesystem links are unsupported. Source paths and
the top-level entry class are checked without executing the module. Declarative
config is validated by the platform's JSON-schema subset (objects reject
undeclared properties). AST import checks
permit the Program interface, PhysicsPort interface, supported pure numerical/
standard-library helpers and package-relative helpers; direct Newton/internal
runtime imports reject. These checks enforce the authoring contract, not an OS
sandbox or protection against malicious Python reflection.

The local `program inspect` operation returns validated metadata, material hash,
source-file hashes, code hash and config hash. Candidate `contentHash` covers all
material filenames and original bytes; the canonical exported archive has its
own SHA256. Dirty workspace bytes are retained, not represented merely by a Git
commit. Identical material retries return the existing immutable version.
Changed material creates a new version and needs a new approved Plan intent
before execution. Files are staged and atomically published under the
existing library/import lock, without overwriting a prior revision.

## Plan and code approval

The existing `kind: training` Plan gains an optional `program` intent:

```yaml
program: {id: program/robot_arm.script.pick-tube@1, contentHash: <64 lowercase hex>}
jobs: {seeds: []}
budget: {gpuHours: 0, episodes: 2}
```

A script round retains its Task, Goal, hypotheses, rationale, interview and
evaluation budget. Algorithm/model/hyperparameter maps are empty and no Job is
created; ordinary training Plans retain their existing required metadata and
behavior. The existing Jobs admission also rejects undeclared seeds.

Push first creates an immutable **unauthorized-for-loading** snapshot. The Plan
then references that actual version/content hash so the human can inspect frozen
source, config and changes before approving. Submission and approval require
those exact bytes to be available and compatible with the Task. The existing
Plan/approval records provide an independent association/readback; binding or
approving a Plan never rewrites the archive. A new revision or rejected package
remains readable and cannot silently borrow another Candidate's approval. No
Python executes during snapshotting or approval.

Evaluation admission requires the current approved training Plan revision whose
program version/content hash matches the exact Candidate, with compatible Task,
Scene/target/action and remaining episode budget. This is checked again under
the existing admission ownership before Source effects. Completed, rejected,
cancelled and invalid evidence retains its actual Plan and Candidate association;
missing environment/judge proof never discards known code identity.

## Shared Subject and binding facts

Policy and script are the two concrete consumers of one simulation Subject
interface. Reuse #35b's proven facts and ordering: immutable `candidateIdentity`,
fixed `evaluationScene`, canonical `target`, complete original `baseBindings`,
one replacement `binding` and `bindingHash`. Policy retains its own model/ORT/
observation validators; script retains its package/Plan/source validators. The
shared layer owns base/all-non-target integrity and exact admitted-descriptor
checks, not device behavior. Transport naming is agreed with the #36 owner
before replacing the existing private policy adapter.

The fixed Suite/evaluation Task, Scene and target slot remain the experiment.
Uploaded code/config/archive/Plan provenance are Candidate material. The static
`robot_arm.script` runtime wrapper and loader are fixed implementation and
declare generic `fixed-runtime` provenance. #132's producer normalizes the same
fixed slot for both artifact kinds and retains observation/physics/judge code;
Candidate package bytes do not split that comparison group. Unknown proof stays
unknown. Script and policy use the same queue, action pipeline, scoring,
Reference selection, recordings, valid-deadline and invalid-attempt semantics.

## Loader and Source ordering

V1 supports the owned Newton simulation Runner, including CPU. Synthetic runs in
the gateway and cannot load uploaded code. Live `/Real` and any non-Runner
context reject before Python import or constructor execution. The gateway may
load the trusted static wrapper definition and declarative config only.

1. Gateway verifies approved exact intent, package bytes, fixed Scene and all
   base bindings; persists the immutable Subject on the original admission queue.
2. Session verifies that the private descriptor equals its admitted record and
   that the requested Source is supported. Public SessionOptions cannot inject it.
3. Gateway and Runner independently build/verify the fixed Catalog and apply only
   the declared target replacement before readiness. Other bindings/interlocks
   remain intact; original Scene bytes are unchanged.
4. Only the Runner's explicit simulation execution context can initialize the
   wrapper. It rechecks package hashes and declarative config, creates a private
   module namespace and then loads the entry class. No workspace is added to the
   gateway or global import path; local helpers are package-relative.
5. The delegate uses the existing Program callbacks and PhysicsPort. Its real
   entry class/interface/config must agree with metadata. Native evidence retains
   the actually loaded artifact/code/config identity and callback activity;
   counters do not claim physical success. The fixed Suite judges actual facts.
   The fixed wrapper owns joint/gripper/TCP/holding/location/zone feedback and
   conservative location interlocks. The delegate receives its reviewed config
   and may publish controller phase; it cannot replace those physical facts.
   Control ticks run while an admitted action is active. Initialization retains
   the fixed Scene's actuator state; stop/cancel/deadline hold the configured
   arm/fingers and preserve an unknown or still occupied zone reservation.
6. Failure, cancellation and cleanup retain real attempt/recording/resource
   evidence. No fallback taught Program executes when loading fails.

V1 runs trusted reviewed Python in the Runner process without an OS sandbox.
Simulation-only means rejection on platform-managed Live execution paths; it is
not a claim that malicious Python cannot access the host or external devices.

## Promotion and ordinary simulation Scenes

#36 owns the shared Promotion transaction, held-out execution, human approval,
new immutable Scene and Controller version. Script exposes its exact immutable
artifact identity, approved-code proof and verified replacement binding to that
interface. It does not create another scorer, scheduler or approval pipeline.
Policy-only learned/observation quality gates are not fabricated for script.

A normal Scene import is not code-execution authorization. Loading a deployed
script in an ordinary simulation Session requires the shared approved Scene/
Controller proof; an unapproved imported script binding rejects before import.
Old Scenes/Controllers and their recordings remain unchanged/readable.

## Public surfaces and verification

The package operations have HTTP/installed CLI/generic MCP equivalents; clients
send package content, not gateway filesystem paths. Plan/approval views expose
code/config identity and reviewable source; evaluation and Notebook use their
existing pages, conditions groups and budget views. No new device-specific MCP
action is added. Errors distinguish malformed/changed package, missing/rejected
approval, unsupported Source, unapplied binding and runtime execution failure.

Pre-agreed verification interfaces: package inspect/push/read/retry; Plan
revision/approval and empty-Job admission; Live/non-Runner rejection with an
import-side-effect marker; actual owned Newton CPU evaluation/recording against
the same fixed Suite as a policy; installed CLI/official MCP; shared Promotion
accept/reject/human/new-Scene readback. A failed physical task is valid evidence
of the engineering path and is not relabeled as success or trained quality.

Finish with bounded simplification, non-author Standards/Spec, mandatory checks,
the four Newton CPU commands and affected actual captures when native inputs
change, exact-head Linux CI and integrated-tree verification. No GPU training,
paid model, real-device execution, SDK source change or asset release is part of
this engineering authorization.
