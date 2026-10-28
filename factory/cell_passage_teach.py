"""Offline, bounded Newton FK teaching; this is reach evidence, not path qualification."""

import argparse
import json
import math
import re
from pathlib import Path

import newton
import numpy as np
import warp as wp
import yaml
from newton.solvers import SolverMuJoCo
from pxr import Sdf, Usd, UsdGeom
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from factory.cell_passage import HANDLE, LAB
from packages.sim_runtime.loaders import import_usd

ROBOT = LAB + "/Robot"


def composed_stage(library):
    text = (library / "scenes/cell-passage/1/stage.usda").read_text()
    text = re.sub(
        r"@library:assets/([^@]+)@",
        lambda match: "@" + (library / "assets" / match[1]).resolve().as_posix() + "@",
        text,
    )
    layer = Sdf.Layer.CreateAnonymous("cell-passage-local.usda")
    layer.ImportFromString(text)
    return Usd.Stage.Open(layer)


def locations(layout):
    stations, result = layout["stations"], []
    yaws = layout.get("stationYawRad", {})

    def add(name, site, point, offset, *, yaw=0.0, requires=(), body=None, zone=None):
        leaf = name.split(".", 1)[0]
        rail = (
            -0.45
            if leaf.startswith("Incubator")
            else 0.4
            if leaf.startswith("Flex")
            else float(np.clip(point[0] - 0.2, -0.5, 0.45))
        )
        if "/Centrifuge/" in site:
            rail = -0.45
        rules = [
            {"entity": LAB + "/RailCarriage", "property": "position", "gte": rail - 0.001},
            {"entity": LAB + "/RailCarriage", "property": "position", "lte": rail + 0.001},
        ]
        if leaf.startswith("Incubator"):
            rules.extend(
                {"entity": LAB + "/Incubator/" + door, "property": "opened", "equals": True}
                for door in ("OuterDoor", "InnerDoor")
            )
        if "/Centrifuge/" in site:
            rules.extend(
                [
                    {"entity": LAB + "/Centrifuge", "property": "lidOpen", "equals": True},
                    {"entity": LAB + "/Centrifuge", "property": "phase", "equals": "idle"},
                ]
            )
        rules.extend(requires)
        offset_world = Rotation.from_euler("z", yaw).apply(offset)
        target = np.asarray(point) + offset_world
        approach = target + [0, 0, 0.14]
        if leaf.startswith("Incubator"):
            approach[1] = -0.05
        result.append(
            {
                "name": name,
                "site": site,
                "target": target.tolist(),
                "approachTarget": approach.tolist(),
                "yawRad": yaw,
                "railPositionM": rail,
                "requires": rules,
                "body": body,
                "zone": zone or ("centrifuge" if "/Centrifuge/" in site else leaf),
            }
        )

    for key, point in stations.items():
        name = key.rsplit("/", 1)[1]
        if name.startswith(("Rack", "FlexTube")):
            add(name + ".tube", key, point, [0.035, 0, 0.038])
        elif name.startswith("Lid"):
            number = int(name[3:])
            offset = [-HANDLE[0], 0, HANDLE[2]] if number <= 4 else [-0.035, 0, 0.022]
            add(name + (".plateLid" if number <= 4 else ".cap"), key, point, offset)
        else:
            add(name + ".plate", key, point, HANDLE, yaw=yaws.get(key, 0))
    for number, key in enumerate(layout["slotSites"], 1):
        offset = ((0.12, 0), (0, 0.12), (-0.12, 0), (0, -0.12))[number - 1]
        add(
            f"CentrifugeSlot{number}.tube",
            key,
            [-0.53 + offset[0], -0.65 + offset[1], 0.999],
            [0.035, 0, 0.038],
        )
    for name, body in layout["bodies"].items():
        if body not in layout["lids"]:
            continue
        if name in ("Mother", "DaughterA", "DaughterB", "Counting"):
            candidates = [("FlexPlate", 0.018)]
            offset = [-HANDLE[0], 0, HANDLE[2]]
        else:
            index = [f"S{i}" for i in range(1, 7)] + ["B5", "B6"]
            candidates = [("Rack" + name, 0.05), (f"FlexTube{index.index(name) + 1}", 0.05)]
            offset = [-0.035, 0, 0.022]
        for station, height in candidates:
            key = LAB + "/Sites/" + station
            point = np.asarray(stations[key]) + [0, 0, height]
            add(
                station + "." + name + "Lid",
                body + "/LidSeat",
                point,
                offset,
                requires=[{"entity": body, "property": "station", "equals": key}],
                body=layout["lids"][body],
            )
    return result


def teach(library):
    library = Path(library).resolve()
    scene = library / "scenes/cell-passage/1"
    layout = json.loads((scene / "mechanical-layout.json").read_text())
    stage = composed_stage(library)
    with wp.ScopedDevice("cpu"):
        builder = newton.ModelBuilder(up_axis=newton.Axis.Z)
        SolverMuJoCo.register_custom_attributes(builder)
        import_usd(builder, stage, root_path=LAB)
        model = builder.finalize(device="cpu")
        state = model.state()
    starts, dstarts = model.joint_q_start.numpy(), model.joint_qd_start.numpy()
    joints = [model.joint_label.index(ROBOT + f"/Joints/fr3_joint{i}") for i in range(1, 8)]
    indices = starts[joints]
    rail = starts[model.joint_label.index(LAB + "/Joints/RailCarriage")]
    hand = model.body_label.index(ROBOT + "/fr3_hand")
    lower = model.joint_limit_lower.numpy()[dstarts[joints]].astype(float) + 1e-5
    upper = model.joint_limit_upper.numpy()[dstarts[joints]].astype(float) - 1e-5
    q = model.joint_q.numpy().copy()
    with wp.ScopedDevice("cpu"):
        newton.eval_fk(model, state.joint_q, state.joint_qd, state)
    initial = state.body_q.numpy()
    body_checks = []
    cache = UsdGeom.XformCache()
    for index, key in enumerate(model.body_label):
        expected = cache.GetLocalToWorldTransform(stage.GetPrimAtPath(key))
        quat = expected.ExtractRotationQuat()
        target_rotation = Rotation.from_quat([*quat.GetImaginary(), quat.GetReal()])
        pose = initial[index]
        body_checks.append(
            {
                "entityKey": key,
                "positionErrorM": float(np.linalg.norm(pose[:3] - np.asarray(expected.ExtractTranslation()))),
                "angleErrorRad": float((target_rotation.inv() * Rotation.from_quat(pose[3:])).magnitude()),
            }
        )
    home = np.asarray([0, -0.4, 0, -2, 0, 1.6, 0.7853981634])
    solved, receipts, failed = {}, [], []

    def forward(values):
        q[indices] = values
        state.joint_q.assign(q)
        with wp.ScopedDevice("cpu"):
            newton.eval_fk(model, state.joint_q, state.joint_qd, state)
        pose = state.body_q.numpy()[hand]
        rot = Rotation.from_quat(pose[3:])
        return pose[:3] + rot.apply([0, 0, 0.1034]), rot

    for descriptor in locations(layout):
        q[rail] = descriptor["railPositionM"]
        target_rotation = Rotation.from_euler("z", descriptor["yawRad"]) * Rotation.from_euler("x", math.pi)
        row, measures = {}, []
        seed = home.copy()
        seed[0] = np.clip(
            math.atan2(descriptor["target"][1] + 0.23, descriptor["target"][0] - descriptor["railPositionM"]),
            lower[0],
            upper[0],
        )
        for pose_name, target_name in (("approach", "approachTarget"), ("grasp", "target")):
            target = np.asarray(descriptor[target_name])

            def residual(values):
                point, rotation = forward(values)
                angle = (target_rotation.inv() * rotation).as_rotvec()
                return np.r_[point - target, angle * 0.2]

            def jacobian(values):
                delta = 0.001
                return np.column_stack(
                    [
                        (residual(values + np.eye(7)[i] * delta) - residual(values - np.eye(7)[i] * delta))
                        / (2 * delta)
                        for i in range(7)
                    ]
                )

            best = None
            for trial in (seed, home, np.asarray([seed[0], -0.8, 0.5, -2.3, -0.5, 1.7, 0.8])):
                value = least_squares(
                    residual,
                    np.clip(trial, lower, upper),
                    jac=jacobian,
                    bounds=(lower, upper),
                    max_nfev=150,
                    ftol=1e-10,
                    xtol=1e-9,
                    gtol=1e-9,
                )
                point, rotation = forward(value.x)
                error = float(np.linalg.norm(point - target))
                angle = float((target_rotation.inv() * rotation).magnitude())
                candidate = {"positions": value.x.tolist(), "positionErrorM": error, "angleErrorRad": angle}
                if best is None or error + angle < best["positionErrorM"] + best["angleErrorRad"]:
                    best = candidate
                if error < 0.001 and angle < 0.01:
                    break
            row[pose_name] = best["positions"]
            seed = np.asarray(best["positions"])
            measures.append({"pose": pose_name, **best})
        valid = all(value["positionErrorM"] < 0.001 and value["angleErrorRad"] < 0.01 for value in measures)
        if not valid:
            failed.append(descriptor["name"])
        solved[descriptor["name"]] = {
            "site": descriptor["site"],
            "zone": descriptor["zone"],
            "requires": descriptor["requires"],
            **row,
        }
        receipts.append({**descriptor, "valid": valid, "measurements": measures})
        print(descriptor["name"], valid, flush=True)
    (scene / "locations.yaml").write_text(
        yaml.safe_dump({"locations": solved}, sort_keys=False), encoding="utf-8"
    )
    (scene / "teaching.json").write_text(
        json.dumps(
            {
                "schema": "lw.cell-passage-teaching/1",
                "scope": "Offline Newton FK reach only; collision and loaded transport remain unqualified",
                "locations": receipts,
                "failed": failed,
                "initialBodyPoseChecks": body_checks,
                "modelCounts": {
                    "bodies": model.body_count,
                    "shapes": model.shape_count,
                    "joints": model.joint_count,
                },
                "jointLimitsRad": list(zip(lower.tolist(), upper.tolist(), strict=True)),
                "physicalTcpLocalM": [0, 0, 0.1034],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    bad_bodies = [
        row["entityKey"]
        for row in body_checks
        if row["positionErrorM"] > 0.002 or row["angleErrorRad"] > 0.01
    ]
    if bad_bodies:
        raise ValueError("Initial body/anchor mismatch: " + ", ".join(bad_bodies))
    if failed:
        raise ValueError("Unreachable locations: " + ", ".join(failed))
    return scene / "locations.yaml"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True, type=Path)
    print(teach(parser.parse_args().library))
