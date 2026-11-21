"""Identity of the runtime and entry that execute a pinned training Job.

The trainer bridge also loads this file under its isolated launcher alias.
"""

import hashlib
import json
import re
from pathlib import Path

MJLAB_ENTRY = ("-m", "lw_mjlab_launcher")
PRE_INITIALIZATION_COMPILER = "7948750d98b8750488bd71a3522dc70accb892c87768882c21b74ae6aa265356"
INITIALIZATION_R1_COMPILER = "4d85344fc6c5fd781cf081a3dcb0ae7776cd0eafc32e2f1f03b56e9a46bd8da6"
INITIALIZATION_R2_COMPILER = "335d7c03abd4f916a03b3e103f51256b1d053e6ee244ee56ccc92bcd8eb4a547"
MJLAB_DISTRIBUTIONS = {
    "mjlab": "mjlab", "mujoco": "mujoco", "mujoco_warp": "mujoco-warp",
    "warp": "warp-lang", "torch": "torch", "rsl_rl": "rsl-rl-lib",
}
ISAACLAB_DISTRIBUTIONS = {
    "isaaclab": "isaaclab", "newton": "newton", "warp": "warp-lang",
    "torch": "torch", "rsl_rl": "rsl-rl-lib",
}
PINS = {
    "mjlab": {
        "mjlab": "1.3.0", "mujoco": "4.0.1", "mujoco_warp": "3.8.1", "warp": "1.12.0", "torch": "2.9.1",
    },
    "isaaclab": {"isaaclab": "3.0.0", "newton": "1.6.0rc1", "warp": "1.17.0", "torch": "2.11.0"},
}


class RuntimeMismatch(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def trainer_distributions(is_mjlab):
    return MJLAB_DISTRIBUTIONS if is_mjlab else ISAACLAB_DISTRIBUTIONS


def job_backend(job, task=None):
    if job.get("backend") is not None:
        return job["backend"]
    if task is None:
        raise ValueError("A retained Job without backend requires its pinned Task")
    return "mjlab" if task.get("mjlab") is not None else "isaaclab"


def legacy_local(node):
    return (
        node["id"] == "local" and not node["hardware"].startswith("node-hardware/2:")
        and all(node.get(name) is None for name in (
            "trainerVersions", "isaaclabVersions", "compilerHash", "trainingEntries",
        ))
    )


def entry_identity(backend, entry):
    entry = tuple(entry)
    if backend == "mjlab":
        return "mjlab:managed" if entry in ((), MJLAB_ENTRY) else None
    if entry == ("-m", "isaaclab"):
        return "isaaclab:cli"
    if entry and not entry[0].startswith("-"):
        try:
            with Path(entry[0]).open("rb") as script:
                digest = hashlib.file_digest(script, "sha256").hexdigest()
        except OSError:
            return None
        args = json.dumps([digest, *entry[1:]], ensure_ascii=False).encode()
        return "isaaclab:script:" + hashlib.sha256(args).hexdigest()
    return None


def reported_versions(node, backend):
    if backend == "isaaclab" and node.get("isaaclabVersions") is not None:
        return node["isaaclabVersions"]
    return node.get("trainerVersions")


def rsl_rl_matches(installed, pinned):
    return isinstance(pinned, str) and bool(pinned) and installed == pinned


def training_runtime(node, job, backend):
    """Return the exact leased identity, or None for supported legacy-local training."""
    if legacy_local(node) and job.get("kind", "training") == "training":
        return None

    versions = reported_versions(node, backend)
    expected_rsl = job["plan"]["algorithm"].get("version")
    if (
        not versions or not rsl_rl_matches(versions.get("rsl_rl"), expected_rsl)
        or (backend == "mjlab" and expected_rsl != "5.0.1")
        or any(versions.get(name, "").split("+", 1)[0] != version
               for name, version in PINS[backend].items())
    ):
        raise RuntimeMismatch(
            "trainer_version_mismatch", "Trainer versions do not match the pinned Job runtime"
        )
    compiler = node.get("compilerHash") if backend == "mjlab" else None
    if backend == "mjlab" and not compiler:
        raise RuntimeMismatch("compiler_digest_required", "The managed mjlab compiler digest is required")
    entry = (node.get("trainingEntries") or {}).get(backend)
    supported = entry == "mjlab:managed" if backend == "mjlab" else (
        entry == "isaaclab:cli" or bool(entry and re.fullmatch(r"isaaclab:script:[a-f0-9]{64}", entry))
    )
    if not supported:
        raise RuntimeMismatch("trainer_entry_unsupported", "The node must report a supported training entry")
    return {
        "backend": backend, "entry": entry, "compilerHash": compiler,
        "trainerVersions": {name: versions[name] for name in trainer_distributions(backend == "mjlab")},
    }


def profile_key(plan, task_hash, hardware, gpu_id):
    value = {
        "taskContentHash": task_hash, "hardware": hardware, "gpuId": gpu_id,
        **{key: plan[key] for key in ("algorithm", "model", "hyperparameters")},
    }
    if init := plan.get("initialization"):
        value["initializationSha256"] = init["sha256"]
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def compatible_checkpoint_runtime(source, target, plan, current_compiler):
    if source == target:
        return True
    historical = (INITIALIZATION_R2_COMPILER,) if plan.get("initialization") is not None else (
        PRE_INITIALIZATION_COMPILER, INITIALIZATION_R1_COMPILER, INITIALIZATION_R2_COMPILER,
    )
    return bool(
        target and source.get("backend") == "mjlab" and source.get("entry") == "mjlab:managed"
        and source.get("compilerHash") in historical
        and target == source | {"compilerHash": current_compiler}
    )


def runtime_profile_key(plan, task_hash, node, resource, runtime):
    if runtime is None:
        return profile_key(plan, task_hash, node["hardware"], resource["id"])
    device = {"kind": "cpu"} if resource["id"] == "cpu" else {
        "model": " ".join(resource["name"].lower().split()),
        "memoryMb": resource.get("memoryTotalMb"),
        "computeCapability": resource.get("computeCapability"),
    }
    hardware = {"class": node["hardware"], "runtime": runtime}
    return profile_key(plan, task_hash, hardware, device)
