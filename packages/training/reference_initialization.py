"""Bounded data contract shared with the isolated managed mjlab launcher."""

from math import sqrt

if __package__:
    from packages.training.checkpoint_format import digest, json_bytes
else:
    from lw_checkpoint_format import digest, json_bytes

SCHEMA = "lw.reference-actor-init/1"
MAX_WIDTH = 4096
MAX_COUNT = 10_000_000
MAX_STATISTIC = 1_000_000
MAX_SHIFT = 10_000
FIT_SHIFT_MARGIN = 8.0
NORMALIZATION_EPSILON = 0.01
CURRENT_JOINT_GAIN = -4
REFERENCE_JOINT_GAIN = 5
# Allow for rounding across the offset, normalization and folded affine operations.
# Sixteen float32 unit roundoffs at this magnitude are below 1e-4.
MAX_FOLD_MAGNITUDE = 96.0
# mjlab 1.3.0 RslRlOnPolicyRunnerCfg and RslRlPpoAlgorithmCfg.
DEFAULT_STEPS_PER_ENV = 24
DEFAULT_MINI_BATCHES = 4
HYPERPARAMETERS = {
    "num_envs", "max_iterations", "learning_rate", "device", "num_steps_per_env",
    "num_learning_epochs", "num_mini_batches", "save_interval",
}
MODEL_FIELDS = {"actor", "critic", "activation", "obs_normalization"}
ALGORITHM = {"library": "rsl_rl", "name": "PPO", "version": "5.0.1"}


def validate_plan_initialization(plan, task, tasks):
    if plan.get("initialization") is not None:
        import yaml

        raw = tasks.observation_bytes(task)
        require_initialization(plan, task["task"], task["contentHash"], raw, yaml.safe_load(raw))


def identity(plan, payload):
    return digest(json_bytes({
        "initialization": {key: value for key, value in payload.items() if key != "sha256"},
        "model": plan["model"],
        "learningRate": float(plan["hyperparameters"]["learning_rate"]),
    }))


def receipt(plan):
    init = plan.get("initialization")
    if init is None:
        return None
    return {
        **{key: init[key] for key in (
            "schema", "sha256", "taskContentHash", "referenceMotionSha256",
            "observationSha256", "initialStd", "shift",
        )},
        **({"fitActionAbsMax": init["fitActionAbsMax"]} if "fitActionAbsMax" in init else {}),
        "normalizationCount": init["normalization"]["count"],
        "normalizationWidth": len(init["normalization"]["mean"]),
        "normalizationFrozen": True,
        "schedule": "fixed",
        "learningRate": float(plan["hyperparameters"]["learning_rate"]),
        "model": plan["model"],
    }


def projection(spec, task):
    """Return source columns in action order and the observation transform."""
    action = spec["action"]
    keys = action["jointKeys"]
    reference_keys = [task["robot"] + key.removeprefix("/Asset")
                      for key in task["referenceMotion"]["jointKeys"]]
    if (spec["schema"] != "lw.obs-spec/2" or action["space"] != "joint_position_offset"
            or set(reference_keys) != set(keys)):
        raise ValueError("Initialization requires reference joint order matching position-offset actions")
    measured = {}
    frames = []
    offset, scale = [], []
    for term in spec["observations"]:
        start = len(offset)
        offset.extend(term["offset"])
        scale.extend(term["scale"])
        if term["source"] == "joint_position":
            if term.get("lower") is not None or term.get("upper") is not None:
                raise ValueError("Initialization requires unclipped joint-position observations")
            for index, key in enumerate(term["keys"]):
                if key in measured:
                    raise ValueError("Initialization requires unambiguous joint-position observations")
                measured[key] = start + index
        if term["source"] == "reference_motion_frame":
            frames.append(start + 7)
    if len(frames) != 1 or not set(keys).issubset(measured):
        raise ValueError("Initialization requires matching current and reference joint observations")
    return ([measured[key] for key in keys],
            [frames[0] + reference_keys.index(key) for key in keys], offset, scale)


def _require_conditioned_fold(init, action, frames, current, reference, offsets, scales):
    statistics = init["normalization"]
    mean, variance, count = (statistics[key] for key in ("mean", "variance", "count"))
    # The matched reference columns are contiguous in the motion's joint order.
    reference_start = min(reference)
    reference_positions = list(zip(*(frame["joints"] for frame in frames), strict=True))
    for index, (measured, desired) in enumerate(zip(current, reference, strict=True)):
        action_scale = abs(action["scale"][index])
        default = action["defaultPosition"][index]
        positions = reference_positions[desired - reference_start]
        reference_bounds = (min(positions), max(positions))
        raw_delta = init["fitActionAbsMax"] * action_scale
        # Linear joint interpolation stays within the pinned frames. Invert the
        # teacher law over that interval and its declared raw action bound.
        current_bounds = (
            (REFERENCE_JOINT_GAIN * reference_bounds[0] - default - raw_delta) / -CURRENT_JOINT_GAIN,
            (REFERENCE_JOINT_GAIN * reference_bounds[1] - default + raw_delta) / -CURRENT_JOINT_GAIN,
        )
        magnitude = init["shift"] + abs(default) / action_scale
        for column, gain, bounds in (
            (measured, CURRENT_JOINT_GAIN, current_bounds),
            (desired, REFERENCE_JOINT_GAIN, reference_bounds),
        ):
            # Account for observation encoding, fitted rows, and centering
            # separately so cancellation cannot conceal large operands.
            centered_position = max(abs(q - offsets[column]) for q in bounds)
            center = abs(mean[column])
            fitted_observation = center + sqrt(count * variance[column])
            observation = max(centered_position * abs(scales[column]), fitted_observation)
            joint_value = abs(offsets[column]) + (observation + center) / abs(scales[column])
            magnitude += abs(gain) * joint_value / action_scale
        if max(magnitude, magnitude * action_scale) > MAX_FOLD_MAGNITUDE:
            joint = action["jointKeys"][index]
            raise ValueError(f"Initialization float32 folding exceeds the calibrated range for {joint}")


def require_initialization(plan, task, task_hash, observation_raw, spec):
    init = plan.get("initialization")
    if init is None:
        return
    pin = task.get("referenceMotionRef")
    if not task.get("mjlab") or task.get("declarative") is None or not pin:
        raise ValueError("Initialization requires a declarative mjlab Task with a pinned Reference Motion")
    if (
        init["taskContentHash"] != task_hash or init["referenceMotionSha256"] != pin["sha256"]
        or init["observationSha256"] != digest(observation_raw)
    ):
        raise ValueError("Initialization Task, Reference Motion or observation digest differs")
    if plan["algorithm"] != ALGORITHM:
        raise ValueError("Initialization requires rsl_rl PPO 5.0.1")
    model, hyper = plan["model"], plan["hyperparameters"]
    measured, desired, offset, scale = projection(spec, task)
    if (
        set(model) != MODEL_FIELDS or model["activation"] != "elu"
        or model["obs_normalization"] is not True
        or any(not isinstance(model[name], list) or not 1 <= len(model[name]) <= 4
               or any(type(size) is not int or not 1 <= size <= 1024 for size in model[name])
               for name in ("actor", "critic"))
        or len(model["actor"]) != 2 or min(model["actor"]) < len(measured)
    ):
        raise ValueError("Initialization requires ELU, normalized observations and a compatible bounded MLP")
    lr = hyper.get("learning_rate")
    if (set(hyper) - HYPERPARAMETERS or type(lr) not in (int, float)
            or not 1e-8 <= lr <= 1e-2 or hyper.get("device", "cuda") not in ("cpu", "cuda")
            or any(type(hyper.get(key)) is not int or not 1 <= hyper[key] <= 2**31 - 1
                   for key in (set(hyper) - {"learning_rate", "device"}) | {"num_envs", "max_iterations"})):
        raise ValueError("Initialization requires supported training settings and an explicit learning rate")
    samples = hyper["num_envs"] * hyper.get("num_steps_per_env", DEFAULT_STEPS_PER_ENV)
    if samples < max(2, hyper.get("num_mini_batches", DEFAULT_MINI_BATCHES)):
        raise ValueError("Initialization requires at least two PPO rollout samples and nonempty mini-batches")
    fit_bound = init.get("fitActionAbsMax")
    if fit_bound is None or not 0 <= fit_bound or init["shift"] < FIT_SHIFT_MARGIN + fit_bound:
        raise ValueError("Initialization shift must cover fitActionAbsMax plus the 8.0 ELU margin")
    norm = init["normalization"]
    if not len(norm["mean"]) == len(norm["variance"]) == len(offset) <= MAX_WIDTH:
        raise ValueError("Initialization statistics do not match the ordered observation width")
    action = spec["action"]
    if (any(not 1e-6 <= abs(value) <= MAX_STATISTIC for value in [*scale, *action["scale"]])
            or any(abs(value) > MAX_STATISTIC for value in [*offset, *action["defaultPosition"]])):
        raise ValueError("Initialization observation or action transforms exceed float32 bounds")
    frames = task["referenceMotion"]["frames"]
    _require_conditioned_fold(init, action, frames, measured, desired, offset, scale)
    if init["sha256"] != identity(plan, init):
        raise ValueError("Initialization digest does not match the approved configuration")
