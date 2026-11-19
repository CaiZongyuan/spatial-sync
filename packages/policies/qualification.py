"""Derive observation qualification from authenticated native witnesses and reviewed profiles."""

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import onnx

from apps.node.observation_capture import canonical_hash, code_hash
from packages.info.plans import PlanError
from packages.library.archive import destination_parent, extract_package, package_files, relative_path
from packages.library.common import BUILTIN_ROOT, LibraryError, content_hash, document

from .consistency import compare_observations
from .library import digest, read_json
from .native_evidence import NATIVE_RECEIPTS, NativeProfile, NativeSamples, validate_witnesses
from .observations import ObservationSpec

# The pre-finalization verifier understood the same v1 evidence. Preserve its
# retained decisions when all other implementation and runtime pins still match.
LEGACY_VERIFIER_FILES = {
    "packages/policies/qualification.py": "e2a48fc0c8dabe4d9f4f8c4d182f782004a6da58773ead36d7aabb93eccdec53",
    "packages/policies/native_evidence.py": (
        "91cdc9b874c6f07bd820721207d38098f92880b139b4db9a274f4ba21b7dfece"
    ),
}


class NativeProfiles:
    """Only repository-owned profiles are admitted by default; fixtures inject their own registry."""

    def __init__(self, profiles=None, *, allow_controlled=False):
        if profiles is None:
            directory = BUILTIN_ROOT / "observation-profiles"
            profiles = []
            for file in sorted(directory.glob("*.yaml")):
                if file.is_symlink() or file.is_junction() or file.stat().st_nlink != 1:
                    raise ValueError("Native profile must be a repository-owned regular file")
                profiles.append(document(file))
        self.profiles = {}
        for raw in profiles:
            profile = NativeProfile.model_validate(raw)
            if profile.evidenceMode != "native" and not allow_controlled:
                raise ValueError("Controlled profiles are never production source authority")
            if profile.id in self.profiles:
                raise ValueError("Native source profile identity is ambiguous")
            self.profiles[profile.id] = profile

    def require(self, identifier):
        profile = self.profiles.get(identifier)
        if profile is None:
            raise LibraryError(
                "observation_source_unverified", "No reviewed actual native source profile matches", 409
            )
        return profile

    def for_task(self, task_hash, algorithm_version):
        matches = [
            value
            for value in self.profiles.values()
            if value.taskContentHash == task_hash and value.versions.get("rsl_rl") == algorithm_version
        ]
        if len(matches) > 1:
            raise ValueError("Task has more than one active native source profile")
        return matches[0].model_dump(mode="json") if matches else None


class NativeCaptureVerifier:
    def __init__(self, evidence, profiles):
        self.evidence, self.profiles = evidence, profiles

    def _facts(self, policy, job, report, envelope, receipt, profile, provider):
        self.evidence._validate_source(policy, job, receipt)
        self.evidence._validate_source(policy, job, envelope)
        if job["kind"] == "checkpoint_finalize":
            if getattr(receipt, "finalizedCheckpointSha256", None) != job["checkpointRef"].split("/", 1)[1]:
                raise ValueError("Native receipt refers to a different source checkpoint")
        elif not receipt.afterLearn:
            raise ValueError("Training observation evidence requires a trained export")
        spec = ObservationSpec.model_validate(
            document(self.evidence.manager.policies.resolve(policy["id"])[1] / "obs_spec.yaml")
        )
        package = self.evidence.manager.policies.resolve(policy["id"])[1]
        graph = onnx.load_model(str(package / policy["onnx"]), load_external_data=False)
        if (
            receipt.capture != envelope.capture
            or receipt.capture.profileSha256 != canonical_hash(profile.model_dump(mode="json"))
            or receipt.capture.runtime.model_dump(mode="json") != profile.runtime()
            or profile.taskContentHash != policy["taskContentHash"]
            or profile.inputWidth != spec.size
            or profile.actionWidth != len(spec.action.jointKeys)
            or any(job["versions"]["versions"].get(key) != value for key, value in profile.versions.items())
            or code_hash(provider) != profile.stateProvider.sha256
            or receipt.samplesArtifact != report["artifact"]["path"]
            or receipt.samplesSha256 != report["artifact"]["sha256"]
            or receipt.sampleCount != len(envelope.samples)
            or receipt.normalizerStateSha256 != canonical_hash(receipt.normalizerState)
            or len(graph.graph.input) != 1
            or graph.graph.input[0].name != profile.inputName
        ):
            raise ValueError(
                "Actual native source, export boundary or material differs from the reviewed profile"
            )
        sample_count = validate_witnesses(receipt, envelope)
        result = compare_observations(spec, [sample.model_dump() for sample in envelope.samples])
        return {
            "qualified": result["numericalPass"],
            "scope": "observation_consistency",
            "evidenceMode": profile.evidenceMode,
            "profile": profile.id,
            "profileSha256": canonical_hash(profile.model_dump(mode="json")),
            "samples": sample_count,
            "maxAbsoluteError": result["maxAbsoluteError"],
            "boundary": profile.boundary,
            "normalization": profile.normalization,
            "policyContentHash": policy["contentHash"],
            "check": report["id"],
        }

    def verify_source(self, policy_id, identifier, artifact):
        from .evidence import implementation_identity, invalid

        try:
            report, path = self.evidence._read(policy_id, identifier)
            policy, _ = self.evidence.manager.policies.resolve(policy_id)
            job, source, entry = self.evidence._artifact(policy, artifact)
            raw = source.read_bytes()
            if digest(source) != entry["sha256"]:
                raise ValueError("Native receipt changed during verification")
            receipt = NATIVE_RECEIPTS.validate_json(raw)
            envelope = NativeSamples.model_validate_json((path / "package/samples.json").read_bytes())
            profile = self.profiles.require(receipt.capture.profile)
            import tempfile

            with tempfile.TemporaryDirectory(prefix="native-source-") as temporary:
                task = Path(temporary)
                extract_package(io.BytesIO(self.evidence.manager.tasks.export(policy["task"])), task)
                provider = task / relative_path(profile.stateProviderFile)
                facts = self._facts(policy, job, report, envelope, receipt, profile, provider)
                provider_bytes = provider.read_bytes()
            inputs = {
                "check": identifier,
                "checkContentHash": content_hash(path / "package"),
                "captureArtifact": entry,
                "profileSha256": facts["profileSha256"],
                "verifier": implementation_identity(),
            }
            verification = {
                "id": "verification-" + canonical_hash(inputs),
                "at": datetime.now(UTC).isoformat(),
                **inputs,
                "status": "native_input_verified",
                **facts,
            }
            return self.evidence._publish_verification(
                policy_id,
                path,
                verification,
                {
                    "capture.json": raw,
                    "profile.json": json.dumps(profile.model_dump(mode="json")).encode(),
                    "state-provider.py": provider_bytes,
                },
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise invalid("Native input verification is invalid") from error

    def qualification(self, policy_id, identifier=None):
        from .evidence import implementation_identity

        reports = [self.evidence.show(policy_id, identifier)] if identifier else self.evidence.list(policy_id)
        candidates = [
            (report, verification)
            for report in reports
            for verification in report["verifications"]
            if verification.get("status") == "native_input_verified"
        ]
        candidates.sort(key=lambda value: (value[1]["at"], value[1]["id"]), reverse=True)
        if not candidates:
            raise PlanError(
                "observation_evidence_required", "Actual trusted native observation evidence is required"
            )
        report, verification = candidates[0]
        policy, _ = self.evidence.manager.policies.resolve(policy_id)
        _, path = self.evidence._read(policy_id, report["id"])
        frozen = destination_parent(path, ("verifications", verification["id"], "package"))
        package_files(frozen)
        receipt = NATIVE_RECEIPTS.validate_json((frozen / "capture.json").read_bytes())
        envelope = NativeSamples.model_validate_json((path / "package/samples.json").read_bytes())
        profile = self.profiles.require(receipt.capture.profile)
        current = implementation_identity()
        compatible = [current]
        if receipt.afterLearn:
            compatible.append(current | {"files": current["files"] | LEGACY_VERIFIER_FILES})
        if verification["verifier"] not in compatible or read_json(
            frozen / "profile.json"
        ) != profile.model_dump(mode="json"):
            raise PlanError(
                "observation_evidence_changed", "Native verification implementation/profile changed"
            )
        facts = self._facts(
            policy,
            self.evidence.manager.jobs.get(policy["jobId"]),
            report,
            envelope,
            receipt,
            profile,
            frozen / "state-provider.py",
        )
        if not facts["qualified"]:
            raise PlanError(
                "observation_consistency_failed",
                "Actual native observations exceed the fixed tolerance",
                maxAbsoluteError=facts["maxAbsoluteError"],
                tolerance=1e-4,
            )
        return facts | {"verification": verification["id"], "verificationContentHash": content_hash(frozen)}
