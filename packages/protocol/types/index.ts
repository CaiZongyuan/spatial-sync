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
export type ClientMessage = Command | Subscribe | ActionRequest | ActionCancel | ActionApprove
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "ServerMessage".
 */
export type ServerMessage =
  | Ready
  | SubscriptionAck
  | TransformFrame
  | SessionState
  | SceneReplaced
  | CommandAck
  | Heartbeat
  | ServerError
  | EntitySnapshot
  | EntityObservations
  | EntityEvent
  | EntityAlert
  | PlanUpdate
  | InvocationUpdate
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "PlanUpdate".
 */
export type PlanUpdate = PlanFullUpdate | PlanStepUpdate
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "OperationStep".
 */
export type OperationStep = OperationDoStep | OperationWaitStep | OperationCheckStep

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
  invocation: Invocation
  invokeRequest: InvokeRequest
  catalog: Catalog
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "SceneManifest".
 */
export interface SceneManifest {
  protocolVersion: 2
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
  sampleTimeClock: 'runner-monotonic' | 'gateway-receive'
  shapeCount?: number | null
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
  protocolVersion: 2
  sceneRevision: string
  rateHz: number
  info: boolean
  requestId?: string | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "ActionRequest".
 */
export interface ActionRequest {
  type: 'action.request'
  requestId: string
  entityKey: string
  action: string
  params: {
    [k: string]: JsonValue
  }
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "ActionCancel".
 */
export interface ActionCancel {
  type: 'action.cancel'
  requestId: string
  invocationId: string
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "ActionApprove".
 */
export interface ActionApprove {
  type: 'action.approve'
  requestId: string
  invocationId: string
  decision: 'approve' | 'deny'
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Ready".
 */
export interface Ready {
  role: 'viewer' | 'operator'
  type: 'server.ready'
  protocolVersion: 2
  sessionId: string
  sceneRevision: string
  revisionNumericId: number
  epoch: number
  sessionState: 'CREATED' | 'LOADING' | 'READY' | 'RUNNING' | 'PAUSED' | 'STOPPING' | 'STOPPED' | 'FAILED'
  simTime: number
  basePublishHz: number
  realtimeFactor: number | null
  heartbeatIntervalMs: number
  sampleTimeClock: 'runner-monotonic' | 'gateway-receive'
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "SubscriptionAck".
 */
export interface SubscriptionAck {
  type: 'subscription.ack'
  grantedRateHz: number
  info: boolean
  requestId?: string | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "TransformFrame".
 */
export interface TransformFrame {
  protocolVersion: 2
  sessionId: string
  sceneRevision: string
  epoch: number
  seq: number
  simTime: number
  sampleTimeMs: number
  poses: [number, number, number, number, number, number, number, number][]
  type: 'transform.frame' | 'stream.snapshot'
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
    | 'history_unavailable'
  message: string
  requestId?: string | null
  invocationId?: string | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "EntitySnapshot".
 */
export interface EntitySnapshot {
  type: 'entity.snapshot'
  sceneRevision: string
  epoch: number
  simTime: number
  sampleTimeMs: number
  entities: EntityValues[]
  invocations: Invocation[]
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "EntityValues".
 */
export interface EntityValues {
  key: string
  rev: number
  values: {
    [k: string]: JsonValue
  }
  meta: {
    [k: string]: ObservationMeta
  }
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "ObservationMeta".
 */
export interface ObservationMeta {
  simTime: number
  sampleTimeMs: number
  source: 'sim' | 'virtual' | 'device'
  stale?: boolean
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Invocation".
 */
export interface Invocation {
  invocationId: string
  requestId: string
  entityKey: string
  action: string
  params: {
    [k: string]: JsonValue
  }
  actor: Actor
  epoch: number
  createdSimTime: number
  createdAtMs: number
  status: 'pending_approval' | 'accepted' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'rejected'
  progress?: number | null
  result?:
    | boolean
    | number
    | string
    | JsonValue[]
    | {
        [k: string]: JsonValue
      }
    | null
  error?: InvocationError | null
  startedSimTime?: number | null
  endedSimTime?: number | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Actor".
 */
export interface Actor {
  kind: 'user' | 'cli' | 'agent'
  name: string
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "InvocationError".
 */
export interface InvocationError {
  code: string
  message: string
  details: {
    [k: string]: JsonValue
  }
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "EntityObservations".
 */
export interface EntityObservations {
  type: 'entity.observations'
  sceneRevision: string
  epoch: number
  simTime: number
  sampleTimeMs: number
  source: 'sim' | 'virtual' | 'device'
  changes: ObservationChange[]
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "ObservationChange".
 */
export interface ObservationChange {
  key: string
  rev: number
  values: {
    [k: string]: JsonValue
  }
  meta?: {
    [k: string]: ObservationMeta
  }
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "EntityEvent".
 */
export interface EntityEvent {
  type: 'entity.event'
  sceneRevision: string
  epoch: number
  entityKey: string
  event: string
  data: {
    [k: string]: JsonValue
  }
  simTime: number
  sampleTimeMs: number
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "EntityAlert".
 */
export interface EntityAlert {
  type: 'entity.alert'
  sceneRevision: string
  epoch: number
  simTime: number
  sampleTimeMs: number
  entityKey: string
  alertId: string
  rule: string
  severity: 'info' | 'warning' | 'critical'
  state: 'raised' | 'acknowledged' | 'cleared'
  message: string
  hint?: string | null
  value?:
    | boolean
    | number
    | string
    | JsonValue[]
    | {
        [k: string]: JsonValue
      }
    | null
  since: number
  actor?: Actor | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "PlanFullUpdate".
 */
export interface PlanFullUpdate {
  type: 'plan.update'
  update: 'full'
  sceneRevision: string
  epoch: number
  simTime: number
  sampleTimeMs: number
  plan: OperationPlan
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "OperationPlan".
 */
export interface OperationPlan {
  id: string
  kind: 'operation'
  title: string
  target: 'session' | 'live'
  revision: number
  status: 'draft' | 'submitted' | 'approved' | 'running' | 'completed' | 'abandoned'
  steps: OperationStep[]
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "OperationDoStep".
 */
export interface OperationDoStep {
  id: number
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped'
  do: PlanAction
  expect?: Condition | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "PlanAction".
 */
export interface PlanAction {
  entity: string
  action: string
  params: {
    [k: string]: JsonValue
  }
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Condition".
 */
export interface Condition {
  [k: string]: JsonValue
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "OperationWaitStep".
 */
export interface OperationWaitStep {
  id: number
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped'
  wait: Condition
  timeoutSeconds?: number | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "OperationCheckStep".
 */
export interface OperationCheckStep {
  id: number
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped'
  check: Condition
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "PlanStepUpdate".
 */
export interface PlanStepUpdate {
  type: 'plan.update'
  update: 'step'
  sceneRevision: string
  epoch: number
  simTime: number
  sampleTimeMs: number
  planId: string
  revision: number
  stepId: number
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped'
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "InvocationUpdate".
 */
export interface InvocationUpdate {
  invocationId: string
  requestId: string
  entityKey: string
  action: string
  params: {
    [k: string]: JsonValue
  }
  actor: Actor
  epoch: number
  createdSimTime: number
  createdAtMs: number
  status: 'pending_approval' | 'accepted' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'rejected'
  progress?: number | null
  result?:
    | boolean
    | number
    | string
    | JsonValue[]
    | {
        [k: string]: JsonValue
      }
    | null
  error?: InvocationError | null
  startedSimTime?: number | null
  endedSimTime?: number | null
  type: 'invocation.update'
  sceneRevision: string
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Snapshot".
 */
export interface Snapshot {
  protocolVersion: 2
  sessionId: string
  sceneRevision: string
  epoch: number
  seq: number
  simTime: number
  sampleTimeMs: number
  poses: [number, number, number, number, number, number, number, number][]
  type: 'stream.snapshot'
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "SessionOptions".
 */
export interface SessionOptions {
  seed: number
  realtimeFactor: number | null
  physicsDt: number
  basePublishHz: number
  maxLagMs: number
  continueWithoutObservers: boolean
  strictLabels: boolean
  device: string
  controlHz: number
  factHz: number
  approval: ApprovalPolicy
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "ApprovalPolicy".
 */
export interface ApprovalPolicy {
  high: 'auto' | 'require'
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
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "InvokeRequest".
 */
export interface InvokeRequest {
  invocationId: string
  requestId: string
  entityKey: string
  action: string
  params: {
    [k: string]: JsonValue
  }
  actor: Actor
  epoch: number
  createdSimTime: number
  createdAtMs: number
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "Catalog".
 */
export interface Catalog {
  sceneRevision: string
  types: {
    [k: string]: EntityType
  }
  entities: {
    [k: string]: EntityBinding
  }
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "EntityType".
 */
export interface EntityType {
  type: string
  title: string
  description: string
  properties: {
    [k: string]: PropertyDefinition
  }
  actions: {
    [k: string]: ActionDefinition
  }
  events: {
    [k: string]: EventDefinition
  }
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "PropertyDefinition".
 */
export interface PropertyDefinition {
  schema: JsonSchema
  description: string
  unit?: string | null
  deadband?: number | null
  maxRateHz?: number | null
  staleAfterSeconds?: number | null
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "JsonSchema".
 */
export interface JsonSchema {
  [k: string]: JsonValue
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "ActionDefinition".
 */
export interface ActionDefinition {
  description: string
  params: {
    [k: string]: JsonSchema
  }
  required: string[]
  preconditions: Condition[]
  longRunning: boolean
  timeoutSeconds: number
  concurrent: boolean
  risk: 'normal' | 'high'
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "EventDefinition".
 */
export interface EventDefinition {
  schema?: JsonSchema | null
  description: string
}
/**
 * This interface was referenced by `ProtocolContract`'s JSON-Schema
 * via the `definition` "EntityBinding".
 */
export interface EntityBinding {
  key: string
  type: string
  program: string
  config: {
    [k: string]: JsonValue
  }
  interlocks: {
    [k: string]: Condition[]
  }
  unplaced: boolean
}

export type CommandRequest = Command
