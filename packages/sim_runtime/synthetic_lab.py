"""Spatial content for virtual devices selected by a versioned lab-demo package."""

import uuid

from packages.programs.robot_arm.virtual import BASE_HEIGHT
from packages.protocol.schema import SceneManifest

IDENTITY = {"p": [0, 0, 0], "q": [0, 0, 0, 1]}
BASE = "/World/Lab"
LINK1, LINK2 = BASE + "/Robot/Link1", BASE + "/Robot/Link2"
JOINT1, JOINT2 = BASE + "/Robot/Joint1", BASE + "/Robot/Joint2"
ROBOT, CENTRIFUGE = BASE + "/RobotA", BASE + "/Centrifuge"


def _primitive(geometry, p=(0, 0, 0)):
    return {"kind": "primitive", "geometry": geometry, "localPose": {"p": p, "q": [0, 0, 0, 1]}}


LAYOUT = {
    "incubator/v1": ("Incubator", [-1.45, 0, 0.7], [0.3, 0.3, 0.7]),
    "biosafety-cabinet/v1": ("Biosafety cabinet", [-1.3, -1, 0.9], [0.6, 0.3, 0.9]),
    "env-sensor/v1": ("Environment sensor", [1.4, 0.6, 0.85], [0.08, 0.04, 0.1]),
}


def device_nodes(bindings):
    nodes = []
    for binding in bindings:
        if binding["type"] not in LAYOUT:
            continue
        name, position, extents = LAYOUT[binding["type"]]
        nodes.append(
            {
                "kind": "static",
                "entityKey": binding["key"],
                "name": name,
                "parentEntityKey": "/World/Lab",
                "initialPose": {"p": position, "q": [0, 0, 0, 1]},
                "visuals": [
                    {
                        "kind": "primitive",
                        "geometry": {"type": "box", "halfExtents": extents},
                        "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]},
                    }
                ],
            }
        )
    return nodes


def build_manifest(session_id, options, scene_package=None, *, declarations=None):
    nodes = [
        {"kind": "group", "entityKey": "/World", "name": "World"},
        {"kind": "group", "entityKey": BASE, "name": "Lab", "parentEntityKey": "/World"},
        {
            "kind": "group",
            "entityKey": BASE + "/Robot",
            "name": "Two-link Robot",
            "parentEntityKey": ROBOT,
        },
        {"kind": "group", "entityKey": BASE + "/Tubes", "name": "20 Tubes", "parentEntityKey": BASE},
        {
            "kind": "group",
            "entityKey": BASE + "/Centrifuge",
            "name": "Centrifuge",
            "parentEntityKey": BASE,
        },
        {
            "kind": "static",
            "entityKey": BASE + "/Floor",
            "name": "Floor",
            "parentEntityKey": BASE,
            "initialPose": IDENTITY,
            "visuals": [_primitive({"type": "plane", "width": 10, "length": 10})],
        },
        {
            "kind": "static",
            "entityKey": BASE + "/Table",
            "name": "Workbench",
            "parentEntityKey": BASE,
            "initialPose": {"p": [0.4, 0, 0.375], "q": [0, 0, 0, 1]},
            "visuals": [_primitive({"type": "box", "halfExtents": [1.3, 0.8, 0.375]})],
        },
        {
            "kind": "body",
            "entityKey": LINK1,
            "name": "Link1",
            "parentEntityKey": BASE + "/Robot",
            "streamId": 1,
            "visuals": [_primitive({"type": "capsule", "radius": 0.06, "halfHeight": 0.24}, (0, 0, 0.3))],
        },
        {
            "kind": "body",
            "entityKey": LINK2,
            "name": "Link2",
            "parentEntityKey": BASE + "/Robot",
            "streamId": 2,
            "visuals": [_primitive({"type": "capsule", "radius": 0.045, "halfHeight": 0.2}, (0, 0, 0.25))],
        },
        {
            "kind": "body",
            "entityKey": BASE + "/Centrifuge/Rotor",
            "name": "Rotor",
            "parentEntityKey": BASE + "/Centrifuge",
            "streamId": 30,
            "visuals": [_primitive({"type": "box", "halfExtents": [0.22, 0.04, 0.025]})],
        },
        {
            "kind": "body",
            "entityKey": BASE + "/FallbackBox",
            "name": "Missing asset fallback",
            "parentEntityKey": BASE,
            "streamId": 31,
            "visuals": [
                {
                    "kind": "glb",
                    "url": "/api/v1/assets/missing-reference.glb",
                    "assetUpAxis": "Y",
                    "scale": [1, 1, 1],
                    "localPose": IDENTITY,
                }
            ],
            "colliders": [_primitive({"type": "box", "halfExtents": [0.1, 0.1, 0.1]})],
        },
    ]
    for index in range(20):
        nodes.append(
            {
                "kind": "body",
                "entityKey": BASE + f"/Tubes/Tube{index + 1:02d}",
                "name": f"Tube {index + 1:02d}",
                "parentEntityKey": BASE + "/Tubes",
                "streamId": 10 + index,
                "visuals": [
                    _primitive({"type": "cylinder", "radius": 0.008, "halfHeight": 0.04}, (0, 0, 0.04))
                ],
            }
        )
    nodes.extend(
        [
            {"kind": "group", "entityKey": ROBOT, "name": "Robot arm", "parentEntityKey": BASE},
            {"kind": "group", "entityKey": BASE + "/Rack", "name": "Tube rack", "parentEntityKey": BASE},
            {
                "kind": "static",
                "entityKey": BASE + "/Rack/Base",
                "name": "Tube rack base",
                "parentEntityKey": BASE + "/Rack",
                "initialPose": {"p": [0.7, -0.2, 0.78], "q": [0, 0, 0, 1]},
                "visuals": [_primitive({"type": "box", "halfExtents": [0.25, 0.18, 0.02]})],
            },
            {
                "kind": "static",
                "entityKey": CENTRIFUGE + "/Housing",
                "name": "Centrifuge housing",
                "parentEntityKey": CENTRIFUGE,
                "initialPose": {"p": [-0.5, 0.4, 0.8], "q": [0, 0, 0, 1]},
                "visuals": [_primitive({"type": "box", "halfExtents": [0.22, 0.22, 0.05]})],
            },
            {
                "kind": "body",
                "entityKey": ROBOT + "/Hand",
                "name": "Hand",
                "parentEntityKey": ROBOT,
                "streamId": 3,
                "visuals": [_primitive({"type": "box", "halfExtents": [0.04, 0.04, 0.025]})],
            },
            {
                "kind": "body",
                "entityKey": CENTRIFUGE + "/Lid",
                "name": "Lid",
                "parentEntityKey": CENTRIFUGE,
                "streamId": 32,
                "visuals": [_primitive({"type": "box", "halfExtents": [0.2, 0.2, 0.015]}, (0.2, 0, 0))],
            },
        ]
    )
    import yaml

    from packages.library.scenes import builtin_scene_path

    if scene_package:
        nodes.extend(device_nodes((declarations or scene_package.bindings)["entities"]))

    locations = yaml.safe_load(
        (
            (scene_package.path if scene_package else builtin_scene_path("lab-demo")) / "locations.yaml"
        ).read_text(encoding="utf-8")
    )["locations"]
    slot_positions = {
        f"centrifuge.slot{index + 1}": point
        for index, point in enumerate(((0.1, 0, 0.1), (0, 0.1, 0.1), (-0.1, 0, 0.1), (0, -0.1, 0.1)))
    }
    sites = []
    for name, location in locations.items():
        if name.startswith("centrifuge"):
            if name not in slot_positions:
                raise ValueError(
                    f"Unsupported Synthetic centrifuge slot: {name}; expected centrifuge.slot1–4"
                )
            local = slot_positions[name]
            sites.append(
                {
                    "entityKey": location["site"],
                    "parentBodyKey": CENTRIFUGE + "/Rotor",
                    "role": "generic",
                    "localPose": {"p": local, "q": [0, 0, 0, 1]},
                }
            )
        else:
            sites.append(
                {
                    "entityKey": location["site"],
                    "role": "generic",
                    "localPose": {"p": location["p"], "q": [0, 0, 0, 1]},
                }
            )
    return SceneManifest.model_validate(
        {
            "sessionId": session_id,
            "sceneRevision": "rev-" + uuid.uuid4().hex[:12],
            "revisionNumericId": 1,
            "epoch": 0,
            "sceneName": (scene_package.metadata["title"] if scene_package else "Synthetic Laboratory"),
            "source": {
                "kind": "synthetic",
                "solver": "DeterministicKinematics",
                "physicsDt": options.physicsDt,
                "basePublishHz": options.basePublishHz,
                "realtimeFactor": options.realtimeFactor,
            },
            "nodes": nodes,
            "sites": sites
            + [
                {
                    "entityKey": JOINT1 + "/anchor",
                    "role": "joint-anchor",
                    "parentBodyKey": LINK1,
                    "jointKey": JOINT1,
                    "localPose": IDENTITY,
                },
                {
                    "entityKey": JOINT2 + "/anchor",
                    "role": "joint-anchor",
                    "parentBodyKey": LINK2,
                    "jointKey": JOINT2,
                    "localPose": IDENTITY,
                },
                {
                    "entityKey": BASE + "/Robot/tcp",
                    "role": "generic",
                    "parentBodyKey": ROBOT + "/Hand",
                    "localPose": IDENTITY,
                },
                {
                    "entityKey": ROBOT + "/tcp",
                    "role": "generic",
                    "parentBodyKey": ROBOT + "/Hand",
                    "localPose": IDENTITY,
                },
            ],
            "joints": [
                {
                    "entityKey": JOINT1,
                    "type": "revolute",
                    "parentBodyKey": None,
                    "childBodyKey": LINK1,
                    "parentLocalPose": {"p": [0, 0, BASE_HEIGHT], "q": [0, 0, 0, 1]},
                    "axis": [0, 1, 0],
                    "anchorSiteKey": JOINT1 + "/anchor",
                    "dofCount": 1,
                },
                {
                    "entityKey": JOINT2,
                    "type": "revolute",
                    "parentBodyKey": LINK1,
                    "childBodyKey": LINK2,
                    "parentLocalPose": {"p": [0, 0, 0.6], "q": [0, 0, 0, 1]},
                    "axis": [0, 1, 0],
                    "anchorSiteKey": JOINT2 + "/anchor",
                    "dofCount": 1,
                },
            ],
            "warnings": [
                {
                    "code": "asset_missing",
                    "entityKey": BASE + "/FallbackBox",
                    "message": "Intentional missing GLB exercises collider fallback",
                },
                {
                    "code": "asset_fallback",
                    "entityKey": BASE + "/FallbackBox",
                    "message": "Use primitive collider when the GLB is unavailable",
                },
            ],
        }
    )
