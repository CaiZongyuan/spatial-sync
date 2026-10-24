"""Controlled sampler artifacts with actual hook/hash mechanics; not genuine trainer samples."""

import importlib.util
import io
import json
from copy import deepcopy
from types import SimpleNamespace

from apps.node.observation_capture import capture_observations
from packages.library.archive import extract_package
from packages.training.inputs import pack_package
from tests.fixtures.policy import artifact_archive


def sampler_task(path):
    path.mkdir(parents=True, exist_ok=True)
    source = path / "sampler.py"
    source.write_text(
        "class Environment:\n"
        "    def lw_policy_observation_samples(self, count):\n"
        "        return {'method': 'controlled arithmetic fixture, not real training', 'samples': [\n"
        "            {'sourceStateId': 'env0-step' + str(i),\n"
        "             'state': {'joints': {'/World/Lab/RobotA/fr3/fr3_joint1': {'q': i/25600, 'qd': 0}}},\n"
        "             'trainingObservation': [i/12800], 'previousAction': [0]}\n"
        "            for i in range(count)]}\n",
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("controlled_policy_dataset", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return SimpleNamespace(env=SimpleNamespace(unwrapped=module.Environment()))


def observation_artifacts(job, output, runner):
    output.mkdir()
    extract_package(io.BytesIO(artifact_archive(job)), output)
    versions = json.loads((output / "versions.json").read_bytes())
    export = json.loads((output / "export.json").read_bytes())
    capture_observations(
        runner, output, {"job": job, "task": {"observation": "obs_spec.yaml"}}, export, versions
    )
    envelope = json.loads((output / "observations/samples.json").read_bytes())
    failed = deepcopy(envelope)
    failed["samples"][-1]["trainingObservation"][0] += 0.002
    duplicate = deepcopy(envelope)
    duplicate["samples"][-1]["sourceStateId"] = duplicate["samples"][0]["sourceStateId"]
    foreign = deepcopy(envelope)
    foreign["policy"]["onnxSha256"] = "f" * 64
    for name, value in (("failed", failed), ("duplicate", duplicate), ("foreign", foreign)):
        (output / f"observations/{name}.json").write_text(json.dumps(value), encoding="utf-8")
    return pack_package(output)
