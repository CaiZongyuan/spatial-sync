"""Versioned wire inputs for the CPU pose runtime."""

from typing import Annotated

from pydantic import Field

from packages.info.plans import PlanError
from packages.training.schema import Schema

MAX_VIDEO_BYTES = 16 * 1024 * 1024
MAX_CLIP_BYTES = 4 * 1024 * 1024
MAX_SECONDS = 20
MAX_FRAMES = 600
MAX_PIXELS = 1920 * 1080
MAX_DIMENSION = 1920
MOTION_TIMEOUT = 180
VIDEO_REF = r"^motion-video/[a-f0-9]{64}$"
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
MODEL_SHA256 = "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a"
SDK_VERSION = "0.10.32"
POSE_RUNTIME = "mediapipe-pose-landmarker-lite/1"
MODEL = {
    "runtime": POSE_RUNTIME,
    "sdkVersion": SDK_VERSION,
    "sha256": MODEL_SHA256,
    "url": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
    "landmarkLicense": "Apache-2.0",
    "licenseSource": "https://storage.googleapis.com/mediapipe-assets/Model%20Card%20BlazePose%20GHUM%203D.pdf",
    "detectorLicense": None,
}
JOINTS = (
    "leftShoulder",
    "rightShoulder",
    "leftElbow",
    "rightElbow",
    "leftWrist",
    "rightWrist",
    "leftHip",
    "rightHip",
    "leftKnee",
    "rightKnee",
    "leftAnkle",
    "rightAnkle",
    "leftHeel",
    "rightHeel",
    "leftFootIndex",
    "rightFootIndex",
)


class VideoLicense(Schema):
    license: str = Field(min_length=1, max_length=256, pattern=r"\S")
    attribution: str = Field(min_length=1, max_length=1024, pattern=r"\S")
    sourceUrl: str | None = Field(default=None, max_length=2048, pattern=r"^https?://\S+$")


class Landmark(Schema):
    position: tuple[float, float, float]
    visibility: float = Field(ge=0, le=1)
    inFrame: bool


BodyLandmarks = Annotated[list[Landmark], Field(min_length=len(JOINTS), max_length=len(JOINTS))]


class PoseFrame(Schema):
    timeSeconds: float
    poses: list[BodyLandmarks] = Field(max_length=2)


class PoseArtifact(Schema):
    sourceSha256: Hash
    modelSha256: Hash
    frames: list[PoseFrame] = Field(min_length=2, max_length=MAX_FRAMES)


def build_clip(artifact, source):
    if (
        artifact.sourceSha256 != source["sha256"]
        or artifact.modelSha256 != MODEL_SHA256
        or [frame.timeSeconds for frame in artifact.frames] != source["frameTimes"]
    ):
        raise PlanError(
            "motion_artifact_mismatch", "Pose evidence differs from the leased video or model", status=422
        )

    frames = []
    seen = visible = 0
    for frame in artifact.frames:
        if len(frame.poses) > 1:
            raise PlanError("motion_multiple_people", "More than one person was detected", status=422)
        joints = None
        if frame.poses:
            seen += 1
            (points,) = frame.poses
            if all(point.inFrame and point.visibility >= 0.5 for point in points):
                visible += 1
                joints = {
                    joint: {"position": list(point.position), "visibility": point.visibility}
                    for joint, point in zip(JOINTS, points, strict=True)
                }
        frames.append({"timeSeconds": frame.timeSeconds, "joints": joints})
    if not seen:
        raise PlanError("motion_no_person", "No person was detected", status=422)
    if visible < 2 or visible * 100 < 85 * len(frames):
        raise PlanError(
            "motion_occluded", "Fewer than 85% of frames show the required body landmarks", status=422
        )
    return {
        "schema": "lw.motion-clip/1",
        "source": source,
        "model": MODEL,
        "coordinates": {
            "unit": "m",
            "up": "Z",
            "axes": ["image-right", "away-from-camera", "up"],
            "origin": "hip-midpoint",
            "rootTranslation": "unobserved",
        },
        "jointKeys": list(JOINTS),
        "frames": frames,
        "diagnostics": {
            "visibleFrames": visible,
            "missingFrames": len(frames) - visible,
            "noPersonFrames": len(frames) - seen,
        },
    }
