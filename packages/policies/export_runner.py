"""Standalone Policy Export reference. Dependencies: numpy and CPU onnxruntime."""

import argparse
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
from action_contract import action_delta_limit, command_in_bounds
from motion import motion_observations


def inverse_rotate(quaternion, value):
    quaternion = np.asarray(quaternion, dtype=np.float64)
    if abs(np.linalg.norm(quaternion) - 1) > 1e-5:
        raise ValueError("Expected a unit xyzw quaternion")
    axis = -quaternion[:3]
    cross = 2 * np.cross(axis, value)
    return value + quaternion[3] * cross + np.cross(axis, cross)


def observations(spec, sample, previous, motion):
    state = sample["state"]
    aliases = sample.get("references", {})
    terms = []
    motion_values = {}
    if any(item["source"] in ("reference_motion_frame", "motion_phase") for item in spec["observations"]):
        motion_values = motion_observations(motion, state["motionElapsed"])
    for term in spec["observations"]:
        source = term["source"]
        key = aliases.get(term.get("key"), term.get("key"))
        frame = aliases.get(term.get("frame"), term.get("frame"))
        if source in motion_values:
            raw = motion_values[source]
        elif source == "previous_action":
            raw = previous
        elif source == "command":
            raw = sample["command"]
            if not command_in_bounds(raw, list(zip(term["lower"], term["upper"], strict=True))):
                raise ValueError("Command outside the declared envelope")
        elif source in ("joint_position", "joint_velocity"):
            field = "q" if source == "joint_position" else "qd"
            raw = [state["joints"][joint][field] for joint in term["keys"]]
        elif source == "projected_gravity":
            raw = inverse_rotate(state["bodies"][key]["q"], np.array([0.0, 0.0, -1.0]))
        elif source == "body_angular_velocity":
            raw = np.asarray(state["bodies"][key]["w"], dtype=np.float64)
            if frame != "world":
                raw = inverse_rotate(state["bodies"][key if frame == "body" else frame]["q"], raw)
        else:
            pose = state["sites" if source.startswith("site_") else "bodies"][key]
            if source.endswith("_orientation"):
                raw = pose["q"]
                if abs(np.linalg.norm(raw) - 1) > 1e-5:
                    raise ValueError("Expected a unit xyzw orientation")
            else:
                raw = np.asarray(pose["p"], dtype=np.float64)
                if frame != "world":
                    origin = state["bodies"][frame]
                    raw = inverse_rotate(origin["q"], raw - origin["p"])
        raw = np.array(raw, dtype=np.float64)
        if list(raw.shape) != term["shape"]:
            raise ValueError("Observation term shape mismatch")
        terms.extend((raw - term["offset"]) * term["scale"])
    result = np.asarray(terms, dtype=np.float32)
    if not np.isfinite(result).all():
        raise ValueError("Observation must be finite float32")
    return result


def inference(directory, samples):
    manifest = json.loads((directory / "manifest.json").read_bytes())
    options = ort.SessionOptions()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(directory / manifest["model"]["path"]), sess_options=options, providers=["CPUExecutionProvider"]
    )
    spec = manifest["observation"]
    action = spec["action"]
    keys = action["jointKeys"]
    previous = np.zeros(len(keys), dtype=np.float32)
    motion_path = manifest.get("referenceMotion")
    motion = json.loads((directory / motion_path).read_bytes()) if motion_path else None
    physical = manifest["actuators"]
    calibration = manifest["sourceCalibration"]
    rows = []
    for sample in samples:
        if sample.get("reset"):
            previous.fill(0)
        vector = observations(spec, sample, previous, motion)
        raw = session.run(None, {manifest["model"]["input"]["name"]: vector[None, :]})[0][0]
        if raw.shape != previous.shape or raw.dtype != np.float32 or not np.isfinite(raw).all():
            raise ValueError("Unexpected model output")
        previous = raw.copy()
        current = np.asarray([sample["state"]["joints"][key]["q"] for key in keys], dtype=np.float64)
        scaled = raw.astype(np.float64) * action["scale"]
        delta = (
            scaled
            if action["space"] == "joint_position_delta"
            else action["defaultPosition"] + scaled - current
        )
        cap = action_delta_limit(action)
        if cap is not None:
            limit = np.asarray(cap)
            delta = np.clip(delta, -limit, limit)
        targets = np.clip(current + delta, action["lower"], action["upper"])
        for index, channel in enumerate(physical):
            for bounds in (channel["physicalLimits"], channel.get("controlRange")):
                if bounds is not None:
                    low, high = bounds
                    targets[index] = np.clip(
                        targets[index], -np.inf if low is None else low, np.inf if high is None else high
                    )
        source_targets = (
            {
                mapping["sourceName"]: (float(target) - mapping["zeroOffset"]) * mapping["sign"]
                for mapping, target in zip(calibration["joints"], targets, strict=True)
            }
            if calibration["status"] == "verified"
            else None
        )
        rows.append(
            {
                "observation": vector.tolist(),
                "raw": raw.tolist(),
                "targets": targets.tolist(),
                "sourceTargets": source_targets,
            }
        )
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", type=Path, help="JSON array of canonical physical states")
    args = parser.parse_args()
    print(
        json.dumps(
            inference(Path(__file__).resolve().parent, json.loads(args.samples.read_bytes())), allow_nan=False
        )
    )
