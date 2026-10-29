# Cell Passage factory

The factory reads the received `platform_packages` directory and writes a fresh
library-shaped output. It copies the supplied packages with their notices and
hashes; it never changes the input delivery or an existing output. Prepared
third-party model bytes stay outside Git.

```bash
python -m factory.cell_passage --source <platform_packages> \
  --out .scratch/cell-passage/candidate --with-workcell
python -m factory.cell_passage_teach --library .scratch/cell-passage/candidate
python -m factory.cell_passage_preflight --library .scratch/cell-passage/candidate \
  --out .scratch/cell-passage/initial-state --seconds 2
```

The first command assembles physical labware, fixtures, the retained FR3/rail/Flex
chain and the P2-owned workcell binding generator. `--bindings-overlay` can supply
an external binding instead; it cannot replace a mechanical owner. The second
uses bounded Newton CPU FK to teach every declared approach/grasp pose, including
the physical 103.4 mm hand frame and each carrier's body-local grasp point.
Teaching is reach evidence. It does not qualify loaded paths or collision safety.
The third performs ordinary structural admission and a short real initial-state
diagnostic. It reports observations rather than declaring transport success.

`mechanical-layout.json` records initial stations, independent lids, parking
sites, rail requirements, pipette calibration, environmental teaching ranges,
tip geometry and the physical solid-waste region. `teaching.json` records every
attempt, its actual FK error and joint limits. Incomplete or failed qualification
must not be replaced by a successful-looking authoring label.

New labware uses real open bores and independent rigid bodies. The raised,
17 mm carrier grips are part of the bodies; the factory does not move the TCP,
stretch the robot or write runtime object poses. Runtime grasping requires the
specified left/right pad shapes to contact the specified grip surface within
the fixed penetration allowance before a hold constraint can activate.

The derived single-level incubator removes upper shelf geometry from both its
collision stage and a new visual attachment. This is a simulation configuration,
not an OEM modification claim. The new open inverted imaging receiver replaces
the renderer-only imager in this scene; its observation/counting results remain
explicitly modeled. The original source package is retained unchanged.

The 2 mm `newton:contactGap` is an authored inactive-candidate envelope for small
fixtures. Native tests compare signed distance, effective constraint cutoff and
normal force against the previous envelope. Original asset bytes and other
scenes' defaults are unchanged.

Public import, validation, launch, controlled actions, continuous loaded-motion
recordings, long seating checks and a real platform viewport are separate
qualification evidence. The ordinary verification requirements still apply.
