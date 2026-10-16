# P0-B Protocol Schema

Status: implementation ready for PM integration and independent review.
Baseline: `633735534aa05376138904582ebb7712a0ea19c9`.
Branch/worktree: `issue/p0-b-protocol`, `spatial-sync-worktrees/p0-b`.

Delivered authoritative Pydantic v2 models, deterministic JSON Schema and
TypeScript generation, complete normal message samples, low-frequency Ajv
Manifest validation, shared coordinate/primitive fixtures, and pure Python
pose/label helpers. Protocol version remains 1. No Newton/GPU imports.

Validation: `pnpm test:protocol` passes 57 Python tests, TypeScript compilation
and 5 Node tests. Python samples round-trip and are validated by the generated
TS-side schemas. Regeneration is checked for JSON fixtures/schema and TS types.
Evidence: [validation output](P0-B-validation.txt),
[events](protocol-events.jsonl).

## Shared interfaces

- Python: `packages.protocol.schema` exports SceneManifest, Pose,
  TransformFrame, Snapshot, SessionState, Heartbeat, Command/CommandRequest,
  CommandAck, Ready, Subscribe, SubscriptionAck, SceneReplaced, ServerError,
  EntityState, ClientMessage/ServerMessage, Visual/PrimitiveGeometry and node,
  site, joint and primitive variant classes.
- `packages.protocol.internal` re-exports SessionOptions, CommandResult and
  JointState. SessionOptions uses camelCase fields; CommandResult contains
  `ack`, optional `frame`, optional `state`.
- TS: `packages/protocol/types/index.ts`; runtime `validateManifest(unknown)`
  in `types/validate.ts`; schema bundle `types/protocol.schema.json`.
- `math.normalize_entity_key` prepends `/` and rejects empty segments, `?`, `#`
  with ValueError. Missing-label provenance belongs to source adapters.
- `EntityState.schema_` aliases wire `schema`, including default serialization.
  `validate_command_args` rejects invalid typed args while preserving unknown
  command strings for executor rejection.

## Change impact and bounded simplification

The complete task-owned scope includes root Node workspace/lock/ignore,
`packages/protocol`, protocol contract/math tests and this evidence directory.
The repository baseline contained only specs and skills. Current consumers are
the Python generator/tests and TS validator/tests. The next registered consumers
are Newton exporter, gateway/SyntheticSource and viewer; exact paths and fields
were sent directly to their owners before integration.

The boundary is Python values → serialized wire JSON → generated schema/types →
TypeScript decoding. Evidence covers integer/finite boundaries, uint32 IDs,
cross-kind entity uniqueness, parent cycles, site/joint references, primitive
semantics, unit xyzw quaternions, normalized label paths, command argument
rejections and serialization precision. Frame JSON rounds positions to 6 decimal
places and quaternions to 7. Manifest validation adds graph/quaternion rules
which ordinary JSON Schema cannot express. Frame processing does not perform
runtime schema validation.

The simplification pass removed redundant per-property schema titles (which
otherwise produced hundreds of meaningless TS aliases), kept one tuple-schema
conversion and one explicit source-only coordination re-export, and clarified
one conditional test. No optional abstraction or dependency was added.

Remaining verification belongs to integration: source producers, real HTTP/WS
messages and viewer geometry must exercise this contract. This issue does not
claim those journeys, command ordering, networking, visual parity, or physics
performance. No timing is invented for the unjournaled initial implementation.
No browser surface is delivered by a schema-only issue.
