"""Immutable policy snapshots from the existing validated Job artifact channel."""

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import yaml

from packages.library.archive import (
    destination_parent,
    extract_package,
    import_lock,
    package_files,
    relative_path,
)
from packages.library.common import NAME, LibraryError, content_hash, document
from packages.library.transactions import durable_json

from .observations import ObservationSpec

REFERENCE = re.compile(r"policy/([a-z0-9]+(?:-[a-z0-9]+)*)@([1-9][0-9]*)")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class PolicyLibrary:
    def __init__(self, roots):
        self.roots = roots

    def _versions(self):
        result = {}
        for root in self.roots.values():
            parent = destination_parent(root, ("policies",))
            if not parent.exists():
                continue
            for name in parent.iterdir():
                if not NAME.fullmatch(name.name):
                    raise ValueError("Invalid policy directory")
                for revision in destination_parent(root, ("policies", name.name)).iterdir():
                    if not re.fullmatch(r"[1-9][0-9]*", revision.name):
                        raise ValueError("Invalid policy revision")
                    identity = f"policy/{name.name}@{revision.name}"
                    if identity in result:
                        raise ValueError("Ambiguous policy version")
                    result[identity] = destination_parent(root, ("policies", name.name, revision.name))
        return result

    def resolve(self, identity):
        try:
            if not isinstance(identity, str) or not REFERENCE.fullmatch(identity):
                raise LibraryError("unknown_policy", "Select an exact policy/name@revision", 404)
            path = self._versions().get(identity)
            if path is None:
                raise LibraryError("unknown_policy", "Unknown policy version", 404)
            record = read_json(path / "metadata.json")
            package = destination_parent(path, ("package",))
            package_files(package)
            if record["id"] != identity or content_hash(package) != record["contentHash"]:
                raise ValueError("Policy snapshot changed")
            provenance = read_json(package / "policy.json")
            return record | provenance, package
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise LibraryError(
                "policy_unavailable", "Policy snapshot changed or is unavailable", 409
            ) from exc

    def show(self, identity):
        return self.resolve(identity)[0]

    def list(self):
        try:
            return [self.show(identity) for identity in sorted(self._versions())]
        except (OSError, ValueError) as exc:
            raise LibraryError("policy_unavailable", "Policy index is unavailable", 409) from exc

    def create(self, manager, checkpoint):
        """No client-supplied weights, scores, host paths or provenance are accepted."""
        try:
            job_id, separator, selected = checkpoint.partition("/")
            if not separator:
                raise ValueError("Select job-id/checkpoint-relative-path")
            selected = relative_path(selected).as_posix()
            job = manager.jobs.get(job_id)
            if job["status"] != "succeeded":
                raise ValueError("Policy requires a succeeded Job with validated artifacts")
            task = manager.tasks.show(job["task"])
            if (
                task["contentHash"] != job["taskContentHash"]
                or task["sceneContentHash"] != job["sceneContentHash"]
            ):
                raise ValueError("Job Task/Scene identity changed")
            root = manager.library_assets.roots["user"]
            artifacts = destination_parent(root, ("jobs", job_id, "artifacts"))
            files = package_files(artifacts)
            records = [
                {"path": name, "size": (artifacts / name).stat().st_size, "sha256": digest(artifacts / name)}
                for name in sorted(files)
            ]
            if records != job["artifacts"]:
                raise ValueError("Job artifacts changed after validation")
            export, versions = read_json(artifacts / "export.json"), read_json(artifacts / "versions.json")
            if (
                export["checkpoint"] != selected
                or export["afterLearn"] is not True
                or digest(artifacts / selected) != export["checkpointSha256"]
                or versions != job["versions"]
                or any(
                    versions[key] != job[key]
                    for key in ("jobId", "task", "taskContentHash", "scene", "sceneContentHash", "seed")
                )
            ):
                raise ValueError("Selected checkpoint/export/versions chain does not match this Job")
            policy_path = relative_path(export["policy"]).as_posix()
            if policy_path not in files or digest(artifacts / policy_path) != export["policySha256"]:
                raise ValueError("ONNX export changed")
            sources = [policy_path, "export.json", "versions.json"]
            for entry in export["externalData"]:
                name = relative_path(entry["path"]).as_posix()
                if (
                    name not in files
                    or digest(artifacts / name) != entry["sha256"]
                    or (artifacts / name).stat().st_size != entry["size"]
                ):
                    raise ValueError("ONNX external data changed")
                sources.append(name)
            with manager.library_assets._lock, import_lock(root):
                return self._freeze(manager, checkpoint, job, task, artifacts, sources, policy_path, export)
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
            raise LibraryError("policy_source_invalid", str(exc), 422) from exc

    def _freeze(self, manager, checkpoint, job, task, artifacts, sources, policy_path, export):
        root = self.roots["user"]
        with tempfile.TemporaryDirectory(prefix=".policy-", dir=root) as temporary:
            stage = Path(temporary)
            package = stage / "package"
            package.mkdir()
            # Consume the same verified confined Task transfer as compute nodes.
            task_path = stage / "task"
            task_path.mkdir()
            extract_package(io.BytesIO(manager.tasks.export(task["id"])), task_path)
            observation = task_path / relative_path(task["task"]["observation"])
            spec = ObservationSpec.model_validate(document(observation))
            if spec.action.space != task["task"]["control"]["action"]:
                raise ValueError("Observation action differs from the pinned Task control")
            (package / "obs_spec.yaml").write_bytes(observation.read_bytes())
            durable_json(package / "task.json", task["task"])
            for name in sources:
                destination = package / "artifacts" / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(artifacts / name, destination)
            # Prove the frozen bytes still match the manifest after copy.
            manifest = {entry["path"]: entry for entry in job["artifacts"]}
            for name in sources:
                copied = package / "artifacts" / name
                if (
                    digest(copied) != manifest[name]["sha256"]
                    or copied.stat().st_size != manifest[name]["size"]
                ):
                    raise ValueError("Source artifacts changed while freezing")
            from .inference import CpuPolicy

            CpuPolicy(
                package / "artifacts" / policy_path,
                spec,
                root=package / "artifacts",
                external=export["externalData"],
            )
            provenance = {
                "checkpoint": checkpoint,
                "checkpointSha256": export["checkpointSha256"],
                "jobId": job["jobId"],
                "task": task["id"],
                "taskContentHash": task["contentHash"],
                "scene": job["scene"],
                "sceneContentHash": job["sceneContentHash"],
                "onnx": "artifacts/" + policy_path,
                "onnxSha256": export["policySha256"],
                "observation": "obs_spec.yaml",
                "observationSha256": digest(package / "obs_spec.yaml"),
                "versions": job["versions"],
                "control": task["task"]["control"],
                "evaluations": [],
                "status": "candidate",
                "qualified": False,
                "missingEvidence": ["platform_newton_evaluation", "trainer_observation_consistency_256"],
            }
            durable_json(package / "policy.json", provenance)
            value_hash = content_hash(package)
            versions = [value for value in self.list() if value["name"] == task["name"]]
            existing = next((value for value in versions if value["checkpoint"] == checkpoint), None)
            if existing:
                if existing["contentHash"] != value_hash:
                    raise ValueError("Checkpoint already identifies different policy bytes/provenance")
                return existing | {"idempotent": True}
            revision = 1 + max((value["rev"] for value in versions), default=0)
            record = {
                "id": f"policy/{task['name']}@{revision}",
                "name": task["name"],
                "rev": revision,
                "title": task["title"],
                "contentHash": value_hash,
            }
            durable_json(stage / "metadata.json", record)
            shutil.rmtree(task_path)
            destination = destination_parent(root, ("policies", task["name"]), create=True) / str(revision)
            if destination.exists() or destination.is_symlink() or destination.is_junction():
                raise ValueError("Policy revision already exists")
            os.rename(stage, destination)
            return record | provenance | {"idempotent": False}


def runtime_library():
    """Use the same library root selection as all existing immutable resources."""
    from packages.library.assets import AssetLibrary

    return PolicyLibrary(AssetLibrary(Path(os.getenv("SPATIAL_STATE_ROOT", ".scratch/policy-state"))).roots)
