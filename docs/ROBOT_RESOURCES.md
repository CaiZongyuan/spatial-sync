# Robot resources

The resource pages read local metadata without starting a Session, loading model
weights, probing a physics engine or connecting a real device. `/resources/robots`
uses the existing `robot/fr3@1` GLB and verifies its exact
`robot-and-tubes@2` → `/World/Lab/RobotA` binding. Its current scene Program is
`robot_arm.newton`; this taught script currently uses `graspMode=weld`.

`fr3-sim` and `fr3-real` are distinct Embodiment identities. The real entry has no
scene/live binding or Controller. A shared asset reference describes the robot
geometry; it is not evidence of a real device connection.

```powershell
lw robot ls --json
lw robot show fr3-sim --json
lw controller show fr3-arm --json
lw robot-model ls --json
lw robot-model show fr3-taught-motion --json
```

The generic MCP tools `robot_list`, `robot_show`, `controller_show`,
`robot_model_list` and `robot_model_show` use the same HTTP reads:
`/api/v1/embodiments[/{id}]`, `/controllers/{id}[/versions]`, and
`/robot-models[/{id}]`. Unknown identities return 404; invalid metadata or a
conflicting binding returns an explicit error. These routes have no mutation or
inference operation.

`runnable` describes a supported platform Program/configuration capability. It
does not certify that this host has the required environment installed, that a
model passed evaluation, or that a deployment was approved. The built-in taught
motion entry is supported through its actual registered Program and Newton
PhysicsPort requirements. Its quality remains `not-evaluated`, with no evaluation
ID; it is not a trained contact policy or a T0/T2 success claim.

The model catalog also registers OpenVLA 7B, SmolVLA 450M and GR00T N1.5 3B.
Their model cards, parameter counts, licences and upstream embodiment examples
are linked in each entry, checked on 2026-10-24. GR00T's checkpoint licence has a
non-commercial use limitation. All three have `runnable: false`, no platform
Embodiment adapter and no downloaded weights. Upstream examples are distinct
from supported local bindings. Opening the page makes no external requests.

Metadata lives under the configured library roots:

- `embodiments/<id>.yaml`: identity, `kind`, exact asset and Scene/entity/source
  bindings, and an optional Controller identity.
- `robot-models/<id>.yaml`: capability, environment requirements, quality status
  and cited sources. VLA entries must remain registration-only.
- `controllers/<id>/controller.yaml`: identity, Embodiment and explicit nullable
  `currentVersion`. Actual immutable snapshots use `<positive-integer>.yaml` and
  record `controller`, `version`, `embodiment`, `robotModel`, `policy`, `program`,
  `action: {space, hz}`, `observation`, `task`, `scene` and `evaluationId`.

Reads validate these schemas, file confinement and identity, exact Scene/asset
bindings and supported Program references. User metadata can shadow bundled
identity metadata; published version bytes cannot conflict across roots. A
snapshot already observed by the running catalog cannot change bytes in place.
A history file alone never makes itself current. The bundled Controller has
`currentVersion: null` and an empty history. The promotion workflow owns future
publication and evaluation linkage; no Controller version or score is invented
to populate the resource page. The promotion acceptance part of #46 remains
dependent on #36.
