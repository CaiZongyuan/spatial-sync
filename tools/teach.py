"""Capture live FR3 joint poses or regenerate offline joint-space waypoints.

After lw moveJoints, use --capture rack.A1 --pose grasp --output locations.yaml.
The runtime never uses IK. Write results outside published library packages.
For another layout, select --scene-version name@rev and optionally --robot key;
the tool reads its pinned FR3, base, effective height and existing bottom Sites.
Selected offline teaching keeps each existing grasp orientation. Selected live
capture requires a paused sample and compares its physical TCP with offline FK.
"""

import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.programs.robot_arm.grasp import nominal_grasp_target
from packages.programs.robot_arm.newton import HOME
from packages.protocol.math import compose_pose

ASSET = ROOT / "assets/robots/franka_emika_panda/urdf/fr3_franka_hand.urdf"
LOCATIONS = ROOT / "library/scenes/robot-and-tubes/1/locations.yaml"


class TeachingError(ValueError):
    """A bounded local IK attempt failed; this is not physical validation."""

    def __init__(self, measurements):
        self.measurements = measurements
        super().__init__("Bounded IK did not meet teaching tolerance: " + json.dumps(measurements))


def approach_height_m(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("The approach height must be a finite positive number in metres")
    return value


class Teacher:
    def __init__(self, asset=ASSET, base=None):
        self.base = np.eye(4) if base is None else np.array(base, dtype=float)
        if base is None:
            self.base[:3, 3] = [-0.5, 0, 0.75]
        joints = ET.parse(asset).getroot().findall("joint")
        by_child = {j.find("child").get("link"): j for j in joints}
        self.chain = []
        link = "fr3_hand_tcp"
        while link in by_child:
            joint = by_child[link]
            self.chain.insert(0, joint)
            link = joint.find("parent").get("link")
        arm = [j for j in self.chain if j.get("type") == "revolute"]
        self.lower = np.array([float(j.find("limit").get("lower")) for j in arm])
        self.upper = np.array([float(j.find("limit").get("upper")) for j in arm])
        self.velocity_limits = [float(j.find("limit").get("velocity")) for j in arm]

    def fk(self, coordinates):
        from scipy.spatial.transform import Rotation

        transform = self.base.copy()
        index = 0
        for joint in self.chain:
            origin = joint.find("origin")
            step = np.eye(4)
            if origin is not None:
                step[:3, 3] = np.fromstring(origin.get("xyz", "0 0 0"), sep=" ")
                step[:3, :3] = Rotation.from_euler(
                    "xyz", np.fromstring(origin.get("rpy", "0 0 0"), sep=" ")
                ).as_matrix()
            transform = transform @ step
            if joint.get("type") == "revolute":
                turn = np.eye(4)
                axis = np.fromstring(joint.find("axis").get("xyz"), sep=" ")
                turn[:3, :3] = Rotation.from_rotvec(axis * coordinates[index]).as_matrix()
                transform = transform @ turn
                index += 1
        return transform

    def solve(self, position, initial=HOME, *, orientation=None):
        initial = np.asarray(initial, dtype=float)
        bounds = (self.lower + 1e-4, self.upper - 1e-4)
        if (initial.shape != self.lower.shape or not np.all(np.isfinite(initial))
                or np.any(initial < bounds[0]) or np.any(initial > bounds[1])):
            raise ValueError("Initial joints must be finite and within the teaching joint limits")
        from scipy.optimize import least_squares
        from scipy.spatial.transform import Rotation

        target = (Rotation.from_euler("xyz", [np.pi, 0, 0]).as_matrix()
                  if orientation is None else np.asarray(orientation))

        def error(q):
            actual = self.fk(q)
            return np.r_[
                actual[:3, 3] - position, Rotation.from_matrix(target.T @ actual[:3, :3]).as_rotvec() * 0.2
            ]

        result = least_squares(
            lambda q: np.r_[error(q), (q - initial) * 1e-5],
            initial,
            bounds=bounds,
            ftol=1e-11,
            xtol=1e-11,
            gtol=1e-11,
            max_nfev=400,
        )
        residual = error(result.x)
        if not np.all(np.isfinite(residual)) or np.linalg.norm(residual) > 1e-4:
            margins = np.minimum(result.x - bounds[0], bounds[1] - result.x)
            raise TeachingError({
                "targetM": np.asarray(position).tolist(),
                "positionErrorM": float(np.linalg.norm(residual[:3])),
                "angleErrorRad": float(np.linalg.norm(residual[3:]) / .2),
                "weightedResidual": float(np.linalg.norm(residual)),
                "jointMarginsRad": margins.tolist(), "minJointMarginRad": float(np.min(margins)),
                "nfev": int(result.nfev), "solverStatus": int(result.status),
                "newtonP6Verified": False,
            })
        return result.x.tolist()


@dataclass
class TeachingScene:
    teacher: Teacher
    config: dict
    bottom_sites: dict
    identity: dict
    protected_roots: tuple[Path, ...]
    calibration: dict | None = None


def select_scene(selector, robot=None, library_root=None):
    """Read one exact scene and its physical pin; never start Newton or a Source."""
    from pxr import UsdGeom

    from apps.api.app.library_validation import program_schemas
    from packages.library.grasp_calibration import physical_calibration
    from packages.library.scenes import SceneLibrary
    from packages.library.structural import USDGraph, validate
    from packages.library.validation import StructuralValidator

    if "@" not in selector:
        raise ValueError("Teaching requires an exact scene version name@rev")
    library = SceneLibrary(user_root=library_root)
    version = library.resolve(selector)
    if version.metadata["stage"].startswith("python:") or version.metadata["sources"] != ["newton"]:
        raise ValueError("Selected-scene teaching requires an imported Newton USD scene")
    if version.template_error or version.asset_error:
        raise ValueError(version.template_error or version.asset_error)
    payload = {"kind": "scene", "subject": "scene:" + version.id, "metadata": version.metadata,
               "package": StructuralValidator.scope(version.path), "bindings": version.bindings,
               "locations": version.locations, "programs": program_schemas(),
               "assets": list(version.runtime_assets.values()), "types": {}}
    from packages.library.common import TYPE_ROOT, document

    payload["types"] = {value["type"]: value for path in TYPE_ROOT.glob("*.yaml")
                        if (value := document(path))}
    report = validate(payload)
    failures = [row["message"] for row in report["checks"] if row["status"] == "fail"]
    if failures:
        raise ValueError("Selected scene structural checks failed: " + "; ".join(failures))
    candidates = [binding for binding in report.get("effectiveBindings", {}).get("newton", [])
                  if binding["program"] == "robot_arm.newton" and (robot is None or binding["key"] == robot)]
    if len(candidates) != 1:
        raise ValueError("Select exactly one configured FR3 with --robot")
    binding = candidates[0]
    graph = USDGraph(payload["package"], payload["assets"])
    stage = graph.compose(version.metadata["stage"])
    calibration = physical_calibration(stage, binding, payload["programs"][binding["program"]],
                                       version.bindings, graph)["expected"]
    if calibration["assetId"] != "robot/fr3@1":
        raise ValueError("Teaching currently supports the pinned robot/fr3@1 physical model")
    asset = graph.assets[calibration["assetId"]]
    # Gf uses row vectors; Teacher uses column vectors.
    base = np.array(UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(binding["key"]))).T
    teacher = Teacher(Path(asset["path"]) / "source/fr3_franka_hand.urdf", base)
    config = binding["config"]
    bottom_sites = {}
    for name, location in config["locations"]["locations"].items():
        prim = stage.GetPrimAtPath(location["site"])
        if not prim:
            raise ValueError("Selected bottom Site is unavailable: " + location["site"])
        bottom_sites[name] = list(UsdGeom.XformCache().GetLocalToWorldTransform(prim).ExtractTranslation())
    return TeachingScene(teacher, config, bottom_sites,
                         {"sceneVersion": version.id, "sceneContentHash": version.content_hash,
                          "robot": binding["key"], "assetId": calibration["assetId"],
                          "assetContentHash": calibration["assetContentHash"],
                          "physicalTcpSite": calibration["tcpSite"], "baseWorldMatrix": base.tolist()},
                         tuple(library.roots.values()), calibration)


def measured_grasp(selection, location, joints, manifest, snapshot):
    """Compare offline FK to the real model's calibrated TCP in one spatial sample."""
    from packages.library.posture import PostureEndpoint, require_calibration

    poses = {row[0]: {"p": row[1:4], "q": row[4:]} for row in snapshot["poses"]}
    bodies = {node["entityKey"]: node["streamId"] for node in manifest["nodes"] if node["kind"] == "body"}

    def site_pose(key):
        sites = [site for site in manifest["sites"] if site["entityKey"] == key]
        if len(sites) != 1:
            raise ValueError("Measured Site is missing or ambiguous: " + key)
        site = sites[0]
        return (site, compose_pose(poses[bodies[site["parentBodyKey"]]], site["localPose"])
                if site.get("parentBodyKey") else site["localPose"])

    tcp_site, physical = site_pose(selection.identity["physicalTcpSite"])
    expected = selection.calibration
    if not expected:
        raise ValueError("Teaching has no pinned physical calibration")
    observed = expected | {"siteOwner": tcp_site.get("parentBodyKey"),
                           "handToTcpPose": [*tcp_site["localPose"]["p"], *tcp_site["localPose"]["q"]]}
    height = selection.config.get("graspHeightM", 0.0)
    endpoint = PostureEndpoint("tcpSite", selection.identity["physicalTcpSite"], height, True)
    # Manifest Site transforms are extracted from the model's float32 storage.
    require_calibration(endpoint, expected, observed, pose_storage="float32")
    _, bottom = site_pose(location["site"])
    nominal = nominal_grasp_target(bottom["p"], height)
    offline = selection.teacher.fk(joints)[:3, 3]
    difference = float(np.linalg.norm(offline - physical["p"]))
    # PoseMessage serializes body positions to six decimal places and quaternion
    # components to seven. If quaternion component error has norm e, normalizing
    # changes its norm by at most 2e/(|q|-e); rotating a point adds <=2|point| times
    # that error. TCP local position has one separately measured float32 rounding.
    # This bounds representation only, not unknown Newton FK-chain arithmetic.
    body = poses[bodies[tcp_site["parentBodyKey"]]]
    quaternion_error = np.sqrt(4) * .5e-7
    quaternion_norm = np.linalg.norm(body["q"])
    local = np.asarray(expected["handToTcpPose"][:3])
    representation = (np.sqrt(3) * .5e-6
                      + 4 * quaternion_error * np.linalg.norm(local)
                      / (quaternion_norm - quaternion_error)
                      + np.linalg.norm(local - local.astype(np.float32).astype(float)))
    if difference > representation:
        raise ValueError(f"Offline teaching FK differs from actual model TCP by {difference:.9f}m; "
                         f"known representation bound is {representation:.9f}m; "
                         "actual model/FK control required before capture")
    return {"bottomSiteWorld": bottom["p"], "graspHeightM": height,
            "graspTargetWorld": list(nominal), "physicalTcpWorld": physical["p"],
            "tcpFk": offline.tolist(), "teacherModelErrorM": difference,
            "snapshotRepresentationBoundM": float(representation),
            "teacherModelComparison": "within-snapshot-representation",
            "tcpErrorM": float(np.linalg.norm(np.asarray(physical["p"]) - nominal)),
            "targetDefinition": "tube-grasp-height/1"}


def teach_selected(selection, *, approach_height=.15):
    approach_height = approach_height_m(approach_height)
    document = deepcopy(selection.config["locations"])
    evidence = []
    height = selection.config.get("graspHeightM", 0.0)
    for name, location in document["locations"].items():
        bottom = selection.bottom_sites[name]
        target = nominal_grasp_target(bottom, height)
        seed = location.get("grasp", HOME)
        orientation = selection.teacher.fk(seed)[:3, :3]
        grasp = selection.teacher.solve(np.array(target), seed, orientation=orientation)
        approach_target = np.array(target) + [0, 0, approach_height]
        approach = selection.teacher.solve(approach_target, grasp, orientation=orientation)
        location.update(grasp=grasp, approach=approach)
        tcp = selection.teacher.fk(grasp)[:3, 3]
        evidence.append(selection.identity | {"location": name, "bottomSiteWorld": bottom,
                        "graspHeightM": height, "graspTargetWorld": list(target),
                        "physicalTcpWorld": tcp.tolist(), "tcpErrorM": float(np.linalg.norm(tcp - target)),
                        "targetDefinition": "tube-grasp-height/1", "approachHeightM": approach_height,
                        "approachTargetWorld": approach_target.tolist(),
                        "approachTcpErrorM": float(np.linalg.norm(
                            selection.teacher.fk(approach)[:3, 3] - approach_target)),
                        "validationScope": "offline-urdf-fk", "newtonP6Verified": False})
    return document, evidence


def write_locations(target, document, protected_roots=()):
    roots = (ROOT / "library", Path(os.getenv("LW_LIBRARY", "~/.spatial-sync/library")).expanduser(),
             *protected_roots)
    target = target.resolve()
    if any(target.is_relative_to(root.resolve() / "scenes") for root in roots):
        raise ValueError("Published scene packages are immutable; use --output outside library/scenes")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def teach(*, approach_height=.15):
    approach_height = approach_height_m(approach_height)
    teacher = Teacher()
    locations, evidence = {}, []
    points = {
        f"rack.{row}{column + 1}": [-0.17 + 0.055 * column, -0.25 + 0.12 * r, 0.84]
        for r, row in enumerate("AB")
        for column in range(4)
    }
    points.update(
        {
            f"centrifuge.slot{i + 1}": [0.06 + x, 0.20 + y, 0.87]
            for i, (x, y) in enumerate([(0.045, 0), (0, 0.045), (-0.045, 0), (0, -0.045)])
        }
    )
    for name, point in points.items():
        zone, suffix = name.split(".")
        site = (
            f"/World/Lab/Rack/{suffix}" if zone == "rack" else f"/World/Lab/Centrifuge/Rotor/Slot{suffix[-1]}"
        )
        grasp = teacher.solve(np.array(point))
        approach_target = np.array(point) + [0, 0, approach_height]
        approach = teacher.solve(approach_target, grasp)
        locations[name] = {"site": site, "zone": zone, "approach": approach, "grasp": grasp}
        if zone == "centrifuge":
            locations[name]["requires"] = [
                {"entity": "/World/Lab/Centrifuge", "property": "lidOpen", "equals": True},
                {"entity": "/World/Lab/Centrifuge", "property": "phase", "equals": "idle"},
            ]
        evidence.append(
            {
                "name": name,
                "site": point,
                "tcpErrorM": float(np.linalg.norm(teacher.fk(grasp)[:3, 3] - point)),
                "approachHeightM": approach_height, "approachTargetWorld": approach_target.tolist(),
                "approachTcpErrorM": float(np.linalg.norm(teacher.fk(approach)[:3, 3] - approach_target)),
                "validationScope": "offline-urdf-fk", "newtonP6Verified": False,
            }
        )
    return {"locations": locations}, evidence


def capture(args):
    """Merge one measured joint vector; preserve all other taught positions."""
    selection = (select_scene(args.scene_version, args.robot, args.library_root)
                 if getattr(args, "scene_version", None) else None)
    robot = selection.identity["robot"] if selection else "/World/Lab/RobotA"
    with httpx.Client(base_url=args.url, timeout=30, trust_env=False) as client:
        session_id = args.session
        if not session_id:
            response = client.get("/api/v1/health")
            response.raise_for_status()
            active = [
                session["sessionId"]
                for session in response.json()["sessions"]
                if session["state"] in ("RUNNING", "PAUSED")
            ]
            if len(active) != 1:
                raise ValueError("Set LW_SESSION or --session to one running laboratory session")
            session_id = active[0]
        route = "/api/v1/sessions/" + session_id
        response = client.get(route)
        response.raise_for_status()
        summary = response.json()
        expected_version = (selection.identity["sceneVersion"] if selection
                            else summary.get("sceneVersion") or "robot-and-tubes@1")
        matches_scene = (summary.get("sceneVersion") == expected_version if selection
                         else summary.get("sceneId") == "robot-and-tubes")
        if summary.get("source") != "newton" or not matches_scene:
            raise ValueError("FR3 teaching requires a matching Newton scene")
        if selection and summary.get("sceneContentHash") != selection.identity["sceneContentHash"]:
            raise ValueError("Teaching session scene content differs from the selected immutable package")
        if selection and summary.get("state") != "PAUSED":
            raise ValueError("Pause the selected teaching session before measuring joints and model TCP")
        response = client.get(route + "/state", params={"keys": robot})
        response.raise_for_status()
        state = response.json()["entities"][robot]
        joints = state["values"]["jointPositions"]
        if len(joints) != 7 or not all(np.isfinite(joints)):
            raise ValueError("The current FR3 state must contain seven finite joint positions")
        target = args.output or args.input
        source = target if target.exists() else args.input
        document = (deepcopy(selection.config["locations"]) if selection and not target.exists()
                    else yaml.safe_load(source.read_text(encoding="utf-8")))
        if args.capture not in document["locations"]:
            raise ValueError(f"Unknown taught location {args.capture}")
        location = document["locations"][args.capture]
        manifest = client.get(route + "/scene")
        manifest.raise_for_status()
        manifest = manifest.json()
        snapshot = client.get(route + "/snapshot")
        snapshot.raise_for_status()
        snapshot = snapshot.json()
        if selection:
            final = client.get(route)
            final.raise_for_status()
            final = final.json()
            clocks = ("epoch", "simTime", "sceneRevision")
            if (final.get("state") != "PAUSED"
                    or any(summary.get(field) != snapshot.get(field)
                           or final.get(field) != snapshot.get(field)
                           for field in clocks)
                    or manifest["sceneRevision"] != snapshot["sceneRevision"]):
                raise ValueError("Teaching sample changed while reading; pause and capture again")
            measurement = measured_grasp(selection, location, joints, manifest, snapshot)
        else:
            poses = {row[0]: {"p": row[1:4], "q": row[4:]} for row in snapshot["poses"]}
            bodies = {node["entityKey"]: node["streamId"] for node in manifest["nodes"]
                      if node["kind"] == "body"}
            site = next(site for site in manifest["sites"] if site["entityKey"] == location["site"])
            world = (compose_pose(poses[bodies[site["parentBodyKey"]]], site["localPose"])
                     if site.get("parentBodyKey") else site["localPose"])
            tcp = Teacher().fk(joints)[:3, 3]
            measurement = {"tcpFk": tcp.tolist(), "bottomSiteWorld": world["p"], "graspHeightM": 0.0,
                           "graspTargetWorld": world["p"], "physicalTcpWorld": tcp.tolist(),
                           "tcpErrorM": float(np.linalg.norm(tcp - world["p"])),
                           "targetDefinition": "tube-grasp-height/1"}
        distance = measurement["tcpErrorM"]
        location[args.pose] = list(joints)
        write_locations(target, document, selection.protected_roots if selection else ())
        warning = args.pose == "grasp" and distance > 0.01
        if warning:
            print(f"WARNING: grasp TCP is {distance:.6f}m from {location['site']} (>0.01m)", file=sys.stderr)
        return {
            "sessionId": session_id,
            "location": args.capture,
            "pose": args.pose,
            "jointPositions": list(joints),
            "sourceRev": state["rev"],
            "sourceMeta": state.get("meta", {}).get("jointPositions"),
            "output": str(target.resolve()),
            "siteWorld": measurement["bottomSiteWorld"],
            "inputIdentity": selection.identity if selection else {"sceneVersion": expected_version},
            "graspDistanceWarning": warning,
            "physicsImported": "newton" in sys.modules or "warp" in sys.modules,
        } | measurement


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Regenerate all default offline joint waypoints")
    mode.add_argument("--capture", help="Capture current jointPositions into one existing location")
    parser.add_argument("--pose", choices=("approach", "grasp"), default="grasp")
    parser.add_argument("--input", type=Path, default=LOCATIONS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--url", default=os.getenv("LW_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--session", default=os.getenv("LW_SESSION"))
    parser.add_argument("--scene-version", help="Exact imported Newton scene name@rev")
    parser.add_argument("--robot", help="Configured robot entityKey in the selected version")
    parser.add_argument("--library-root", type=Path, help="User library root for the selected scene")
    parser.add_argument("--approach-height-m", type=float,
                        help="Explicit offline approach height above grasp (default: 0.15 m)")
    args = parser.parse_args()
    if args.capture:
        if args.approach_height_m is not None:
            parser.error("--approach-height-m is an offline teaching option; omit it with --capture")
        print(json.dumps(capture(args), indent=2))
    else:
        height = approach_height_m(.15 if args.approach_height_m is None else args.approach_height_m)
        selection = (select_scene(args.scene_version, args.robot, args.library_root)
                     if args.scene_version else None)
        document, evidence = (teach_selected(selection, approach_height=height) if selection
                              else teach(approach_height=height))
        if args.write or args.output:
            target = args.output or args.input
            write_locations(target, document, selection.protected_roots if selection else ())
        print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
