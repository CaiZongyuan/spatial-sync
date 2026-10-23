"""Import measured calibration evidence without changing the Task being measured."""

import hashlib
import json
import math

from packages.info.schema_subset import json_equal
from packages.training.jobs import profile_key


def measured_profile(raw, job, plan, node, resource_id):
    receipt = json.loads(raw)
    expected = {
        "taskContentHash": job["taskContentHash"],
        "hardware": node["hardware"],
        "resourceId": resource_id,
        **{name: plan[name] for name in ("algorithm", "model", "hyperparameters")},
    }
    if receipt.get("schema") != "lw.training-profile/1" or not json_equal(
        {key: receipt.get(key) for key in expected}, expected
    ):
        raise ValueError("Measurement does not match this exact Task, plan configuration and node resource")
    resources = {gpu["id"] for gpu in node["gpus"]} | ({"cpu"} if node.get("cpuAvailable") else set())
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
    if (
        type(setup) not in (int, float)
        or not math.isfinite(setup)
        or setup < 0
        or type(measured) not in (int, float)
        or not math.isfinite(measured)
        or measured <= 0
    ):
        raise ValueError("Measurement setup duration and timestamp are required")
    digest = hashlib.sha256(raw).hexdigest()
    return {
        "key": profile_key(plan, job["taskContentHash"], node["hardware"], resource_id),
        "iterationSeconds": max(samples),
        "setupSeconds": setup,
        "samples": len(samples),
        "measuredAt": measured,
        "evidence": f"measured receipt sha256:{digest}",
    }, digest
