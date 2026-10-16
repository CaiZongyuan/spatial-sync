# Spatial protocol v1

`schema/__init__.py` is the authoritative Pydantic v2 contract. `types/index.ts`,
`types/protocol.schema.json` and `fixtures/*.json` are generated; edit the Python
models or fixture generator and run:

```powershell
pnpm install --frozen-lockfile
pnpm protocol:generate
pnpm test:protocol
pnpm protocol:check
```

The protocol checks install only Pydantic and pytest through isolated `uv run`;
they do not import Newton, Warp, or GPU libraries. Python 3.13 and Node/pnpm are
required. The parent Python project can declare the same Pydantic dependency.

```python
from packages.protocol.schema import SceneManifest, Command, TransformFrame
from packages.protocol.internal import SessionOptions, CommandResult, JointState

# camelCase attributes match the wire. Outbound messages include defaults.
payload = frame.model_dump(mode="json", exclude_none=True)
```

`EntityState` uses Python `schema_` (alias `schema`) because BaseModel already has
a `schema()` method. Its default serialization emits the wire alias. Other
fields use their wire names directly. Command strings remain open so the
executor can return `unknown_command`; `validate_command_args(command)` checks
known command arguments and rejects non-integer/non-positive step counts.
Manual step batches are bounded by the shared `MAX_MANUAL_STEPS = 1000`; larger
counts return `invalid_args` without clamping, consistently across all sources.
State, authorization, idempotency and command ordering are gateway/source duties.

```typescript
import type { SceneManifest, ServerMessage, Visual } from './types'
import { validateManifest } from './types/validate'
const manifest: SceneManifest = validateManifest(await response.json())
```

`validateManifest` runs Ajv plus graph, quaternion and joint-reference checks at
scene loading boundaries. High-frequency frame handling uses generated types
without runtime schema validation. The schema describes serialized messages:
producer-populated default fields are required; optional `None` fields can be
omitted. Pydantic's fixed tuples are translated from 2020-12 `prefixItems` to the
equivalent draft7 `items` tuple representation for Ajv and TypeScript tooling.
The discriminator mappings and all constraints remain derived from Pydantic.

`fixtures/manifest.json` contains all primitive and asset variants;
`fixtures/messages.json` covers all twelve message types;
`fixtures/math.json` contains independently specified §4.3 dimensions, Z-up
mapping, quaternion hemisphere and Body × SiteLocal expected values. Viewer
geometry/interpolation tests should consume this same math fixture. Pure Python
math and label helpers live in `math.py` and require no physics engine.

`BodyNode` and `StaticNode` may include `fallbackVisuals` when an external GLB
binding replaces native Newton visuals. Each array is a simultaneous group.
Choose the first nonempty group whose assets all load, in this order: `visuals`,
`fallbackVisuals`, `colliders`, then a recognizable placeholder. Render one group
only; never concatenate a binding with its fallback shapes. Missing optional
`fallbackVisuals` preserves the original v1 behavior. Asset transforms remain
Body-local, with asset up-axis conversion applied once inside that transform.
