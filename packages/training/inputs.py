"""Immutable package transfer for the isolated compute node."""

import hashlib
import io
import json
import os
import re
import tarfile
import tempfile
from pathlib import Path

from packages.info.plans import PlanError
from packages.library.archive import destination_parent, extract_package, package_files
from packages.library.common import content_hash
from packages.training.checkpoint_format import MAX_METADATA, decode_json, digest, json_bytes
from packages.training.runtime import job_backend, trainer_distributions


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
    for item in {item["id"]: item for item in job.get("demonstrationSets", [])}.values():
        manager.demonstrations.set_package(item["id"], item["contentHash"])
        values.append({"kind": "demoset", "id": item["id"], "contentHash": item["contentHash"]})
    values.extend({"kind": "recording", **item} for item in job.get("demonstrationRecordings", []))
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
    if selected["kind"] == "demoset":
        return pack_package(manager.demonstrations.set_package(selected["id"], selected["contentHash"]))
    if selected["kind"] == "recording":
        return pack_package(manager.demonstrations.recording(selected["id"], selected["contentHash"]))
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


def _bounded_checkpoint_json(file):
    with file.open("rb") as source:
        return decode_json(source.read(MAX_METADATA + 1))


def _validate_portable_export(job, descriptor, stage, export, checkpoint, policy, versions):
    if (
        descriptor is None or checkpoint != "checkpoint.safetensors"
        or export.get("schemaVersion") != 2
        or export.get("checkpointArchiveSha256") != descriptor["sha256"]
        or export["iteration"] != descriptor["iteration"]
    ):
        raise ValueError("Export does not identify a committed portable checkpoint")
    metadata = _bounded_checkpoint_json(stage / "checkpoint.json")
    if (
        digest(json_bytes(metadata)) != descriptor["metadataSha256"]
        or export["checkpointSha256"] != descriptor["tensorSha256"]
    ):
        raise ValueError("Exported checkpoint differs from the committed bytes")
    if job["kind"] != "checkpoint_finalize":
        if descriptor["iteration"] != job["plan"]["hyperparameters"]["max_iterations"]:
            raise ValueError("Training export requires the approved final iteration")
        return
    from packages.policies.native_evidence import NATIVE_RECEIPTS, NativeSamples, validate_witnesses
    from packages.training.views import attempt_identity

    if export.get("operation") != "checkpoint_finalize":
        raise ValueError("Checkpoint finalization requires an explicit export operation")
    receipt = NATIVE_RECEIPTS.validate_json((stage / "observations/native-capture.json").read_bytes())
    samples_file = stage / "observations/native-samples.json"
    samples = NativeSamples.model_validate_json(samples_file.read_bytes())
    validate_witnesses(receipt, samples)
    if (
        getattr(receipt, "finalizedCheckpointSha256", None) != descriptor["sha256"]
        or receipt.samplesArtifact != "observations/native-samples.json"
        or receipt.samplesSha256 != _file_hash(samples_file)
        or receipt.capture != samples.capture
        or receipt.iteration != descriptor["iteration"]
        or receipt.source != samples.source or receipt.source.versions != versions
        or any(getattr(receipt.source, key) != job[key] for key in (
            "jobId", "task", "taskContentHash", "scene", "sceneContentHash", "seed",
        ))
        or receipt.source.attemptId != attempt_identity(job)
        or receipt.policy != samples.policy
        or receipt.policy.onnxSha256 != export["policySha256"]
        or receipt.policy.checkpointSha256 != export["checkpointSha256"]
    ):
        raise ValueError("Checkpoint finalization requires 256 distinct native witnesses for this export")
    import onnx

    graph = onnx.load_model(str(stage / policy), load_external_data=False).graph
    if not graph.input or not graph.output or any(
        dim.WhichOneof("value") != "dim_value" or dim.dim_value <= 0
        for value in (*graph.input, *graph.output)
        for dim in value.type.tensor_type.shape.dim
    ):
        raise ValueError("Checkpoint finalization requires a static ONNX signature")


def publish_attempt_artifacts(manager, job):
    """Promote a verified attempt directory before committing a succeeded Job."""
    root = manager.library_assets.roots["user"]
    directory = destination_parent(root, ("jobs", job["jobId"]))
    destination = directory / "artifacts"
    stage = directory / ("artifacts-" + job["artifactLeaseId"])
    if stage.is_dir():
        # A prior attempt may have renamed its output before a crash interrupted
        # the final Job save. Keep those bytes outside the successful path.
        if destination.exists():
            retired = Path(tempfile.mkdtemp(prefix="retired-artifacts-", dir=directory))
            os.rename(destination, retired / "artifacts")
        os.rename(stage, destination)
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    elif not destination.is_dir():
        raise PlanError("artifacts_incomplete", "Verified attempt artifacts are missing", status=422)


def store_artifacts(manager, job, stream, verify_lease=None, trainer_versions=None, *,
                    portable=False, descriptor=None):
    root = manager.library_assets.roots["user"]
    with manager.library_assets._lock, tempfile.TemporaryDirectory(prefix=".upload-", dir=root) as temporary:
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
                or versions.get("demonstrationSets", []) != job.get("demonstrationSets", [])
            ):
                raise ValueError("Artifact versions do not identify the approved inputs/device")
            task_doc = manager.tasks.show(job["task"])["task"] if job.get("backend") is None else None
            required_versions = trainer_distributions(job_backend(job, task_doc) == "mjlab")
            if any(
                not isinstance(versions["versions"].get(name), str) or not versions["versions"][name]
                for name in required_versions
            ):
                raise ValueError("Required runtime versions are missing")
            expected_versions = trainer_versions or {}
            if any(versions["versions"].get(name) != version for name, version in expected_versions.items()):
                raise ValueError("Artifact trainer versions differ from the leased Compute Node capabilities")
            if job["executionDevice"] == "cpu" and versions["hardware"].get("gpu") is not None:
                raise ValueError("CPU execution cannot claim a GPU model")
            if job["executionDevice"] == "cuda" and versions["hardware"].get("gpu") != {
                "uuid": job["resourceId"], "name": job["resourceModel"]
            }:
                raise ValueError("GPU UUID/model must match the assigned resource")
            if not {"stdout.log", "stderr.log", "versions.json"}.issubset(files):
                raise ValueError("Runtime versions and both process logs are required")
            if not portable and not any(
                "events.out.tfevents." in name and (stage / name).stat().st_size > 0 for name in files
            ):
                raise ValueError("Missing TensorBoard events")
            export = json.loads((stage / "export.json").read_text(encoding="utf-8"))
            from packages.library.archive import relative_path

            checkpoint = relative_path(export["checkpoint"]).as_posix()
            policy = relative_path(export["policy"]).as_posix()
            if any(name.lower().endswith((".pt", ".pth", ".pkl", ".pickle")) for name in files):
                raise ValueError("Serialized Python checkpoint files must stay at the node")
            expected_checkpoint = "checkpoint.safetensors" if portable else "checkpoint-origin.json"
            if checkpoint != expected_checkpoint or checkpoint not in files:
                raise ValueError("Export requires its portable checkpoint or a JSON source reference")
            if not portable:
                origin = _bounded_checkpoint_json(stage / checkpoint)
                original_digest = origin["checkpointSha256"]
                if origin != {
                    "schema": "lw.node-local-checkpoint/1", "jobId": job["jobId"],
                    "leaseId": job["leaseId"], "iteration": export["iteration"],
                    "checkpointSha256": original_digest,
                } or not re.fullmatch(r"[a-f0-9]{64}", original_digest):
                    raise ValueError("Export does not identify a local checkpoint")
            if (
                export["afterLearn"] is not (job["kind"] != "checkpoint_finalize")
                or type(export["iteration"]) is not int
                or export["iteration"] < 0
                or policy not in files
                or not policy.endswith(".onnx")
                or (stage / policy).stat().st_size == 0
                or _file_hash(stage / checkpoint) != export["checkpointSha256"]
                or _file_hash(stage / policy) != export["policySha256"]
            ):
                raise ValueError("ONNX export does not identify its exact trained checkpoint")
            if portable:
                _validate_portable_export(job, descriptor, stage, export, checkpoint, policy, versions)
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
        parts = ("jobs", job["jobId"])
        directory = destination_parent(root, parts)
        destination = directory / (f"artifacts-{job['leaseId']}" if portable else "artifacts")
        if portable and job["status"] == "succeeded":
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
        with manager._resource_lock:
            if verify_lease is not None:
                verify_lease()
            if not destination.exists():
                destination_parent(root, parts, create=True)
                os.rename(stage, destination)
        return records, versions
