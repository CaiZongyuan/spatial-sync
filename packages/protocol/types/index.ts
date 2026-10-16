/* Generated from Pydantic by pnpm protocol:generate. Do not edit. */

/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "SceneNode".
 */
export type SceneNode = GroupNode | BodyNode | StaticNode
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Visual".
 */
export type Visual = Primitive | Mesh | Glb
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "PrimitiveGeometry".
 */
export type PrimitiveGeometry = Box | Sphere | Capsule | Cylinder | Cone | Plane
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "JsonValue".
 */
export type JsonValue =
  | boolean
  | number
  | string
  | JsonValue[]
  | {
      [k: string]: JsonValue
    }
  | null
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "ClientMessage".
 */
export type ClientMessage = Command | Subscribe
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "ServerMessage".
 */
export type ServerMessage =
  | Ready
  | SubscriptionAck
  | TransformFrame
  | TransformDelta
  | SessionState
  | SceneReplaced
  | CommandAck
  | EntityState
  | Heartbeat
  | ServerError

export interface ProtocolContract {
  manifest: SceneManifest
  clientMessage: ClientMessage
  serverMessage: ServerMessage
  snapshot: Snapshot
  sessionOptions: SessionOptions
  commandResult: CommandResult
  jointState: JointState
  stepArgs: StepArgs
  realtimeFactorArgs: RealtimeFactorArgs
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "SceneManifest".
 */
export interface SceneManifest {
  protocolVersion: 1
  sessionId: string
  sceneRevision: string
  revisionNumericId: number
  epoch: number
  sceneName: string
  source: SourceInfo
  coordinates: Coordinates
  nodes: SceneNode[]
  sites: Site[]
  joints: Joint[]
  warnings: SceneWarning[]
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "SourceInfo".
 */
export interface SourceInfo {
  kind: 'newton' | 'synthetic' | 'replay' | 'device'
  newtonVersion?: string | null
  solver?: string | null
  physicsDt: number
  basePublishHz: number
  realtimeFactor: number | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Coordinates".
 */
export interface Coordinates {
  handedness: 'right'
  upAxis: 'Z'
  units: 'm'
  quaternionOrder: 'xyzw'
  poseSpace: 'world'
  primitiveConvention: 'newton'
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "GroupNode".
 */
export interface GroupNode {
  entityKey: string
  kind: 'group'
  name: string
  parentEntityKey?: string | null
  stableKey: boolean
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "BodyNode".
 */
export interface BodyNode {
  entityKey: string
  kind: 'body'
  streamId: number
  name: string
  parentEntityKey?: string | null
  transformMode: 'stream'
  stableKey: boolean
  visuals: Visual[]
  fallbackVisuals?: Visual[] | null
  colliders: Visual[]
  debug: {
    [k: string]: JsonValue
  }
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Primitive".
 */
export interface Primitive {
  kind: 'primitive'
  geometry: PrimitiveGeometry
  localPose: Pose
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Box".
 */
export interface Box {
  type: 'box'
  /**
   * @minItems 3
   * @maxItems 3
   */
  halfExtents: [number, number, number]
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Sphere".
 */
export interface Sphere {
  type: 'sphere'
  radius: number
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Capsule".
 */
export interface Capsule {
  type: 'capsule'
  radius: number
  halfHeight: number
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Cylinder".
 */
export interface Cylinder {
  type: 'cylinder'
  radius: number
  halfHeight: number
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Cone".
 */
export interface Cone {
  type: 'cone'
  radius: number
  halfHeight: number
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Plane".
 */
export interface Plane {
  type: 'plane'
  width: number
  length: number
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Pose".
 */
export interface Pose {
  /**
   * @minItems 3
   * @maxItems 3
   */
  p: [number, number, number]
  /**
   * @minItems 4
   * @maxItems 4
   */
  q: [number, number, number, number]
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Mesh".
 */
export interface Mesh {
  kind: 'mesh'
  meshId: string
  url: string
  assetUpAxis: 'Z'
  /**
   * @minItems 3
   * @maxItems 3
   */
  scale: [number, number, number]
  localPose: Pose
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Glb".
 */
export interface Glb {
  kind: 'glb'
  url: string
  assetUpAxis: 'Y' | 'Z'
  /**
   * @minItems 3
   * @maxItems 3
   */
  scale: [number, number, number]
  localPose: Pose
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "StaticNode".
 */
export interface StaticNode {
  entityKey: string
  kind: 'static'
  name: string
  parentEntityKey?: string | null
  transformMode: 'static'
  initialPose: Pose
  stableKey: boolean
  visuals: Visual[]
  fallbackVisuals?: Visual[] | null
  colliders: Visual[]
  debug: {
    [k: string]: JsonValue
  }
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Site".
 */
export interface Site {
  entityKey: string
  role: string
  parentBodyKey?: string | null
  jointKey?: string | null
  localPose: Pose
  stableKey: boolean
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Joint".
 */
export interface Joint {
  entityKey: string
  type: string
  parentBodyKey?: string | null
  childBodyKey: string
  parentLocalPose?: Pose | null
  axis?: [number, number, number] | null
  anchorSiteKey?: string | null
  dofCount: number
  stableKey: boolean
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "SceneWarning".
 */
export interface SceneWarning {
  code: string
  entityKey?: string | null
  message: string
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Command".
 */
export interface Command {
  type: 'command.request'
  requestId: string
  command: string
  args: {
    [k: string]: JsonValue
  }
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Subscribe".
 */
export interface Subscribe {
  type: 'client.subscribe'
  protocolVersion: 1
  sceneRevision: string
  scope: 'all' | 'entities' | 'device' | 'viewport'
  rateHz: number
  format: 'json' | 'binary'
  entityKeys?: string[] | null
  deviceKey?: string | null
  viewport?: Viewport | null
  delta: boolean
  requestId?: string | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Viewport".
 */
export interface Viewport {
  /**
   * @minItems 6
   * @maxItems 6
   */
  planes: [
    [number, number, number, number],
    [number, number, number, number],
    [number, number, number, number],
    [number, number, number, number],
    [number, number, number, number],
    [number, number, number, number]
  ]
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Ready".
 */
export interface Ready {
  type: 'server.ready'
  protocolVersion: 1
  sessionId: string
  sceneRevision: string
  revisionNumericId: number
  epoch: number
  sessionState: 'CREATED' | 'LOADING' | 'READY' | 'RUNNING' | 'PAUSED' | 'STOPPING' | 'STOPPED' | 'FAILED'
  simTime: number
  basePublishHz: number
  realtimeFactor: number | null
  heartbeatIntervalMs: number
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "SubscriptionAck".
 */
export interface SubscriptionAck {
  type: 'subscription.ack'
  scope: 'all' | 'entities' | 'device' | 'viewport'
  grantedRateHz: number
  format: 'json' | 'binary'
  delta: boolean
  subscriptionId?: number | null
  requestId?: string | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "TransformFrame".
 */
export interface TransformFrame {
  protocolVersion: 1
  sessionId: string
  sceneRevision: string
  epoch: number
  seq: number
  simTime: number
  sampleTimeMs: number
  poses: [number, number, number, number, number, number, number, number][]
  type: 'transform.frame' | 'stream.snapshot'
  subscriptionId?: number | null
  keyframe?: boolean | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "TransformDelta".
 */
export interface TransformDelta {
  protocolVersion: 1
  sessionId: string
  sceneRevision: string
  epoch: number
  seq: number
  simTime: number
  sampleTimeMs: number
  poses: [number, number, number, number, number, number, number, number][]
  type: 'transform.delta'
  subscriptionId: number
  baseSeq: number
  removedStreamIds: number[]
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "SessionState".
 */
export interface SessionState {
  type: 'session.state'
  sessionId: string
  state: 'CREATED' | 'LOADING' | 'READY' | 'RUNNING' | 'PAUSED' | 'STOPPING' | 'STOPPED' | 'FAILED'
  sceneRevision: string
  epoch: number
  seq: number
  simTime: number
  realtimeFactor: number | null
  actualRtf: number
  cause: Cause
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Cause".
 */
export interface Cause {
  kind: 'command' | 'lifecycle' | 'error'
  requestId?: string | null
  command?: string | null
  code?: string | null
  message?: string | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "SceneReplaced".
 */
export interface SceneReplaced {
  type: 'scene.replaced'
  sessionId: string
  sceneRevision: string
  revisionNumericId: number
  epoch: number
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "CommandAck".
 */
export interface CommandAck {
  type: 'command.ack'
  requestId: string
  status: 'applied' | 'rejected'
  simTime: number
  epoch: number
  reason?: ('invalid_state' | 'unknown_command' | 'invalid_args' | 'unauthorized' | 'source_unavailable') | null
  message?: string | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "EntityState".
 */
export interface EntityState {
  type: 'entity.state'
  sessionId: string
  sceneRevision: string
  entityKey: string
  schema: string
  stateSeq: number
  simTime: number
  payload: {
    [k: string]: JsonValue
  }
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Heartbeat".
 */
export interface Heartbeat {
  type: 'server.heartbeat'
  state: 'CREATED' | 'LOADING' | 'READY' | 'RUNNING' | 'PAUSED' | 'STOPPING' | 'STOPPED' | 'FAILED'
  simTime: number
  lastSeq: number
  actualRtf: number
  serverTimeMs: number
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "ServerError".
 */
export interface ServerError {
  type: 'server.error'
  code:
    | 'invalid_message'
    | 'unsupported_protocol'
    | 'revision_mismatch'
    | 'session_not_ready'
    | 'unknown_session'
    | 'unknown_entity'
    | 'source_unavailable'
    | 'internal_error'
    | 'unauthorized'
    | 'invalid_subscription'
  message: string
  requestId?: string | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Snapshot".
 */
export interface Snapshot {
  protocolVersion: 1
  sessionId: string
  sceneRevision: string
  epoch: number
  seq: number
  simTime: number
  sampleTimeMs: number
  poses: [number, number, number, number, number, number, number, number][]
  type: 'stream.snapshot'
  subscriptionId?: number | null
  keyframe?: boolean | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "SessionOptions".
 */
export interface SessionOptions {
  realtimeFactor: number | null
  physicsDt: number
  basePublishHz: number
  maxLagMs: number
  continueWithoutObservers: boolean
  strictLabels: boolean
  device: string
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "CommandResult".
 */
export interface CommandResult {
  ack: CommandAck
  frame?: TransformFrame | null
  state?: SessionState | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "JointState".
 */
export interface JointState {
  entityKey: string
  simTime: number
  epoch: number
  positions: number[]
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "StepArgs".
 */
export interface StepArgs {
  count: number
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "RealtimeFactorArgs".
 */
export interface RealtimeFactorArgs {
  value: number | null
}

export type CommandRequest = Command
