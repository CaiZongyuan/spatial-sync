"""Import measured calibration evidence without changing the Task being measured."""

import hashlib
import json
import math

from packages.info.schema_subset import json_equal
from packages.training.runtime import job_backend, legacy_local, runtime_profile_key, training_runtime


def measured_profile(raw, job, plan, node, resource_id, *, task=None):
    receipt = json.loads(raw)
    if not isinstance(receipt, dict):
        raise ValueError("Measurement receipt must be an object")
    node = {"id": "local", **node}
    if legacy_local(node) and job.get("kind", "training") == "training":
        runtime = None
        schema = "lw.training-profile/1"
    else:
        runtime = training_runtime(node, job | {"plan": plan}, job_backend(job, task))
        schema = "lw.training-profile/2"
    expected = {
        "taskContentHash": job["taskContentHash"],
        "hardware": node["hardware"],
        "resourceId": resource_id,
        **{name: plan[name] for name in ("algorithm", "model", "hyperparameters")},
    }
    if runtime is not None:
        expected["runtimeIdentity"] = runtime
    if receipt.get("schema") != schema or not json_equal(
        {key: receipt.get(key) for key in expected}, expected
    ):
        raise ValueError("Measurement does not match this exact Task, plan configuration and node resource")
    resources = {gpu["id"]: gpu for gpu in node["gpus"]}
    if node.get("cpuAvailable"):
        resources["cpu"] = {"id": "cpu"}
    if resource_id not in resources:
        raise ValueError("Measurement resource is not advertised by this node")
    samples = receipt.get("samplesSeconds")
    if (
        not isinstance(samples, list)
        or not samples
        or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in samples)
    ):
        raise ValueError("Provide actual positive finite iteration-duration samples")
    setup, measured = receipt.get("setupSeconds"), receipt.get("measuredAt")
    dispatch = receipt.get("dispatchOverheadSeconds") if runtime is not None else 0
    if (
        type(setup) not in (int, float)
        or not math.isfinite(setup)
        or setup < 0
        or type(measured) not in (int, float)
        or not math.isfinite(measured)
        or measured <= 0
        or type(dispatch) not in (int, float)
        or not math.isfinite(dispatch)
        or dispatch < 0
    ):
        raise ValueError("Measurement setup duration and timestamp are required")
    digest = hashlib.sha256(raw).hexdigest()
    key = runtime_profile_key(plan, job["taskContentHash"], node, resources[resource_id], runtime)
    return {
        "key": key,
        "iterationSeconds": max(samples),
        "setupSeconds": setup,
        "dispatchOverheadSeconds": dispatch,
        "samples": len(samples),
        "measuredAt": measured,
        "evidence": f"measured receipt sha256:{digest}",
    }, digest
