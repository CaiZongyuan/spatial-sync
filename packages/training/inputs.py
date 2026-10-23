"""Immutable package transfer for the isolated compute node."""

import hashlib
import io
import json
import os
import tarfile
import tempfile
from pathlib import Path

from packages.info.plans import PlanError
from packages.library.archive import destination_parent, extract_package, package_files
from packages.library.common import content_hash


def pack_package(path):
    """Freeze regular package files; transfer identity is independent of tar timestamps."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(package_files(path)):
            file = path / name
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = file.stat().st_size, 0o644
            with file.open("rb") as stream:
                archive.addfile(entry, stream)
    return output.getvalue()


def input_packages(manager, job):
    task = manager.tasks.show(job["task"])
    scene = manager.library.resolve(job["scene"])
    if task["contentHash"] != job["taskContentHash"] or content_hash(scene.path) != job["sceneContentHash"]:
        raise PlanError("job_inputs_changed", "Pinned Task or scene content changed")
    values = [
        {"kind": "task", "id": task["id"], "contentHash": task["contentHash"]},
        {
            "kind": "scene",
            "id": scene.id,
            "contentHash": scene.content_hash,
            "stage": scene.metadata["stage"],
        },
    ]
    values.extend({"kind": "asset", **item} for item in job["assets"])
    for index, value in enumerate(values):
        value["url"] = f"/api/v1/jobs/{job['jobId']}/inputs/packages/{index}"
    return values


def export_input(manager, job, index):
    values = input_packages(manager, job)
    if not 0 <= index < len(values):
        raise PlanError("unknown_job_input", "Unknown input package", status=404)
    selected = values[index]
    if selected["kind"] == "task":
        return manager.tasks.export(selected["id"])
    package = (
        manager.library.resolve(selected["id"])
        if selected["kind"] == "scene"
        else manager.library_assets.resolve(selected["id"])
    )
    if content_hash(package.path) != selected["contentHash"]:
        raise PlanError("job_inputs_changed", "Pinned input package content changed")
    return pack_package(package.path)


def _file_hash(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def store_artifacts(manager, job, stream):
    with manager.library_assets._lock:
        return _store_artifacts(manager, job, stream)


def _store_artifacts(manager, job, stream):
    root = manager.library_assets.roots["user"]
    directory = destination_parent(root, ("jobs", job["jobId"]), create=True)
    with tempfile.TemporaryDirectory(prefix=".upload-", dir=directory) as temporary:
        stage = Path(temporary)
        extract_package(stream, stage)
        files = package_files(stage)
        try:
            versions = json.loads((stage / "versions.json").read_text(encoding="utf-8"))
            if (
                type(versions["seed"]) is not int
                or versions["seed"] != job["seed"]
                or any(
                    versions[key] != job[key]
                    for key in ("jobId", "task", "taskContentHash", "scene", "sceneContentHash")
                )
                or versions["hardware"]["device"] != job["executionDevice"]
            ):
                raise ValueError("Artifact versions do not identify the approved inputs/device")
            if any(
                not isinstance(versions["versions"][name], str) or not versions["versions"][name]
                for name in ("isaaclab", "newton", "warp", "torch", "rsl_rl")
            ):
                raise ValueError("Required runtime versions are missing")
            if job["executionDevice"] == "cpu" and versions["hardware"].get("gpu") is not None:
                raise ValueError("CPU execution cannot claim a GPU model")
            if job["executionDevice"] == "cuda" and versions["hardware"].get("gpu") != {
                "uuid": job["resourceId"], "name": job["resourceModel"]
            }:
                raise ValueError("GPU UUID/model must match the assigned resource")
            if not {"stdout.log", "stderr.log", "versions.json"}.issubset(files):
                raise ValueError("Runtime versions and both process logs are required")
            for predicate, label in (
                (lambda name: name.endswith(".pt"), "checkpoint"),
                (lambda name: name.endswith(".onnx"), "ONNX policy"),
                (lambda name: "events.out.tfevents." in name, "TensorBoard events"),
            ):
                if not any(predicate(name) and (stage / name).stat().st_size > 0 for name in files):
                    raise ValueError(f"Missing {label}")
            export = json.loads((stage / "export.json").read_text(encoding="utf-8"))
            from packages.library.archive import relative_path

            checkpoint = relative_path(export["checkpoint"]).as_posix()
            policy = relative_path(export["policy"]).as_posix()
            if (
                export["afterLearn"] is not True
                or type(export["iteration"]) is not int
                or export["iteration"] < 0
                or checkpoint not in files
                or policy not in files
                or not checkpoint.endswith(".pt")
                or not policy.endswith(".onnx")
                or _file_hash(stage / checkpoint) != export["checkpointSha256"]
                or _file_hash(stage / policy) != export["policySha256"]
            ):
                raise ValueError("ONNX export does not identify its exact trained checkpoint")
            external = export["externalData"]
            if not isinstance(external, list) or len({entry["path"] for entry in external}) != len(external):
                raise ValueError("ONNX external data manifest must contain distinct files")
            for entry in external:
                name = relative_path(entry["path"]).as_posix()
                if (
                    name not in files
                    or type(entry["size"]) is not int
                    or entry["size"] < 0
                    or (stage / name).stat().st_size != entry["size"]
                    or _file_hash(stage / name) != entry["sha256"]
                ):
                    raise ValueError("ONNX external data is missing or changed")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PlanError("artifacts_incomplete", str(exc), status=422) from exc
        records = [
            {"path": name, "size": (stage / name).stat().st_size, "sha256": _file_hash(stage / name)}
            for name in sorted(files)
        ]
        destination = directory / "artifacts"
        if destination.exists():
            existing = [
                {
                    "path": name,
                    "size": (destination / name).stat().st_size,
                    "sha256": _file_hash(destination / name),
                }
                for name in sorted(package_files(destination))
            ]
            if existing != records:
                raise PlanError("artifact_conflict", "This dispatch already has different artifacts")
            return existing, versions
        os.rename(stage, destination)
        return records, versions
