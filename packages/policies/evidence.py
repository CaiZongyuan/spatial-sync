"""Immutable observation checks over exact, authenticated source-Job artifacts."""

import importlib.metadata
import os
import platform
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field

from apps.node.observation_capture import canonical_hash, code_hash
from packages.library.archive import (
    destination_parent,
    extract_package,
    import_lock,
    package_files,
    relative_path,
)
from packages.library.common import REPO_ROOT, LibraryError, content_hash, document
from packages.library.transactions import durable_json
from packages.training.views import attempt_identity

from .consistency import compare_observations
from .library import REFERENCE, digest, read_json
from .observations import ObservationSpec, vector
from .subjects import Hash, Schema, Text


class Source(Schema):
    jobId: Text
    task: Text
    taskContentHash: Hash
    scene: Text
    sceneContentHash: Hash
    seed: int = Field(ge=0)
    attemptId: Text
    versions: dict


class Weights(Schema):
    checkpointSha256: Hash
    onnxSha256: Hash
    observationSha256: Hash


class Capture(Schema):
    producer: Literal["task-env-observations/1"]
    method: Text
    hookModule: Text
    hookQualname: Text
    taskSourceFile: Text
    taskSourceSha256: Hash
    implementationSha256: Hash
    runnerModule: Text
    runnerQualname: Text
    hookStatus: Literal["returned"]
    requestedSamples: Literal[256]
    capturedAt: AwareDatetime


class Sample(Schema):
    sourceStateId: Text
    state: dict
    trainingObservation: list[float]
    previousAction: list[float]
    references: dict[str, str] = Field(default_factory=dict)


class Samples(Schema):
    schema_version: Literal["lw.policy-observation-samples/1"] = Field(alias="schema")
    source: Source
    policy: Weights
    capture: Capture
    samples: list[Sample] = Field(min_length=1)


class CaptureReceipt(Schema):
    schema_version: Literal["lw.policy-observation-capture/1"] = Field(alias="schema")
    source: Source
    policy: Weights
    capture: Capture
    samplesArtifact: Text
    samplesSha256: Hash
    sampleCount: int = Field(ge=256)
    sourceStateIdsSha256: Hash


def implementation_identity():
    return {
        "scheme": "lw.observation-comparison/1",
        "files": {
            name: code_hash(REPO_ROOT / name)
            for name in (
                "packages/policies/observations.py",
                "packages/policies/consistency.py",
                "packages/policies/evidence.py",
                "apps/node/observation_capture.py",
                "packages/protocol/math.py",
                "uv.lock",
            )
        },
        "runtime": {
            "python": platform.python_version(),
            "numpy": importlib.metadata.version("numpy"),
            "pydantic": importlib.metadata.version("pydantic"),
        },
    }


def invalid(message):
    return LibraryError("observation_evidence_invalid", message, 422)


class ObservationEvidence:
    def __init__(self, manager):
        self.manager = manager
        self.assets = manager.library_assets
        self.root = self.assets.roots["user"]

    def _parent(self, policy_id, *, create=False):
        match = REFERENCE.fullmatch(policy_id)
        if match is None:
            raise LibraryError("unknown_policy", "Select an exact policy/name@revision", 404)
        return destination_parent(self.root, ("policy-evidence", match[1], match[2]), create=create)

    def _artifact(self, policy, name):
        name = relative_path(name).as_posix()
        job = self.manager.jobs.get(policy["jobId"])
        if job["status"] != "succeeded" or job["taskContentHash"] != policy["taskContentHash"]:
            raise invalid("Observation evidence requires the exact succeeded source Job")
        entries = [entry for entry in job["artifacts"] if entry["path"] == name]
        if len(entries) != 1:
            raise invalid("Select an existing source-Job artifact")
        directory = destination_parent(self.root, ("jobs", job["jobId"], "artifacts"))
        if name not in package_files(directory):
            raise invalid("Source artifact is missing")
        path, entry = directory / name, entries[0]
        if path.stat().st_size != entry["size"] or digest(path) != entry["sha256"]:
            raise invalid("Source artifact changed after authenticated upload")
        return job, path, entry

    @staticmethod
    def _validate_source(policy, job, envelope):
        expected = {
            name: job[name]
            for name in (
                "jobId",
                "task",
                "taskContentHash",
                "scene",
                "sceneContentHash",
                "seed",
            )
        }
        expected.update(attemptId=attempt_identity(job), versions=job["versions"])
        weights = {
            name: policy[name]
            for name in (
                "checkpointSha256",
                "onnxSha256",
                "observationSha256",
            )
        }
        if envelope.source.model_dump(mode="json") != expected or envelope.policy.model_dump() != weights:
            raise invalid("Samples identify a different dispatch, Task, Scene, model or observation")

    def check(self, policy_id, artifact):
        try:
            policy, package = self.manager.policies.resolve(policy_id)
            job, source, entry = self._artifact(policy, artifact)
            envelope = Samples.model_validate_json(source.read_bytes())
            self._validate_source(policy, job, envelope)
            identifiers = [sample.sourceStateId for sample in envelope.samples]
            if len(set(identifiers)) != len(identifiers):
                raise invalid("Sample state identities must be distinct")
            spec = ObservationSpec.model_validate(document(package / "obs_spec.yaml"))
            for sample in envelope.samples:
                vector(sample.previousAction, len(spec.action.jointKeys))
            result = compare_observations(spec, [sample.model_dump() for sample in envelope.samples])
            result["provenance"] = "authenticated source-Job artifact"
            result["independentTrainingSemantics"] = "not_verified_by_numerical_comparison"
            implementation = implementation_identity()
            inputs = {
                "policy": policy_id,
                "policyContentHash": policy["contentHash"],
                "artifact": entry,
                "implementation": implementation,
            }
            identifier = "check-" + canonical_hash(inputs)
            report = {
                "schema": "lw.policy-observation-check/1",
                "id": identifier,
                **inputs,
                "at": datetime.now(UTC).isoformat(),
                "result": result,
                "declaredSource": envelope.source.model_dump(mode="json"),
                "declaredCapture": envelope.capture.model_dump(mode="json"),
                "sourceVerification": {"status": "unverified"},
                "qualified": False,
            }
            with self.assets._lock, import_lock(self.root):
                parent = self._parent(policy_id, create=True)
                destination = parent / identifier
                if destination.exists():
                    return self.show(policy_id, identifier) | {"idempotent": True}
                with tempfile.TemporaryDirectory(prefix=".check-", dir=parent) as temporary:
                    stage = Path(temporary)
                    frozen = stage / "package"
                    frozen.mkdir()
                    shutil.copyfile(source, frozen / "samples.json")
                    if digest(frozen / "samples.json") != entry["sha256"]:
                        raise invalid("Source samples changed during freezing")
                    durable_json(frozen / "report.json", report)
                    durable_json(
                        stage / "metadata.json",
                        {"id": identifier, "policy": policy_id, "contentHash": content_hash(frozen)},
                    )
                    os.rename(stage, destination)
                return self.show(policy_id, identifier) | {"idempotent": False}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise invalid("Observation sample artifact is invalid") from exc

    def _read(self, policy_id, identifier):
        if not identifier.startswith("check-") or len(identifier) != 70:
            raise LibraryError("unknown_observation_check", "Unknown observation check", 404)
        if any(character not in "0123456789abcdef" for character in identifier[6:]):
            raise LibraryError("unknown_observation_check", "Unknown observation check", 404)
        parent = self._parent(policy_id)
        path = destination_parent(parent, (identifier,))
        try:
            metadata = read_json(path / "metadata.json")
            package = destination_parent(path, ("package",))
            package_files(package)
            if (
                metadata["id"] != identifier
                or metadata["policy"] != policy_id
                or content_hash(package) != metadata["contentHash"]
            ):
                raise ValueError("Frozen observation report changed")
            report = read_json(package / "report.json")
            if report["id"] != identifier or report["policy"] != policy_id:
                raise ValueError("Observation report belongs to another policy")
            return report, path
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise LibraryError(
                "observation_check_unavailable", "Frozen observation evidence changed", 409
            ) from exc

    def show(self, policy_id, identifier):
        report, path = self._read(policy_id, identifier)
        checks = []
        verification_parent = destination_parent(path, ("verifications",))
        if verification_parent.exists():
            for entry in verification_parent.iterdir():
                if not entry.name.startswith("verification-"):
                    continue  # Publication staging directories are not indexed evidence.
                directory = destination_parent(verification_parent, (entry.name,))
                metadata = read_json(directory / "metadata.json")
                package = destination_parent(verification_parent, (entry.name, "package"))
                package_files(package)
                if content_hash(package) != metadata["contentHash"]:
                    raise LibraryError("observation_check_unavailable", "Capture verification changed", 409)
                checks.append(read_json(package / "verification.json"))
        checks.sort(key=lambda value: (value["at"], value["id"]))
        return report | {
            "verifications": checks,
            "sourceVerification": checks[-1] if checks else report["sourceVerification"],
            "currentImplementation": report["implementation"] == implementation_identity(),
        }

    def list(self, policy_id):
        parent = self._parent(policy_id)
        if not parent.exists():
            return []
        return [
            self.show(policy_id, path.name)
            for path in sorted(parent.iterdir())
            if path.name.startswith("check-")
        ]

    def verify_source(self, policy_id, identifier, artifact):
        import io

        try:
            report, check_path = self._read(policy_id, identifier)
            policy, _ = self.manager.policies.resolve(policy_id)
            job, source, entry = self._artifact(policy, artifact)
            receipt = CaptureReceipt.model_validate_json(source.read_bytes())
            self._validate_source(policy, job, receipt)
            envelope = Samples.model_validate_json((check_path / "package/samples.json").read_bytes())
            identities = [sample.sourceStateId for sample in envelope.samples]
            if (
                receipt.samplesArtifact != report["artifact"]["path"]
                or receipt.samplesSha256 != report["artifact"]["sha256"]
                or receipt.sampleCount != len(identities)
                or receipt.sourceStateIdsSha256 != canonical_hash(identities)
                or receipt.capture != envelope.capture
                or receipt.capture.implementationSha256
                != code_hash(REPO_ROOT / "apps/node/observation_capture.py")
            ):
                raise invalid("Capture receipt differs from the frozen observation input")
            with tempfile.TemporaryDirectory(prefix="policy-hook-") as temporary:
                task = Path(temporary)
                extract_package(io.BytesIO(self.manager.tasks.export(policy["task"])), task)
                hook = task / relative_path(receipt.capture.taskSourceFile)
                if not hook.is_file() or code_hash(hook) != receipt.capture.taskSourceSha256:
                    raise invalid("Actual hook source does not match the frozen Task")
                hook_bytes = hook.read_bytes()
            inputs = {
                "check": identifier,
                "checkContentHash": content_hash(check_path / "package"),
                "captureArtifact": entry,
                "verifier": implementation_identity(),
            }
            verification_id = "verification-" + canonical_hash(inputs)
            verification = {
                "id": verification_id,
                "at": datetime.now(UTC).isoformat(),
                **inputs,
                "status": "capture_contract_verified",
                "trainerProcessExitCode": 0,
                "hookStatus": "returned",
                "taskModule": receipt.capture.hookModule,
                "taskSourceSha256": receipt.capture.taskSourceSha256,
                "nativeRunnerModule": receipt.capture.runnerModule,
                "versions": receipt.source.versions,
                "realTrainingAndIndependentSemantics": "not_asserted_by_source_verification",
                "qualified": False,
            }
            with self.assets._lock, import_lock(self.root):
                parent = destination_parent(check_path, ("verifications",), create=True)
                destination = parent / verification_id
                if destination.exists():
                    return self.show(policy_id, identifier) | {"idempotent": True}
                with tempfile.TemporaryDirectory(prefix=".verification-", dir=parent) as temporary:
                    stage = Path(temporary)
                    package = stage / "package"
                    package.mkdir()
                    shutil.copyfile(source, package / "capture.json")
                    if digest(package / "capture.json") != entry["sha256"]:
                        raise invalid("Capture source changed during freezing")
                    (package / "hook-source.py").write_bytes(hook_bytes)
                    durable_json(package / "verification.json", verification)
                    durable_json(stage / "metadata.json", {"contentHash": content_hash(package)})
                    os.rename(stage, destination)
                return self.show(policy_id, identifier) | {"idempotent": False}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise invalid("Native capture verification is invalid") from exc
