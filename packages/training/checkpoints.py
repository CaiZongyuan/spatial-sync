"""Immutable portable checkpoint storage owned by the gateway."""

import os
import tempfile

from packages.info.plans import PlanError
from packages.library.archive import destination_parent
from packages.training.checkpoint_format import MAX_BYTES, digest, json_bytes, read_checkpoint


def identity(job, lease):
    fixed = {
        key: job[key]
        for key in (
            "planId",
            "planRevision",
            "planApprovalId",
            "task",
            "taskContentHash",
            "scene",
            "sceneContentHash",
            "assets",
            "seed",
            "executionDevice",
        )
    }
    fixed.update({key: job.get(key, []) for key in ("demonstrationSets", "demonstrationRecordings")})
    fixed.update({key: job["plan"][key] for key in ("model", "algorithm", "hyperparameters")})
    return {
        "schemaVersion": 1,
        "jobId": job["jobId"],
        "leaseId": lease["leaseId"],
        "inputsSha256": digest(json_bytes(fixed)),
        "runtime": lease["runtimeIdentity"],
    }


def validate(raw, job, lease):
    try:
        metadata, _, _, _ = read_checkpoint(raw)
        if any(metadata[key] != value for key, value in identity(job, lease).items()):
            raise ValueError("Checkpoint does not identify this leased dispatch and its pinned inputs")
        maximum = job["plan"]["hyperparameters"]["max_iterations"]
        if not lease["startIteration"] < metadata["iteration"] <= maximum:
            raise ValueError("Checkpoint iteration is outside the leased training range")
    except (ValueError, TypeError, OverflowError) as exc:
        raise PlanError("checkpoint_invalid", str(exc), status=422) from exc
    return {
        "jobId": job["jobId"],
        "leaseId": lease["leaseId"],
        "iteration": metadata["iteration"],
        "sha256": digest(raw),
        "size": len(raw),
        "tensorSha256": metadata["tensorSha256"],
        "inputsSha256": metadata["inputsSha256"],
        "runtime": metadata["runtime"],
        "metadataSha256": digest(json_bytes(metadata)),
    }


def persist(manager, descriptor, raw):
    directory = destination_parent(
        manager.library_assets.roots["user"],
        ("jobs", descriptor["jobId"], "checkpoints"),
        create=True,
    )
    destination = directory / (descriptor["sha256"] + ".tar")
    with tempfile.NamedTemporaryFile(dir=directory) as pending:
        pending.write(raw)
        pending.flush()
        os.fsync(pending.fileno())
        try:
            os.link(pending.name, destination)
        except FileExistsError:
            load(manager, descriptor)
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def load(manager, descriptor):
    directory = destination_parent(
        manager.library_assets.roots["user"],
        ("jobs", descriptor["jobId"], "checkpoints"),
    )
    try:
        with (directory / (descriptor["sha256"] + ".tar")).open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError as exc:
        raise PlanError("checkpoint_unavailable", "Checkpoint bytes are unavailable") from exc
    if len(raw) != descriptor["size"] or digest(raw) != descriptor["sha256"]:
        raise PlanError("checkpoint_unavailable", "Committed checkpoint bytes changed")
    return raw
