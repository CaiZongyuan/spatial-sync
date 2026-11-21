"""Derive observation qualification from authenticated native witnesses and reviewed profiles."""

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import onnx

from apps.node.mjlab_launcher import compiler_hash, compiler_sources
from apps.node.observation_capture import canonical_hash, code_hash
from packages.info.plans import PlanError
from packages.info.schema_subset import json_equal
from packages.library.archive import destination_parent, extract_package, package_files, relative_path
from packages.library.common import BUILTIN_ROOT, LibraryError, content_hash, document
from packages.training.reference_initialization import MAX_STATISTIC, NORMALIZATION_EPSILON
from packages.training.reference_initialization import receipt as initialization_receipt
from packages.training.runtime import (
    INITIALIZATION_R1_COMPILER,
    INITIALIZATION_R2_COMPILER,
    PINS,
    PRE_INITIALIZATION_COMPILER,
)

from .consistency import compare_observations
from .library import digest, read_json
from .native_evidence import (
    NATIVE_RECEIPTS,
    NativeProfile,
    NativeSamples,
    validate_native_schema,
    validate_witnesses,
)
from .observations import ObservationSpec

PRE_INITIALIZATION_VERIFIER = "51f6c3eded5669dcad50604359cfb6bfb58c406e41e876a65e6382eebd27046a"
INITIALIZATION_R1_VERIFIER = "2cd813677830ff3ed0abc451dafe3e5875559df6220a376c30786971547ba8e1"
INITIALIZATION_R2_VERIFIER = "a125d3b416cd51ba528874383d0d9cb382ac7ed0d3249d92c920ac6e6af4b061"

# The pre-finalization verifier understood the same v1 evidence. Preserve its
# retained decisions when all other implementation and runtime pins still match.
LEGACY_VERIFIER_FILES = {
    "packages/policies/observations.py": "22b2d3b128d7990715eafb326c14986c3968dff043b5dc00dd0c24ce46318366",
    "packages/policies/consistency.py": "bd3e6ff4d38cbaa72441769eb74346189c66119eab252664a665fdf76adea5c1",
    "packages/policies/evidence.py": "ffac862ffbe5a11c286841ceef5f8722424157ea19ba79901cf5bbb5e7ca4117",
    "packages/policies/qualification.py": "e2a48fc0c8dabe4d9f4f8c4d182f782004a6da58773ead36d7aabb93eccdec53",
    "packages/policies/native_evidence.py": (
        "91cdc9b874c6f07bd820721207d38098f92880b139b4db9a274f4ba21b7dfece"
    ),
}


def compatible_legacy_verifier(current):
    files = {
        name: sha for name, sha in current["files"].items()
        if name not in (
            "packages/policies/motion.py",
            "packages/policies/action_contract.py",
            "apps/node/declarative_mjlab.py",
            "apps/node/declarative_capture.py",
        )
    }
    return current | {"files": files | LEGACY_VERIFIER_FILES}


def _normalizer_row(state, field, width, lower):
    value = state.get(field)
    if (not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], list)
            or len(value[0]) != width
            or any(type(number) not in (int, float) or not lower <= number <= MAX_STATISTIC
                   for number in value[0])):
        raise ValueError("Native normalizer state differs from the approved frozen statistics")
    return value


def verify_initialized_normalizer(plan, versions, state):
    init = plan.get("initialization")
    if init is None:
        return
    if not json_equal(versions.get("initialization"), initialization_receipt(plan)):
        raise ValueError("Native source lacks the approved managed initialization receipt")
    settings = init["normalization"]
    mean = np.array([settings["mean"]], dtype=np.float32)
    variance = np.array([settings["variance"]], dtype=np.float32)
    width = mean.shape[1]
    std = np.array(_normalizer_row(state, "_std", width, 0), dtype=np.float32)
    expected_std = np.sqrt(variance)
    if (
        type(state.get("count")) is not int or state["count"] != settings["count"]
        or _normalizer_row(state, "_mean", width, -MAX_STATISTIC) != mean.tolist()
        or _normalizer_row(state, "_var", width, 0) != variance.tolist()
        or not np.all(np.abs(std - expected_std) <= np.spacing(expected_std))
    ):
        raise ValueError("Native normalizer state differs from the approved frozen statistics")
    return mean, std + np.float32(NORMALIZATION_EPSILON)


def _verify_exported_normalizer(model, normalization, widths):
    """Match the pinned RSL exporter from raw input through the action output."""
    graph = model.graph
    initializers = {tensor.name: tensor for tensor in graph.initializer}
    error = "ONNX action path differs from the reviewed normalized actor"
    if ([(op.domain, op.version) for op in model.opset_import] != [("", 18)]
            or len(graph.input) != 1 or len(graph.output) != 1 or len(graph.node) != 7
            or len(initializers) != len(graph.initializer) or graph.sparse_initializer):
        raise ValueError(error)
    attr = onnx.AttributeProto
    affine_defaults = {"alpha": (attr.FLOAT, 1.0), "beta": (attr.FLOAT, 1.0),
                       "transA": (attr.INT, 0), "transB": (attr.INT, 0)}
    gemm = affine_defaults | {"transB": (attr.INT, 1)}
    elu = {"alpha": (attr.FLOAT, 1.0)}
    defaults = {"Gemm": affine_defaults, "Elu": elu}
    inputs, first, second, output = widths
    schema = (
        ("Sub", ((1, inputs),), {}),
        ("Div", ((1, inputs),), {}),
        ("Gemm", ((first, inputs), (first,)), gemm),
        ("Elu", (), elu),
        ("Gemm", ((second, first), (second,)), gemm),
        ("Elu", (), elu),
        ("Gemm", ((output, second), (output,)), gemm),
    )
    value = graph.input[0].name
    for node, (operator, shapes, attributes) in zip(graph.node, schema, strict=True):
        if (node.domain or node.overload or node.op_type != operator or len(node.input) != 1 + len(shapes)
                or node.input[0] != value or len(node.output) != 1 or len(node.attribute) > len(attributes)):
            raise ValueError(error)
        actual = {item.name: (item.type, onnx.helper.get_attribute_value(item)) for item in node.attribute}
        if len(actual) != len(node.attribute) or defaults.get(operator, {}) | actual != attributes:
            raise ValueError(error)
        for name, shape in zip(node.input[1:], shapes, strict=True):
            tensor = initializers.get(name)
            if tensor is None or tensor.data_type != onnx.TensorProto.FLOAT or tuple(tensor.dims) != shape:
                raise ValueError(error)
            if (tensor.data_location == onnx.TensorProto.DEFAULT
                    and not np.isfinite(onnx.numpy_helper.to_array(tensor)).all()):
                raise ValueError(error)
        value = node.output[0]
    if value != graph.output[0].name:
        raise ValueError(error)
    for node, expected in zip(graph.node[:2], normalization, strict=True):
        tensor = initializers[node.input[1]]
        if (tensor.data_location != onnx.TensorProto.DEFAULT or tensor.external_data
                or not np.array_equal(onnx.numpy_helper.to_array(tensor), expected)):
            raise ValueError(error)


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


def declarative_profile(task, spec, job, runtime):
    """Use the managed compiler and reviewed trainer, with layout from pinned Task data."""
    allowed = {compiler_hash(), INITIALIZATION_R2_COMPILER}
    init = job["plan"].get("initialization")
    if not init:
        allowed.add(PRE_INITIALIZATION_COMPILER)
    if not init or "fitActionAbsMax" not in init:
        allowed.add(INITIALIZATION_R1_COMPILER)
    if (not runtime or runtime.get("entry") != "mjlab:managed"
            or runtime.get("compilerHash") not in allowed):
        raise LibraryError("observation_source_unverified", "Unreviewed declarative compiler identity", 409)
    versions = runtime["trainerVersions"]
    expected = PINS["mjlab"] | {"rsl_rl": "5.0.1"}
    if any(versions.get(key, "").split("+", 1)[0] != value for key, value in expected.items()):
        raise LibraryError("observation_source_unverified", "Unreviewed declarative trainer versions", 409)
    # The existing native boundary is the same reviewed mjlab/RSL implementation.
    driver = document(BUILTIN_ROOT / "compiler-native-runtime.yaml")
    driver.update(
        id="platform-declarative-mjlab",
        compilerHash=runtime["compilerHash"],
        taskContentHash=task["contentHash"],
        versions=versions,
        inputWidth=spec.size,
        actionWidth=len(spec.action.jointKeys),
        environments=list(range(min(4, job["plan"]["hyperparameters"]["num_envs"]))),
        stateProviderFile="declarative_mjlab.py",
        stateProvider={
            "module": "lw_declarative_mjlab",
            "qualname": "DeclarativeEnvironment.lw_policy_observation_state",
            "sha256": code_hash(compiler_sources()["declarative_mjlab.py"]),
        },
    )
    return NativeProfile.model_validate(driver)


class NativeCaptureVerifier:
    def __init__(self, evidence, profiles):
        self.evidence, self.profiles = evidence, profiles

    def _profile(self, policy, job, receipt):
        task = self.evidence.manager.tasks.show(policy["task"])
        if task["task"].get("declarative") is None:
            return self.profiles.require(receipt.capture.profile)
        profile = declarative_profile(
            task, self.evidence.manager.tasks.observation(task["id"]), job,
            job["versions"].get("runtimeIdentity"),
        )
        if receipt.capture.profile != profile.id:
            raise ValueError("Declarative receipt does not identify the managed compiler")
        return profile

    def _facts(self, policy, job, report, envelope, receipt, profile, provider):
        self.evidence._validate_source(policy, job, receipt)
        self.evidence._validate_source(policy, job, envelope)
        normalization = verify_initialized_normalizer(
            job["plan"], receipt.source.versions, receipt.normalizerState,
        )
        if job["kind"] == "checkpoint_finalize":
            if getattr(receipt, "finalizedCheckpointSha256", None) != job["checkpointRef"].split("/", 1)[1]:
                raise ValueError("Native receipt refers to a different source checkpoint")
        elif not receipt.afterLearn:
            raise ValueError("Training observation evidence requires a trained export")
        spec = ObservationSpec.model_validate(
            document(self.evidence.manager.policies.resolve(policy["id"])[1] / "obs_spec.yaml")
        )
        package = self.evidence.manager.policies.resolve(policy["id"])[1]
        model = onnx.load_model(str(package / policy["onnx"]), load_external_data=False)
        graph = model.graph
        export = read_json(package / "artifacts/export.json")
        if (
            (profile.compilerHash is not None and receipt.iteration != export["iteration"])
            or receipt.capture != envelope.capture
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
            or len(graph.input) != 1
            or graph.input[0].name != profile.inputName
        ):
            raise ValueError(
                "Actual native source, export boundary or material differs from the reviewed profile"
            )
        if normalization is not None:
            widths = (spec.size, *job["plan"]["model"]["actor"], len(spec.action.jointKeys))
            _verify_exported_normalizer(model, normalization, widths)
        task = read_json(package / "task.json")
        sample_count = validate_witnesses(
            receipt, envelope, task=task if profile.compilerHash else None
        )
        validate_native_schema(
            receipt, envelope, compiler=profile.compilerHash, finalized=job["kind"] == "checkpoint_finalize"
        )
        result = compare_observations(
            spec, [sample.model_dump() for sample in envelope.samples], motion=task.get("referenceMotion")
        )
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
            profile = self._profile(policy, job, receipt)
            import tempfile

            with tempfile.TemporaryDirectory(prefix="native-source-") as temporary:
                if profile.compilerHash:
                    provider = compiler_sources()[profile.stateProviderFile]
                else:
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
        job = self.evidence.manager.jobs.get(policy["jobId"])
        profile = self._profile(policy, job, receipt)
        current = implementation_identity()
        retained_verifiers = [INITIALIZATION_R2_VERIFIER]
        init = job["plan"].get("initialization")
        if not init or "fitActionAbsMax" not in init:
            retained_verifiers.append(INITIALIZATION_R1_VERIFIER)
        if not init:
            retained_verifiers.append(PRE_INITIALIZATION_VERIFIER)
        compatible = [current, *(current | {"files": current["files"] | {
            "packages/policies/qualification.py": sha,
        }} for sha in retained_verifiers)]
        if receipt.afterLearn and not profile.compilerHash:
            earlier = compatible_legacy_verifier(current)
            compatible.append(earlier)
        if verification["verifier"] not in compatible or read_json(
            frozen / "profile.json"
        ) != profile.model_dump(mode="json"):
            raise PlanError(
                "observation_evidence_changed", "Native verification implementation/profile changed"
            )
        facts = self._facts(
            policy,
            job,
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
