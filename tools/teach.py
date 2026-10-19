"""Capture live FR3 joint poses or regenerate offline joint-space waypoints.

After lw moveJoints, use --capture rack.A1 --pose grasp --output locations.yaml.
The runtime never uses IK; --write regenerates the default offline waypoints.
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import numpy as np
import yaml
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.programs.robot_arm.newton import HOME
from packages.protocol.math import compose_pose

ASSET = ROOT / "assets/robots/franka_emika_panda/urdf/fr3_franka_hand.urdf"
LOCATIONS = ROOT / "library/scenes/robot-and-tubes/1/locations.yaml"


class Teacher:
    def __init__(self):
        joints = ET.parse(ASSET).getroot().findall("joint")
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
        transform = np.eye(4)
        transform[:3, 3] = [-0.5, 0, 0.75]
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

    def solve(self, position, initial=HOME):
        target = Rotation.from_euler("xyz", [np.pi, 0, 0]).as_matrix()

        def error(q):
            actual = self.fk(q)
            return np.r_[
                actual[:3, 3] - position, Rotation.from_matrix(target.T @ actual[:3, :3]).as_rotvec() * 0.2
            ]

        result = least_squares(
            lambda q: np.r_[error(q), (q - np.asarray(initial)) * 1e-5],
            initial,
            bounds=(self.lower + 1e-4, self.upper - 1e-4),
            ftol=1e-11,
            xtol=1e-11,
            gtol=1e-11,
            max_nfev=400,
        )
        if np.linalg.norm(error(result.x)) > 1e-4:
            raise ValueError(f"Unreachable taught point {position}: {error(result.x)}")
        return result.x.tolist()


def teach():
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
        approach = teacher.solve(np.array(point) + [0, 0, 0.15], grasp)
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
            }
        )
    return {"locations": locations}, evidence


def capture(args):
    """Merge one measured joint vector; preserve all other taught positions."""
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
        if summary.get("source") != "newton" or summary.get("sceneId") != "robot-and-tubes":
            raise ValueError("FR3 teaching requires a Newton robot-and-tubes session")
        response = client.get(route + "/state", params={"keys": "/World/Lab/RobotA"})
        response.raise_for_status()
        state = response.json()["entities"]["/World/Lab/RobotA"]
        joints = state["values"]["jointPositions"]
        if len(joints) != 7 or not all(np.isfinite(joints)):
            raise ValueError("The current FR3 state must contain seven finite joint positions")
        target = args.output or args.input
        source = target if target.exists() else args.input
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
        if args.capture not in document["locations"]:
            raise ValueError(f"Unknown taught location {args.capture}")
        location = document["locations"][args.capture]
        manifest = client.get(route + "/scene")
        manifest.raise_for_status()
        manifest = manifest.json()
        snapshot = client.get(route + "/snapshot")
        snapshot.raise_for_status()
        poses = {row[0]: {"p": row[1:4], "q": row[4:]} for row in snapshot.json()["poses"]}
        bodies = {node["entityKey"]: node["streamId"] for node in manifest["nodes"] if node["kind"] == "body"}
        site = next(site for site in manifest["sites"] if site["entityKey"] == location["site"])
        world = (
            compose_pose(poses[bodies[site["parentBodyKey"]]], site["localPose"])
            if site.get("parentBodyKey")
            else site["localPose"]
        )
        tcp = Teacher().fk(joints)[:3, 3]
        distance = float(np.linalg.norm(tcp - world["p"]))
        location[args.pose] = list(joints)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
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
            "tcpFk": tcp.tolist(),
            "siteWorld": world["p"],
            "tcpErrorM": distance,
            "graspDistanceWarning": warning,
            "physicsImported": "newton" in sys.modules or "warp" in sys.modules,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Regenerate all default offline joint waypoints")
    mode.add_argument("--capture", help="Capture current jointPositions into one existing location")
    parser.add_argument("--pose", choices=("approach", "grasp"), default="grasp")
    parser.add_argument("--input", type=Path, default=LOCATIONS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--url", default=os.getenv("LW_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--session", default=os.getenv("LW_SESSION"))
    args = parser.parse_args()
    if args.capture:
        print(json.dumps(capture(args), indent=2))
    else:
        document, evidence = teach()
        if args.write or args.output:
            target = args.output or args.input
            target.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        print(json.dumps(evidence, indent=2))
