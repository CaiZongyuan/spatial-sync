"""Copied into the isolated trainer. Capture a declared Task hook without assembling observations."""

import hashlib
import inspect
import json
import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def code_hash(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def canonical_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()


def task_file(root, relative):
    parts = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or parts.is_absolute()
        or any(part in (".", "..") for part in parts.parts)
    ):
        raise ValueError("Observation path must stay in the pinned Task package")
    path = root / parts
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Observation input is not a pinned regular Task file")
    return path


def capture_observations(runner, output, inputs, export, versions):
    """Only an actual hook return produces samples; old Tasks retain missing evidence."""
    wrapper = getattr(runner, "env", None)
    env = getattr(wrapper, "unwrapped", wrapper)
    hook = getattr(env, "lw_policy_observation_samples", None)
    if not callable(hook):
        return None
    package = Path(os.environ["LW_TASK_PACKAGE"])
    source = inspect.getsourcefile(inspect.unwrap(hook))
    if source is None:
        raise ValueError("Sampling hook source is unavailable")
    source_hash = code_hash(source)
    matches = [
        path
        for path in package.rglob("*.py")
        if not path.is_symlink()
        and path.resolve().is_relative_to(package.resolve())
        and code_hash(path) == source_hash
    ]
    if len(matches) != 1:
        raise ValueError("Sampling hook must match one frozen Task source file")
    # The Task owns independent native actor observations and raw canonical state extraction.
    returned = hook(256)
    if (
        not isinstance(returned, dict)
        or set(returned) != {"method", "samples"}
        or not isinstance(returned["method"], str)
        or not returned["method"].strip()
        or not isinstance(returned["samples"], list)
        or len(returned["samples"]) < 256
    ):
        raise ValueError("Sampling hook must return its method and at least 256 independent samples")
    samples = returned["samples"]
    identities = [sample["sourceStateId"] for sample in samples]
    if any(not isinstance(value, str) or not value for value in identities) or len(set(identities)) != len(
        identities
    ):
        raise ValueError("Sampling hook must supply distinct source state identities")
    job = inputs["job"]
    identity = {
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
    if not job.get("leaseId"):
        raise ValueError("Sampling capture requires an actual fenced Job dispatch")
    identity["attemptId"] = (
        "attempt-" + hashlib.sha256(f"{job['jobId']}:{job['leaseId']}".encode()).hexdigest()
    )
    identity["versions"] = versions
    policy = {name: export[name] for name in ("checkpointSha256", "policySha256")}
    policy["onnxSha256"] = policy.pop("policySha256")
    policy["observationSha256"] = file_hash(task_file(package, inputs["task"]["observation"]))
    capture = {
        "producer": "task-env-observations/1",
        "method": returned["method"],
        "hookModule": hook.__module__,
        "hookQualname": hook.__qualname__,
        "taskSourceFile": matches[0].relative_to(package).as_posix(),
        "taskSourceSha256": source_hash,
        "implementationSha256": code_hash(__file__),
        "runnerModule": type(runner).__module__,
        "runnerQualname": type(runner).__qualname__,
        "hookStatus": "returned",
        "requestedSamples": 256,
        "capturedAt": datetime.now(UTC).isoformat(),
    }
    directory = Path(output) / "observations"
    directory.mkdir(exist_ok=True)
    envelope = {
        "schema": "lw.policy-observation-samples/1",
        "source": identity,
        "policy": policy,
        "capture": capture,
        "samples": samples,
    }
    path = directory / "samples.json"
    path.write_text(json.dumps(envelope, sort_keys=True, allow_nan=False), encoding="utf-8")
    receipt = {
        "schema": "lw.policy-observation-capture/1",
        "source": identity,
        "policy": policy,
        "capture": capture,
        "samplesArtifact": "observations/samples.json",
        "samplesSha256": file_hash(path),
        "sampleCount": len(samples),
        "sourceStateIdsSha256": canonical_hash(identities),
    }
    (directory / "capture.json").write_text(
        json.dumps(receipt, sort_keys=True, allow_nan=False), encoding="utf-8"
    )
    return receipt
