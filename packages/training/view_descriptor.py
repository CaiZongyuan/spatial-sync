"""Freeze pinned preview geometry for a Job; captured poses never enter the view path."""

import hashlib
import json
import shutil
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from apps.api.app.recordings import _standalone_gltf
from packages.info.plans import PlanError
from packages.library.archive import destination_parent
from packages.library.common import content_hash
from packages.library.previews import relative_file
from packages.library.transactions import durable_json
from packages.protocol.schema import SceneManifest, SourceInfo


def pins(job):
    return {
        "task": job["task"],
        "taskContentHash": job["taskContentHash"],
        "scene": job["scene"],
        "sceneContentHash": job["sceneContentHash"],
        "assets": sorted(job["assets"], key=lambda item: item["id"]),
    }


def descriptor_digest(scope, manifest, resources):
    canonical = {
        key: value
        for key, value in manifest.items()
        if key not in ("sessionId", "sceneRevision", "revisionNumericId", "epoch", "source")
    }
    payload = {
        "scope": scope, "manifest": canonical, "resources": resources, "captureSource": manifest["source"]
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ViewDescriptor:
    digest: str
    directory: Path
    manifest: dict
    resources: dict[str, str]
    source: dict
    scope: dict

    def hint(self):
        return {
            "descriptorDigest": self.digest,
            "bodyTable": [
                {"entityKey": node["entityKey"], "streamId": node["streamId"]}
                for node in self.manifest["nodes"]
                if node["kind"] == "body"
            ],
            "sites": self.manifest["sites"],
            "joints": self.manifest["joints"],
            "capture": {"source": self.source},
        }

    def for_environment(self, job_id, view_id, env_index, source):
        manifest = deepcopy(self.manifest)
        manifest.update(
            sessionId=f"training-view:{view_id}:env:{env_index}",
            sceneRevision=f"training-scene-{self.digest}",
            revisionNumericId=1,
            epoch=0,
            source=SourceInfo.model_validate(source).model_dump(mode="json"),
        )
        prefix = f"/api/v1/jobs/{job_id}/view-assets/{self.digest}/"
        for node in manifest["nodes"]:
            if node["kind"] == "group":
                continue
            for visual in (*node["visuals"], *(node.get("fallbackVisuals") or []), *node["colliders"]):
                if visual["kind"] != "primitive":
                    visual["url"] = prefix + visual["url"]
        return SceneManifest.model_validate(manifest).model_dump(mode="json")


class ViewDescriptors:
    def __init__(self, manager, previews):
        self.manager, self.previews = manager, previews
        self.root = manager.settings.state_root / "training-view-assets"
        self._lock = threading.RLock()
        self._cache = {}
        self._retired = set()

    def prepare(self, job):
        """Run in a worker: this independent owner never acquires the Session/resource lock."""
        scope = pins(job)
        try:
            with self._lock:
                if job["jobId"] in self._retired:
                    raise ValueError("Job is no longer running")
                cached = self._cache.get(job["jobId"])
                if cached is not None:
                    if cached.scope != scope:
                        raise ValueError("Job descriptor inputs changed")
                    return cached
                self.root.mkdir(parents=True, exist_ok=True)
                directory = destination_parent(self.root, (job["jobId"],), create=True)
                record = directory / "descriptor.json"
                if record.is_file():
                    saved = json.loads(record.read_text(encoding="utf-8"))
                    if saved["scope"] != scope:
                        raise ValueError("Stored Job descriptor inputs changed")
                    result = self._load(directory, saved)
                else:
                    result = self._capture(job, directory, scope)
                if job["jobId"] in self._retired:
                    raise ValueError("Job ended while preparing its descriptor")
                self._cache[job["jobId"]] = result
                return result
        except Exception as error:
            message = (str(error) or type(error).__name__)[:4000]
            raise PlanError("training_view_manifest_unavailable", message, status=409) from error

    def _load(self, directory, saved):
        manifest = SceneManifest.model_validate(saved["manifest"]).model_dump(mode="json")
        if SourceInfo.model_validate(saved["source"]).model_dump(mode="json") != manifest["source"]:
            raise ValueError("Job capture provenance changed")
        if descriptor_digest(saved["scope"], manifest, saved["resources"]) != saved["digest"]:
            raise ValueError("Job geometry descriptor changed")
        for relative, digest in saved["resources"].items():
            if hashlib.sha256(relative_file(directory, relative).read_bytes()).hexdigest() != digest:
                raise ValueError("Job geometry resource changed")
        return ViewDescriptor(
            saved["digest"], directory, manifest, saved["resources"], saved["source"], saved["scope"]
        )

    def _capture(self, job, directory, scope):
        # Job references protect the immutable versions from API deletion. Hash the
        # fixed bytes without holding library/resource locks during geometry I/O.
        version = self.manager.library.resolve(job["scene"])
        if content_hash(version.path) != job["sceneContentHash"]:
            raise ValueError("Pinned Scene bytes changed")
        dependencies = {
            item["id"]: item["contentHash"]
            for item in (*version.asset_dependencies(), *version.template_dependencies())
        }
        if dependencies != {item["id"]: item["contentHash"] for item in job["assets"]}:
            raise ValueError("Pinned Asset closure differs from the Job")
        # This is the existing strict capture validator, not a live preview source.
        capture_directory, capture = self.previews._capture(version, "newton")
        manifest = SceneManifest.model_validate_json(
            (capture_directory / capture["documents"]["manifest"]).read_bytes()
        ).model_dump(mode="json")
        resources = {}
        for node in manifest["nodes"]:
            if node["kind"] == "group":
                continue
            node["debug"] = {"capture": node.get("debug", {})}
            for visual in (*node["visuals"], *(node.get("fallbackVisuals") or []), *node["colliders"]):
                if visual["kind"] == "primitive" or visual["url"] not in capture["files"]:
                    continue  # Indexed missing-resource markers retain their normal fallback behavior.
                relative = visual["url"]
                payload = relative_file(capture_directory, relative).read_bytes()
                _standalone_gltf(payload, Path(relative).suffix)
                resources[relative] = hashlib.sha256(payload).hexdigest()
                if resources[relative] != capture["files"][relative]:
                    raise ValueError("Pinned geometry changed while preparing the Job descriptor")
                parent = destination_parent(directory, tuple(Path(relative).parts[:-1]), create=True)
                target = parent / Path(relative).name
                target.write_bytes(payload)
        source = manifest["source"]
        digest = descriptor_digest(scope, manifest, resources)
        saved = {
            "digest": digest,
            "scope": scope,
            "manifest": manifest,
            "resources": resources,
            "source": source,
        }
        durable_json(directory / "descriptor.json", saved)
        return self._load(directory, saved)

    def read(self, job_id, digest, relative):
        with self._lock:
            descriptor = self._cache.get(job_id)
            if descriptor is None or descriptor.digest != digest or relative not in descriptor.resources:
                raise PlanError("training_view_asset_unavailable", "Unknown Job view resource", status=404)
            payload = relative_file(descriptor.directory, relative).read_bytes()
            if hashlib.sha256(payload).hexdigest() != descriptor.resources[relative]:
                raise PlanError("training_view_asset_unavailable", "Job view resource changed", status=404)
            return payload

    def mark_terminal(self, job_id):
        self._retired.add(job_id)

    def release(self, job_id):
        with self._lock:
            self._cache.pop(job_id, None)
            directory = destination_parent(self.root, (job_id,), create=False)
            if directory.exists():
                resolved = directory.resolve()
                if resolved == self.root.resolve() or not resolved.is_relative_to(self.root.resolve()):
                    raise ValueError("Job view cleanup target is outside its owner")
                shutil.rmtree(directory)
