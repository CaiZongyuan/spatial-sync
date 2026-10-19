import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import Ajv from 'ajv'
import manifest from '../fixtures/manifest.json'
import messages from '../fixtures/messages.json'
import alertCatalog from '../fixtures/catalog-alerts.json'
import math from '../fixtures/math.json'
import schema from '../types/protocol.schema.json'
import type { Catalog, ClientMessage, CommandRequest, Pose, PrimitiveGeometry, SceneManifest, ServerMessage, Snapshot, TransformFrame, Visual } from '../types/index'
import { validateManifest } from '../types/validate'

const ajv = new Ajv({ strict: false, allErrors: true })
ajv.addSchema(schema)
const server = ajv.compile<ServerMessage>({ $ref: `${schema.$id}#/$defs/ServerMessage` })
const client = ajv.compile<ClientMessage>({ $ref: `${schema.$id}#/$defs/ClientMessage` })

test('Python-produced alert declarations validate as typed defaults and binding overrides', () => {
  const check = ajv.compile<Catalog>({ $ref: `${schema.$id}#/$defs/Catalog` })
  assert.ok(check(alertCatalog), ajv.errorsText(check.errors))
  const typed = alertCatalog as Catalog
  assert.equal(typed.types['incubator/v1'].alerts?.tempDeviation.forSeconds, 60)
  assert.equal(typed.entities['/World/Incubator'].alerts?.tempDeviation.forSeconds, 10)
  const invalid = structuredClone(alertCatalog)
  invalid.types['incubator/v1'].alerts.tempDeviation.forSeconds = -1
  assert.equal(check(invalid), false)
})

test('Python-produced Manifest validates and preserves full contract', () => {
  const value: SceneManifest = validateManifest(manifest)
  const visual: Visual = value.nodes[1].kind === 'body' ? value.nodes[1].visuals[0] : assert.fail('expected body')
  const geometry: PrimitiveGeometry = visual.kind === 'primitive' ? visual.geometry : assert.fail('expected primitive')
  assert.equal(geometry.type, 'box')
  assert.deepEqual(JSON.parse(JSON.stringify(value)), manifest)
})

test('all Python-produced events validate against generated TS-side schemas', () => {
  const observed = new Set<string>()
  for (const message of messages) {
    const check = message.type === 'client.subscribe' || message.type === 'command.request' || message.type.startsWith('action.') ? client : server
    assert.ok(check(message), ajv.errorsText(check.errors))
    observed.add(message.type)
  }
  assert.equal(observed.size, 20)
  // These assignments prove the exported consumer-facing types are usable.
  const frame = messages.find(message => message.type === 'stream.snapshot')
  assert.ok(server(frame))
  const typed: TransformFrame = frame as TransformFrame
  const snapshot: Snapshot = { ...typed, type: 'stream.snapshot' }
  const command: CommandRequest = { type: 'command.request', requestId: 'typed', command: 'step', args: { count: 1 } }
  const pose: Pose = { p: snapshot.poses[0].slice(1, 4) as Pose['p'], q: [0, 0, 0, 1] }
  assert.equal(command.args.count, 1)
  assert.equal(pose.p[0], 1)
})

test('operation message grammar enforces exclusive steps and positive correlation identifiers', () => {
  const full = messages.find(message => message.type === 'plan.update' && message.update === 'full')
  for (const step of [
    { id: 1, status: 'pending' },
    { id: 1, status: 'pending', do: { entity: '/World', action: 'start', params: {} }, check: { property: 'phase', equals: 'idle' } },
    { id: 1, status: 'pending', wait: { property: 'phase', equals: 'idle' }, expect: { property: 'phase', equals: 'idle' } },
  ]) {
    const value = JSON.parse(JSON.stringify(full))
    value.plan.steps = [step]
    assert.equal(server(value), false)
  }
  const delta = JSON.parse(JSON.stringify(messages.find(message => message.type === 'plan.update' && message.update === 'step')))
  assert.equal(server({ ...delta, revision: 0 }), false)
  assert.equal(server({ ...delta, stepId: 0 }), false)
  assert.equal(client({ type: 'action.request', requestId: 'future', entityKey: '/World', action: 'start', planId: 'p', stepId: 1 }), false)
})

test('low-frequency validator rejects broken graph and geometric semantics', () => {
  for (const mutate of [
    (m: typeof manifest) => { m.nodes[1].parentEntityKey = '/Missing' },
    (m: typeof manifest) => { m.nodes[1].streamId = -1 },
    (m: typeof manifest) => { m.sites[0].parentBodyKey = '/World/Floor' },
    (m: typeof manifest) => { m.joints[0].axis = [0, 0, 0] },
    (m: typeof manifest) => { m.nodes[1].visuals![0].localPose.q = [0, 0, 0, 2] },
    (m: typeof manifest) => { m.sites[0].entityKey = '/World' },
  ]) {
    const bad = structuredClone(manifest)
    mutate(bad)
    assert.throws(() => validateManifest(bad))
  }
})

test('shared convention fixtures expose all six primitive cases and independent poses', () => {
  assert.deepEqual(math.primitiveCases.map(item => item.geometry.type), ['box', 'sphere', 'capsule', 'cylinder', 'cone', 'plane'])
  assert.deepEqual(math.poseCases[0].expected.p, [1, 3, 5])
  assert.deepEqual(math.axisCases[0].expected, [1, 3, -2])
})

test('optional fallback groups accept meshes and validate their local quaternion', () => {
  const value = structuredClone(validateManifest(manifest))
  const body = value.nodes.find(node => node.kind === 'body')!
  assert.equal(body.kind, 'body')
  if (body.kind !== 'body') return
  body.fallbackVisuals = [{ kind: 'mesh', meshId: 'sha256-fallback', url: '/meshes/sha256-fallback', assetUpAxis: 'Z', scale: [1, 1, 1], localPose: { p: [0, 0, 0], q: [0, 0, 0, 1] } }]
  assert.equal(validateManifest(value).nodes.length, manifest.nodes.length)
  body.fallbackVisuals[0].localPose.q = [0, 0, 0, 2]
  assert.throws(() => validateManifest(value), /Quaternion/)
})

test('TS generated artifact is deterministic', () => {
  const path = new URL('../types/index.ts', import.meta.url)
  const before = readFileSync(path, 'utf8')
  execFileSync(process.execPath, ['packages/protocol/scripts/generate_types.mjs'], { cwd: new URL('../../../', import.meta.url) })
  assert.equal(readFileSync(path, 'utf8'), before)
})
