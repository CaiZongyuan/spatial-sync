# Script Program packages

Script packages are immutable reviewed Python controllers for the owned Newton
simulation Runner. The Gateway inspects their metadata and AST without importing
uploaded code. Live and Synthetic execution are unsupported. This runs trusted
reviewed Python in the Runner process; it is not an OS sandbox.

## Snapshot and review

```powershell
lw program inspect ./controller --json
lw program push ./controller --json
lw program ls --json
lw program show program/robot_arm.script.pick-tube@1 --json
lw program source program/robot_arm.script.pick-tube@1 controller.py --json
lw program export program/robot_arm.script.pick-tube@1 frozen-program.tar --json
```

Use the exact version returned by push. Export creates a new client-side file and
does not overwrite an existing file. Clients send package bytes to the same HTTP
resources; a directory name is never a Gateway filesystem path.

The directory contains `program.yaml`, UTF-8 Python files and optionally
`README.md`. Filesystem links, bytecode, native extensions and dependency
installation are unsupported. Limits are 256 files, 8 MiB total, 1 MiB per Python
file and 64 KiB manifest. Dirty files and original line endings are material bytes.
Identical material reuses its version; changed material creates a new version.

```yaml
schema: lw.program-package/1
name: pick-tube
title: Reviewed robot controller
entrypoint: controller.py:ScriptProgram
implements: robot-arm/v1.pickPlace
configSchema:
  type: object
  properties:
    speed: {type: number, minimum: 0, maximum: 1}
  required: [speed]
config: {speed: 0.2}
```

Configuration uses the platform's JSON-schema subset and rejects undeclared
properties. Inspect/show retain material, code, configuration, individual-file
and archive hashes. Source links return plain text. Plan associations are read
from Plan/approval records and never rewrite the archive.

## Program contract

The entry class declares `id = "robot_arm.script.<name>"`,
`type = "robot-arm/v1"`, `tick_priority = 10`, a frozen `ProgramConfig` subclass
and synchronous `init`, `accept`, `start`, `tick`, `cancel` callbacks. Its actual
Config must preserve the exact declaratively reviewed values; implicit defaults
or normalization differences reject before constructor/readiness.

Use the public Program/PhysicsPort interfaces, supported numerical/standard
helpers and confined package-relative imports. Direct Newton/internal imports
and dynamic code/import calls are outside the package contract. Imports and
constructors run only after exact approved material is checked in the owned
Runner, in a private module namespace. Immutable packages receive no `pycache`.

The fixed wrapper derives joint, gripper, TCP, holding, location and zone facts
from PhysicsPort feedback. A delegate can publish controller phase, but cannot
replace those physical facts. Control ticks run during an admitted action.
Initialization retains the Scene's actuator state; stop, cancellation, terminal
actions and finite deadlines hold the configured joints. Unknown location
feedback preserves its reservation. Failed initialization prevents readiness.

## Evaluate the frozen code

Push first, then reference the returned immutable identity in a training Plan:

```yaml
program:
  id: program/robot_arm.script.pick-tube@1
  contentHash: <exact hash returned by push>
jobs: {seeds: []}
budget: {gpuHours: 0, episodes: 4}
```

The Plan retains its real Task/Goal, evaluation intent, hypotheses, rationale
and interview. Algorithm/model/hyperparameter maps are empty; no training Job
is created. `lw plan submit` reads the interview from the named local Markdown
file. Review the actual source/config on `/plans/<plan-id>` and approve that
exact revision through the existing human approval interface.

```powershell
lw eval run program/robot_arm.script.pick-tube@1 --suite suite/fr3-transfer@1 --plan <plan-id> --revision 1 --json
lw eval show <evaluation-id> --json
lw notebook show <goal-id> --json
```

Select an existing compatible exact Suite. Admission and each episode recheck
the current approved Plan, artifact/hash, Task/Scene/target and episode budget.
The Runner independently checks durable code approval and actual package bytes.
Evaluation uses the existing queue, action pipeline, scoring and recordings.
Only the target binding changes; every other binding remains fixed.

Teacher, policy and script candidates share a fixed Suite comparison group.
Known code identity remains readable when environment/judge proof is unavailable;
unknown comparison evidence cannot establish a Reference. The native receipt
identifies actual loaded code/config and callback counts. The Suite separately
judges physical outcomes, so a completed gripper action can still fail a tube
transfer. Evaluation/Notebook views show code identity and link actual replay.

An ordinary imported Scene does not authorize code loading. Deployment requires
the shared committed human Scene/Controller transaction with exact material and
complete binding proof; a committed deployment does not borrow a later training
Plan approval or depend on that Plan remaining approved.

HTTP package routes are `/api/v1/programs`, `/programs/inspect`,
`/programs/<name>/<revision>`, `/files/<filename>` and `/archive` under the same
prefix. Generic MCP exposes `program_inspect`, `program_push`, `program_list`,
`program_show`, `program_source`, `program_export` and the existing
`evaluation_run` with `planId`/`planRevision`. No device-specific MCP action is added.
