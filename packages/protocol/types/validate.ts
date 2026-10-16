// Validate Manifest at loading boundaries. Transform frames bypass runtime schema validation.
import Ajv from 'ajv'
import schema from './protocol.schema.json'
import type { SceneManifest } from './index'

const ajv = new Ajv({ strict: false, allErrors: true })
ajv.addSchema(schema)
const validate = ajv.compile<SceneManifest>({ $ref: `${schema.$id}#/$defs/SceneManifest` })

export function validateManifest(value: unknown): SceneManifest {
  if (!validate(value)) throw new Error(`Invalid Manifest: ${ajv.errorsText(validate.errors)}`)
  const nodes = new Map(value.nodes.map(node => [node.entityKey, node]))
  const keys = [...value.nodes, ...value.sites, ...value.joints].map(item => item.entityKey)
  if (new Set(keys).size !== keys.length) throw new Error('Duplicate entityKey')
  const bodies = new Set(value.nodes.filter(node => node.kind === 'body').map(node => node.entityKey))
  const streams = value.nodes.flatMap(node => node.kind === 'body' ? [node.streamId] : [])
  if (new Set(streams).size !== streams.length) throw new Error('Duplicate streamId')
  for (const node of value.nodes) {
    const visited = new Set([node.entityKey])
    let parent = node.parentEntityKey
    while (parent != null) {
      if (visited.has(parent) || !nodes.has(parent)) throw new Error('Invalid logical hierarchy')
      visited.add(parent)
      parent = nodes.get(parent)!.parentEntityKey
    }
  }
  const joints = new Map(value.joints.map(joint => [joint.entityKey, joint]))
  const sites = new Map(value.sites.map(site => [site.entityKey, site]))
  const bodyExists = (key: string | null | undefined) => key == null || bodies.has(key)
  for (const site of value.sites) {
    if (!bodyExists(site.parentBodyKey) || (site.jointKey != null && !joints.has(site.jointKey))) throw new Error('Invalid Site reference')
  }
  for (const joint of value.joints) {
    if (!bodies.has(joint.childBodyKey) || !bodyExists(joint.parentBodyKey)) throw new Error('Invalid Joint Body')
    if (joint.type === 'free') {
      if (joint.parentLocalPose != null || joint.axis != null || joint.anchorSiteKey != null) throw new Error('Invalid free Joint')
    } else {
      const anchor = joint.anchorSiteKey ? sites.get(joint.anchorSiteKey) : undefined
      if (!joint.parentLocalPose || !anchor || anchor.parentBodyKey !== joint.childBodyKey || anchor.jointKey !== joint.entityKey || anchor.role !== 'joint-anchor') throw new Error('Invalid Joint anchor')
    }
    if (joint.type === 'revolute' || joint.type === 'prismatic') {
      if (!joint.axis || Math.abs(joint.axis.reduce((sum, x) => sum + x * x, 0) - 1) > 1e-5 || joint.dofCount !== 1) throw new Error('Invalid Joint axis')
    } else if (joint.axis != null) throw new Error('Unexpected Joint axis')
  }
  const unitQuaternion = (q: number[]) => {
    if (Math.abs(q.reduce((sum, x) => sum + x * x, 0) - 1) > 1e-5) throw new Error('Quaternion must be unit xyzw')
  }
  for (const node of value.nodes) {
    if (node.kind === 'static') unitQuaternion(node.initialPose.q)
    if (node.kind !== 'group') for (const visual of [...node.visuals, ...(node.fallbackVisuals ?? []), ...node.colliders]) unitQuaternion(visual.localPose.q)
  }
  for (const site of value.sites) unitQuaternion(site.localPose.q)
  for (const joint of value.joints) if (joint.parentLocalPose) unitQuaternion(joint.parentLocalPose.q)
  return value
}
