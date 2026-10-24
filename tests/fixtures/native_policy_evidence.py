"""Actual collector calls under explicitly controlled profiles; never a real training claim."""

import io
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from apps.node import native_observation_capture as native
from apps.node.observation_capture import code_hash
from packages.library.archive import extract_package
from packages.policies.native_evidence import NativeProfile
from packages.training.inputs import pack_package
from tests.fixtures.policy import artifact_archive, observation_spec
from tests.gateway.test_policies import uploaded_job
from tests.unit import test_native_observation_capture as controlled


def native_inputs(client, tmp_path, monkeypatch, registry=None):
    package = tmp_path / "task"
    package.mkdir()
    (package / "native_provider.py").write_bytes(Path(controlled.__file__).read_bytes())
    monkeypatch.setenv("LW_TASK_PACKAGE", str(package))
    spec = observation_spec()
    spec["observations"][0]["keys"] = spec["action"]["jointKeys"] = ["/World/Joint"]

    def artifacts(job):
        output = tmp_path / "output"
        output.mkdir()
        extract_package(io.BytesIO(artifact_archive(job)), output)
        versions = json.loads((output / "versions.json").read_bytes())
        export = json.loads((output / "export.json").read_bytes())
        runner, env = controlled.Runner(), controlled.Environment()
        observer = SimpleNamespace(env=env, selected=[0], epochs=[0], physics_count=0, disabled=False)
        env.sim = SimpleNamespace(get_physics_step_count=lambda: observer.physics_count)
        profile = NativeProfile(
            id="controlled-native-input",
            evidenceMode="controlled",
            taskContentHash=job["taskContentHash"],
            stateProviderFile="native_provider.py",
            versions=versions["versions"],
            inputWidth=1,
            inputName="observations",
            actionWidth=1,
            collectorSha256=code_hash(native.__file__),
            runner=native.source_identity(runner),
            actor=native.source_identity(runner.alg.actor),
            act=native.source_identity(runner.alg.act),
            exporter=native.source_identity(runner.export_policy_to_onnx),
            stateProvider=native.source_identity(env.lw_policy_observation_state),
            observationGroups=["policy"],
            environments=[0],
            boundary="rsl-mlp-pre-normalizer",
            normalization="inside-exported-graph",
            previousAction="raw-native-algorithm-output",
            previousActionReset="zero-on-environment-epoch",
        )
        if registry is not None:
            registry.profiles[profile.id] = profile  # Constructor-owned controlled registry, no HTTP setter.
        with native.NativeObservationCapture(
            runner,
            observer,
            profile.model_dump(mode="json"),
            {"job": job, "task": {"observation": "obs_spec.yaml"}},
        ) as capture:
            runner.alg.act(controlled.Tensor([[0.0]]))
            for index in range(1, 257):
                observer.physics_count = index
                env.state["joints"]["/World/Joint"]["q"] = index / 25600
                runner.alg.act(controlled.Tensor([[index / 12800]]))
        assert capture.error is None
        proof = capture.publish(output, export, versions)
        wrong = deepcopy(proof)
        wrong["witnesses"][-1]["nativeInputSha256"] = "f" * 64
        (output / "observations/wrong-native.json").write_text(json.dumps(wrong), encoding="utf-8")
        return pack_package(output)

    job = uploaded_job(client, tmp_path, spec=spec, artifact_factory=artifacts)
    created = client.post("/api/v1/policies", json={"checkpoint": job["jobId"] + "/checkpoints/model_0.pt"})
    assert created.status_code == 201, created.text
    return created.json(), "/api/v1/policies/training-probe/1/observation-checks"
