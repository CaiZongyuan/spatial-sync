"""Authoritative Pydantic v2 models for spatial protocol version 2.

Use model_dump(mode="json", exclude_none=True) for outbound wire messages.
Validate low-frequency/untrusted boundaries, not every rendered high-frequency frame.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeAliasType

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_serializer,
    field_validator,
    model_validator,
)

PROTOCOL_VERSION = 2
MAX_MANUAL_STEPS = 1000
Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
JsonValue = TypeAliasType(
    "JsonValue", "None | StrictBool | StrictInt | StrictStr | Finite | list[JsonValue] | dict[str, JsonValue]"
)
Positive = Annotated[Finite, Field(gt=0)]
NonNegative = Annotated[Finite, Field(ge=0)]
UInt32 = Annotated[int, Field(strict=True, ge=0, le=4294967295)]
Counter = Annotated[int, Field(strict=True, ge=0)]
NonEmpty = Annotated[str, Field(strict=True, min_length=1)]
EntityKey = Annotated[str, Field(strict=True, pattern=r"^/[^/?#]+(?:/[^/?#]+)*$")]
Vec3 = tuple[Finite, Finite, Finite]
Quaternion = tuple[Finite, Finite, Finite, Finite]
Lifecycle = Literal["CREATED", "LOADING", "READY", "RUNNING", "PAUSED", "STOPPING", "STOPPED", "FAILED"]
CommandName = Literal["pause", "resume", "reset", "step", "set_realtime_factor"]
RejectionReason = Literal[
    "invalid_state", "unknown_command", "invalid_args", "unauthorized", "source_unavailable"
]
ErrorCode = Literal[
    "invalid_message",
    "unsupported_protocol",
    "revision_mismatch",
    "session_not_ready",
    "unknown_session",
    "unknown_entity",
    "source_unavailable",
    "internal_error",
    "unauthorized",
]


class Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_default=True,
        allow_inf_nan=False,
        json_schema_serialization_defaults_required=True,
    )

    @model_validator(mode="before")
    @classmethod
    def validate_protocol_version(cls, data):
        if isinstance(data, dict) and "protocolVersion" in data and type(data["protocolVersion"]) is not int:
            raise ValueError("protocolVersion must be integer 2")
        return data


def _unit_quaternion(q: Quaternion) -> Quaternion:
    if abs(sum(x * x for x in q) - 1) > 1e-5:
        raise ValueError("quaternion must be unit length in xyzw order")
    return q


class Pose(Model):
    p: Vec3 = (0, 0, 0)
    q: Quaternion = (0, 0, 0, 1)

    _quaternion = field_validator("q")(_unit_quaternion)


class Box(Model):
    type: Literal["box"] = "box"
    halfExtents: tuple[Positive, Positive, Positive]


class Sphere(Model):
    type: Literal["sphere"] = "sphere"
    radius: Positive


class Capsule(Model):
    type: Literal["capsule"] = "capsule"
    radius: Positive
    halfHeight: NonNegative


class Cylinder(Model):
    type: Literal["cylinder"] = "cylinder"
    radius: Positive
    halfHeight: Positive


class Cone(Model):
    type: Literal["cone"] = "cone"
    radius: Positive
    halfHeight: Positive


class Plane(Model):
    type: Literal["plane"] = "plane"
    width: NonNegative
    length: NonNegative


PrimitiveGeometry = TypeAliasType(
    "PrimitiveGeometry",
    Annotated[Box | Sphere | Capsule | Cylinder | Cone | Plane, Field(discriminator="type")],
)


class Primitive(Model):
    kind: Literal["primitive"] = "primitive"
    geometry: PrimitiveGeometry
    localPose: Pose = Field(default_factory=Pose)


class Mesh(Model):
    kind: Literal["mesh"] = "mesh"
    meshId: NonEmpty
    url: NonEmpty
    assetUpAxis: Literal["Z"] = "Z"
    scale: Vec3 = (1, 1, 1)
    localPose: Pose = Field(default_factory=Pose)


class Glb(Model):
    kind: Literal["glb"] = "glb"
    url: NonEmpty
    assetUpAxis: Literal["Y", "Z"]
    scale: Vec3
    localPose: Pose


Visual = TypeAliasType("Visual", Annotated[Primitive | Mesh | Glb, Field(discriminator="kind")])


class GroupNode(Model):
    entityKey: EntityKey
    kind: Literal["group"] = "group"
    name: NonEmpty
    parentEntityKey: EntityKey | None = None
    stableKey: StrictBool = True


class BodyNode(Model):
    entityKey: EntityKey
    kind: Literal["body"] = "body"
    streamId: UInt32
    name: NonEmpty
    parentEntityKey: EntityKey | None = None
    transformMode: Literal["stream"] = "stream"
    stableKey: StrictBool = True
    visuals: list[Visual] = Field(default_factory=list)
    fallbackVisuals: list[Visual] | None = None
    colliders: list[Visual] = Field(default_factory=list)
    debug: dict[str, JsonValue] = Field(default_factory=dict)


class StaticNode(Model):
    entityKey: EntityKey
    kind: Literal["static"] = "static"
    name: NonEmpty
    parentEntityKey: EntityKey | None = None
    transformMode: Literal["static"] = "static"
    initialPose: Pose
    stableKey: StrictBool = True
    visuals: list[Visual] = Field(default_factory=list)
    fallbackVisuals: list[Visual] | None = None
    colliders: list[Visual] = Field(default_factory=list)
    debug: dict[str, JsonValue] = Field(default_factory=dict)


SceneNode = TypeAliasType(
    "SceneNode", Annotated[GroupNode | BodyNode | StaticNode, Field(discriminator="kind")]
)


class Site(Model):
    entityKey: EntityKey
    role: NonEmpty = "generic"
    parentBodyKey: EntityKey | None = None
    jointKey: EntityKey | None = None
    localPose: Pose
    stableKey: StrictBool = True


class Joint(Model):
    entityKey: EntityKey
    type: NonEmpty
    parentBodyKey: EntityKey | None = None
    childBodyKey: EntityKey
    parentLocalPose: Pose | None = None
    axis: Vec3 | None = None
    anchorSiteKey: EntityKey | None = None
    dofCount: UInt32
    stableKey: StrictBool = True

    @model_validator(mode="after")
    def validate_joint(self):
        if self.type == "free":
            if self.parentLocalPose is not None or self.axis is not None or self.anchorSiteKey is not None:
                raise ValueError("free joints omit parentLocalPose, axis and anchorSiteKey")
        elif self.parentLocalPose is None or self.anchorSiteKey is None:
            raise ValueError("non-free joints require parentLocalPose and anchorSiteKey")
        if self.type in ("revolute", "prismatic"):
            if self.axis is None or abs(sum(x * x for x in self.axis) - 1) > 1e-5:
                raise ValueError("single-DOF joint axis must be a unit vector")
            if self.dofCount != 1:
                raise ValueError("single-DOF joint dofCount must be 1")
        elif self.axis is not None:
            raise ValueError("axis is only defined for single-DOF joints")
        return self


class SceneWarning(Model):
    code: NonEmpty
    entityKey: EntityKey | None = None
    message: NonEmpty


class SourceInfo(Model):
    kind: Literal["newton", "synthetic", "replay", "device"]
    newtonVersion: NonEmpty | None = None
    solver: NonEmpty | None = None
    physicsDt: Positive
    basePublishHz: Positive = 30
    realtimeFactor: Positive | None = 1.0
    sampleTimeClock: Literal["runner-monotonic", "gateway-receive"] = "runner-monotonic"
    shapeCount: UInt32 | None = None


class Coordinates(Model):
    handedness: Literal["right"] = "right"
    upAxis: Literal["Z"] = "Z"
    units: Literal["m"] = "m"
    quaternionOrder: Literal["xyzw"] = "xyzw"
    poseSpace: Literal["world"] = "world"
    primitiveConvention: Literal["newton"] = "newton"


class SceneManifest(Model):
    protocolVersion: Literal[2] = 2
    sessionId: NonEmpty
    sceneRevision: NonEmpty
    revisionNumericId: UInt32
    epoch: UInt32 = 0
    sceneName: NonEmpty
    source: SourceInfo
    coordinates: Coordinates = Field(default_factory=Coordinates)
    nodes: list[SceneNode]
    sites: list[Site] = Field(default_factory=list)
    joints: list[Joint] = Field(default_factory=list)
    warnings: list[SceneWarning] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self):
        keys = [x.entityKey for x in [*self.nodes, *self.sites, *self.joints]]
        if len(set(keys)) != len(keys):
            raise ValueError("entityKey must be unique across nodes, sites and joints")
        nodes = {node.entityKey: node for node in self.nodes}
        bodies = {node.entityKey for node in self.nodes if node.kind == "body"}
        streams = [node.streamId for node in self.nodes if node.kind == "body"]
        if len(set(streams)) != len(streams):
            raise ValueError("body streamId must be unique in a scene revision")
        for node in self.nodes:
            visited = {node.entityKey}
            parent = node.parentEntityKey
            while parent is not None:
                if parent not in nodes:
                    raise ValueError(f"unknown parentEntityKey: {parent}")
                if parent in visited:
                    raise ValueError("logical node hierarchy cannot contain a cycle")
                visited.add(parent)
                parent = nodes[parent].parentEntityKey
        sites = {site.entityKey: site for site in self.sites}
        joints = {joint.entityKey: joint for joint in self.joints}
        for site in self.sites:
            if site.parentBodyKey is not None and site.parentBodyKey not in bodies:
                raise ValueError(f"unknown Site parent Body: {site.parentBodyKey}")
            if site.jointKey is not None and site.jointKey not in joints:
                raise ValueError(f"unknown Site joint: {site.jointKey}")
        for joint in self.joints:
            if joint.childBodyKey not in bodies or (
                joint.parentBodyKey is not None and joint.parentBodyKey not in bodies
            ):
                raise ValueError(f"unknown Joint Body: {joint.entityKey}")
            if joint.type != "free":
                site = sites.get(joint.anchorSiteKey)
                if (
                    site is None
                    or site.parentBodyKey != joint.childBodyKey
                    or site.jointKey != joint.entityKey
                    or site.role != "joint-anchor"
                ):
                    raise ValueError(
                        f"Joint anchor must reference its child-side joint-anchor Site: {joint.entityKey}"
                    )
        return self


PoseRow = tuple[UInt32, Finite, Finite, Finite, Finite, Finite, Finite, Finite]


class PoseMessage(Model):
    protocolVersion: Literal[2] = 2
    sessionId: NonEmpty
    sceneRevision: NonEmpty
    epoch: UInt32
    seq: Counter
    simTime: NonNegative
    sampleTimeMs: NonNegative
    poses: list[PoseRow]

    @field_validator("poses")
    @classmethod
    def validate_poses(cls, rows):
        stream_ids = [row[0] for row in rows]
        if len(set(stream_ids)) != len(stream_ids):
            raise ValueError("frame streamIds must be unique")
        for row in rows:
            _unit_quaternion(row[4:8])
        return rows

    @field_serializer("poses", when_used="json")
    def serialize_poses(self, rows):
        return [[row[0], *(round(x, 6) for x in row[1:4]), *(round(x, 7) for x in row[4:8])] for row in rows]


class TransformFrame(PoseMessage):
    type: Literal["transform.frame", "stream.snapshot"] = "transform.frame"


class Snapshot(TransformFrame):
    type: Literal["stream.snapshot"] = "stream.snapshot"


class Cause(Model):
    kind: Literal["command", "lifecycle", "error"]
    requestId: NonEmpty | None = None
    command: NonEmpty | None = None
    code: NonEmpty | None = None
    message: NonEmpty | None = None


class SessionState(Model):
    type: Literal["session.state"] = "session.state"
    sessionId: NonEmpty
    state: Lifecycle
    sceneRevision: NonEmpty
    epoch: UInt32
    seq: Counter
    simTime: NonNegative
    realtimeFactor: Positive | None
    actualRtf: NonNegative
    cause: Cause


class Heartbeat(Model):
    type: Literal["server.heartbeat"] = "server.heartbeat"
    state: Lifecycle
    simTime: NonNegative
    lastSeq: Counter
    actualRtf: NonNegative
    serverTimeMs: NonNegative


class Command(Model):
    type: Literal["command.request"] = "command.request"
    requestId: NonEmpty
    command: NonEmpty
    args: dict[str, JsonValue] = Field(default_factory=dict)


CommandRequest = Command


class EmptyArgs(Model):
    pass


class StepArgs(Model):
    count: Annotated[int, Field(strict=True, ge=1, le=MAX_MANUAL_STEPS)] = 1


class RealtimeFactorArgs(Model):
    value: Positive | None


def validate_command_args(command: Command) -> EmptyArgs | StepArgs | RealtimeFactorArgs:
    model = {
        "pause": EmptyArgs,
        "resume": EmptyArgs,
        "reset": EmptyArgs,
        "step": StepArgs,
        "set_realtime_factor": RealtimeFactorArgs,
    }.get(command.command)
    if model is None:
        raise ValueError("unknown_command")
    return model.model_validate(command.args)


class CommandAck(Model):
    type: Literal["command.ack"] = "command.ack"
    requestId: NonEmpty
    status: Literal["applied", "rejected"]
    simTime: NonNegative
    epoch: UInt32
    reason: RejectionReason | None = None
    message: NonEmpty | None = None

    @model_validator(mode="after")
    def validate_reason(self):
        if (self.status == "rejected") != (self.reason is not None):
            raise ValueError("only rejected command acknowledgements require a reason")
        return self


class Ready(Model):
    type: Literal["server.ready"] = "server.ready"
    protocolVersion: Literal[2] = 2
    sessionId: NonEmpty
    sceneRevision: NonEmpty
    revisionNumericId: UInt32
    epoch: UInt32
    sessionState: Lifecycle
    simTime: NonNegative
    basePublishHz: Positive
    realtimeFactor: Positive | None
    heartbeatIntervalMs: Positive = 1000
    sampleTimeClock: Literal["runner-monotonic", "gateway-receive"] = "runner-monotonic"


class Subscribe(Model):
    type: Literal["client.subscribe"] = "client.subscribe"
    protocolVersion: Literal[2] = 2
    sceneRevision: NonEmpty
    rateHz: Positive = 30
    info: StrictBool = True
    requestId: NonEmpty | None = None


class SubscriptionAck(Model):
    type: Literal["subscription.ack"] = "subscription.ack"
    grantedRateHz: Positive
    info: StrictBool = True
    requestId: NonEmpty | None = None


class SceneReplaced(Model):
    type: Literal["scene.replaced"] = "scene.replaced"
    sessionId: NonEmpty
    sceneRevision: NonEmpty
    revisionNumericId: UInt32
    epoch: UInt32 = 0


class ServerError(Model):
    type: Literal["server.error"] = "server.error"
    code: ErrorCode
    message: NonEmpty
    requestId: NonEmpty | None = None


class SessionOptions(Model):
    realtimeFactor: Positive | None = 1.0
    physicsDt: Positive = 1 / 240
    basePublishHz: Positive = 30
    maxLagMs: NonNegative = 250
    continueWithoutObservers: StrictBool = True
    strictLabels: StrictBool = False
    device: NonEmpty = "cpu"


class CommandResult(Model):
    ack: CommandAck
    frame: TransformFrame | None = None
    state: SessionState | None = None


class JointState(Model):
    entityKey: EntityKey
    simTime: NonNegative
    epoch: UInt32
    positions: list[Finite]


ClientMessage = TypeAliasType("ClientMessage", Annotated[Command | Subscribe, Field(discriminator="type")])
ServerMessage = TypeAliasType(
    "ServerMessage",
    Annotated[
        Ready
        | SubscriptionAck
        | TransformFrame
        | SessionState
        | SceneReplaced
        | CommandAck
        | Heartbeat
        | ServerError,
        Field(discriminator="type"),
    ],
)


class ProtocolContract(Model):
    manifest: SceneManifest
    clientMessage: ClientMessage
    serverMessage: ServerMessage
    snapshot: Snapshot
    sessionOptions: SessionOptions
    commandResult: CommandResult
    jointState: JointState
    stepArgs: StepArgs
    realtimeFactorArgs: RealtimeFactorArgs
