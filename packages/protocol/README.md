# Spatial protocol 2.1

`protocolVersion` is the integer **wire major**: Manifest, ready, subscribe,
JSON poses and frames continue to carry `2`. The semantic contract is 2.1,
identified by the generated schema ID `/protocol/v2.1/schema.json`. Recording
metadata and `/api/v1/health` additionally carry `protocolMinorVersion: 1`.
An absent minor marker means 2.0. The marker is not added to Manifest or frame
envelopes, so frozen 2.0 Manifest validators remain usable.
Session recordings may also include optional JSON-safe integer `seed` (0 through
9,007,199,254,740,991), the
actual session seed accepted by the host. Old recordings with no known seed
keep it absent; readers never infer a default or rewrite historical metadata.
Evaluation recordings also omit this optional field to keep private suite seeds
outside public metadata. Ordinary interactive recordings retain their actual seed.

Format 1 and 2 recording files remain readable without rewriting their bytes.
The metadata format remains 2, the LWLG segment codec remains 1, and the NSYN
spatial codec remains 1. Ordinary frozen Python file readers scan the original
records without validating every information message; the frozen browser reader
continues spatial/scalar playback and seeking while ignoring new messages or
fields. Its UI need not understand freshness. Strict 2.0 producer models are
closed schemas and do not validate new fields; they are not the ordinary file
reader path. New readers preserve freshness through snapshots and segment seeks.

Property definitions may declare positive `staleAfterSeconds`. Raw property
`values` remain scalars/arrays/objects of their declared type. Per-property
`ObservationMeta.stale` is optional (absent means fresh); observations may carry
an additive `changes[].meta` map. A stale transition preserves the last actual
sample timestamps and emits once; any subsequent write, including an equal
value, refreshes it. Simulation freshness advances with simulation time and
freezes when paused. Metadata transitions bypass value deadbands/rate limits.

The 2.1 grammar also declares reliable `entity.alert` and `plan.update` for the
alert and operation-plan features. These are contract declarations; their
engines, storage and APIs are separate features. Alerts reuse `Actor`; `since`
is the nonnegative simulation time at which the condition first became true.
Acknowledgement notes belong to the alert API when it is implemented.
Plan updates use `update: "full"` with an `OperationPlan`, or `update: "step"`
with `planId`, positive integer `revision`/`stepId`, and the step status.
Steps contain exactly one `do`, `wait`, or `check`; only `do` may include
`expect`. Direct condition atoms remain valid; no extra `all` wrapper is
required. Operation-plan correlation (`planId`/`stepId`, optionally
`planRevision`) is an HTTP adapter contract. It stays inside the gateway and is
not added to strict ActionRequest, InvokeRequest or Invocation wire documents.
WebSocket actions still reject these fields. Associations/deviation audit uses
existing EntityEvent data; step state uses the declared plan.update messages.

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

Command strings remain open so the
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
`fixtures/messages.json` covers all twenty message types;
`fixtures/math.json` contains independently specified §4.3 dimensions, Z-up
mapping, quaternion hemisphere and Body × SiteLocal expected values. Viewer
geometry/interpolation tests should consume this same math fixture. Pure Python
math and label helpers live in `math.py` and require no physics engine.

`BodyNode` and `StaticNode` may include `fallbackVisuals` when an external GLB
binding replaces native Newton visuals. Each array is a simultaneous group.
Choose the first nonempty group whose assets all load, in this order: `visuals`,
`fallbackVisuals`, `colliders`, then a recognizable placeholder. Render one group
only; never concatenate a binding with its fallback shapes. Missing optional
`fallbackVisuals` preserves the default visual fallback behavior. Asset transforms remain
Body-local, with asset up-axis conversion applied once inside that transform.


## Full binary subscriptions

`client.subscribe` contains `sceneRevision`, `rateHz`, optional `requestId`, and
`info` (default true, reserved for the information plane). The gateway replies
with `subscription.ack`, including `grantedRateHz`, `info`, and the request ID,
then a complete binary snapshot. Rates are clamped to 1?basePublishHz.

Every WebSocket transform and snapshot carries every Manifest Body. HTTP
snapshot and pose endpoints continue returning JSON. Re-subscription and
reconnect always provide a complete recovery snapshot. Command frames remain
reliable and precede their acknowledgement and session state.

The NSYN binary format retains its 44-byte header, wire-format version 1,
32-byte rows, and kinds 1 (full transform) and 2 (snapshot). The spatial protocol
advertised by Manifest, ready, subscribe and HTTP snapshot is version 2; v1
JSON contracts are rejected. `fixtures/binary-frame.json` provides produced
bytes for the browser's independent decoder tests. Generated artifacts use LF.

The information plane uses reliable JSON `entity.snapshot`, batched
`entity.observations`, `entity.event`, and flattened `invocation.update`
messages. Catalog maps resolved entity keys to type definitions and bindings.
`InvokeRequest` is a host coordination value; `ActionRequest`, `ActionCancel`
and `ActionApprove` are public client messages. Actor annotations record
attribution; only the authenticated connection role grants permissions.
