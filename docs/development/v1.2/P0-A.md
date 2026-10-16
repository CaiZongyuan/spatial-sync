# P0-A — Newton baseline

Status: ready for independent review and PM integration. Implementation baseline:
`633735534aa05376138904582ebb7712a0ea19c9`; shared protocol dependency:
`80b5b1d4185f943fd769ae9bb61ee2da60d5f233`. This stage owns Python root configuration,
`packages/sim_runtime`, its tests, the falling-boxes example, and the two reference CLIs.
It does not deliver the gateway/IPC integration assigned to P0-E.

## Dependency decision D-01

Checked on 2026-10-16 against PyPI metadata, official stable releases, installed APIs, and
real CPU/CUDA execution. Exact Newton/Warp pins live in `pyproject.toml`; every resolved
transitive dependency is in `uv.lock`.

| Component | Locked version | Primary evidence |
|---|---|---|
| Python | 3.13.14 | `uv python install 3.13`; CPU/CUDA receipts |
| Newton | 1.7.0 | [official release](https://github.com/newton-physics/newton/releases/tag/v1.7.0), published 2026-10-14; [PyPI](https://pypi.org/pypi/newton/1.7.0/json) |
| Warp | 1.18.0 | [official release](https://github.com/NVIDIA/warp/releases/tag/v1.18.0), published 2026-10-05; [PyPI](https://pypi.org/pypi/warp-lang/1.18.0/json) |
| MuJoCo / mujoco-warp | 3.15.0 / 3.15.0 | Newton 1.7.0's `sim` extra and `uv.lock`; imports exercised |
| USD / Newton USD schemas | 26.8 / 0.6.0 | Separate `usd` extra, pinned/resolved in lock |
| Viser | 1.1.1 | `simulation` extra; actual ViewerViser 90-frame and 600-frame runs |
| Pydantic | 2.12.4 | Exact pin matches P0-B's schema generation environment |

The default environment includes gateway/protocol/tooling dependencies and excludes Newton,
Warp, MuJoCo, USD, and Viser. Verified with `uv export --no-dev --no-emit-project --no-hashes`.
`uv sync --extra simulation --extra usd` enables the real engine and import smoke fixtures.
Newton's MuJoCo extra is named **`sim`**, not `mujoco`; the project installs `newton[sim]==1.7.0`.
P0-A uses SolverXPBD. SolverMuJoCo's articulated behavior is intentionally reserved for P1-A.

## Actual API findings

The official [ModelBuilder API](https://newton-physics.github.io/newton/latest/api/_generated/newton.ModelBuilder.html),
[Sites](https://newton-physics.github.io/newton/latest/concepts/sites.html),
[Conventions](https://newton-physics.github.io/newton/latest/concepts/conventions.html), and
[USD parsing](https://newton-physics.github.io/newton/latest/concepts/usd_parsing.html) pages
were read alongside the installed 1.7.0 implementation. Runtime findings take precedence
over assumptions about future `latest` documentation.

- Builder and model fields are mutable Python label lists: `body_label`, `shape_label`,
  `joint_label`. Sites are shape entries with `ShapeFlags.SITE`; there is no `site_label`.
- `add_builder(child, label_prefix="World")` joins with `/`: body `Box` becomes
  `World/Box`, not `/World/Box`. The shared protocol normalizer adds the leading slash.
- `add_body(label="Box")` creates an implicit free joint labelled `Box_free_joint`.
  Free joints are exported but do not acquire anchor Sites.
- The MJCF importer preserves a complete hierarchy. The fixture's prefixed body is
  `World/Robot/label-smoke/worldbody/Link`; its joint and Site end with `/Hinge` and `/tip`.
  Leaf-only import labels would be an incorrect assumption on this version.
- `add_usd()` returns string Prim Path → index maps for body/shape/joint. Immediate
  assignment through these maps works and survives finalize. The fixture verifies all
  three maps with distinct body, shape, and revolute-joint paths. The loader uses
  `apply_up_axis_from_stage=True` once. A Y-up asset journey remains a P1-B check.
- `CollisionPipeline.contacts()` is the current contact allocation API.
  `Model.contacts()` raises a deprecation warning and was removed from the implementation.
- Initialization and reset both call `newton.eval_fk(model, state.joint_q, state.joint_qd, state)`.
  The pendulum deliberately starts with a builder body origin different from the correct FK
  result, making the zero-step literal assertion sensitive to a missing FK call.
- ViewerViser exposes `port` but no `host`. The reference tool subclasses its pinned
  `_get_viser` hook with a local module proxy so Viser binds `127.0.0.1`; it does not mutate
  the upstream module. It closes the server on exit and rejects automatic port migration.

### Missing-label provenance (D-08)

Newton fills absent labels with index-based strings before finalize, making a genuinely
omitted label indistinguishable from an explicit label such as `body_0` in model arrays.
Following PM's decision, the exporter never guesses from spelling. `resolve_labels` and
`labels_for_model` accept `missing_labels={(kind, index)}` from construction/import
provenance. Missing labels use `/__unlabeled/{kind}_{index}`, `stableKey=false`, warnings,
and strict-mode rejection; explicitly supplied `body_0` remains stable. All shipped
Python scene entities are explicitly labelled. Unknown external builder/import omission
provenance is a documented limit for subsequent loader work.

## Verification

```powershell
uv sync --extra simulation --extra usd
uv run --extra simulation --extra usd pytest tests/contract tests/unit tests/sim_runtime -q
uv run --extra simulation --extra usd python tools/smoke_newton.py --device cpu
uv run --extra simulation --extra usd python tools/smoke_newton.py --device cuda:0
uv run --extra simulation python tools/viser_reference.py falling-boxes --frames 90
uv run ruff check packages/sim_runtime tools tests/sim_runtime tests/unit/test_labels.py
```

- Python tests: **66 passed**, including the existing protocol checks and 9 Newton/label
  checks. Tests cover actual gravity and joint motion; pause/manual step/reset/epoch;
  zero-step FK; MJCF prefix/Site labels; USD map rewrites; static/stream classification;
  primitive half-extents; duplicate labels; missing-label provenance; and child-side anchors.
- [CPU receipt](P0-A-cpu.json) and [CUDA receipt](P0-A-cuda.json): 24 steps at 1/240 s,
  `simTime≈0.1`, `/World/Box` moves from z=1.5 to z=1.4489063, pendulum moves, reset
  maximum pose error 0.0. Float tolerances are used; no bitwise CPU/GPU determinism claim.
- Hardware: Windows 11 build 26200, Intel family 6/model 154 CPU, NVIDIA GeForce
  RTX 3070 Ti Laptop GPU 8 GiB, driver 610.62; Warp reports CUDA toolkit 13.4/driver 13.3.
  Initial CUDA kernel compilation took about 25 s. Receipt elapsed times include the
  complete smoke workload and cache effects; they are not performance benchmarks.
- ViewerViser rendered the same Newton model through the upstream
  `set_model → begin_frame → log_state → end_frame` API for 90 frames (3 s simTime),
  then 600 frames (20 s simTime), and stopped. The second run selected 8083 when Viser
  could not reuse 8080; the CLI now rejects that automatic migration. No browser visual
  comparison or screenshot has been claimed; the P0-E integrated visual journey remains.
- Ruff: all scoped checks passed. The reference servers and CUDA smoke processes exited;
  no P0-A listening server is retained. `.venv` and ignored `.scratch/p0-a` retain local
  dependencies and downloaded primary-source evidence for follow-on work.

The test-first slices initially failed because their public modules were not implemented;
MJCF's first asserted leaf-only path failed against the real importer and was corrected to
the observed official hierarchy. This was an API-assumption correction, not engine substitution.

## Change impact and simplification

Changed dependency boundary → consumers: default root Python environment → protocol
generator and future gateway; simulation extra → Simulation, import helpers, reference CLI;
protocol models/math → labels/exporter. Real registered scene loading, protocol validation,
and CPU/CUDA receipts prove these local boundaries. External consumers did not exist at the
baseline. The manifest is validated through P0-B's authoritative `SceneManifest` model.
Body IDs are revision-local stream IDs; external identity is normalized labels. Global
static shapes do not index `body_q` or appear as streamed bodies.

Bounded simplification inspected all P0-A source and its tests against baseline plus the
shared protocol. It retained one simulation step boundary, one authoritative protocol math
module, and a single immutable exporter; no worthwhile additional extraction or deletion
was found. Lint formatting and the deprecated contact call were repaired. Pacing,
asynchronous extraction, process isolation, command deduplication, and WS delivery ordering
remain assigned to P0-E and are not implied by these tests.

Recommended spec §17 update for PM: D-01 = Newton 1.7.0 / Warp 1.18.0 / Python 3.13.14,
Newton `sim` extra resolves MuJoCo and mujoco-warp 3.15.0; D-09 = verified on 1.7.0.
Add the MJCF hierarchy and missing-label provenance observations to the implementation notes.

## Nonauthor review repairs

PM identified two concrete specification differences in the frozen baseline. Missing-label
warnings now use the prescribed `unlabeled_entity` code. Anchor generation now reuses a
same-name Newton Site, comparing normalized entity paths; it rejects names occupied by
non-Site geometry, ambiguous duplicates, or a Site attached to a different body. Reused
Sites retain their original local pose and are exported with `role="joint-anchor"`.

The regression tests first reproduced three target assertion failures: the wrong warning
code, a newly allocated anchor index instead of the existing Site index, and no error for
non-Site name occupation. After repair, nine affected label/Site/Manifest/simulation tests
passed. The new real-Newton fixture proves no shape is added, confirms the exported role,
and independently composes the child Body and Site pose with Warp to recover the known
world anchor `(1, 0, 1.5)`. Scoped Ruff and whitespace checks passed. GPU execution was
not repeated because this repair changes construction/identity logic and no device math.

The bounded repair simplification retained one normalized lookup and explicit invalid
ownership errors. Missing-label provenance forwarding into the actual exporter/source
remains a P0-E/P1 follow-up; baseline scenes label all entities explicitly.
