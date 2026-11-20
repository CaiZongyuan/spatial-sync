"""Immutable motion input and clip catalog rooted in the user library."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from packages.info.plans import PlanError
from packages.library.archive import destination_parent, import_lock
from packages.library.transactions import durable_bytes, durable_json, sync_directory
from packages.motion.contract import MAX_CLIP_BYTES, VIDEO_REF, PoseArtifact, build_clip
from packages.motion.video import inspect_video


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class MotionLibrary:
    def __init__(self, root, jobs):
        self.root = root
        self.jobs = jobs

    def publish_video(self, path, license):
        info = inspect_video(path)
        raw = path.read_bytes()
        metadata = info | {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "license": license.model_dump(exclude_none=True),
        }
        digest = hashlib.sha256(_canonical(metadata)).hexdigest()
        metadata["id"] = f"motion-video/{digest}"
        self.root.mkdir(parents=True, exist_ok=True)
        with import_lock(self.root):
            parent = destination_parent(self.root, ("motion-videos",), create=True)
            target = destination_parent(parent, (digest,))
            if not target.exists():
                with tempfile.TemporaryDirectory(prefix=".video-", dir=parent) as temporary:
                    package = Path(temporary) / "package"
                    durable_bytes(package / "video", raw)
                    durable_json(package / "video.json", metadata)
                    os.rename(package, target)
                    sync_directory(parent)
                    sync_directory(self.root)
        return self.video(metadata["id"])

    def video(self, identity):
        if not re.fullmatch(VIDEO_REF, identity):
            raise PlanError("unknown_motion_video", "Unknown motion video", status=404)
        parent = destination_parent(self.root, ("motion-videos", identity.split("/")[1]))
        try:
            metadata = json.loads((parent / "video.json").read_bytes())
        except FileNotFoundError as exc:
            raise PlanError("unknown_motion_video", "Unknown motion video", status=404) from exc
        fields = {key: value for key, value in metadata.items() if key != "id"}
        if (
            metadata["id"] != identity
            or hashlib.sha256(_canonical(fields)).hexdigest() != identity.split("/")[1]
        ):
            raise PlanError("motion_video_changed", "Motion video metadata changed", status=409)
        return metadata

    def video_bytes(self, metadata):
        parent = destination_parent(self.root, ("motion-videos", metadata["id"].split("/")[1]))
        path = parent / "video"
        if path.is_symlink() or path.is_junction():
            raise PlanError("motion_video_changed", "Motion video bytes changed", status=409)
        try:
            with path.open("rb") as stream:
                raw = stream.read(metadata["bytes"] + 1)
        except OSError as exc:
            raise PlanError("motion_video_changed", "Motion video bytes unavailable", status=409) from exc
        if len(raw) != metadata["bytes"] or hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
            raise PlanError("motion_video_changed", "Motion video bytes changed", status=409)
        return raw

    def videos(self):
        parent = destination_parent(self.root, ("motion-videos",))
        return [
            self.video(f"motion-video/{path.parent.name}") for path in sorted(parent.glob("*/video.json"))
        ]

    def prepare_clip(self, source, raw):
        try:
            artifact = PoseArtifact.model_validate_json(raw)
        except ValueError as exc:
            raise PlanError("motion_artifact_invalid", "Invalid motion pose evidence", status=422) from exc
        return _canonical(build_clip(artifact, source))

    def store_clip(self, raw, *, existing=None):
        if len(raw) > MAX_CLIP_BYTES:
            raise PlanError("motion_artifact_invalid", "Motion clip exceeds the size limit", status=422)
        digest = hashlib.sha256(raw).hexdigest()
        identity = f"motion-clip/{digest}@1"
        descriptor = {"id": identity, "sha256": digest, "bytes": len(raw)}
        if existing is not None and existing != descriptor:
            raise PlanError(
                "motion_artifact_conflict", "This motion dispatch already has different artifacts",
            )
        parent = destination_parent(self.root, ("motion-clips",), create=True)
        path = parent / f"{digest}.json"
        if not path.exists():
            durable_bytes(path, raw)
        elif path.is_symlink() or path.read_bytes() != raw:
            raise PlanError("motion_clip_changed", "Motion clip bytes changed", status=409)
        return descriptor

    def clips(self):
        self.jobs._require_available()
        descriptors = {
            job["motionClip"]["id"]: job["motionClip"]
            for job in tuple(self.jobs.records.values())
            if job["kind"] == "motion_preprocess"
            and job["status"] == "succeeded"
            and job["jobId"] not in self.jobs._pending_job_events
        }
        return [descriptors[key] for key in sorted(descriptors)]

    def clip(self, identity):
        descriptor = next((item for item in self.clips() if item["id"] == identity), None)
        if descriptor is None:
            raise PlanError("unknown_motion_clip", "Unknown motion clip", status=404)
        parent = destination_parent(self.root, ("motion-clips",))
        path = parent / (descriptor["sha256"] + ".json")
        if path.is_symlink() or path.is_junction() or not path.is_file():
            raise PlanError("motion_clip_changed", "Motion clip bytes changed", status=409)
        with path.open("rb") as stream:
            raw = stream.read(descriptor["bytes"] + 1)
        if hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
            raise PlanError("motion_clip_changed", "Motion clip bytes changed", status=409)
        return descriptor | json.loads(raw)
